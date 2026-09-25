"""FE 対策アプリのビュー。

出題は 1 問 1 答。GET で 1 問出し，POST で採点してその場で解説を出す。
出題する問題の選定は fe.selection，成績の集計は fe.stats に分けてある。

成績は Learner（本アプリの中の ID）ごとに持つ。/fe/ で ID を入力すると
セッションが覚え、以後の画面はその ID の分を出す。ダッシュボードだけは
/fe/<ID>/ に置き、ほかの ID のものは開けない（404）。問題と技術解説は
全 ID で共有し、取り込みなどの管理は管理用の ID だけができる。
"""

import logging

import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import generic

from .exam import EXAM, build_prompt
from .forms import LearnerForm, PassReportForm, QuestionUploadForm
from .importer import export_records, replace_all
from .learning import current_item, grade, note_menu, start_round
from .models import (Attempt, Category, CategoryProgress, Learner, LearningNote,
                     LearningRound, PassReport, Question, QuestionTemplate,
                     StudySession)
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


def _get_study_session(learner):
    session, _ = StudySession.objects.get_or_create(learner=learner)
    return session


def _enter(request, learner):
    """その ID で入る。前の ID の出題途中の状態は持ち越さない。"""
    for key in (SESSION_RECENT, SESSION_PENDING, SESSION_SHOWN_AT):
        request.session.pop(key, None)
    request.session[SESSION_LEARNER] = learner.pk


def current_learner(request):
    """セッションが覚えている ID。入っていなければ None。"""
    pk = request.session.get(SESSION_LEARNER)
    if pk is None:
        return None
    learner = Learner.objects.filter(pk=pk).first()
    if learner is None:
        request.session.pop(SESSION_LEARNER, None)
    return learner


class LearnerRequiredMixin(LoginRequiredMixin):
    """ID で入っている人向けの画面。入っていなければ ID の入力へ戻す。

    入っている ID は self.learner に置く。ヘッダーに ID を出すため、
    request.fe_learner にも載せておく。
    """

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            self.learner = current_learner(request)
            if self.learner is None:
                return redirect('fe:index')
            request.fe_learner = self.learner
            self.check_learner(self.learner)
        return super().dispatch(request, *args, **kwargs)

    def check_learner(self, learner):
        """入っている ID でこの画面を使えるか。使えなければ例外を投げる。"""


class AdminRequiredMixin(LearnerRequiredMixin):
    """問題集を管理する画面。管理用の ID でなければ 403。"""

    def check_learner(self, learner):
        if not learner.is_admin:
            raise PermissionDenied('問題集を管理できるのは管理用の ID だけです。')


def dashboard_url(learner):
    return reverse('fe:dashboard', args=[learner.code])


class EnterView(LoginRequiredMixin, generic.FormView):
    """ID で入る、または新しく作る。

    すでに入っていても開ける。別の ID を入力すれば、その ID に切り替わる。
    """

    template_name = 'fe/enter.html'
    form_class = LearnerForm

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
            messages.success(self.request, 'ID「{}」を作りました。'.format(learner.code))
        else:
            learner = form.learner
        _enter(self.request, learner)
        return redirect(dashboard_url(learner))


class IndexView(EnterView):
    """アプリの入口。入っていれば、そのまま自分のダッシュボードへ送る。"""

    def get(self, request, *args, **kwargs):
        learner = current_learner(request)
        if learner is not None:
            return redirect(dashboard_url(learner))
        return super().get(request, *args, **kwargs)


