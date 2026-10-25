from collections import defaultdict
from datetime import date, timedelta

from django.contrib import messages
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
    ApprovalForm, DocumentForm, FeedbackForm, GoalForm, GoalPickForm, InterviewQuestionForm, IssueForm, KitProposalForm, LineForm,
    PromiseCheckForm, ReachForm, publish_form_for,
    ResponseForm, SearchForm, SiteForm, StructureGapForm, SummaryForm, SummaryReviewForm, SupportForm, TaskProgressForm,
    TermForm,
)
from .metrics import DEFINITIONS, THRESHOLDS, WEEKLY_RATE_MIN, WEEKLY_WINDOW, site_metrics
from .models import (
    KNOWLEDGE_CANDIDATE, KNOWLEDGE_NONE, KNOWLEDGE_PUBLISHED, VISIBILITY_COMPANY, VISIBILITY_TEAM,
    ChecklistItem, Document, Goal, GoalEvidence, InterviewQuestion, KitProposal, Membership, Question, Site, Summary,
    Term, week_start,
)


class GuestMixin:

    def setup(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            request.user = roles.guest_user()
        super().setup(request, *args, **kwargs)


class GenbaMixin(GuestMixin):
    role_name = ''

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['roles'] = roles.available_roles(self.request.user)
        context['current_role'] = self.role_name
        return context


def check_writable(user, site):
    if not roles.can_read_site(user, site) or site.is_archived:
        raise PermissionDenied


class SiteMixin(GenbaMixin):
    role_name = 'member'
    writable = False

    def dispatch(self, request, *args, **kwargs):
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
    sites = roles.member_sites(user)
    public_q = Q(visibility=VISIBILITY_COMPANY) & (Q(public_title__icontains=q) | Q(public_body__icontains=q))
    docs = Document.objects.filter(
        (Q(site__in=sites) & (Q(title__icontains=q) | Q(body__icontains=q))) | (~Q(site__in=sites) & public_q)
    ).select_related('site').distinct()[:30]
    terms = Term.objects.filter(
        (Q(site__in=sites) & (Q(term__icontains=q) | Q(meaning__icontains=q) | Q(general_difference__icontains=q)))
        | (~Q(site__in=sites) & public_q)
    ).select_related('site').distinct()[:30]
    return docs, terms


def checklist_progress(site, user):
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


def leader_concerns(user, sites):
    leaders = Membership.objects.filter(site__in=sites, role=Membership.ROLE_LEADER)
    q = Q()
    for m in leaders:
        q |= Q(site_id=m.site_id, author_id=m.user_id)
    if not leaders:
        return Question.objects.none()
    return (Question.objects.filter(q, kind=Question.KIND_PROBLEM, is_open=True).exclude(author=user)
            .select_related('site', 'author').order_by('received_at', 'created_at'))


def open_issues(sites):
    return Question.objects.filter(site__in=sites, is_open=True, stage='').select_related('site', 'author')


class PortalView(GenbaMixin, generic.TemplateView):
    template_name = 'genba/portal.html'


class KitView(GenbaMixin, generic.TemplateView):
    template_name = 'genba/kit.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            version=kit.TEMPLATE_VERSION, doc_sections=kit.DOC_SECTIONS, issue_kinds=kit.ISSUE_KINDS,
            issue_labels=kit.ISSUE_LABELS, lifecycle=kit.LIFECYCLE, checklist=kit.checklist(),
            checklist_title=kit_data.CHECKLIST_TITLE, checklist_intro=kit_data.CHECKLIST_INTRO,
            danger_signs=kit_data.DANGER_SIGNS,
            proposals=KitProposal.objects.select_related('author'),
        )
        return context


