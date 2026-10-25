from collections import defaultdict
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View, generic

from . import growth, kit, kit_data, roles, signals, skillset_data
from .forms import (
    DocumentForm, FeedbackForm, GoalForm, GoalPickForm, IssueForm, LineForm, PromiseCheckForm, ReachForm,
    ResponseForm, SearchForm, SiteForm, SummaryForm, SummaryReviewForm, SupportForm, TaskProgressForm, TermForm,
)
from .metrics import THRESHOLDS, WEEKLY_RATE_MIN, WEEKLY_WINDOW, site_metrics
from .models import (
    KNOWLEDGE_CANDIDATE, KNOWLEDGE_NONE, KNOWLEDGE_REVIEWED, VISIBILITY_COMPANY, VISIBILITY_TEAM,
    ChecklistItem, Document, Goal, GoalEvidence, Membership, Question, Site, Summary, Term, week_start,
)


class GenbaMixin(LoginRequiredMixin):
    """ナビに出す立場の一覧をテンプレートへ渡す"""
    role_name = ''

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['roles'] = roles.available_roles(self.request.user)
        context['current_role'] = self.role_name
        return context


def check_writable(user, site):
    """同じ現場の人だけが書ける。Archive した現場は読むだけ。"""
    if not roles.can_read_site(user, site) or site.is_archived:
        raise PermissionDenied


class SiteMixin(GenbaMixin):
    """URL の site_pk の現場を読み込み、その現場のメンバー・リーダーだけ通す。
    writable なビューは、Archive した現場では使えない。"""
    role_name = 'member'
    writable = False

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.site = get_object_or_404(Site, pk=kwargs['site_pk'])
        if self.writable:
            check_writable(request.user, self.site)
        elif not roles.can_read_site(request.user, self.site):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['site'] = self.site
        context['is_leader'] = roles.is_leader(self.request.user, self.site)
        return context


def published_documents():
    return Document.objects.filter(visibility=VISIBILITY_COMPANY).select_related('site')


def published_terms():
    return Term.objects.filter(visibility=VISIBILITY_COMPANY).select_related('site')


def search_knowledge(user, q):
    """自分の現場の全文と、会社公開された記録から探す"""
    sites = roles.member_sites(user)
    doc_q = Q(title__icontains=q) | Q(body__icontains=q)
    term_q = Q(term__icontains=q) | Q(meaning__icontains=q) | Q(general_difference__icontains=q)
    docs = Document.objects.filter(doc_q).filter(
        Q(site__in=sites) | Q(visibility=VISIBILITY_COMPANY)).select_related('site').distinct()[:30]
    terms = Term.objects.filter(term_q).filter(
        Q(site__in=sites) | Q(visibility=VISIBILITY_COMPANY)).select_related('site').distinct()[:30]
    return docs, terms


def checklist_progress(site, user):
    """その人の、段階ごとのチェックリストの進み具合 [{'key','label','done','total','issues'}]"""
    issues = site.questions.filter(author=user).exclude(stage='').prefetch_related('items').order_by('order')
    stages = []
    for key, label in kit.STAGE_LABELS.items():
        qs = [q for q in issues if q.stage == key]
        items = [i for q in qs for i in q.items.all()]
        stages.append({
            'key': key, 'label': label, 'issues': qs,
            'done': sum(1 for i in items if i.is_done), 'total': len(items),
        })
    return stages


def open_issues(sites):
    """未解決の Issue（チェックリストの作業は除く）"""
    return Question.objects.filter(site__in=sites, is_open=True, stage='').select_related('site', 'author')


# ---------------------------------------------------------------------------
# 入口・テンプレート
# ---------------------------------------------------------------------------

class PortalView(GenbaMixin, generic.TemplateView):
    """genba の入口。持っている立場の画面へ案内する。"""
    template_name = 'genba/portal.html'


class KitView(GenbaMixin, generic.TemplateView):
    """現場テンプレート（genba-template に当たるもの）"""
    template_name = 'genba/kit.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            version=kit.TEMPLATE_VERSION, doc_sections=kit.DOC_SECTIONS, issue_kinds=kit.ISSUE_KINDS,
            issue_labels=kit.ISSUE_LABELS, lifecycle=kit.LIFECYCLE, checklist=kit.checklist(),
            checklist_title=kit_data.CHECKLIST_TITLE, checklist_intro=kit_data.CHECKLIST_INTRO,
            danger_signs=kit_data.DANGER_SIGNS,
        )
        return context


class PhrasesView(GenbaMixin, generic.TemplateView):
    """言い回し集：確認の言い回しと SI業界ワード確認表"""
    template_name = 'genba/phrases.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            phrases=kit_data.PHRASES, word_title=kit_data.WORD_TABLE_TITLE, word_intro=kit_data.WORD_TABLE_INTRO,
            categories=kit_data.WORD_CATEGORIES, values=kit_data.WORD_VALUES, closing=kit_data.WORD_CLOSING,
        )
        return context


