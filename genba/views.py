from collections import defaultdict
from datetime import timedelta

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

from . import kit, kit_data, roles, signals
from .forms import (
    DocumentForm, IssueForm, PromiseCheckForm, SearchForm, SiteForm, SummaryForm, SummaryReviewForm,
    TaskProgressForm, TermForm,
)
from .metrics import THRESHOLDS, WEEKLY_RATE_MIN, WEEKLY_WINDOW, site_metrics
from .models import (
    KNOWLEDGE_CANDIDATE, KNOWLEDGE_NONE, KNOWLEDGE_REVIEWED, VISIBILITY_COMPANY, VISIBILITY_TEAM,
    ChecklistItem, Document, Membership, Question, Site, Summary, Term, week_start,
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


def checklist_progress(site):
    """段階ごとのチェックリストの進み具合 [{'key','label','done','total','issues'}]"""
    issues = site.questions.exclude(stage='').prefetch_related('items').order_by('order')
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
                'stages': checklist_progress(site),
                'signals': [s for s in signals.signals_for(user, site) if s.detected],
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
        Membership.objects.create(site=site, user=self.request.user, role=form.cleaned_data['my_role'],
                                  joined_on=site.started_on)
        kit.create_checklist(site, self.request.user)
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
        context['stages'] = checklist_progress(site)
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
            site.questions.filter(kind=Question.KIND_TASK).select_related('author'),
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

    def post(self, request, *args, **kwargs):
        """作業の予定と実績を保存する"""
        self.object = q = self.get_object()
        check_writable(request.user, q.site)
        form = TaskProgressForm(request.POST, instance=q)
        if form.is_valid():
            form.save()
            messages.success(request, '予定と実績を保存しました。')
            return redirect(q)
        return self.render_to_response(self.get_context_data(progress_form=form))


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
        context.update(
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
        summary, _ = Summary.objects.get_or_create(site=self.site, author=request.user)
        action = request.POST.get('action')
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
# リーダー：メンバーの状況把握とフォロー（見るのは作業量ではなく詰まり）
# ---------------------------------------------------------------------------

class LeaderHomeView(GenbaMixin, generic.TemplateView):
    template_name = 'genba/leader_home.html'
    role_name = 'leader'

    def get(self, request, *args, **kwargs):
        if not roles.leader_sites(request.user).exists():
            raise PermissionDenied
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        since = week_start(timezone.localdate()) - timedelta(weeks=1)
        panels = []
        for site in roles.leader_sites(self.request.user):
            by_member = defaultdict(list)
            for q in open_issues([site]):
                by_member[q.author].append(q)
            members = sorted(
                ({'user': u, 'questions': qs, 'max_days': max(q.days_open for q in qs)}
                 for u, qs in by_member.items()),
                key=lambda m: -m['max_days'],
            )
            panels.append({
                'site': site,
                'members': members,
                'weekly': site.documents.filter(kind=Document.KIND_WEEKLY, week_of__gte=since)
                .exclude(stuck='').select_related('author').order_by('-week_of'),
                'candidates': [
                    *site.documents.filter(knowledge_status=KNOWLEDGE_CANDIDATE).select_related('author'),
                    *site.terms.filter(knowledge_status=KNOWLEDGE_CANDIDATE).select_related('author'),
                ],
            })
        context['panels'] = panels
        context['stall_days'] = THRESHOLDS['stall_days']
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
        context['thresholds'] = THRESHOLDS
        context['weekly_rate_min'] = WEEKLY_RATE_MIN
        context['weekly_window'] = WEEKLY_WINDOW
        return context


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
