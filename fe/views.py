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

from .forms import QuestionUploadForm
from .importer import (ImportError_, bundled_records, export_records,
                       replace_all, validate)
from .models import (Attempt, Category, CategoryProgress, Question,
                     QuestionTemplate, StudySession)
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
        context['question_total'] = Question.objects.active().filter(
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
        context['bundled_count'] = len(bundled_records())
        context['has_questions'] = Question.objects.filter(
            owner=self.request.user
        ).exists()
        return context

    def form_valid(self, form):
        created, removed = replace_all(self.request.user, form.cleaned_data['records'])
        note = '問題集を {} 問に差し替えました。'.format(created)
        if removed:
            note += ' これまでの {} 問は削除しました（解答履歴は残っています）。'.format(removed)
        messages.success(self.request, note)
        return super().form_valid(form)


class BundledImportView(LoginRequiredMixin, generic.View):
    """アプリに同梱している問題バンクを、自分の問題として取り込む。

    最初の1回でつまずかないための入口。中身はリポジトリの
    fe/data/questions_*.json そのもので、扱いは手元の JSON と変わらない。
    """

    def post(self, request, *args, **kwargs):
        records = bundled_records()
        try:
            validate(records)
        except ImportError_ as exc:
            messages.error(request, '同梱の問題バンクを読み込めませんでした：{}'.format(exc))
            return redirect(reverse('fe:manage_upload'))

        created, removed = replace_all(request.user, records)
        note = '同梱の問題バンク {} 問を取り込みました。'.format(created)
        if removed:
            note += ' これまでの {} 問は削除しました（解答履歴は残っています）。'.format(removed)
        messages.success(request, note)
        return redirect(reverse('fe:manage_list'))


class QuestionExportView(LoginRequiredMixin, generic.View):
    """自分の問題を、取り込みと同じ形式の JSON で書き出す。"""

    def get(self, request, *args, **kwargs):
        records = export_records(request.user, request.GET.get('subject'))
        body = json.dumps(records, ensure_ascii=False, indent=1)
        response = HttpResponse(body, content_type='application/json; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="fe_questions.json"'
        return response