# ---------------------------------------------------------------------------
# メンバー：現場で迷わず働くための支援 kit
# ---------------------------------------------------------------------------

class MemberHomeView(GenbaMixin, generic.TemplateView):
    template_name = 'genba/member_home.html'
    role_name = 'member'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        sites = roles.member_sites(user)
        context['site_cards'] = [
            {
                'site': site,
                'stages': checklist_progress(site, user),
                'signals': [s for s in signals.signals_for(user, site) if s.detected],
                'goals': site.goals.filter(author=user),
            }
            for site in sites.order_by('is_archived', '-started_on')
        ]
        context['open_questions'] = open_issues(sites).order_by('created_at')
        context['terms'] = Term.objects.filter(site__in=sites).select_related('site')[:20]
        context['minutes'] = (
            Document.objects.filter(site__in=sites, kind=Document.KIND_MINUTES).select_related('site')[:5]
        )
        form = SearchForm(self.request.GET or None)
        context['search_form'] = form
        if form.is_valid() and form.cleaned_data['q']:
            context['query'] = form.cleaned_data['q']
            context['found_docs'], context['found_terms'] = search_knowledge(user, form.cleaned_data['q'])
        return context


class SiteCreateView(GenbaMixin, generic.CreateView):
    """テンプレートから現場を作る。チェックリストが作業 Issue として並ぶ。"""
    form_class = SiteForm
    template_name = 'genba/form.html'
    role_name = 'member'

    @transaction.atomic
    def form_valid(self, form):
        response = super().form_valid(form)
        site = self.object
        kit.join_site(site, self.request.user, form.cleaned_data['my_role'], site.started_on)
        messages.success(self.request, f'テンプレートから「{site.name}」を作りました。入場時チェックリストが並んでいます。')
        return response

    def get_success_url(self):
        return reverse('genba:site_detail', args=[self.object.pk]) + '?tab=checklist'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['heading'] = 'テンプレートから現場を作る'
        context['lead'] = 'docs の8区分と、入場時チェックリスト（作業 Issue）が並びます。'
        context['cancel_url'] = reverse('genba:member_home')
        return context


class SiteDetailView(SiteMixin, generic.TemplateView):
    template_name = 'genba/site_detail.html'
    TABS = ['checklist', 'issues', 'unreadable', 'progress', 'docs', 'terms', 'promises', 'members']

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        site = self.site
        tab = self.request.GET.get('tab', 'checklist')
        context['tab'] = tab if tab in self.TABS else 'checklist'
        context['signals'] = signals.signals_for(self.request.user, site)
        context['detected'] = signals.detected_count(context['signals'])
        context['stages'] = checklist_progress(site, self.request.user)
        context['memberships'] = site.memberships.select_related('user')
        context['open_questions'] = open_issues([site])
        context['closed_questions'] = (
            site.questions.filter(is_open=False, stage='').select_related('author').order_by('-closed_at')[:10]
        )
        unreadable = site.questions.filter(unreadable=True).select_related('author').order_by('-is_open', 'created_at')
        context['unreadable'] = [(q, signals.unreadable_needs_question(q)) for q in unreadable]
        context['unreadable_open'] = sum(1 for q, _ in context['unreadable'] if q.is_open)
        context['phrase_unreadable'] = kit.phrase('読めない資料を上げる')
        context['tasks'] = sorted(
            site.questions.filter(Q(stage='') | Q(author=self.request.user), kind=Question.KIND_TASK).select_related('author'),
            key=lambda q: (q.due_on is None, q.due_on or timezone.localdate(), q.order, q.created_at),
        )
        context['today'] = timezone.localdate()
        terms = list(site.terms.select_related('author'))
        others = defaultdict(list)
        names = {t.term for t in terms}
        for t in published_terms().exclude(site=site).filter(term__in=names):
            others[t.term].append(t)
        context['terms'] = [(t, others.get(t.term, [])) for t in terms]
        context['promise_checks'] = site.promise_checks.all()
        docs = defaultdict(list)
        for d in site.documents.select_related('author').order_by('-week_of', '-created_at'):
            docs[d.kind].append(d)
        context['doc_groups'] = [(k, label, note, docs.get(k, [])) for k, label, note in kit.DOC_SECTIONS]
        context['summaries'] = site.summaries.select_related('author')
        context['goals'] = site.goals.select_related('author')
        return context


