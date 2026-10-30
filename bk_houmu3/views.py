"""法務3級 対策アプリのビュー。

出題は 1 問 1 答。GET で 1 問出し，POST で採点してその場で解説を出す。
出題する問題の選定は bk_houmu3.selection，成績の集計は bk_houmu3.stats に分けてある。

成績は Learner（本アプリの中の ID）ごとに持つ。/bk_houmu3/ で ID を入力すると
セッションが覚え、以後の画面はその ID の分を出す。ダッシュボードだけは
/bk_houmu3/<ID>/ に置き、ほかの ID のものは開けない（404）。問題と技術解説は
全 ID で共有し、取り込みなどの管理は管理用の ID だけができる。

サイト全体は LoginRequiredMiddleware でログイン必須で、本アプリもすべての画面で
hirahira_room へのログインが要る。基本情報版は学習の画面を login_not_required で
外しているが、法務3級版は外さない。問題が市販の問題解説集からの転記で、
誰でも読める状態にしてはいけないため。問題の管理は、さらにスタッフ権限が要る。
"""

import logging

import json
import re

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import generic

from .exam import EXAM, build_prompt
from .forms import LearnerForm, PassReportForm, QuestionUploadForm
from .importer import (ImportError_, export_records, needs_confirmation, parse,
                       preview, sync, validate)
from .learning import current_item, focus_category, grade, note_menu, start_round
from .models import (Attempt, Category, CategoryProgress, Learner, LearningNote,
                     LearningRound, PassReport, Question, QuestionTemplate,
                     StudySession)
from . import speech
from .selection import pick_question
from .stats import (categories_for, category_options, category_stats, daily_counts,
                    field_stats, overall_stats, record_progress, site_summary,
                    weak_categories)

logger = logging.getLogger(__name__)

# 直前に出した問題が続けて出ないようにするための履歴の長さ
RECENT_LIMIT = 12
SESSION_RECENT = 'bk_houmu3_recent_question_ids'
SESSION_PENDING = 'bk_houmu3_pending_question_id'
SESSION_SHOWN_AT = 'bk_houmu3_question_shown_at'
# 聞き流しで直前に流した問題。1問1答の直近の履歴とは別に持つ（聞き流しは解答にならない）
SESSION_LISTEN_RECENT = 'bk_houmu3_listen_recent_question_ids'
# 聞き流しで一度に渡す問題の数。画面を消している間は次を取りに行けないことがあるので、まとめて渡す
LISTEN_BATCH = 10
# 直前に流した問題を、どれだけ続けて避けるか
LISTEN_RECENT_LIMIT = 60
# 入っている ID。hirahira_room のアカウントとは別に、ブラウザのセッションで持つ。
SESSION_LEARNER = 'bk_houmu3_learner_id'
# 入っている ID を、ブラウザを閉じたら消える Cookie にも持たせる。セッションは
# 閉じても2週間残る（ログインの「ログインしたままにする」と同じ入れ物）ので、
# それだけだと共用の端末で次の人が前の人の ID のまま入ってしまう。
LEARNER_COOKIE = 'bk_houmu3_learner'
LEARNER_COOKIE_SALT = 'bk_houmu3.learner'


def _get_study_session(learner):
    session, _ = StudySession.objects.get_or_create(learner=learner)
    return session


def _enter(request, learner):
    """その ID で入る。前の ID の出題途中の状態は持ち越さない。"""
    for key in (SESSION_RECENT, SESSION_PENDING, SESSION_SHOWN_AT, SESSION_LISTEN_RECENT):
        request.session.pop(key, None)
    request.session[SESSION_LEARNER] = learner.pk
    # 応答を返すときに、ブラウザを閉じたら消える Cookie を置く（remember_entry）
    request.bk_houmu3_entered = learner.pk