class PhrasesView(GenbaMixin, generic.TemplateView):
    template_name = 'genba/phrases.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            phrases=kit_data.PHRASES, word_title=kit_data.WORD_TABLE_TITLE, word_intro=kit_data.WORD_TABLE_INTRO,
            categories=kit_data.WORD_CATEGORIES, values=kit_data.WORD_VALUES, closing=kit_data.WORD_CLOSING,
        )
        return context


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
        context['leader_concerns'] = leader_concerns(user, sites)
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
    TABS = ['checklist', 'issues', 'unreadable', 'progress', 'docs', 'terms', 'promises', 'structure', 'members']

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
        for t in published_terms().exclude(site=site).filter(public_title__in=names):
            others[t.public_title].append(t)
        context['terms'] = [(t, others.get(t.term, [])) for t in terms]
        context['promise_checks'] = site.promise_checks.all()
        context['structure_gaps'] = site.structure_gaps.select_related('author')
        context['leader_concerns'] = leader_concerns(self.request.user, [site])
        docs = defaultdict(list)
        for d in site.documents.select_related('author').order_by('-week_of', '-created_at'):
            docs[d.kind].append(d)
        context['doc_groups'] = [(k, label, note, docs.get(k, [])) for k, label, note in kit.DOC_SECTIONS]
        context['summaries'] = site.summaries.select_related('author')
        context['goals'] = site.goals.select_related('author')
        return context


class SiteArchiveView(GuestMixin, View):

    def post(self, request, site_pk):
        site = get_object_or_404(Site, pk=site_pk)
        if not roles.is_leader(request.user, site):
            raise PermissionDenied
        site.is_archived = not site.is_archived
        site.save(update_fields=['is_archived'])
        messages.success(request, 'Archive しました。読むだけになります。' if site.is_archived else 'Archive を戻しました。')
        return redirect('genba:site_detail', site_pk=site.pk)


class SiteCreateMixin(SiteMixin):
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
        if q.kind == Question.KIND_PROBLEM and not q.site.is_archived and roles.can_receive_concern(self.request.user, q):
            context['response_form'] = kwargs.get('response_form') or ResponseForm(instance=q)
        context['from_leader'] = q.kind == Question.KIND_PROBLEM and roles.is_leader(q.author, q.site)
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

    FORMS = {
        'progress': (TaskProgressForm, False, '予定と実績を保存しました。'),
        'support': (SupportForm, True, '説明の時間を割り当てました。メンバーの Issue に出ます。'),
        'response': (ResponseForm, False, '懸念を受け取り、返事を書きました。書いた人の Issue に出ます。'),
    }

    def post(self, request, *args, **kwargs):
        self.object = q = self.get_object()
        check_writable(request.user, q.site)
        name = request.POST.get('form', 'progress')
        if name not in self.FORMS:
            raise Http404
        form_class, leader_only, msg = self.FORMS[name]
        if leader_only and not roles.is_leader(request.user, q.site):
            raise PermissionDenied
        if name == 'response' and (q.kind != Question.KIND_PROBLEM or not roles.can_receive_concern(request.user, q)):
            raise PermissionDenied
        form = form_class(request.POST, instance=q)
        if form.is_valid():
            q = form.save(commit=False)
            if name == 'response':
                q.received_at, q.received_by = timezone.now(), request.user
            q.save()
            messages.success(request, msg)
            return redirect(q)
        return self.render_to_response(self.get_context_data(**{f'{name}_form': form}))


class QuestionToggleView(GuestMixin, View):

    def post(self, request, pk):
        question = get_object_or_404(Question, pk=pk)
        check_writable(request.user, question.site)
        question.is_open = not question.is_open
        question.closed_at = None if question.is_open else timezone.now()
        if question.kind == Question.KIND_TASK and not question.is_open and not question.done_on:
            question.done_on = timezone.localdate()
        question.save()
        return redirect(request.POST.get('next') or question.get_absolute_url())


class ChecklistItemToggleView(GuestMixin, View):

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


class StructureGapCreateView(SiteCreateMixin, generic.CreateView):
    form_class = StructureGapForm
    tab = 'structure'
    heading = '体制のズレを記録する'
    lead = '日々の指示者や決裁者が体制図と違うとき、実際の決裁の流れをメモし、自社に共有します。件数がマネジメントの指標になります。'


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


