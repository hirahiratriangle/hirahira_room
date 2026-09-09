"""FE 対策アプリのビュー。

出題は 1 問 1 答。GET で 1 問出し，POST で採点してその場で解説を出す。
出題する問題の選定は fe.selection，成績の集計は fe.stats に分けてある。
"""

import logging

import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import generic

from .exam import EXAM, build_prompt
from .forms import QuestionUploadForm
from .importer import export_records, replace_all
from .learning import current_item, grade, note_menu, start_round
from .models import (Attempt, Category, CategoryProgress, LearningNote,
                     LearningRound, Question, QuestionTemplate, StudySession)
from .selection import pick_question
from .stats import (category_stats, daily_counts, field_stats, overall_stats,
                    record_progress, weak_categories)

logger = logging.getLogger(__name__)

# 直前に出した問題が続けて出ないようにするための履歴の長さ
RECENT_LIMIT = 12
SESSION_RECENT = 'fe_recent_question_ids'
SESSION_PENDING = 'fe_pending_question_id'
SESSION_SHOWN_AT = 'fe_question_shown_at'


def _get_study_session(user):
    session, _ = StudySession.objects.get_or_create(user=user)
    return session


class IndexView(LoginRequiredMixin, generic.TemplateView):
    """ダッシュボード。今の実力と，次に何をやるべきかを一目で分かるようにする。"""

    template_name = 'fe/index.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        rows = category_stats(user)
        context['overall'] = overall_stats(user)
        context['fields'] = field_stats(rows)
        context['weak'] = weak_categories(rows, limit=5)
        context['untouched'] = [r for r in rows if r['is_untouched'] and r['question_count']]
        context['study_session'] = _get_study_session(user)
        context['daily'] = daily_counts(user, days=14)
        context['exam'] = EXAM
        context['question_total'] = Question.objects.active().owned_by(user).filter(
            template__isnull=True
        ).count()
        context['template_total'] = QuestionTemplate.objects.filter(is_active=True).count()
        context['max_daily'] = max([d['total'] for d in context['daily']] + [1])
        return context


class QuizSettingsView(LoginRequiredMixin, generic.View):
    """出題モード・科目・分野の変更。"""

    def post(self, request, *args, **kwargs):
        session = _get_study_session(request.user)
        mode = request.POST.get('mode')
        subject = request.POST.get('subject')
        category_code = request.POST.get('category')

        valid_modes = {choice[0] for choice in StudySession.MODE_CHOICES}
        if mode in valid_modes:
            session.mode = mode
        if subject in {Question.SUBJECT_A, Question.SUBJECT_B}:
            session.subject = subject

        session.category = None
        if session.mode == StudySession.MODE_CATEGORY and category_code:
            session.category = Category.objects.filter(code=category_code).first()
            if session.category is None:
                messages.error(request, '指定された分野が見つかりませんでした。')
                session.mode = StudySession.MODE_FOCUS

        session.save()
        # 設定を変えたら履歴の除外はいったんリセットする
        request.session[SESSION_RECENT] = []
        return redirect('fe:quiz')