class SiteArchiveView(LoginRequiredMixin, View):
    """現場の一生の最後。Archive した現場は読むだけになる（リーダーだけが切り替える）"""

    def post(self, request, site_pk):
        site = get_object_or_404(Site, pk=site_pk)
        if not roles.is_leader(request.user, site):
            raise PermissionDenied
        site.is_archived = not site.is_archived
        site.save(update_fields=['is_archived'])
        messages.success(request, 'Archive しました。読むだけになります。' if site.is_archived else 'Archive を戻しました。')
        return redirect('genba:site_detail', site_pk=site.pk)


class SiteCreateMixin(SiteMixin):
    """現場に記録を足すフォームの共通部分"""
    template_name = 'genba/form.html'
    writable = True
    tab = 'issues'
    heading = ''
    lead = ''
    phrase = None

    def form_valid(self, form):
        form.instance.site = self.site
        form.instance.author = self.request.user
        messages.success(self.request, f'「{form.instance}」を記録しました。')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('genba:site_detail', args=[self.site.pk]) + f'?tab={self.tab}'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['heading'] = self.heading
        context['lead'] = self.lead
        context['phrase'] = self.phrase
        context['cancel_url'] = self.get_success_url()
        return context


class IssueCreateView(SiteCreateMixin, generic.CreateView):
    """Issue を作る。種類（確認事項・読めない・課題・作業・スコープ外）で欄を変える。"""
    form_class = IssueForm
    KINDS = {
        'question': (Question.KIND_QUESTION, '確認事項を追加', '誰に・何を確かめるかを書きます。', None),
        'unreadable': (Question.KIND_QUESTION, '「読めない」を残す',
                       'どの資料のどこが、なぜ読めないか。2日経っても進まなければ、一覧を持って質問の時間をもらいます。',
                       '読めない資料を上げる'),
        'problem': (Question.KIND_PROBLEM, '課題（懸念）を書く',
                    '懸念は「事実・自分の解釈・未確認のこと」に分けて、影響と一緒に上げます。人ではなく、事実と影響を書きます。',
                    '違和感を伝える'),
        'task': (Question.KIND_TASK, '作業を追加', '予定日を入れると進捗表に並びます。', None),
        'scope_out': (Question.KIND_TASK, '契約にない作業・責任を頼まれた（スコープ外）',
                      'その場では引き受けず、自社の課長に相談します。', '契約外の依頼'),
    }

    def dispatch(self, request, *args, **kwargs):
        if kwargs['kind'] not in self.KINDS:
            raise Http404
        self.issue_kind = kwargs['kind']
        self.kind, self.heading, self.lead, scene = self.KINDS[self.issue_kind]
        self.phrase = kit.phrase(scene) if scene else None
        self.tab = 'unreadable' if self.issue_kind == 'unreadable' else (
            'progress' if self.kind == Question.KIND_TASK else 'issues')
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['issue_kind'] = self.issue_kind
        return kwargs

    def form_valid(self, form):
        form.instance.kind = self.kind
        form.instance.unreadable = self.issue_kind == 'unreadable'
        form.instance.scope_out = self.issue_kind == 'scope_out'
        return super().form_valid(form)


class IssueDetailView(GenbaMixin, generic.DetailView):
    """Issue の詳細。チェック項目、作業の予定と実績、解決／再開"""
    model = Question
    template_name = 'genba/issue_detail.html'
    context_object_name = 'q'
    role_name = 'member'

    def get_object(self, queryset=None):
        q = super().get_object(queryset)
        if not roles.can_read_site(self.request.user, q.site):
            raise PermissionDenied
        return q

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        q = self.object
        context['site'] = q.site
        context['items'] = q.items.all()
        context['writable'] = not q.site.is_archived
        if q.kind == Question.KIND_TASK:
            context['progress_form'] = kwargs.get('progress_form') or TaskProgressForm(instance=q)
        context['is_leader'] = is_leader = roles.is_leader(self.request.user, q.site) and not q.site.is_archived
        if is_leader and q.unreadable:
            context['support_form'] = kwargs.get('support_form') or SupportForm(instance=q)
        if is_leader and q.kind == Question.KIND_PROBLEM:
            context['response_form'] = kwargs.get('response_form') or ResponseForm(instance=q)
        context['needs_question'] = signals.unreadable_needs_question(q)
        if q.unreadable:
            context['phrase'] = kit.phrase('読めない資料を上げる')
        elif q.scope_out:
            context['phrase'] = kit.phrase('契約外の依頼')
        elif q.kind == Question.KIND_PROBLEM:
            context['phrase'] = kit.phrase('違和感を伝える')
        elif q.stage in ('pre', 'day1'):
            context['phrase'] = kit.phrase('期待値を聞く')
        return context

    # フォームの種類 → (フォーム, リーダーだけか, 保存したときの知らせ)
    FORMS = {
        'progress': (TaskProgressForm, False, '予定と実績を保存しました。'),
        'support': (SupportForm, True, '説明の時間を割り当てました。メンバーの Issue に出ます。'),
        'response': (ResponseForm, True, '懸念を受け取り、返事を書きました。メンバーの Issue に出ます。'),
    }

    def post(self, request, *args, **kwargs):
        """作業の予定と実績（同じ現場の人）、説明の時間・懸念の返事（リーダー）を保存する"""
        self.object = q = self.get_object()
        check_writable(request.user, q.site)
        name = request.POST.get('form', 'progress')
        if name not in self.FORMS:
            raise Http404
        form_class, leader_only, msg = self.FORMS[name]
        if leader_only and not roles.is_leader(request.user, q.site):
            raise PermissionDenied
        form = form_class(request.POST, instance=q)
        if form.is_valid():
            q = form.save(commit=False)
            if name == 'response':
                q.received_at = timezone.now()
            q.save()
            messages.success(request, msg)
            return redirect(q)
        return self.render_to_response(self.get_context_data(**{f'{name}_form': form}))