class KnowledgeActionView(GuestMixin, View):

    MODELS = {'doc': Document, 'term': Term}

    def post(self, request, model, pk, action):
        if model not in self.MODELS:
            raise Http404
        obj = get_object_or_404(self.MODELS[model], pk=pk)
        user = request.user
        status = obj.knowledge_status
        check_writable(user, obj.site)
        if not obj.is_publishable:
            raise PermissionDenied
        if action == 'candidate' and status == KNOWLEDGE_NONE:
            obj.knowledge_status = KNOWLEDGE_CANDIDATE
            msg = 'ナレッジ候補にしました。リーダーが一般化して会社に公開します。'
        elif action == 'withdraw' and status == KNOWLEDGE_CANDIDATE:
            obj.knowledge_status = KNOWLEDGE_NONE
            msg = 'ナレッジ候補から外しました。'
        elif action == 'unpublish' and status == KNOWLEDGE_PUBLISHED and roles.is_leader(user, obj.site):
            obj.knowledge_status, obj.visibility = KNOWLEDGE_CANDIDATE, VISIBILITY_TEAM
            msg = '会社への公開をやめ、ナレッジ候補に戻しました。'
        else:
            raise PermissionDenied
        obj.save()
        messages.success(request, msg)
        return redirect(request.POST.get('next') or reverse('genba:site_detail', args=[obj.site_id]))


class KnowledgePublishView(GenbaMixin, generic.UpdateView):
    template_name = 'genba/knowledge_publish.html'
    role_name = 'leader'

    def get_form_class(self):
        return publish_form_for(type(self.object))

    def get_object(self, queryset=None):
        model = KnowledgeActionView.MODELS.get(self.kwargs['model'])
        if model is None:
            raise Http404
        obj = get_object_or_404(model, pk=self.kwargs['pk'])
        if not obj.is_publishable or not roles.is_leader(self.request.user, obj.site) or obj.site.is_archived:
            raise PermissionDenied
        if obj.knowledge_status == KNOWLEDGE_NONE:
            raise PermissionDenied
        return obj

    def get_initial(self):
        obj = self.object
        if obj.public_title or obj.public_body:
            return {}
        if isinstance(obj, Term):
            return {'public_title': obj.term, 'public_body': obj.meaning}
        return {'public_title': obj.title, 'public_body': obj.body}

    def form_valid(self, form):
        obj = form.save(commit=False)
        obj.knowledge_status, obj.visibility = KNOWLEDGE_PUBLISHED, VISIBILITY_COMPANY
        obj.published_by, obj.published_at = self.request.user, timezone.now()
        obj.save()
        messages.success(self.request, '一般化した版を会社に公開しました。原文は現場チームの中に残ります。')
        return redirect(reverse('genba:site_detail', args=[obj.site_id]) + ('?tab=terms' if isinstance(obj, Term) else '?tab=docs'))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obj = self.object
        context['is_term'] = isinstance(obj, Term)
        context['site'] = obj.site
        context['original_title'] = obj.term if isinstance(obj, Term) else obj.title
        context['original_body'] = obj.meaning if isinstance(obj, Term) else obj.body
        return context


class SkillsetView(GenbaMixin, generic.TemplateView):
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


def can_read_reach(user, goal):
    if roles.can_read_site(user, goal.site):
        return True
    return roles.is_management(user) and goal.author_id != user.pk and (
        goal.stage in (Goal.STAGE_CONFIRMED, Goal.STAGE_APPROVED) or (goal.stage == Goal.STAGE_SUBMITTED and goal.is_solo))


def reach_context(goal):
    return {
        'levels': growth.level_descriptions(goal.skill_item),
        'grade': growth.ae_grade(goal.reached_level, goal.mentored),
        'itss': growth.itss_equivalent(goal.role, goal.team_size, len(goal.complexity_numbers)),
        'paste': growth.paste_text(goal, [e.title for e in goal.evidence.all()]),
        'evidence': goal.evidence.all(),
    }


