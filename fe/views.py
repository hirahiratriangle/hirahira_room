"""FE 対策アプリのビュー。

出題は 1 問 1 答。GET で 1 問出し，POST で採点してその場で解説を出す。
出題する問題の選定は fe.selection，成績の集計は fe.stats に分けてある。

成績は Learner（本アプリの中の ID）ごとに持つ。/fe/ で ID を入力すると
セッションが覚え、以後の画面はその ID の分を出す。ダッシュボードだけは
/fe/<ID>/ に置き、ほかの ID のものは開けない（404）。問題と技術解説は
全 ID で共有し、取り込みなどの管理は管理用の ID だけができる。

サイト全体は LoginRequiredMiddleware でログイン必須だが、本アプリの学習の画面は
login_not_required で外してあり、hirahira_room にログインしなくても使える。
問題の管理だけは、これまでどおりログインも要る。
"""

import logging

import hmac
import json
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.core.validators import validate_email
from django.utils.decorators import method_decorator
from django.views import generic
from django.views.decorators.csrf import csrf_exempt

from .exam import EXAM, build_prompt
from .forms import LearnerForm, PassReportForm, QuestionUploadForm
from .importer import (ImportError_, needs_confirmation, parse,
                       preview, sync, validate)
from .learning import current_item, focus_category, grade, note_menu, start_round
from . import mail, speech, x_post
from .models import (Attempt, Category, CategoryProgress, Learner, LearningNote,
                     LearningRound, MailSubscription, PassReport, Question,
                     QuestionTemplate, StudySession, XPost)
from .selection import pick_question
from .stats import (categories_for, category_options, category_stats, daily_counts,
                    field_stats, overall_stats, record_progress, site_summary,
                    weak_categories)

logger = logging.getLogger(__name__)

# 直前に出した問題が続けて出ないようにするための履歴の長さ
RECENT_LIMIT = 12
SESSION_RECENT = 'fe_recent_question_ids'
SESSION_PENDING = 'fe_pending_question_id'
SESSION_SHOWN_AT = 'fe_question_shown_at'
# 入っている ID。hirahira_room のアカウントとは別に、ブラウザのセッションで持つ。
SESSION_LEARNER = 'fe_learner_id'
# 入っている ID を、ブラウザを閉じたら消える Cookie にも持たせる。セッションは
# 閉じても2週間残る（ログインの「ログインしたままにする」と同じ入れ物）ので、
# それだけだと共用の端末で次の人が前の人の ID のまま入ってしまう。
LEARNER_COOKIE = 'fe_learner'
LEARNER_COOKIE_SALT = 'fe.learner'


def _get_study_session(learner):
    session, _ = StudySession.objects.get_or_create(learner=learner)
    return session


def _enter(request, learner):
    """その ID で入る。前の ID の出題途中の状態は持ち越さない。"""
    for key in (SESSION_RECENT, SESSION_PENDING, SESSION_SHOWN_AT):
        request.session.pop(key, None)
    request.session[SESSION_LEARNER] = learner.pk
    # 応答を返すときに、ブラウザを閉じたら消える Cookie を置く（remember_entry）
    request.fe_entered = learner.pk


def remember_entry(request, response):
    """入ったばかりなら、ブラウザを閉じたら消える Cookie に ID を置く。

    期限を付けないので、ブラウザを閉じると消える。消えたら current_learner が
    セッションの ID も捨てるので、次に開いたときは入口からになる。
    """
    pk = getattr(request, 'fe_entered', None)
    if pk is not None:
        response.set_signed_cookie(
            LEARNER_COOKIE, str(pk), salt=LEARNER_COOKIE_SALT,
            httponly=True, samesite='Lax', secure=request.is_secure(),
        )
    return response


def current_learner(request):
    """セッションが覚えている記録。始めていなければ None。

    ID を登録した人も、履歴を残さずに使っている人も、同じようにここから取る。
    ブラウザを閉じて Cookie が消えていれば、セッションに残っていても入っていない
    扱いにする。
    """
    pk = request.session.get(SESSION_LEARNER)
    if pk is None:
        return None
    entered = getattr(request, 'fe_entered', None) == pk or request.get_signed_cookie(
        LEARNER_COOKIE, default=None, salt=LEARNER_COOKIE_SALT,
    ) == str(pk)
    if not entered:
        request.session.pop(SESSION_LEARNER, None)
        return None
    learner = Learner.objects.filter(pk=pk).first()
    if learner is None:
        request.session.pop(SESSION_LEARNER, None)
    return learner


def start_temporary(request):
    """履歴を残さずに始める。ID を持たない記録を作ってセッションに結び付ける。"""
    learner = Learner.objects.create(code=None)
    _enter(request, learner)
    return learner


def touch(learner):
    """最終利用日時を進める。使われなくなった一時的な記録を見分けるため。

    登録済みの ID は消さないので、一時的な記録のときだけ書き込む。
    """
    if learner.is_temporary:
        Learner.objects.filter(pk=learner.pk).update(last_seen_at=timezone.now())


@method_decorator(login_not_required, name='dispatch')
class LearnerRequiredMixin:
    """始めている人向けの画面。始めていなければトップへ戻す。

    hirahira_room へのログインは求めない。いまの記録は self.learner に置く。
    ヘッダーに出すため、request.fe_learner にも載せておく。
    """

    def dispatch(self, request, *args, **kwargs):
        self.learner = current_learner(request)
        if self.learner is None:
            return redirect('fe:index')
        request.fe_learner = self.learner
        touch(self.learner)
        self.check_learner(self.learner)
        return super().dispatch(request, *args, **kwargs)

    def check_learner(self, learner):
        """入っている ID でこの画面を使えるか。使えなければ例外を投げる。"""