class QuestionToggleView(LoginRequiredMixin, View):
    """Issue を解決済みにする／未解決に戻す（同じ現場の人なら誰でも）"""

    def post(self, request, pk):
        question = get_object_or_404(Question, pk=pk)
        check_writable(request.user, question.site)
        question.is_open = not question.is_open
        question.closed_at = None if question.is_open else timezone.now()
        if question.kind == Question.KIND_TASK and not question.is_open and not question.done_on:
            question.done_on = timezone.localdate()
        question.save()
        return redirect(request.POST.get('next') or question.get_absolute_url())


class ChecklistItemToggleView(LoginRequiredMixin, View):
    """チェック項目を済みにする／戻す。全部済めば作業 Issue も閉じる。"""

    def post(self, request, pk):
        item = get_object_or_404(ChecklistItem.objects.select_related('question__site'), pk=pk)
        q = item.question
        check_writable(request.user, q.site)
        item.is_done = not item.is_done
        item.save(update_fields=['is_done'])
        all_done = not q.items.filter(is_done=False).exists()
        if all_done and q.is_open:
            q.is_open, q.closed_at = False, timezone.now()
            q.done_on = q.done_on or timezone.localdate()
            q.save()
        elif not all_done and not q.is_open:
            q.is_open, q.closed_at, q.done_on = True, None, None
            q.save()
        return redirect(request.POST.get('next') or q.get_absolute_url())


class SummaryView(SiteMixin, generic.TemplateView):
    """1週目のまとめ。自分の分を書き、出す。同じ現場の人の分は読める。"""
    template_name = 'genba/summary.html'

    def get_summary(self):
        author_id = self.request.GET.get('user')
        if author_id and str(author_id) != str(self.request.user.pk):
            return get_object_or_404(Summary, site=self.site, author_id=author_id), False
        summary, _ = Summary.objects.get_or_create(site=self.site, author=self.request.user)
        return summary, True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        summary, mine = kwargs.get('summary_pair') or self.get_summary()
        site = self.site
        is_leader = roles.is_leader(self.request.user, site)
        goals = site.goals.filter(author=summary.author)
        context.update(
            goals=[(g, growth.level_descriptions(g.skill_item)) for g in goals],
            feedback_form=kwargs.get('feedback_form') or FeedbackForm(instance=summary),
            can_feedback=is_leader and not mine and summary.submitted_at and not site.is_archived,
            summary=summary, mine=mine, writable=mine and not site.is_archived,
            form=kwargs.get('form') or SummaryForm(instance=summary),
            review_form=kwargs.get('review_form') or SummaryReviewForm(instance=summary),
            terms=site.terms.all(),
            open_questions=site.questions.filter(kind=Question.KIND_QUESTION, is_open=True, unreadable=False),
            unreadable=site.questions.filter(unreadable=True, is_open=True),
            phrase=kit.phrase('理解を確かめる'),
        )
        return context

    def post(self, request, *args, **kwargs):
        check_writable(request.user, self.site)
        action = request.POST.get('action')
        if action == 'feedback':
            return self.post_feedback(request)
        summary, _ = Summary.objects.get_or_create(site=self.site, author=request.user)
        if action == 'review':
            form = SummaryReviewForm(request.POST, instance=summary)
        else:
            form = SummaryForm(request.POST, instance=summary)
        if not form.is_valid():
            key = 'review_form' if action == 'review' else 'form'
            return self.render_to_response(self.get_context_data(summary_pair=(summary, True), **{key: form}))
        summary = form.save(commit=False)
        if action == 'submit':
            summary.submitted_at = timezone.now()
            msg = 'まとめを出しました。リーダーに15分もらい「この理解で合っていますか」と確かめましょう。'
        elif action == 'withdraw':
            summary.submitted_at = None
            msg = 'まとめを書きかけに戻しました。'
        else:
            msg = '保存しました。'
        summary.save()
        messages.success(request, msg)
        return redirect('genba:summary', site_pk=self.site.pk)

    def post_feedback(self, request):
        """リーダーが、出されたまとめに差と次の作業を返す"""
        summary = get_object_or_404(Summary, site=self.site, author_id=request.POST.get('user'),
                                    submitted_at__isnull=False)
        if not roles.is_leader(request.user, self.site) or summary.author_id == request.user.pk:
            raise PermissionDenied
        form = FeedbackForm(request.POST, instance=summary)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(summary_pair=(summary, False), feedback_form=form))
        summary = form.save(commit=False)
        summary.feedback_by, summary.feedback_at = request.user, timezone.now()
        summary.save()
        messages.success(request, f'{summary.author.username} さんのまとめに返しました。')
        return redirect(reverse('genba:summary', args=[self.site.pk]) + f'?user={summary.author_id}')


