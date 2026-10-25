from collections import defaultdict
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View, generic

from . import roles
from .forms import DocumentForm, PromiseCheckForm, QuestionForm, SearchForm, TermForm
from .metrics import THRESHOLDS, WEEKLY_RATE_MIN, WEEKLY_WINDOW, site_metrics
from .models import (
    KNOWLEDGE_CANDIDATE, KNOWLEDGE_NONE, KNOWLEDGE_REVIEWED, VISIBILITY_COMPANY, VISIBILITY_TEAM,
    Document, Question, Site, Term, week_start,
)


class GenbaMixin(LoginRequiredMixin):
    """ナビに出す立場の一覧をテンプレートへ渡す"""
    role_name = ''

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['roles'] = roles.available_roles(self.request.user)
        context['current_role'] = self.role_name
        return context


class SiteMixin(GenbaMixin):
    """URL の site_pk の現場を読み込み、その現場のメンバー・リーダーだけ通す"""
    role_name = 'member'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.site = get_object_or_404(Site, pk=kwargs['site_pk'])
        if not roles.can_read_site(request.user, self.site):
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


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

class PortalView(GenbaMixin, generic.TemplateView):
    """genba の入口。持っている立場の画面へ案内する。"""
    template_name = 'genba/portal.html'


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
        context['sites'] = sites
        context['open_questions'] = (
            Question.objects.filter(site__in=sites, is_open=True)
            .select_related('site', 'author').order_by('created_at')
        )
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


class SiteDetailView(SiteMixin, generic.TemplateView):
    template_name = 'genba/site_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        site = self.site
        context['memberships'] = site.memberships.select_related('user')
        context['open_questions'] = site.questions.filter(is_open=True).select_related('author')
        context['closed_questions'] = site.questions.filter(is_open=False).select_related('author').order_by('-closed_at')[:10]
        context['terms'] = site.terms.select_related('author')
        context['promise_checks'] = site.promise_checks.all()
        docs = defaultdict(list)
        for d in site.documents.select_related('author').order_by('-week_of', '-created_at'):
            docs[d.kind].append(d)
        context['doc_groups'] = [(k, label, docs.get(k, [])) for k, label in Document.KIND_CHOICES]
        context['tab'] = self.request.GET.get('tab', 'questions')
        return context


class SiteCreateMixin(SiteMixin):
    """現場に記録を足すフォームの共通部分"""
    template_name = 'genba/form.html'
    tab = 'questions'
    heading = ''

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
        context['cancel_url'] = self.get_success_url()
        return context


class QuestionCreateView(SiteCreateMixin, generic.CreateView):
    form_class = QuestionForm
    heading = '確認事項・課題を追加'


class TermCreateView(SiteCreateMixin, generic.CreateView):
    form_class = TermForm
    tab = 'terms'
    heading = '用語を追加'


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
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['kind'] = self.kind
        if self.kind == Document.KIND_WEEKLY and 'data' not in kwargs:
            kwargs['initial'] = {'week_of': week_start(timezone.localdate())}
        return kwargs

    def form_valid(self, form):
        form.instance.kind = self.kind
        return super().form_valid(form)

    @property
    def heading(self):
        return f'{dict(Document.KIND_CHOICES)[self.kind]}を書く'


class QuestionToggleView(LoginRequiredMixin, View):
    """確認事項を解決済みにする／未解決に戻す（同じ現場の人なら誰でも）"""

    def post(self, request, pk):
        question = get_object_or_404(Question, pk=pk)
        if not roles.can_read_site(request.user, question.site):
            raise PermissionDenied
        question.is_open = not question.is_open
        question.closed_at = None if question.is_open else timezone.now()
        question.save()
        return redirect(request.POST.get('next') or reverse('genba:site_detail', args=[question.site_id]))


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
        context['is_author'] = doc.author_id == self.request.user.pk
        context['is_leader'] = roles.is_leader(self.request.user, doc.site)
        context['current_role'] = 'member' if self.can_read_site else 'company'
        return context


class DocumentUpdateView(GenbaMixin, generic.UpdateView):
    model = Document
    template_name = 'genba/form.html'
    role_name = 'member'

    def get_object(self, queryset=None):
        doc = super().get_object(queryset)
        if doc.author_id != self.request.user.pk:
            raise PermissionDenied
        return doc

    def get_form(self, form_class=None):
        return DocumentForm(**self.get_form_kwargs(), kind=self.object.kind)

    def get_success_url(self):
        return reverse('genba:document_detail', args=[self.object.pk])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['heading'] = f'{self.object.get_kind_display()}を編集'
        context['cancel_url'] = self.get_success_url()
        context['site'] = self.object.site
        return context


class KnowledgeActionView(LoginRequiredMixin, View):
    """公開レビューの流れ: 候補にする → リーダーが一般化を確認 → 本人が会社公開に切り替える。
    公開するかどうかの最終判断は書いた本人が持つ。"""

    MODELS = {'doc': Document, 'term': Term}

    def post(self, request, model, pk, action):
        if model not in self.MODELS:
            raise Http404
        obj = get_object_or_404(self.MODELS[model], pk=pk)
        user = request.user
        is_author = obj.author_id == user.pk
        status, public = obj.knowledge_status, obj.is_public

        if not obj.is_publishable:
            raise PermissionDenied
        if action == 'candidate' and is_author and status == KNOWLEDGE_NONE:
            obj.knowledge_status = KNOWLEDGE_CANDIDATE
            msg = 'ナレッジ候補にしました。リーダーが一般化を確認します。'
        elif action == 'review' and roles.is_leader(user, obj.site) and status == KNOWLEDGE_CANDIDATE:
            obj.knowledge_status = KNOWLEDGE_REVIEWED
            msg = '一般化を確認しました。公開するかは書いた本人が決めます。'
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
            for q in site.questions.filter(is_open=True).select_related('author'):
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
                .exclude(risks='').select_related('author').order_by('-week_of'),
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
        context['interviews'] = docs.filter(kind=Document.KIND_INTERVIEW)
        skills = defaultdict(int)
        for item in docs.filter(kind=Document.KIND_RETRO).exclude(skill_item='').values_list('skill_item', flat=True):
            skills[item] += 1
        context['skills'] = sorted(skills.items(), key=lambda kv: -kv[1])
        return context