class AdminRequiredMixin(LoginRequiredMixin, LearnerRequiredMixin):
    """問題集を管理する画面。管理用の ID で、スタッフ権限のあるアカウントでなければ 403。

    ID には合言葉がないので、ID だけでは通さない。hirahira_room にログインして
    いるだけでも通さない。アカウントの新規作成は止めたが、以前に作られたものが
    残っているため、ログインは「本人である」ことの証明にならない。スタッフ権限
    （is_staff）は Django の管理画面でしか付けられないので、それを条件にする。
    """

    def check_learner(self, learner):
        if not learner.is_admin:
            raise PermissionDenied('問題集を管理できるのは管理用の ID だけです。')
        if not self.request.user.is_staff:
            raise PermissionDenied(
                '問題集を管理できるのは、スタッフ権限のあるアカウントだけです。'
            )


def dashboard_url(learner):
    """ダッシュボードの場所。履歴を残さない利用は URL に出さず、入口がそのまま画面になる。"""
    if learner.is_temporary:
        return reverse('fe:index')
    return reverse('fe:dashboard', args=[learner.code])


@method_decorator(login_not_required, name='dispatch')
class EnterView(generic.FormView):
    """ID で入る、または新しく作る。

    すでに入っていても開ける。別の ID を入力すれば、その ID に切り替わる。
    """

    template_name = 'fe/enter.html'
    form_class = LearnerForm

    def dispatch(self, request, *args, **kwargs):
        # 入ったら、ブラウザを閉じたら消える Cookie を置く。入口（IndexView）も同じ
        return remember_entry(request, super().dispatch(request, *args, **kwargs))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['current'] = current_learner(self.request)
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['creating'] = self.request.POST.get('action') == 'create'
        return kwargs

    def form_valid(self, form):
        if form.creating:
            learner = Learner.objects.create(code=form.cleaned_data['code'])
            messages.success(
                self.request,
                'ID「{}」を作りました。これからの解答はこの ID に残ります。'.format(learner.code),
            )
        else:
            learner = form.learner
        # 履歴を残さずに使っていた分は引き継がない。放っておけば掃除で消える。
        _enter(self.request, learner)
        return redirect(dashboard_url(learner))


class IndexView(EnterView):
    """アプリの入口。

    まだ始めていなければ、履歴を残すかどうかを選ぶ画面を出す。
    ID で入っていれば /fe/<ID>/ へ送り、履歴を残さない利用なら
    この場所がそのままダッシュボードになる。
    """

    template_name = 'fe/start.html'

    def get(self, request, *args, **kwargs):
        learner = current_learner(request)
        if learner is None:
            return super().get(request, *args, **kwargs)
        if learner.is_temporary:
            touch(learner)
            request.fe_learner = learner
            return DashboardView.render_for(request, learner)
        return redirect(dashboard_url(learner))

    def post(self, request, *args, **kwargs):
        """「履歴を残さずに始める」。ID の登録は EnterView 側で扱う。"""
        if request.POST.get('action') == 'temporary':
            start_temporary(request)
            return redirect('fe:index')
        return super().post(request, *args, **kwargs)


class DashboardView(LearnerRequiredMixin, generic.TemplateView):
    """ダッシュボード（マイページ）。今の実力と，次に何をやるべきかを一目で分かるようにする。

    URL の ID は、いま入っている ID のときだけ開ける。ほかの ID なら 404。
    履歴を残さない利用には URL がないので、IndexView から render_for で描く。
    """

    template_name = 'fe/index.html'

    @classmethod
    def render_for(cls, request, learner):
        """URL を持たない利用のために、ダッシュボードをその場で描く。"""
        view = cls()
        view.request = request
        view.learner = learner
        view.kwargs = {}
        view.args = ()
        return view.render_to_response(view.get_context_data())

    def get(self, request, code, *args, **kwargs):
        if code.lower() != self.learner.code:
            raise Http404
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        learner = self.learner
        context['learner'] = learner

        rows = category_stats(learner)
        context['overall'] = overall_stats(learner)
        context['fields'] = field_stats(rows)
        context['weak'] = weak_categories(rows, limit=5)
        context['untouched'] = [r for r in rows if r['is_untouched'] and r['question_count']]
        context['study_session'] = _get_study_session(learner)
        # 出題設定のフォームを他の画面と共有しているので、分野の選択肢も渡す
        context['categories'] = category_options(
            learner, context['study_session'].mode
        )
        context['daily'] = daily_counts(learner, days=14)
        context['exam'] = EXAM
        context['question_total'] = Question.objects.active().filter(
            template__isnull=True
        ).count()
        context['template_total'] = QuestionTemplate.objects.filter(is_active=True).count()
        context['max_daily'] = max([d['total'] for d in context['daily']] + [1])
        # 全 ID を合わせた集計と、自分の合格申告
        context['summary'] = site_summary()
        context['my_report'] = PassReport.objects.filter(learner=learner).first()
        context['pass_form'] = PassReportForm()
        context.update(mail_context(learner))
        return context