class DashboardView(LearnerRequiredMixin, generic.TemplateView):
    """ダッシュボード（マイページ）。今の実力と，次に何をやるべきかを一目で分かるようにする。

    URL の ID は、いま入っている ID のときだけ開ける。ほかの ID なら 404。
    """

    template_name = 'fe/index.html'

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
        context['categories'] = category_options(learner)
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

        # 分野が効くのは「分野を指定」のときだけ。ほかのモードに変えたら、
        # 残しておいても誤解のもとなので消す。
        session.category = None
        if session.mode == StudySession.MODE_CATEGORY and category_code:
            session.category = Category.objects.filter(
                code=category_code, subject=session.subject
            ).first()
            if session.category is None:
                messages.error(request, 'その分野は選んだ科目にありません。')
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
                'categories': category_options(self.learner),
                'no_question': True,
            })

        request.session[SESSION_PENDING] = question.id
        request.session[SESSION_SHOWN_AT] = timezone.now().isoformat()

        return render(request, self.template_name, {
            'question': question,
            'reason': reason,
            'study_session': study_session,
            'categories': category_options(self.learner),
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
                'categories': category_options(self.learner),
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
            'categories': category_options(self.learner),
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
    """本番合格の申告と取り消し。1人1件で、申告し直すと受験日を上書きする。"""

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
# 一覧・取り込み・書き出しは、管理用の ID だけが使える。


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

    def form_valid(self, form):
        created, removed, notes = replace_all(form.records)
        note = '問題集を {} 問に差し替えました。'.format(created)
        if notes:
            note += ' 技術解説 {} 本も取り込みました。'.format(notes)
        if removed:
            note += ' これまでの {} 問と、全 ID のその解答履歴は削除しました' \
                    '（分野ごとの正答率と苦手分野の判定は残ります）。'.format(removed)
        messages.success(self.request, note)
        return super().form_valid(form)


class LearnStartView(LearnerRequiredMixin, generic.View):
    """学習モードの入口。読む時間を決めてラウンドを始める。"""

    template_name = 'fe/learn_start.html'

    def get(self, request, *args, **kwargs):
        session = _get_study_session(self.learner)
        return render(request, self.template_name, {
            'minute_choices': LearningRound.MINUTE_CHOICES,
            'default_minutes': LearningRound.DEFAULT_MINUTES,
            'count': LearningRound.QUESTION_COUNT,
            'study_session': session,
            'groups': note_menu(self.learner, session.subject),
            'recent': LearningRound.objects.filter(
                learner=self.learner, phase=LearningRound.PHASE_DONE
            )[:5],
        })

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

        elapsed = (timezone.now() - round_.started_at).total_seconds()
        return render(request, self.template_name, {
            'round': round_,
            'note': round_.note,
            'finished': round_.phase == LearningRound.PHASE_DONE,
            # 途中で再読み込みしても、残り時間は開始時刻から数え直す
            'remaining': max(0, int(round_.reading_seconds - elapsed)),
        })

    def post(self, request, pk, *args, **kwargs):
        round_ = get_object_or_404(LearningRound, pk=pk, learner=self.learner)
        if round_.phase == LearningRound.PHASE_READING:
            round_.phase = LearningRound.PHASE_QUIZ
            round_.save(update_fields=['phase'])
        return redirect('fe:learn_quiz', pk=round_.pk)


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


class LearnResultView(LearnerRequiredMixin, generic.DetailView):
    """ラウンドの結果。まちがえた問題を読み返せるようにする。"""

    template_name = 'fe/learn_result.html'
    context_object_name = 'round'

    def get_queryset(self):
        return LearningRound.objects.filter(learner=self.learner)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        items = self.object.items.select_related('question', 'question__category')
        context['items'] = items
        context['missed'] = [i for i in items if i.is_correct is False]
        return context


class QuestionExportView(AdminRequiredMixin, generic.View):
    """共有の問題を、取り込みと同じ形式の JSON で書き出す。"""

    def get(self, request, *args, **kwargs):
        subject = request.GET.get('subject')
        payload = export_records(subject)
        body = json.dumps(payload, ensure_ascii=False, indent=1)
        response = HttpResponse(body, content_type='application/json; charset=utf-8')
        name = 'fe_{}.json'.format(subject.lower()) if subject in ('A', 'B') else 'fe_all.json'
        response['Content-Disposition'] = 'attachment; filename="{}"'.format(name)
        return response