class WeeklyBoardView(SiteMixin, generic.TemplateView):
    """週次共有の一覧：週ごとに、メンバーの週報4欄を横に並べる（現場チーム）"""
    template_name = 'genba/weekly_board.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            week = week_start(date.fromisoformat(self.request.GET['week']))
        except (KeyError, ValueError):
            week = week_start(timezone.localdate()) - timedelta(weeks=1)
        reports = {d.author_id: d for d in self.site.documents.filter(kind=Document.KIND_WEEKLY, week_of=week)}
        context['week'] = week
        context['prev_week'] = week - timedelta(weeks=1)
        context['next_week'] = week + timedelta(weeks=1)
        context['rows'] = [
            (m, reports.get(m.user_id))
            for m in self.site.memberships.select_related('user').order_by('joined_on')
            if week_start(m.joined_on) <= week
        ]
        return context


class LinesView(SiteMixin, generic.TemplateView):
    """裁量の線：「自分で決める範囲」と「相談して決める範囲」。本人とリーダーが書く"""
    template_name = 'genba/lines.html'

    def can_edit(self, membership):
        user = self.request.user
        return not self.site.is_archived and (membership.user_id == user.pk or roles.is_leader(user, self.site))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        invalid = kwargs.get('invalid')
        context['rows'] = [
            (m, (invalid if invalid and invalid.instance.pk == m.pk else LineForm(instance=m)) if self.can_edit(m) else None)
            for m in self.site.memberships.select_related('user').order_by('joined_on')
        ]
        context['phrase'] = kit.phrase('裁量の線を引く')
        return context

    def post(self, request, *args, **kwargs):
        membership = get_object_or_404(Membership, site=self.site, pk=request.POST.get('membership'))
        if not self.can_edit(membership):
            raise PermissionDenied
        form = LineForm(request.POST, instance=membership)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(invalid=form))
        membership = form.save(commit=False)
        membership.line_updated_at = timezone.now()
        membership.save()
        messages.success(request, f'{membership.user.username} さんの裁量の線を残しました。')
        return redirect('genba:lines', site_pk=self.site.pk)


class TermCreateView(SiteCreateMixin, generic.CreateView):
    form_class = TermForm
    tab = 'terms'
    heading = '用語を追加'
    lead = '分からない語も、意味を空のまま載せてかまいません。'


class PromiseCheckCreateView(SiteCreateMixin, generic.CreateView):
    form_class = PromiseCheckForm
    tab = 'promises'
    heading = '約束と実際を追加'


class DocumentCreateView(SiteCreateMixin, generic.CreateView):
    form_class = DocumentForm
    tab = 'docs'

    def dispatch(self, request, *args, **kwargs):
        self.kind = kwargs['kind']
        if self.kind not in dict(Document.KIND_CHOICES):
            raise Http404
        self.heading = f'{dict(Document.KIND_CHOICES)[self.kind]} に書く'
        self.lead = kit.DOC_SECTION_NOTES[self.kind]
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['kind'] = self.kind
        if self.kind == Document.KIND_WEEKLY and 'data' not in kwargs:
            today = week_start(timezone.localdate())
            kwargs['initial'] = {'week_of': today, 'title': f'週報 {today.month}/{today.day}の週'}
        return kwargs

    def form_valid(self, form):
        form.instance.kind = self.kind
        return super().form_valid(form)


class DocumentDetailView(GenbaMixin, generic.DetailView):
    """同じ現場の人は原文を、それ以外の人は会社公開された文書だけ読める"""
    model = Document
    template_name = 'genba/document_detail.html'
    context_object_name = 'doc'

    def get_object(self, queryset=None):
        doc = super().get_object(queryset)
        self.can_read_site = roles.can_read_site(self.request.user, doc.site)
        if not self.can_read_site and not doc.is_public:
            raise PermissionDenied
        return doc

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        doc = self.object
        context['can_read_site'] = self.can_read_site
        context['is_author'] = doc.author_id == self.request.user.pk and not doc.site.is_archived
        context['is_leader'] = roles.is_leader(self.request.user, doc.site)
        context['current_role'] = 'member' if self.can_read_site else 'company'
        context['section_note'] = kit.DOC_SECTION_NOTES[doc.kind]
        return context