class GoalReachView(GenbaMixin, generic.UpdateView):
    model = Goal
    form_class = ReachForm
    template_name = 'genba/goal_reach.html'
    role_name = 'member'

    def get_object(self, queryset=None):
        goal = super().get_object(queryset)
        if not can_read_reach(self.request.user, goal):
            raise PermissionDenied
        return goal

    def is_author(self):
        return self.object.author_id == self.request.user.pk

    def can_confirm(self):
        goal = self.object
        return (goal.stage == Goal.STAGE_SUBMITTED and not goal.site.is_archived
                and goal.confirmers().filter(pk=self.request.user.pk).exists())

    def post(self, request, *args, **kwargs):
        self.object = goal = self.get_object()
        action = request.POST.get('action', 'save')
        if action in ('confirm', 'return'):
            if not self.can_confirm():
                raise PermissionDenied
            goal.confirm_comment = request.POST.get('comment', '')
            if action == 'confirm':
                goal.confirmed_at, goal.confirmed_by = timezone.now(), request.user
                messages.success(request, '確認しました。マネジメントの承認に進みます。')
            else:
                goal.reset()
                messages.success(request, '差し戻しました。')
            goal.save()
            return redirect('genba:goal_reach', pk=goal.pk)
        if not self.is_author():
            raise PermissionDenied
        check_writable(request.user, goal.site)
        if action == 'withdraw':
            if goal.stage == Goal.STAGE_APPROVED:
                raise PermissionDenied
            goal.reset()
            goal.save()
            messages.success(request, '依頼を取り下げました。')
            return redirect('genba:goal_reach', pk=goal.pk)
        if goal.stage != Goal.STAGE_DRAFT:
            raise PermissionDenied
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
        if self.request.POST.get('action') == 'submit' and goal.reached_level:
            goal.submitted_at = timezone.now()
            goal.save()
            messages.success(self.request, '確認を依頼しました。')
        else:
            messages.success(self.request, '到達を保存しました。')
        return redirect('genba:goal_reach', pk=goal.pk)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        goal = self.object
        context.update(reach_context(goal))
        context['site'] = goal.site
        context['is_author'] = self.is_author()
        context['can_confirm'] = self.can_confirm()
        context['editable'] = self.is_author() and goal.stage == Goal.STAGE_DRAFT and not goal.site.is_archived
        context['candidates'] = growth.candidates(goal) if context['editable'] else []
        return context


class GoalApprovalView(GenbaMixin, generic.UpdateView):
    model = Goal
    form_class = ApprovalForm
    template_name = 'genba/goal_approval.html'
    context_object_name = 'goal'
    role_name = 'management'

    def get_object(self, queryset=None):
        goal = super().get_object(queryset)
        user = self.request.user
        if not roles.is_management(user) or goal.author_id == user.pk or not can_read_reach(user, goal):
            raise PermissionDenied
        return goal

    def post(self, request, *args, **kwargs):
        self.object = goal = self.get_object()
        if goal.stage == Goal.STAGE_APPROVED:
            raise PermissionDenied
        if request.POST.get('action') == 'return':
            goal.approval_comment = request.POST.get('approval_comment', '')
            goal.reset()
            goal.save()
            messages.success(request, f'{goal.author.username} さんに差し戻しました。')
            return redirect('genba:management')
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        goal = form.save(commit=False)
        user, now = self.request.user, timezone.now()
        if not goal.confirmed_at:
            goal.confirmed_at, goal.confirmed_by = now, user
        goal.approved_on, goal.approved_by = now.date(), user
        goal.save()
        messages.success(self.request, f'{goal.author.username} さんの到達を承認しました。')
        return redirect('genba:management')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(reach_context(self.object))
        return context


class LeaderHomeView(GenbaMixin, generic.TemplateView):
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
                    'weeks_missing': signals.weeks_missing_in_row(m),
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
                'reach_requests': site.goals.filter(submitted_at__isnull=False, confirmed_at__isnull=True)
                .exclude(author=user).select_related('author'),
                'candidates': [
                    *site.documents.filter(knowledge_status=KNOWLEDGE_CANDIDATE).select_related('author'),
                    *site.terms.filter(knowledge_status=KNOWLEDGE_CANDIDATE).select_related('author'),
                ],
            })
        context['panels'] = panels
        context['stall_days'] = THRESHOLDS['stall_days']
        context['ask_days'] = signals.UNREADABLE_ASK_DAYS
        context['escalate_weeks'] = signals.WEEKLY_GAP_ESCALATE
        return context


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
        goals = Goal.objects.exclude(author=self.request.user).select_related('author', 'site', 'confirmed_by')
        context['approval_requests'] = [
            g for g in goals.filter(approved_on__isnull=True, submitted_at__isnull=False).order_by('submitted_at')
            if g.confirmed_at or g.is_solo
        ]
        context['approved_recently'] = goals.filter(
            approved_on__gte=timezone.localdate() - timedelta(days=30)).order_by('-approved_on')
        context['thresholds'] = THRESHOLDS
        context['weekly_rate_min'] = WEEKLY_RATE_MIN
        context['weekly_window'] = WEEKLY_WINDOW
        context['definitions'] = DEFINITIONS
        return context