class QuizView(LoginRequiredMixin, generic.View):
    """1 問 1 答の出題と採点。"""

    template_name = 'fe/quiz.html'

    def get(self, request, *args, **kwargs):
        study_session = _get_study_session(request.user)
        recent = request.session.get(SESSION_RECENT, [])
        question, reason = pick_question(request.user, study_session, exclude_ids=recent)

        if question is None:
            return render(request, self.template_name, {
                'study_session': study_session,
                'categories': Category.objects.all(),
                'no_question': True,
            })

        request.session[SESSION_PENDING] = question.id
        request.session[SESSION_SHOWN_AT] = timezone.now().isoformat()

        return render(request, self.template_name, {
            'question': question,
            'reason': reason,
            'study_session': study_session,
            'categories': Category.objects.all(),
            'progress': self._progress(request.user, question.category),
        })

    def post(self, request, *args, **kwargs):
        study_session = _get_study_session(request.user)
        pending_id = request.session.get(SESSION_PENDING)
        question_id = request.POST.get('question_id')

        if not question_id or str(pending_id) != str(question_id):
            # 二重送信やブラウザの戻る操作。採点せずに次の問題へ送る。
            messages.info(request, 'その問題は既に解答済みです。次の問題を表示します。')
            return redirect('fe:quiz')

        question = Question.objects.filter(id=pending_id).select_related('category').first()
        if question is None:
            return redirect('fe:quiz')

        selected = request.POST.get('choice')
        if selected is None or not selected.isdigit() or int(selected) >= len(question.choices):
            messages.error(request, '選択肢を選んでください。')
            return render(request, self.template_name, {
                'question': question,
                'study_session': study_session,
                'categories': Category.objects.all(),
                'progress': self._progress(request.user, question.category),
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
            user=request.user,
            question=question,
            category=question.category,
            selected_index=selected,
            is_correct=is_correct,
            elapsed_ms=elapsed_ms,
        )
        # 問題が入れ替わっても残る記録。分野別の正答率と苦手判定はこちらを見る。
        record_progress(request.user, question, is_correct)

        # 採点済みなので保留を落とし，直近履歴に積む
        request.session.pop(SESSION_PENDING, None)
        request.session.pop(SESSION_SHOWN_AT, None)
        recent = request.session.get(SESSION_RECENT, [])
        recent.append(question.id)
        request.session[SESSION_RECENT] = recent[-RECENT_LIMIT:]

        return render(request, self.template_name, {
            'question': question,
            'study_session': study_session,
            'categories': Category.objects.all(),
            'answered': True,
            'selected': selected,
            'selected_label': Question.choice_label(selected),
            'is_correct': is_correct,
            'elapsed_ms': elapsed_ms,
            'progress': self._progress(request.user, question.category),
        })

    @staticmethod
    def _progress(user, category):
        """その中分類の現在の成績（解答直後に見せる）。"""
        totals = CategoryProgress.objects.filter(user=user, category=category).aggregate(
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


class StatsView(LoginRequiredMixin, generic.TemplateView):
    """分野別の正答率の一覧。"""

    template_name = 'fe/stats.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        subject = self.request.GET.get('subject')
        if subject not in (Question.SUBJECT_A, Question.SUBJECT_B):
            subject = None

        rows = category_stats(user, subject=subject)
        context['rows'] = rows
        context['fields'] = field_stats(rows)
        context['overall'] = overall_stats(user)
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


class HistoryView(LoginRequiredMixin, generic.ListView):
    """解答履歴。まちがえた問題を見返すために使う。"""

    template_name = 'fe/history.html'
    context_object_name = 'attempts'
    paginate_by = 20

    def get_queryset(self):
        queryset = (
            Attempt.objects.filter(user=self.request.user)
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
        context['categories'] = Category.objects.all()
        context['result_filter'] = self.request.GET.get('result', '')
        context['category_filter'] = self.request.GET.get('category', '')
        return context


# ================================================================ 問題の管理
# 問題はアカウントごとに持ち、JSON の取り込みが唯一の作成・更新経路。
# 画面に入力フォームを置かないので、手元の JSON と画面の内容がずれない。


class ManageListView(LoginRequiredMixin, generic.ListView):
    """自分の問題の一覧。中身の確認と、出題対象の切り替えだけを行う。"""

    template_name = 'fe/manage_list.html'
    context_object_name = 'questions'
    paginate_by = 25

    def get_queryset(self):
        user = self.request.user
        queryset = (
            Question.objects.select_related('category', 'template')
            .filter(owner=user)
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
        mine = Question.objects.filter(owner=self.request.user, template__isnull=True)
        context['categories'] = Category.objects.all()
        context['filters'] = self.request.GET
        context['query_string'] = self.request.GET.urlencode()
        context['summary'] = {
            'total': mine.count(),
            'subject_a': mine.filter(subject=Question.SUBJECT_A).count(),
            'subject_b': mine.filter(subject=Question.SUBJECT_B).count(),
            'generated': Question.objects.filter(
                owner=self.request.user, template__isnull=False
            ).count(),
        }
        return context


class QuestionUploadView(LoginRequiredMixin, generic.FormView):
    """JSON を取り込んで、自分の問題にする。"""

    template_name = 'fe/manage_upload.html'
    form_class = QuestionUploadForm
    success_url = reverse_lazy('fe:manage_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['exam'] = EXAM
        context['prompt'] = build_prompt()
        return context

    def form_valid(self, form):
        created, removed, notes = replace_all(self.request.user, form.records)
        note = '問題集を {} 問に差し替えました。'.format(created)
        if notes:
            note += ' 技術解説 {} 本も取り込みました。'.format(notes)
        if removed:
            note += ' これまでの {} 問と、その解答履歴は削除しました' \
                    '（分野ごとの正答率と苦手分野の判定は残ります）。'.format(removed)
        messages.success(self.request, note)
        return super().form_valid(form)


class LearnStartView(LoginRequiredMixin, generic.View):
    """学習モードの入口。読む時間を決めてラウンドを始める。"""

    template_name = 'fe/learn_start.html'

    def get(self, request, *args, **kwargs):
        session = _get_study_session(request.user)
        return render(request, self.template_name, {
            'minute_choices': LearningRound.MINUTE_CHOICES,
            'default_minutes': LearningRound.DEFAULT_MINUTES,
            'count': LearningRound.QUESTION_COUNT,
            'study_session': session,
            'groups': note_menu(request.user, session.subject),
            'recent': LearningRound.objects.filter(
                user=request.user, phase=LearningRound.PHASE_DONE
            )[:5],
        })

    def post(self, request, *args, **kwargs):
        raw = request.POST.get('minutes')
        minutes = int(raw) if raw and raw.isdigit() else LearningRound.DEFAULT_MINUTES
        if minutes not in LearningRound.MINUTE_CHOICES:
            minutes = LearningRound.DEFAULT_MINUTES

        # 空なら「おまかせ」。他人の解説を指定されても拾わない。
        note = None
        chosen = request.POST.get('note')
        if chosen and chosen.isdigit():
            note = LearningNote.objects.filter(
                pk=chosen, owner=request.user
            ).select_related('category').first()
            if note is None:
                messages.error(request, 'その解説は見つかりませんでした。')
                return redirect('fe:learn_start')

        session = _get_study_session(request.user)
        round_ = start_round(request.user, session, minutes, note=note)
        if round_ is None:
            if note is not None:
                messages.error(
                    request,
                    '「{}」に出題できる問題がありません。'
                    'この分野の問題も取り込んでください。'.format(note.title),
                )
                return redirect('fe:learn_start')
            messages.error(
                request,
                '学習モードには技術解説が要ります。'
                '解説と問題をまとめた JSON を取り込んでください。',
            )
            return redirect('fe:manage_upload')
        return redirect('fe:learn_read', pk=round_.pk)


class LearnReadView(LoginRequiredMixin, generic.View):
    """読む段階。設問ではなく、分野ごとにまとめた技術解説を出す。"""

    template_name = 'fe/learn_read.html'

    def get(self, request, pk, *args, **kwargs):
        round_ = get_object_or_404(LearningRound, pk=pk, user=request.user)
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
        round_ = get_object_or_404(LearningRound, pk=pk, user=request.user)
        if round_.phase == LearningRound.PHASE_READING:
            round_.phase = LearningRound.PHASE_QUIZ
            round_.save(update_fields=['phase'])
        return redirect('fe:learn_quiz', pk=round_.pk)


class LearnQuizView(LoginRequiredMixin, generic.View):
    """解く段階。読んだ範囲をそのまま1問1答で出す。"""

    template_name = 'fe/learn_quiz.html'

    def get(self, request, pk, *args, **kwargs):
        round_ = get_object_or_404(LearningRound, pk=pk, user=request.user)
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
        round_ = get_object_or_404(LearningRound, pk=pk, user=request.user)
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
                user=request.user, question=question, category=question.category,
                selected_index=selected, is_correct=is_correct,
            )
            record_progress(request.user, question, is_correct)

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


class LearnNextView(LoginRequiredMixin, generic.View):
    """採点結果を見てから次の1問へ進む。"""

    def post(self, request, pk, *args, **kwargs):
        round_ = get_object_or_404(LearningRound, pk=pk, user=request.user)
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


class LearnResultView(LoginRequiredMixin, generic.DetailView):
    """ラウンドの結果。まちがえた問題を読み返せるようにする。"""

    template_name = 'fe/learn_result.html'
    context_object_name = 'round'

    def get_queryset(self):
        return LearningRound.objects.filter(user=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        items = self.object.items.select_related('question', 'question__category')
        context['items'] = items
        context['missed'] = [i for i in items if i.is_correct is False]
        return context


class QuestionExportView(LoginRequiredMixin, generic.View):
    """自分の問題を、取り込みと同じ形式の JSON で書き出す。"""

    def get(self, request, *args, **kwargs):
        records = export_records(request.user, request.GET.get('subject'))
        body = json.dumps(records, ensure_ascii=False, indent=1)
        response = HttpResponse(body, content_type='application/json; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="fe_questions.json"'
        return response