class DocumentUpdateView(GenbaMixin, generic.UpdateView):
    model = Document
    template_name = 'genba/form.html'
    role_name = 'member'

    def get_object(self, queryset=None):
        doc = super().get_object(queryset)
        if doc.author_id != self.request.user.pk:
            raise PermissionDenied
        check_writable(self.request.user, doc.site)
        return doc

    def get_form(self, form_class=None):
        return DocumentForm(**self.get_form_kwargs(), kind=self.object.kind)

    def get_success_url(self):
        return reverse('genba:document_detail', args=[self.object.pk])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['heading'] = f'{self.object.get_kind_display()} を編集'
        context['cancel_url'] = self.get_success_url()
        context['site'] = self.object.site
        return context


class KnowledgeActionView(LoginRequiredMixin, View):
    """公開レビューの流れ: 候補にする → リーダーが一般化を確認 → 本人が会社公開に切り替える。
    （誰が最終判断を持つかは spec 005 で見直す）"""

    MODELS = {'doc': Document, 'term': Term}

    def post(self, request, model, pk, action):
        if model not in self.MODELS:
            raise Http404
        obj = get_object_or_404(self.MODELS[model], pk=pk)
        user = request.user
        is_author = obj.author_id == user.pk
        status, public = obj.knowledge_status, obj.is_public

        if not obj.is_publishable or obj.site.is_archived:
            raise PermissionDenied
        if action == 'candidate' and is_author and status == KNOWLEDGE_NONE:
            obj.knowledge_status = KNOWLEDGE_CANDIDATE
            msg = 'ナレッジ候補にしました。リーダーが一般化を確認します。'
        elif action == 'review' and roles.is_leader(user, obj.site) and status == KNOWLEDGE_CANDIDATE:
            obj.knowledge_status = KNOWLEDGE_REVIEWED
            msg = '一般化を確認しました。'
        elif action == 'publish' and is_author and status == KNOWLEDGE_REVIEWED and not public:
            obj.visibility = VISIBILITY_COMPANY
            msg = '会社全体に公開しました。'
        elif action == 'unpublish' and is_author and public:
            obj.visibility = VISIBILITY_TEAM
            msg = '公開をやめ、現場チームだけに戻しました。'
        elif action == 'withdraw' and is_author and status != KNOWLEDGE_NONE and not public:
            obj.knowledge_status = KNOWLEDGE_NONE
            msg = 'ナレッジ候補から外しました。'
        else:
            raise PermissionDenied
        obj.save()
        messages.success(request, msg)
        return redirect(request.POST.get('next') or reverse('genba:site_detail', args=[obj.site_id]))


# ---------------------------------------------------------------------------
# 成長基軸（メンバー本人）：目標を立て、記録で到達を示す
# ---------------------------------------------------------------------------

class SkillsetView(GenbaMixin, generic.TemplateView):
    """基本設計スキルセット（ITSS対応）"""
    template_name = 'genba/skillset.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['d'] = skillset_data
        context['items'] = [
            {**item, 'skills': growth.concrete_skills(item['name']), 'levels': growth.level_descriptions(item['name'])}
            for item in skillset_data.SKILL_ITEMS
        ]
        return context


class GrowthView(SiteMixin, generic.TemplateView):
    """自分の成長：この現場の目標と、証拠になる記録"""
    template_name = 'genba/growth.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        goals = []
        for goal in self.site.goals.filter(author=user).prefetch_related('evidence'):
            rows = growth.candidates(goal)
            goals.append({
                'goal': goal, 'matches': sum(1 for r in rows if r['matches']), 'chosen': goal.evidence.count(),
                'grade': growth.ae_grade(goal.reached_level, goal.mentored),
            })
        context['goals'] = goals
        context['goal_actions'] = (
            self.site.documents.filter(author=user, kind=Document.KIND_WEEKLY).exclude(goal_action='').order_by('-week_of')
        )
        context['usage'] = skillset_data.USAGE
        return context