class CompanyView(GenbaMixin, generic.TemplateView):
    template_name = 'genba/company.html'
    role_name = 'company'

    def dispatch(self, request, *args, **kwargs):
        if not roles.is_company(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        docs = published_documents()
        terms = published_terms()
        form = SearchForm(self.request.GET or None)
        if form.is_valid() and form.cleaned_data['q']:
            q = form.cleaned_data['q']
            docs = docs.filter(Q(public_title__icontains=q) | Q(public_body__icontains=q))
            terms = terms.filter(Q(public_title__icontains=q) | Q(public_body__icontains=q))
            context['query'] = q
        context['search_form'] = form
        terms_by_industry = defaultdict(list)
        for t in terms.order_by('site__industry', 'public_title'):
            terms_by_industry[t.site.industry].append(t)
        context['terms_by_industry'] = dict(terms_by_industry)
        context['designs'] = docs.filter(kind=Document.KIND_DESIGN)
        context['retros'] = docs.filter(kind=Document.KIND_RETRO)
        context['interviews'] = docs.filter(kind=Document.KIND_PRE)
        context['questions'] = InterviewQuestion.objects.select_related('source__site')
        context['question_form'] = kwargs.get('question_form') or InterviewQuestionForm()
        context['proposals'] = KitProposal.objects.select_related('author', 'decided_by')
        reaches = growth.approved_reaches()
        distribution = growth.skill_distribution(reaches)
        context['ledger'] = growth.ledger(reaches)
        context['skill_items'] = growth.SKILL_ITEMS
        context['distribution'] = distribution
        context['itss'] = growth.itss_distribution(reaches)
        context['complexity'] = growth.complexity_experience(reaches)
        context['thin_items'] = growth.thin_items(distribution)
        context.update(growth_links=kit.GROWTH_LINKS, growth_loop=kit.GROWTH_LOOP, growth_note=kit.GROWTH_NOTE,
                       role_growth=kit.ROLE_GROWTH, role_growth_note=kit.ROLE_GROWTH_NOTE)
        return context

    def post(self, request, *args, **kwargs):
        action = request.POST.get('action')
        if action == 'question':
            form = InterviewQuestionForm(request.POST)
            if not form.is_valid():
                return self.render_to_response(self.get_context_data(question_form=form))
            q = form.save(commit=False)
            q.author = request.user
            q.save()
            messages.success(request, '営業・面談の質問リストに足しました。')
        elif action in ('adopted', 'declined'):
            proposal = get_object_or_404(KitProposal, pk=request.POST.get('proposal'))
            proposal.status, proposal.decided_by = action, request.user
            proposal.save()
            messages.success(request, f'「{proposal.title}」を{proposal.get_status_display()}にしました。')
        else:
            raise Http404
        return redirect('genba:company')


class KitProposalCreateView(GenbaMixin, generic.CreateView):
    form_class = KitProposalForm
    template_name = 'genba/form.html'

    def get_initial(self):
        return {'target': self.request.GET.get('target', 'checklist')}

    def form_valid(self, form):
        form.instance.author = self.request.user
        messages.success(self.request, '改訂を提案しました。会社の立場で採用するかを決めます。')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('genba:kit')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['heading'] = '改訂を提案する'
        context['lead'] = '現場ごとの教訓で、入場時チェックリスト・言い回し集・成長の物差しを育てます。採用したものは次の版で反映します。'
        context['cancel_url'] = reverse('genba:kit')
        return context