class QuizSettingsView(LearnerRequiredMixin, generic.View):
    """出題モード・科目・分野の変更。"""

    def post(self, request, *args, **kwargs):
        session = _get_study_session(self.learner)
        mode = request.POST.get('mode')
        subject = request.POST.get('subject')
        category_code = request.POST.get('category')

        valid_modes = {choice[0] for choice in StudySession.MODE_CHOICES}
        if mode in valid_modes:
            session.mode = mode
        if subject in {Question.SUBJECT_A, Question.SUBJECT_B}:
            session.subject = subject

        # 分野はどのモードでも選べる。選んだら、そのモードの出し方のまま
        # その分野の中だけから出す。選ばなければ全分野から出す。
        session.category = None
        if category_code:
            session.category = Category.objects.filter(
                code=category_code, subject=session.subject
            ).first()
            if session.category is None:
                messages.error(request, 'その分野は選んだ科目にありません。')
                # 「分野を指定」は分野が無いと成り立たない。あとの2つは分野なしで続ける。
                if session.mode == StudySession.MODE_CATEGORY:
                    session.mode = StudySession.MODE_FOCUS

        session.save()
        # 設定を変えたら履歴の除外はいったんリセットする
        request.session[SESSION_RECENT] = []
        # ダッシュボードから変えたときは、そのまま戻して結果を確かめられるようにする。
        # 演習画面からなら、次の問題へ進む。
        if request.POST.get('next') == 'index':
            return redirect(dashboard_url(self.learner))
        return redirect('fe:quiz')


class QuizView(LearnerRequiredMixin, generic.View):
    """1 問 1 答の出題と採点。"""

    template_name = 'fe/quiz.html'

    def get(self, request, *args, **kwargs):
        study_session = _get_study_session(self.learner)
        recent = request.session.get(SESSION_RECENT, [])
        question, reason = pick_question(self.learner, study_session, exclude_ids=recent)

        if question is None:
            return render(request, self.template_name, {
                'study_session': study_session,
                'categories': category_options(self.learner, study_session.mode),
                'no_question': True,
            })

        request.session[SESSION_PENDING] = question.id
        request.session[SESSION_SHOWN_AT] = timezone.now().isoformat()

        return render(request, self.template_name, {
            'question': question,
            'reason': reason,
            'study_session': study_session,
            'categories': category_options(self.learner, study_session.mode),
            'progress': self._progress(self.learner, question.category),
        })

    def post(self, request, *args, **kwargs):
        study_session = _get_study_session(self.learner)
        pending_id = request.session.get(SESSION_PENDING)
        question_id = request.POST.get('question_id')

        if not question_id or str(pending_id) != str(question_id):
            # 二重送信やブラウザの戻る操作。採点せずに次の問題へ送る。
            messages.info(request, 'その問題は既に解答済みです。次の問題を表示します。')
            return redirect('fe:quiz')

        question = (
            Question.objects.filter(id=pending_id)
            .select_related('category').first()
        )
        if question is None:
            return redirect('fe:quiz')

        selected = request.POST.get('choice')
        if selected is None or not selected.isdigit() or int(selected) >= len(question.choices):
            messages.error(request, '選択肢を選んでください。')
            return render(request, self.template_name, {
                'question': question,
                'study_session': study_session,
                'categories': category_options(self.learner, study_session.mode),
                'progress': self._progress(self.learner, question.category),
            })

        selected = int(selected)
        is_correct = selected == question.answer_index

        elapsed_ms = None
        shown_at = request.session.get(SESSION_SHOWN_AT)
        if shown_at:
            try:
                delta = timezone.now() - timezone.datetime.fromisoformat(shown_at)
                # 途中で離席した場合に極端な値が入らないよう上限を設ける
                elapsed_ms = min(int(delta.total_seconds() * 1000), 30 * 60 * 1000)
            except ValueError:
                elapsed_ms = None

        Attempt.objects.create(
            learner=self.learner,
            question=question,
            category=question.category,
            selected_index=selected,
            is_correct=is_correct,
            elapsed_ms=elapsed_ms,
        )
        # 問題が入れ替わっても残る記録。分野別の正答率と苦手判定はこちらを見る。
        record_progress(self.learner, question, is_correct)

        # 採点済みなので保留を落とし，直近履歴に積む
        request.session.pop(SESSION_PENDING, None)
        request.session.pop(SESSION_SHOWN_AT, None)
        recent = request.session.get(SESSION_RECENT, [])
        recent.append(question.id)
        request.session[SESSION_RECENT] = recent[-RECENT_LIMIT:]

        return render(request, self.template_name, {
            'question': question,
            'study_session': study_session,
            'categories': category_options(self.learner, study_session.mode),
            'answered': True,
            'selected': selected,
            'selected_label': Question.choice_label(selected),
            'is_correct': is_correct,
            'elapsed_ms': elapsed_ms,
            'progress': self._progress(self.learner, question.category),
        })

    @staticmethod
    def _progress(learner, category):
        """その中分類の現在の成績（解答直後に見せる）。"""
        totals = CategoryProgress.objects.filter(learner=learner, category=category).aggregate(
            total=Sum('answered'), correct=Sum('correct')
        )
        total = totals['total'] or 0
        correct = totals['correct'] or 0
        return {
            'category': category,
            'total': total,
            'correct': correct,
            'rate_percent': round(correct / total * 100) if total else None,
        }


class StatsView(LearnerRequiredMixin, generic.TemplateView):
    """分野別の正答率の一覧。"""

    template_name = 'fe/stats.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        learner = self.learner
        subject = self.request.GET.get('subject')
        if subject not in (Question.SUBJECT_A, Question.SUBJECT_B):
            subject = None

        rows = category_stats(learner, subject=subject)
        context['rows'] = rows
        context['fields'] = field_stats(rows)
        context['overall'] = overall_stats(learner)
        context['weak'] = weak_categories(rows, limit=5)
        context['subject'] = subject
        context['subject_choices'] = Question.SUBJECT_CHOICES
        # 重点出題でどれだけ重みが乗っているかを見せる
        total_weight = sum(
            r['category'].exam_weight * (0.3 + 2.0 * r['weakness'])
            for r in rows if r['question_count']
        ) or 1
        for row in rows:
            weight = row['category'].exam_weight * (0.3 + 2.0 * row['weakness'])
            row['focus_share'] = round(weight / total_weight * 100, 1) if row['question_count'] else 0.0
        return context