def remember_entry(request, response):
    """入ったばかりなら、ブラウザを閉じたら消える Cookie に ID を置く。

    期限を付けないので、ブラウザを閉じると消える。消えたら current_learner が
    セッションの ID も捨てるので、次に開いたときは入口からになる。
    """
    pk = getattr(request, 'bk_houmu3_entered', None)
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
    entered = getattr(request, 'bk_houmu3_entered', None) == pk or request.get_signed_cookie(
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


class LearnerRequiredMixin:
    """始めている人向けの画面。始めていなければトップへ戻す。

    hirahira_room へのログインはサイト全体のミドルウェアが求める。いまの記録は self.learner に置く。
    ヘッダーに出すため、request.bk_houmu3_learner にも載せておく。
    """

    def dispatch(self, request, *args, **kwargs):
        self.learner = current_learner(request)
        if self.learner is None:
            return redirect('bk_houmu3:index')
        request.bk_houmu3_learner = self.learner
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
        return reverse('bk_houmu3:index')
    return reverse('bk_houmu3:dashboard', args=[learner.code])


class EnterView(generic.FormView):
    """ID で入る、または新しく作る。

    すでに入っていても開ける。別の ID を入力すれば、その ID に切り替わる。
    """

    template_name = 'bk_houmu3/enter.html'
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
    ID で入っていれば /bk_houmu3/<ID>/ へ送り、履歴を残さない利用なら
    この場所がそのままダッシュボードになる。
    """

    template_name = 'bk_houmu3/start.html'

    def get(self, request, *args, **kwargs):
        learner = current_learner(request)
        if learner is None:
            return super().get(request, *args, **kwargs)
        if learner.is_temporary:
            touch(learner)
            request.bk_houmu3_learner = learner
            return DashboardView.render_for(request, learner)
        return redirect(dashboard_url(learner))

    def post(self, request, *args, **kwargs):
        """「履歴を残さずに始める」。ID の登録は EnterView 側で扱う。"""
        if request.POST.get('action') == 'temporary':
            start_temporary(request)
            return redirect('bk_houmu3:index')
        return super().post(request, *args, **kwargs)


class DashboardView(LearnerRequiredMixin, generic.TemplateView):
    """ダッシュボード（マイページ）。今の実力と，次に何をやるべきかを一目で分かるようにする。

    URL の ID は、いま入っている ID のときだけ開ける。ほかの ID なら 404。
    履歴を残さない利用には URL がないので、IndexView から render_for で描く。
    """

    template_name = 'bk_houmu3/index.html'

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
        return redirect('bk_houmu3:quiz')


class QuizView(LearnerRequiredMixin, generic.View):
    """1 問 1 答の出題と採点。"""

    template_name = 'bk_houmu3/quiz.html'

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
            return redirect('bk_houmu3:quiz')

        question = (
            Question.objects.filter(id=pending_id)
            .select_related('category').first()
        )
        if question is None:
            return redirect('bk_houmu3:quiz')

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


class ListenView(LearnerRequiredMixin, generic.TemplateView):
    """聞き流し。画面に問題を出さず、問題・選択肢 → 解答（頭の中で選ぶ間）→ 解説 と続けて流す。

    出す問題は1問1答と同じ出題設定（モード・分野）で選ぶ。解答はしないので、
    解答の記録にも正答率にも入れない。流す問題は ListenNextView から受け取る。
    """

    template_name = 'bk_houmu3/listen.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        study_session = _get_study_session(self.learner)
        context['study_session'] = study_session
        context['categories'] = category_options(self.learner, study_session.mode)
        context['listen_think_seconds'] = LISTEN_THINK_SECONDS
        return context


# 解答の時間（選択肢を流してから解説を流すまでの間）と、次の問題までの間
LISTEN_THINK_SECONDS = 5
LISTEN_GAP_SECONDS = 2


class ListenNextView(LearnerRequiredMixin, generic.View):
    """聞き流しで次に流す問題を、まとめて返す。

    問題文と解説の両方の音声がある問題だけを返す。選び方は1問1答と同じ
    （pick_question）で、直前に流した問題は避ける。出せる問題が尽きたら、
    避ける履歴を空にして選び直す。
    """

    def get(self, request, *args, **kwargs):
        study_session = _get_study_session(self.learner)
        recent = request.session.get(SESSION_LISTEN_RECENT, [])
        items = self._pick(study_session, recent)
        if not items and recent:
            recent = []
            items = self._pick(study_session, recent)
        request.session[SESSION_LISTEN_RECENT] = recent[-LISTEN_RECENT_LIMIT:]
        return JsonResponse({'items': items, 'gap_seconds': LISTEN_GAP_SECONDS})

    def _pick(self, study_session, recent):
        """recent を書き足しながら、流せる問題を LISTEN_BATCH 問まで選ぶ。"""
        # 音声がそろっていない問題は、選ぶ前に除いておく（引いてから除くと、
        # 音声の少ないうちは引き直しが尽きて、流せる問題が無いように見える）
        silent = [
            question.id for question in Question.objects.filter(is_active=True)
            if not all(speech.has_audio(text) for text in
                       (speech.question_text(question), speech.explanation_text(question)))
        ]
        items = []
        while len(items) < LISTEN_BATCH:
            question, _reason = pick_question(
                self.learner, study_session, exclude_ids=recent + silent,
            )
            # 除いた問題しか残っていないと、除外を解いて選び直すモードがある
            if question is None or question.id in recent or question.id in silent:
                break
            recent.append(question.id)
            items.append({
                'id': question.id,
                'field': question.category.get_field_display(),
                'category': question.category.name,
                'question_url': speech.audio_url(speech.question_text(question)),
                'explanation_url': speech.audio_url(speech.explanation_text(question)),
            })
        return items


_RANGE = re.compile(r'^bytes=(\d*)-(\d*)$')


class QuestionAudioView(LearnerRequiredMixin, generic.View):
    """問題と解説の読み上げ音声を返す。

    iPhone の Safari は音声を「何バイト目から何バイト目まで」と区切って取りに来る
    （Range 要求）。これに 206 で応えないと、再生や途中への移動ができないことがある。
    ファイル名は読ませる文から計算したハッシュで、中身が変わることはないので長く持たせる。
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


class StatsView(LearnerRequiredMixin, generic.TemplateView):
    """分野別の正答率の一覧。"""

    template_name = 'bk_houmu3/stats.html'

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

    template_name = 'bk_houmu3/history.html'
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

    template_name = 'bk_houmu3/manage_list.html'
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

    template_name = 'bk_houmu3/manage_upload.html'
    form_class = QuestionUploadForm
    success_url = reverse_lazy('bk_houmu3:manage_list')

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
                '{}：追加 {} / 更新 {} / 変更なし {} / 削除 {}'.format(
                    label, counts['added'], counts['updated'],
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
                return redirect('bk_houmu3:manage_upload')
            result = sync(form.records)
            messages.success(request, '　'.join(self._count_lines(result)))
            return redirect(self.success_url)
        return super().post(request, *args, **kwargs)


class LearnStartView(LearnerRequiredMixin, generic.View):
    """学習モードの入口。読む時間を決めてラウンドを始める。"""

    template_name = 'bk_houmu3/learn_start.html'

    def get(self, request, *args, **kwargs):
        session = _get_study_session(self.learner)
        finished = LearningRound.objects.filter(
            learner=self.learner, phase=LearningRound.PHASE_DONE
        )
        # 直前に学習した中分類を開いて見せる。学習した結果（学習回数）は小分類の行に
        # 出るので、閉じたままだと、学習したことが一覧に反映されていないように見える。
        # ダッシュボードで指定している分野は、おまかせの範囲になるだけで、開かない。
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
            'opened': last.note.category if last else None,
            'recent': finished[:5],
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
                return redirect('bk_houmu3:learn_start')

        session = _get_study_session(self.learner)
        round_ = start_round(self.learner, session, minutes, note=note)
        if round_ is None:
            if note is not None:
                messages.error(
                    request,
                    '「{}」に出題できる問題がありません。'.format(note.title),
                )
                return redirect('bk_houmu3:learn_start')
            focus = focus_category(session)
            if focus is not None:
                messages.error(
                    request,
                    '{}には、出題できる問題のある技術解説がまだありません。'
                    'ダッシュボードの分野の指定を変えてください。'.format(focus.name),
                )
                return redirect('bk_houmu3:learn_start')
            messages.error(request, '学習モードに使う技術解説が、まだ用意されていません。')
            # 管理用の ID なら、そのまま取り込みへ案内する
            if self.learner.is_admin:
                return redirect('bk_houmu3:manage_upload')
            return redirect('bk_houmu3:learn_start')
        return redirect('bk_houmu3:learn_read', pk=round_.pk)


class LearnReadView(LearnerRequiredMixin, generic.View):
    """読む段階。設問ではなく、分野ごとにまとめた技術解説を出す。"""

    template_name = 'bk_houmu3/learn_read.html'

    def get(self, request, pk, *args, **kwargs):
        round_ = get_object_or_404(LearningRound, pk=pk, learner=self.learner)
        # 終わったラウンドの解説は、結果画面から読み返せるようにしておく
        if round_.phase == LearningRound.PHASE_QUIZ:
            return redirect('bk_houmu3:learn_quiz', pk=round_.pk)

        elapsed = (timezone.now() - round_.started_at).total_seconds()
        return render(request, self.template_name, {
            'round': round_,
            # 同じ小分類の解説は、まとめて続けて読む
            'notes': round_.notes,
            'finished': round_.phase == LearningRound.PHASE_DONE,
            # 途中で再読み込みしても、残り時間は開始時刻から数え直す
            'remaining': max(0, int(round_.reading_seconds - elapsed)),
        })

    def post(self, request, pk, *args, **kwargs):
        round_ = get_object_or_404(LearningRound, pk=pk, learner=self.learner)
        if round_.phase == LearningRound.PHASE_READING:
            round_.phase = LearningRound.PHASE_QUIZ
            round_.save(update_fields=['phase'])
        return redirect('bk_houmu3:learn_quiz', pk=round_.pk)


class LearnQuizView(LearnerRequiredMixin, generic.View):
    """解く段階。読んだ範囲をそのまま1問1答で出す。"""

    template_name = 'bk_houmu3/learn_quiz.html'

    def get(self, request, pk, *args, **kwargs):
        round_ = get_object_or_404(LearningRound, pk=pk, learner=self.learner)
        if round_.phase == LearningRound.PHASE_READING:
            return redirect('bk_houmu3:learn_read', pk=round_.pk)
        if round_.phase == LearningRound.PHASE_DONE:
            return redirect('bk_houmu3:learn_result', pk=round_.pk)

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
        return redirect('bk_houmu3:learn_result', pk=round_.pk)


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
            return redirect('bk_houmu3:learn_result', pk=round_.pk)
        return redirect('bk_houmu3:learn_quiz', pk=round_.pk)


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
            return redirect('bk_houmu3:learn_result', pk=round_.pk)
        round_.delete()
        messages.info(request, 'ラウンドを中断しました。解いたぶんの記録は残っています。')
        return redirect('bk_houmu3:learn_start')


class LearnResultView(LearnerRequiredMixin, generic.DetailView):
    """ラウンドの結果。解いた問題を、正解したものも含めて全問読み返せるようにする。"""

    template_name = 'bk_houmu3/learn_result.html'
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


class QuestionExportView(AdminRequiredMixin, generic.View):
    """共有の問題を、取り込みと同じ形式の JSON で書き出す。"""

    def get(self, request, *args, **kwargs):
        subject = request.GET.get('subject')
        payload = export_records(subject)
        body = json.dumps(payload, ensure_ascii=False, indent=1)
        response = HttpResponse(body, content_type='application/json; charset=utf-8')
        name = 'bk_houmu3_{}.json'.format(subject.lower()) if subject in ('A', 'B') else 'bk_houmu3_all.json'
        response['Content-Disposition'] = 'attachment; filename="{}"'.format(name)
        return response