class GoalCreateView(SiteMixin, generic.TemplateView):
    """目標を立てる。項目と今のレベルを選ぶと、一つ上の具体記述と具体スキルが出る。"""
    template_name = 'genba/goal_form.html'
    writable = True

    def picked(self, data):
        pick = GoalPickForm(data or None)
        if pick.is_valid():
            return pick, pick.cleaned_data['item'], pick.cleaned_data['level']
        return pick, None, None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pick, item, level = kwargs.get('picked') or self.picked(self.request.GET)
        context['pick'] = pick
        context['item'], context['level'] = item, level
        context['usage'] = skillset_data.USAGE[2:]
        if item:
            target = growth.level_description(item, level + 1)
            context['target_level'] = level + 1
            context['levels'] = growth.level_descriptions(item)
            context['skills'] = growth.concrete_skills(item)
            context['form'] = kwargs.get('form') or GoalForm(item=item, initial={'target_text': target})
        return context

    def post(self, request, *args, **kwargs):
        picked = self.picked(request.POST)
        _, item, level = picked
        if not item:
            return self.render_to_response(self.get_context_data(picked=picked))
        form = GoalForm(request.POST, item=item)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(picked=picked, form=form))
        goal = form.save(commit=False)
        goal.site, goal.author, goal.skill_item, goal.current_level = self.site, request.user, item, level
        goal.save()
        messages.success(request, '目標を立てました。課会資料の「現参画PJを通しての目標」に書けます。')
        return redirect('genba:growth', site_pk=self.site.pk)


class GoalReachView(GenbaMixin, generic.UpdateView):
    """到達を説明する（本人だけ）。証拠を選び、具体記述に当てはめ、目安と貼る文章を出す。"""
    model = Goal
    form_class = ReachForm
    template_name = 'genba/goal_reach.html'
    role_name = 'member'

    def get_object(self, queryset=None):
        goal = super().get_object(queryset)
        if goal.author_id != self.request.user.pk:
            raise PermissionDenied
        return goal

    def post(self, request, *args, **kwargs):
        check_writable(request.user, self.get_object().site)
        return super().post(request, *args, **kwargs)

    @transaction.atomic
    def form_valid(self, form):
        goal = form.save()
        rows = {r['key']: r for r in growth.candidates(goal)}
        goal.evidence.all().delete()
        GoalEvidence.objects.bulk_create(
            GoalEvidence(goal=goal, record_type=rows[k]['type'], record_id=rows[k]['id'], title=rows[k]['title'])
            for k in self.request.POST.getlist('evidence') if k in rows
        )
        messages.success(self.request, '到達を保存しました。')
        return redirect('genba:goal_reach', pk=goal.pk)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        goal = self.object
        context['site'] = goal.site
        context['candidates'] = growth.candidates(goal)
        context['levels'] = growth.level_descriptions(goal.skill_item)
        context['grade'] = growth.ae_grade(goal.reached_level, goal.mentored)
        context['itss'] = growth.itss_equivalent(goal.role, goal.team_size, len(goal.complexity_numbers))
        context['paste'] = growth.paste_text(goal, [e.title for e in goal.evidence.all()])
        context['itss_levels'] = skillset_data.ITSS_LEVELS
        context['ae_note'] = skillset_data.AE_NOTE
        context['cautions'] = skillset_data.CAUTIONS
        context['writable'] = not goal.site.is_archived
        return context


# ---------------------------------------------------------------------------
# リーダー：メンバーの状況把握とフォロー（見るのは作業量ではなく詰まり）
# ---------------------------------------------------------------------------

class LeaderHomeView(GenbaMixin, generic.TemplateView):
    """担当現場ごとに：詰まり、読めない、まとめ確認、週次共有、裁量の線、懸念、ナレッジ候補"""
    template_name = 'genba/leader_home.html'
    role_name = 'leader'

    def get(self, request, *args, **kwargs):
        if not roles.leader_sites(request.user).exists():
            raise PermissionDenied
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        panels = []
        for site in roles.leader_sites(user):
            memberships = site.memberships.exclude(user=user).select_related('user').order_by('joined_on')
            by_member = defaultdict(list)
            for q in open_issues([site]):
                by_member[q.author_id].append(q)
            members = []
            for m in memberships:
                qs = by_member.get(m.user_id, [])
                members.append({
                    'membership': m, 'questions': qs, 'max_days': max((q.days_open for q in qs), default=0),
                    'weekly_missing': signals.missing_last_weekly(m),
                })
            members.sort(key=lambda r: (-r['weekly_missing'], -r['max_days']))
            unreadable = site.questions.filter(unreadable=True, is_open=True).select_related('author')
            panels.append({
                'site': site,
                'members': members,
                'unreadable': [(q, signals.unreadable_needs_question(q)) for q in unreadable],
                'summaries': site.summaries.filter(submitted_at__isnull=False).exclude(author=user)
                .select_related('author').order_by('feedback_at', 'submitted_at'),
                'lines_missing': [m for m in memberships if not m.line_updated_at],
                'concerns': site.questions.filter(kind=Question.KIND_PROBLEM, is_open=True).exclude(author=user)
                .select_related('author').order_by('received_at', 'created_at'),
                'weekly': site.documents.filter(kind=Document.KIND_WEEKLY,
                                                week_of__gte=week_start(timezone.localdate()) - timedelta(weeks=1))
                .exclude(stuck='').select_related('author').order_by('-week_of'),
                'candidates': [
                    *site.documents.filter(knowledge_status=KNOWLEDGE_CANDIDATE).select_related('author'),
                    *site.terms.filter(knowledge_status=KNOWLEDGE_CANDIDATE).select_related('author'),
                ],
            })
        context['panels'] = panels
        context['stall_days'] = THRESHOLDS['stall_days']
        context['ask_days'] = signals.UNREADABLE_ASK_DAYS
        return context