class PassReportView(LearnerRequiredMixin, generic.View):
    """本番合格の申告と取り消し。1人1件で、申告し直すと受験日を上書きする。

    履歴を残さない利用からは申告できない。消える記録なので、
    合格者数に数えると実態と合わなくなるため。
    """

    def check_learner(self, learner):
        if learner.is_temporary:
            raise PermissionDenied(
                '合格の申告には ID の登録が必要です。履歴を残さない利用では申告できません。'
            )

    def post(self, request, *args, **kwargs):
        back = dashboard_url(self.learner) + '#summary'
        if request.POST.get('action') == 'withdraw':
            PassReport.objects.filter(learner=self.learner).delete()
            messages.info(request, '合格の申告を取り消しました。')
            return redirect(back)

        form = PassReportForm(request.POST)
        if not form.is_valid():
            for error in form.errors.get('passed_on', []):
                messages.error(request, error)
            return redirect(back)
        PassReport.objects.update_or_create(
            learner=self.learner, defaults={'passed_on': form.cleaned_data['passed_on']},
        )
        messages.success(request, '合格おめでとうございます。合格者数に数えました。')
        return redirect(back)


class HistoryView(LearnerRequiredMixin, generic.ListView):
    """解答履歴。まちがえた問題を見返すために使う。"""

    template_name = 'fe/history.html'
    context_object_name = 'attempts'
    paginate_by = 20

    def get_queryset(self):
        queryset = (
            Attempt.objects.filter(learner=self.learner)
            .select_related('question', 'category')
        )
        if self.request.GET.get('result') == 'wrong':
            queryset = queryset.filter(is_correct=False)
        elif self.request.GET.get('result') == 'correct':
            queryset = queryset.filter(is_correct=True)
        category_code = self.request.GET.get('category')
        if category_code and category_code.isdigit():
            queryset = queryset.filter(category__code=category_code)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['categories'] = categories_for(_get_study_session(self.learner).subject)
        context['result_filter'] = self.request.GET.get('result', '')
        context['category_filter'] = self.request.GET.get('category', '')
        return context


# ================================================================ 問題の管理
# 問題は全 ID で共有し、JSON の取り込みが唯一の作成・更新経路。
# 画面に入力フォームを置かないので、手元の JSON と画面の内容がずれない。
# 一覧・取り込みは、管理用の ID だけが使える。


class ManageListView(AdminRequiredMixin, generic.ListView):
    """共有の問題の一覧。中身の確認だけを行う。"""

    template_name = 'fe/manage_list.html'
    context_object_name = 'questions'
    paginate_by = 25

    def get_queryset(self):
        queryset = (
            Question.objects.select_related('category', 'template')
            .annotate(attempt_count=Count('attempts'))
        )
        params = self.request.GET
        if params.get('subject') in (Question.SUBJECT_A, Question.SUBJECT_B):
            queryset = queryset.filter(subject=params['subject'])
        if params.get('category', '').isdigit():
            queryset = queryset.filter(category__code=params['category'])
        keyword = params.get('q', '').strip()
        if keyword:
            queryset = queryset.filter(stem__icontains=keyword)
        if params.get('generated') != 'on':
            queryset = queryset.filter(template__isnull=True)
        return queryset.order_by('category__code', 'id')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        mine = Question.objects.filter(template__isnull=True)
        context['categories'] = Category.objects.all()  # 一覧は両科目を横断して絞り込む
        context['filters'] = self.request.GET
        context['query_string'] = self.request.GET.urlencode()
        context['summary'] = {
            'total': mine.count(),
            'subject_a': mine.filter(subject=Question.SUBJECT_A).count(),
            'subject_b': mine.filter(subject=Question.SUBJECT_B).count(),
            'generated': Question.objects.filter(template__isnull=False).count(),
        }
        return context


