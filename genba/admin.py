from django.contrib import admin

from .models import (
    ChecklistItem, Document, Goal, GoalEvidence, InterviewQuestion, KitProposal, Membership, PromiseCheck, Question, Site,
    StructureGap, Summary, Term,
)


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ('name', 'repo_name', 'industry', 'started_on', 'template_version', 'is_archived')
    search_fields = ('name', 'repo_name', 'industry')
    inlines = [MembershipInline]


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ('title', 'site', 'kind', 'author', 'visibility', 'knowledge_status', 'created_at')
    list_filter = ('site', 'kind', 'visibility', 'knowledge_status')
    search_fields = ('title', 'body')


@admin.register(Term)
class TermAdmin(admin.ModelAdmin):
    list_display = ('term', 'site', 'author', 'visibility', 'knowledge_status')
    list_filter = ('site', 'visibility', 'knowledge_status')
    search_fields = ('term', 'meaning')


class ChecklistItemInline(admin.TabularInline):
    model = ChecklistItem
    extra = 0


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ('title', 'site', 'kind', 'author', 'is_open', 'scope_out', 'unreadable', 'stage', 'created_at')
    list_filter = ('site', 'kind', 'is_open', 'scope_out', 'unreadable', 'stage')
    search_fields = ('title', 'body')
    inlines = [ChecklistItemInline]


@admin.register(Summary)
class SummaryAdmin(admin.ModelAdmin):
    list_display = ('site', 'author', 'submitted_at', 'updated_at')
    list_filter = ('site',)


@admin.register(PromiseCheck)
class PromiseCheckAdmin(admin.ModelAdmin):
    list_display = ('promise', 'actual', 'is_gap', 'site')
    list_filter = ('site', 'is_gap')


class GoalEvidenceInline(admin.TabularInline):
    model = GoalEvidence
    extra = 0


@admin.register(Goal)
class GoalAdmin(admin.ModelAdmin):
    list_display = ('site', 'author', 'skill_item', 'current_level', 'reached_level', 'submitted_at', 'confirmed_at', 'approved_on')
    list_filter = ('site', 'skill_item')
    inlines = [GoalEvidenceInline]


@admin.register(StructureGap)
class StructureGapAdmin(admin.ModelAdmin):
    list_display = ('site', 'role_kind', 'on_chart', 'actual', 'created_at')
    list_filter = ('site', 'role_kind')


@admin.register(KitProposal)
class KitProposalAdmin(admin.ModelAdmin):
    list_display = ('title', 'target', 'status', 'author', 'created_at')
    list_filter = ('target', 'status')


@admin.register(InterviewQuestion)
class InterviewQuestionAdmin(admin.ModelAdmin):
    list_display = ('question', 'industry', 'created_at')