# ---------------------------------------------------------------------------
# マネジメント：現場単位の集計値だけを見る
# ---------------------------------------------------------------------------

class ManagementView(GenbaMixin, generic.TemplateView):
    template_name = 'genba/management.html'
    role_name = 'management'

    def get(self, request, *args, **kwargs):
        if not roles.is_management(request.user):
            raise PermissionDenied
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        rows = [site_metrics(s) for s in Site.objects.all()]
        rows.sort(key=lambda m: (-len(m.alerts), m.site.name))
        context['rows'] = rows
        # 到達の合意は、本人が依頼したものだけ（自分の目標は自分で合意しない）
        requested = Goal.objects.filter(agreement_requested_at__isnull=False).exclude(author=self.request.user)
        context['agreement_requests'] = requested.filter(agreed_on__isnull=True).select_related('author', 'site')
        context['agreed_recently'] = (
            requested.filter(agreed_on__gte=timezone.localdate() - timedelta(days=30))
            .select_related('author', 'site').order_by('-agreed_on')
        )
        context['thresholds'] = THRESHOLDS
        context['weekly_rate_min'] = WEEKLY_RATE_MIN
        context['weekly_window'] = WEEKLY_WINDOW
        return context


class GoalAgreementView(GenbaMixin, generic.DetailView):
    """マネジメント（課長）が、本人から依頼された到達の説明を読んで合意する／差し戻す"""
    model = Goal
    template_name = 'genba/goal_agreement.html'
    context_object_name = 'goal'
    role_name = 'management'

    def get_object(self, queryset=None):
        goal = super().get_object(queryset)
        user = self.request.user
        if not roles.is_management(user) or goal.author_id == user.pk or not goal.agreement_requested_at:
            raise PermissionDenied
        return goal

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        goal = self.object
        context['paste'] = growth.paste_text(goal, [e.title for e in goal.evidence.all()])
        context['evidence'] = goal.evidence.all()
        return context

    def post(self, request, *args, **kwargs):
        goal = self.get_object()
        goal.agreement_comment = request.POST.get('comment', '')
        if request.POST.get('action') == 'agree':
            goal.agreed_on, goal.agreed_by = timezone.localdate(), request.user
            messages.success(request, f'{goal.author.username} さんの到達に合意しました。')
        else:
            goal.agreement_requested_at, goal.agreed_on, goal.agreed_by = None, None, None
            messages.success(request, f'{goal.author.username} さんに差し戻しました。コメントが本人に見えます。')
        goal.save()
        return redirect('genba:management')


# ---------------------------------------------------------------------------
# 会社：会社公開に切り替えられた記録だけ（顧客名の代わりに業界を出す）
# ---------------------------------------------------------------------------

class CompanyView(GenbaMixin, generic.TemplateView):
    template_name = 'genba/company.html'
    role_name = 'company'

    def get(self, request, *args, **kwargs):
        if not roles.is_company(request.user):
            raise PermissionDenied
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        docs = published_documents()
        terms = published_terms()
        form = SearchForm(self.request.GET or None)
        if form.is_valid() and form.cleaned_data['q']:
            q = form.cleaned_data['q']
            docs = docs.filter(Q(title__icontains=q) | Q(body__icontains=q))
            terms = terms.filter(Q(term__icontains=q) | Q(meaning__icontains=q) | Q(general_difference__icontains=q))
            context['query'] = q
        context['search_form'] = form
        terms_by_industry = defaultdict(list)
        for t in terms.order_by('site__industry', 'term'):
            terms_by_industry[t.site.industry].append(t)
        context['terms_by_industry'] = dict(terms_by_industry)
        context['designs'] = docs.filter(kind=Document.KIND_DESIGN)
        context['retros'] = docs.filter(kind=Document.KIND_RETRO)
        context['interviews'] = docs.filter(kind=Document.KIND_PRE)
        skills = defaultdict(int)
        for item in docs.filter(kind=Document.KIND_RETRO).exclude(skill_item='').values_list('skill_item', flat=True):
            skills[item] += 1
        context['skills'] = sorted(skills.items(), key=lambda kv: -kv[1])
        return context