class QuestionUploadView(AdminRequiredMixin, generic.FormView):
    """JSON を取り込んで、共有の問題集を差し替える。"""

    template_name = 'fe/manage_upload.html'
    form_class = QuestionUploadForm
    success_url = reverse_lazy('fe:manage_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['exam'] = EXAM
        context['prompt'] = build_prompt()
        return context

    @staticmethod
    def _count_lines(result):
        """追加・更新・変更なし・削除の件数を、画面に出す文にする。"""
        lines = []
        for label, key in (('問題', 'questions'), ('解説', 'notes')):
            counts = result[key]
            if counts is None:
                lines.append('{}：変更なし（ファイルに{}が含まれていません）'.format(label, label))
                continue
            lines.append(
                '{}（{}）：追加 {} / 更新 {} / 変更なし {} / 削除 {}'.format(
                    label, '・'.join('科目' + s for s in counts['subjects']),
                    counts['added'], counts['updated'],
                    counts['unchanged'], counts['removed'],
                )
            )
        return lines

    def form_valid(self, form):
        # まず書き込まずに数える。削除が多いときは、確定の前に一度止める。
        counts = preview(form.records)
        if needs_confirmation(counts) and self.request.POST.get('confirm') != '1':
            return self.render_to_response(self.get_context_data(
                form=form, confirm=self._count_lines(counts),
                payload=json.dumps(form.records, ensure_ascii=False),
            ))

        result = sync(form.records)
        messages.success(self.request, '　'.join(self._count_lines(result)))
        return super().form_valid(form)

    def post(self, request, *args, **kwargs):
        """確認のあとの実行。ファイルを選び直さずに済むよう、内容を持ち回す。"""
        if request.POST.get('confirm') == '1' and 'payload' in request.POST:
            form = self.get_form()
            try:
                form.records = validate(parse(request.POST['payload']))
            except ImportError_ as exc:
                messages.error(request, str(exc))
                return redirect('fe:manage_upload')
            result = sync(form.records)
            messages.success(request, '　'.join(self._count_lines(result)))
            return redirect(self.success_url)
        return super().post(request, *args, **kwargs)


class LearnStartView(LearnerRequiredMixin, generic.View):
    """学習モードの入口。読む時間を決めてラウンドを始める。"""

    template_name = 'fe/learn_start.html'

    def get(self, request, *args, **kwargs):
        session = _get_study_session(self.learner)
        finished = LearningRound.objects.filter(
            learner=self.learner, phase=LearningRound.PHASE_DONE
        )
        last = (
            finished.filter(subject=session.subject, note__isnull=False)
            .select_related('note__category').first()
        )
        return render(request, self.template_name, {
            'minute_choices': LearningRound.MINUTE_CHOICES,
            'default_minutes': LearningRound.DEFAULT_MINUTES,
            'count': LearningRound.QUESTION_COUNT,
            'study_session': session,
            'groups': note_menu(self.learner, session.subject),
            'opened': self._opened_categories(session, last),
            'recent': finished[:5],
        })

    @staticmethod
    def _opened_categories(session, last):
        """一覧で開いておく中分類。次の2つを、重ねずに両方開く。

        ・ダッシュボードで指定した分野。おまかせはこの中から選ぶので、開いておくと
          何が対象か分かる。開かないと、分野をどう選び直しても一覧の開く先が変わらず、
          前に解き終えた中分類に固定されているように見えた。
        ・最後に解き終えたラウンドの中分類。学習した結果（学習回数）は小分類の行に
          出るので、閉じたままだと、学習したことが一覧に反映されていないように見える。

        どちらか一方だけにすると、学習モードで指定とは別の分野を解き終えたときに、
        ダッシュボードの指定と一覧の開く先がずれる。指定のほうを書き換えて合わせると、
        おまかせや1問1答の出題範囲まで変わってしまうので、両方を開いて済ませる。
        指定が今の科目のものでなければ（focus_category が None なら）使わない。
        """
        opened = []
        for category in (focus_category(session), last.note.category if last else None):
            if category is not None and category not in opened:
                opened.append(category)
        return opened

    def post(self, request, *args, **kwargs):
        raw = request.POST.get('minutes')
        minutes = int(raw) if raw and raw.isdigit() else LearningRound.DEFAULT_MINUTES
        if minutes not in LearningRound.MINUTE_CHOICES:
            minutes = LearningRound.DEFAULT_MINUTES

        # 空なら「おまかせ」
        note = None
        chosen = request.POST.get('note')
        if chosen and chosen.isdigit():
            note = LearningNote.objects.filter(pk=chosen).select_related('category').first()
            if note is None:
                messages.error(request, 'その解説は見つかりませんでした。')
                return redirect('fe:learn_start')

        session = _get_study_session(self.learner)
        round_ = start_round(self.learner, session, minutes, note=note)
        if round_ is None:
            if note is not None:
                messages.error(
                    request,
                    '「{}」に出題できる問題がありません。'.format(note.title),
                )
                return redirect('fe:learn_start')
            focus = focus_category(session)
            if focus is not None:
                messages.error(
                    request,
                    '{}には、出題できる問題のある技術解説がまだありません。'
                    'ダッシュボードの分野の指定を変えてください。'.format(focus.name),
                )
                return redirect('fe:learn_start')
            messages.error(request, '学習モードに使う技術解説が、まだ用意されていません。')
            # 管理用の ID なら、そのまま取り込みへ案内する
            if self.learner.is_admin:
                return redirect('fe:manage_upload')
            return redirect('fe:learn_start')
        return redirect('fe:learn_read', pk=round_.pk)


class LearnReadView(LearnerRequiredMixin, generic.View):
    """読む段階。設問ではなく、分野ごとにまとめた技術解説を出す。"""

    template_name = 'fe/learn_read.html'

    def get(self, request, pk, *args, **kwargs):
        round_ = get_object_or_404(LearningRound, pk=pk, learner=self.learner)
        # 終わったラウンドの解説は、結果画面から読み返せるようにしておく
        if round_.phase == LearningRound.PHASE_QUIZ:
            return redirect('fe:learn_quiz', pk=round_.pk)

        # 開き直した時点で読み上げは止まっているので、止めていた時間を動かす
        if round_.phase == LearningRound.PHASE_READING:
            round_.resume()
        # 同じ小分類の解説は、まとめて続けて読む
        notes = round_.notes
        for note in notes:
            note.audio_url = (
                reverse('fe:note_audio', args=[speech.audio_name(note)])
                if speech.has_audio(note) else None
            )
        return render(request, self.template_name, {
            'round': round_,
            'notes': notes,
            'finished': round_.phase == LearningRound.PHASE_DONE,
            # 途中で再読み込みしても、残り時間は開始時刻から数え直す
            'remaining': round_.remaining_seconds(),
        })

    def post(self, request, pk, *args, **kwargs):
        round_ = get_object_or_404(LearningRound, pk=pk, learner=self.learner)
        if round_.phase == LearningRound.PHASE_READING:
            round_.phase = LearningRound.PHASE_QUIZ
            round_.save(update_fields=['phase'])
        return redirect('fe:learn_quiz', pk=round_.pk)


class LearnTimerView(LearnerRequiredMixin, generic.View):
    """読む時間を止める・動かす。読み上げを再生している間は止める。

    残り時間はサーバーで数えているので、止めたこともサーバーに残す。画面の
    数字だけ止めると、再読み込みしたときに止めていた分が消える。
    """

    def post(self, request, pk, *args, **kwargs):
        round_ = get_object_or_404(LearningRound, pk=pk, learner=self.learner)
        if round_.phase == LearningRound.PHASE_READING:
            if request.POST.get('state') == 'pause':
                round_.pause()
            else:
                round_.resume()
        return JsonResponse({'remaining': round_.remaining_seconds()})


_RANGE = re.compile(r'^bytes=(\d*)-(\d*)$')


class NoteAudioView(LearnerRequiredMixin, generic.View):
    """技術解説の読み上げ音声を返す。

    iPhone の Safari は音声を「何バイト目から何バイト目まで」と区切って取りに来る
    （Range 要求）。これに 206 で応えないと、再生や途中への移動ができないことがある。
    ファイル名は本文から計算したハッシュで、中身が変わることはないので長く持たせる。
    """

    def get(self, request, name, *args, **kwargs):
        if not speech.NAME_PATTERN.match(name):
            raise Http404
        path = speech.audio_root() / name
        if not path.is_file():
            raise Http404
        size = path.stat().st_size
        match = _RANGE.match(request.headers.get('Range', ''))
        if match is None:
            response = FileResponse(open(path, 'rb'), content_type='audio/mpeg')
        else:
            first, last = match.groups()
            if first:
                start, end = int(first), int(last) if last else size - 1
            elif last:
                # 「末尾の n バイト」の形
                start, end = max(0, size - int(last)), size - 1
            else:
                start, end = 0, size - 1
            end = min(end, size - 1)
            if start > end:
                response = HttpResponse(status=416)
                response['Content-Range'] = 'bytes */{}'.format(size)
                return response
            with open(path, 'rb') as audio:
                audio.seek(start)
                data = audio.read(end - start + 1)
            response = HttpResponse(data, status=206, content_type='audio/mpeg')
            response['Content-Range'] = 'bytes {}-{}/{}'.format(start, end, size)
            response['Content-Length'] = str(len(data))
        response['Accept-Ranges'] = 'bytes'
        response['Cache-Control'] = 'private, max-age=31536000, immutable'
        return response


class LearnQuizView(LearnerRequiredMixin, generic.View):
    """解く段階。読んだ範囲をそのまま1問1答で出す。"""

    template_name = 'fe/learn_quiz.html'

    def get(self, request, pk, *args, **kwargs):
        round_ = get_object_or_404(LearningRound, pk=pk, learner=self.learner)
        if round_.phase == LearningRound.PHASE_READING:
            return redirect('fe:learn_read', pk=round_.pk)
        if round_.phase == LearningRound.PHASE_DONE:
            return redirect('fe:learn_result', pk=round_.pk)

        item = current_item(round_)
        if item is None:
            return self._finish(round_)
        return render(request, self.template_name, {
            'round': round_, 'item': item, 'question': item.question,
        })

    def post(self, request, pk, *args, **kwargs):
        round_ = get_object_or_404(LearningRound, pk=pk, learner=self.learner)
        item = current_item(round_)
        if item is None:
            return self._finish(round_)

        question = item.question
        selected = request.POST.get('choice')
        if selected is None or not selected.isdigit() or int(selected) >= len(question.choices):
            messages.error(request, '選択肢を選んでください。')
            return render(request, self.template_name, {
                'round': round_, 'item': item, 'question': question,
            })

        selected = int(selected)
        if item.is_correct is None:
            is_correct = grade(item, selected)
            # 演習と同じ記録に積む。学習モードで解いた分も理解度に反映する。
            Attempt.objects.create(
                learner=self.learner, question=question, category=question.category,
                selected_index=selected, is_correct=is_correct,
            )
            record_progress(self.learner, question, is_correct)

        return render(request, self.template_name, {
            'round': round_, 'item': item, 'question': question,
            'answered': True,
            'selected': item.selected_index,
            'selected_label': item.selected_label,
            'is_correct': item.is_correct,
        })

    def _finish(self, round_):
        if round_.phase != LearningRound.PHASE_DONE:
            round_.phase = LearningRound.PHASE_DONE
            round_.finished_at = timezone.now()
            round_.save(update_fields=['phase', 'finished_at'])
        return redirect('fe:learn_result', pk=round_.pk)


class LearnNextView(LearnerRequiredMixin, generic.View):
    """採点結果を見てから次の1問へ進む。"""

    def post(self, request, pk, *args, **kwargs):
        round_ = get_object_or_404(LearningRound, pk=pk, learner=self.learner)
        item = current_item(round_)
        if item is not None and item.is_correct is not None:
            round_.position += 1
            round_.save(update_fields=['position'])
        if current_item(round_) is None:
            round_.phase = LearningRound.PHASE_DONE
            round_.finished_at = timezone.now()
            round_.save(update_fields=['phase', 'finished_at'])
            return redirect('fe:learn_result', pk=round_.pk)
        return redirect('fe:learn_quiz', pk=round_.pk)


class LearnAbandonView(LearnerRequiredMixin, generic.View):
    """途中のラウンドをやめて学習モードトップへ戻る。

    やめたラウンドは残さずに消す。続きから再開する道を用意しないので、
    残しても「解答中」のまま溜まるだけで、どこからも開けない記録になる。
    解いたぶんの解答履歴と分野別の理解度はラウンドとは別に積んであり、
    消しても失われない。
    """

    def post(self, request, pk, *args, **kwargs):
        round_ = get_object_or_404(LearningRound, pk=pk, learner=self.learner)
        if round_.phase == LearningRound.PHASE_DONE:
            # 終わったラウンドは結果を見返すためのものなので消さない
            return redirect('fe:learn_result', pk=round_.pk)
        round_.delete()
        messages.info(request, 'ラウンドを中断しました。解いたぶんの記録は残っています。')
        return redirect('fe:learn_start')


class LearnResultView(LearnerRequiredMixin, generic.DetailView):
    """ラウンドの結果。解いた問題を、正解したものも含めて全問読み返せるようにする。"""

    template_name = 'fe/learn_result.html'
    context_object_name = 'round'

    def get_queryset(self):
        return LearningRound.objects.filter(learner=self.learner)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        items = self.object.items.select_related(
            'question', 'question__category', 'question__template'
        )
        context['items'] = items
        context['missed'] = [i for i in items if i.is_correct is False]
        return context


# ---- 問題メール（fe.mail） ----

def mail_context(learner):
    """ダッシュボードの「問題メール」の欄に渡すもの。"""
    if learner.is_temporary:
        return {'mail_subscription': None}
    mail.purge_pending()
    subscription = MailSubscription.objects.filter(learner=learner).first()
    picked = {
        mail.topic_value(code, topic) for code, topic in (subscription.training_topics if subscription else [])
    }
    total = len(mail.marathon_questions()) if subscription and subscription.marathon else 0
    return {
        'mail_subscription': subscription,
        'mail_topic_groups': mail.topic_choices(),
        'mail_picked_topics': picked,
        'mail_marathon_total': total,
    }


class MailSettingsView(LearnerRequiredMixin, generic.View):
    """問題メールの登録と、送り方の変更。

    未登録なら、アドレスに確認のメールを送る（登録はリンクを開いてから）。
    登録済みならアドレスは変えず、送り方と特訓の小分類だけを変える。
    action=send_stop なら、配信先へ解除用のメールを送る。
    """

    def check_learner(self, learner):
        if learner.is_temporary:
            raise PermissionDenied('問題メールには ID の登録が必要です。')

    def post(self, request, *args, **kwargs):
        back = dashboard_url(self.learner) + '#mail'
        subscription, _ = MailSubscription.objects.get_or_create(learner=self.learner)

        if request.POST.get('action') == 'send_stop':
            if not subscription.email:
                return redirect(back)
            if not mail.can_send_link(subscription):
                messages.error(request, '解除用のメールは送ったばかりです。少し時間をおいてください。')
                return redirect(back)
            mail.send_stop_link(subscription)
            subscription.link_sent_at = timezone.now()
            subscription.save(update_fields=['link_sent_at'])
            messages.success(request, '登録されているアドレスに、解除用のメールを送りました。')
            return redirect(back)

        marathon = request.POST.get('marathon') == 'on'
        training = request.POST.get('training') == 'on'
        topics = mail.parse_topic_values(request.POST.getlist('topics'))
        if training and not topics:
            messages.error(request, '特訓では、小分類を1つ以上選んでください。')
            return redirect(back)

        email = request.POST.get('email', '').strip()
        if not subscription.email:
            if not (marathon or training):
                messages.error(request, 'マラソンか特訓の、少なくとも一方を選んでください。')
                return redirect(back)
            try:
                validate_email(email)
            except ValidationError:
                messages.error(request, 'メールアドレスの形が正しくありません。')
                return redirect(back)
            if not mail.can_send_link(subscription):
                messages.error(request, '確認のメールは送ったばかりです。少し時間をおいてください。')
                return redirect(back)

        if topics != subscription.training_topics:
            # 選び直したら、選んだ小分類の最初から
            subscription.training_index = subscription.training_offset = 0
        subscription.marathon = marathon
        subscription.training = training
        subscription.training_topics = topics

        if subscription.email:
            subscription.save()
            messages.success(request, '問題メールの送り方を変えました。')
            return redirect(back)

        subscription.pending_email = email
        subscription.link_sent_at = timezone.now()
        subscription.save()
        mail.send_confirmation(subscription)
        messages.success(
            request,
            '確認のメールを送りました。メールのリンクを開いて「受け取る」を押すと登録されます。',
        )
        return redirect(back)


@method_decorator(login_not_required, name='dispatch')
class MailConfirmView(generic.View):
    """確認のメールのリンク。開いただけでは登録せず、「受け取る」を押して登録する。

    メールソフトや迷惑メールの検査がリンクを先に開くことがあるため。
    別の端末で開くこともあるので、入っている ID は見ない。
    """

    template_name = 'fe/mail_link.html'

    def get(self, request, token):
        try:
            subscription = mail.read_confirm_token(token)
        except mail.LinkInvalid:
            return render(request, self.template_name, {'invalid': True, 'kind': 'confirm'})
        return render(request, self.template_name, {'kind': 'confirm', 'subscription': subscription})

    def post(self, request, token):
        try:
            subscription = mail.read_confirm_token(token)
        except mail.LinkInvalid:
            return render(request, self.template_name, {'invalid': True, 'kind': 'confirm'})
        mail.confirm(subscription)
        return render(request, self.template_name, {'done': True, 'kind': 'confirm', 'subscription': subscription})


@method_decorator(login_not_required, name='dispatch')
class MailStopView(generic.View):
    """解除のリンク。開いただけでは解除せず、「配信を止める」を押して解除する。"""

    template_name = 'fe/mail_link.html'

    def get(self, request, token):
        try:
            subscription = mail.read_stop_token(token)
        except mail.LinkInvalid:
            return render(request, self.template_name, {'invalid': True, 'kind': 'stop'})
        return render(request, self.template_name, {'kind': 'stop', 'subscription': subscription})

    def post(self, request, token):
        try:
            subscription = mail.read_stop_token(token)
        except mail.LinkInvalid:
            return render(request, self.template_name, {'invalid': True, 'kind': 'stop'})
        mail.stop(subscription)
        return render(request, self.template_name, {'done': True, 'kind': 'stop', 'subscription': subscription})


@method_decorator(login_not_required, name='dispatch')
@method_decorator(csrf_exempt, name='dispatch')
class SendMailsView(generic.View):
    """問題メールの送信を、GitHub Actions の定期実行から起こす入口。

    Azure App Service には cron が無いため、毎日0時に POST で叩かせる。
    ログインの代わりに共有トークン（settings.FE_MAIL_TOKEN）で確かめる。
    countdown の SendRemindersView と同じ形。
    """

    def post(self, request):
        expected = settings.FE_MAIL_TOKEN
        if not expected:
            logger.error('FE_MAIL_TOKEN が未設定のため、問題メールを送れません。')
            return JsonResponse({'detail': '問題メール用のトークンが未設定です。'}, status=503)
        provided = request.headers.get('X-Fe-Mail-Token', '')
        if not hmac.compare_digest(provided, expected):
            logger.warning('問題メールの送信が、不正なトークンで試みられました。')
            return JsonResponse({'detail': '認証できません。'}, status=403)
        result = mail.send_daily()
        logger.info('問題メールを送りました（%s）', result)
        return JsonResponse(result)


# X の解答ページで答えた投稿の code を覚える Cookie。同じブラウザの2回目以降は数えない。
X_ANSWERED_COOKIE = 'fe_x_answered'
X_ANSWERED_SALT = 'fe.x_answered'
# 覚えておく投稿の数。投稿は FE_X_KEEP_DAYS 日で消えるので、その間の分が入れば足りる
X_ANSWERED_LIMIT = 120


@method_decorator(login_not_required, name='dispatch')
class XAnswerView(generic.View):
    """X に投稿した問題の解答ページ。ID なし・ログインなしで、誰でも開ける。

    GET で問題と選択肢を出し、POST で採点して正誤・解説・出典・fe への案内を出す。
    中身は投稿のときに写した XPost から描く。期限を過ぎた投稿と存在しない code には、
    同じ案内ページを出す。

    答えは誰のものかを残さず、全 ID を合わせた集計（OpenProgress）にだけ足す。
    """

    template_name = 'fe/x_answer.html'

    def dispatch(self, request, code, *args, **kwargs):
        self.post_obj = XPost.objects.filter(code=code).select_related('category').first()
        if self.post_obj is None or not x_post.is_open(self.post_obj):
            return render(request, self.template_name, self.base_context(None), status=404)
        return super().dispatch(request, code, *args, **kwargs)

    def base_context(self, post):
        return {
            'post': post,
            'fe_url': settings.FE_SITE_URL.rstrip('/') + reverse('fe:index'),
            'citation': x_post.citation(post.source) if post else '',
        }

    def get(self, request, code):
        return render(request, self.template_name, self.base_context(self.post_obj))

    def post(self, request, code):
        post = self.post_obj
        context = self.base_context(post)
        selected = request.POST.get('choice', '')
        if not selected.isdigit() or int(selected) >= len(post.choices):
            context['error'] = '選択肢を選んでください。'
            return render(request, self.template_name, context)
        selected = int(selected)

        answered = request.get_signed_cookie(
            X_ANSWERED_COOKIE, default='', salt=X_ANSWERED_SALT,
        ).split()
        if post.code in answered:
            is_correct = selected == post.answer_index
        else:
            is_correct = x_post.record_answer(post, selected)
            answered = (answered + [post.code])[-X_ANSWERED_LIMIT:]

        context.update({
            'answered': True,
            'selected': selected,
            'selected_label': Question.choice_label(selected),
            'is_correct': is_correct,
        })
        response = render(request, self.template_name, context)
        response.set_signed_cookie(
            X_ANSWERED_COOKIE, ' '.join(answered), salt=X_ANSWERED_SALT,
            max_age=settings.FE_X_KEEP_DAYS * 24 * 60 * 60,
            secure=request.is_secure(), httponly=True, samesite='Lax',
        )
        return response


@method_decorator(login_not_required, name='dispatch')
@method_decorator(csrf_exempt, name='dispatch')
class PostXView(generic.View):
    """X への投稿を、GitHub Actions の定期実行から起こす入口（朝・昼・夜）。

    共有トークン（settings.FE_X_TOKEN）で確かめ、時間帯（slot）を受け取る。
    期限を過ぎた投稿の削除も、ここで一緒に行う。SendMailsView と同じ形。
    """

    def post(self, request):
        expected = settings.FE_X_TOKEN
        if not expected:
            logger.error('FE_X_TOKEN が未設定のため、X に投稿できません。')
            return JsonResponse({'detail': 'X 投稿用のトークンが未設定です。'}, status=503)
        provided = request.headers.get('X-Fe-X-Token', '')
        if not hmac.compare_digest(provided, expected):
            logger.warning('X への投稿が、不正なトークンで試みられました。')
            return JsonResponse({'detail': '認証できません。'}, status=403)
        slot = request.POST.get('slot') or request.GET.get('slot', '')
        if slot not in dict(XPost.SLOT_CHOICES):
            return JsonResponse({'detail': '時間帯（slot）が正しくありません。'}, status=400)

        purged = x_post.purge_old()
        try:
            result = x_post.post_slot(slot)
        except x_post.PostError as e:
            logger.error('X への投稿に失敗しました（%s）: %s', slot, e)
            return JsonResponse({'detail': 'X への投稿に失敗しました。', 'error': str(e)}, status=502)
        result['purged'] = purged
        logger.info('X への投稿（%s）: %s', slot, result)
        return JsonResponse(result)
