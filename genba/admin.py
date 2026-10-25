from django.contrib import admin

from .models import Document, Membership, PromiseCheck, Question, Site, Term


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ('name', 'repo_name', 'industry', 'started_on')
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


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ('title', 'site', 'kind', 'author', 'is_open', 'scope_out', 'created_at')
    list_filter = ('site', 'kind', 'is_open', 'scope_out')
    search_fields = ('title', 'body')


@admin.register(PromiseCheck)
class PromiseCheckAdmin(admin.ModelAdmin):
    list_display = ('promise', 'actual', 'is_gap', 'site')
    list_filter = ('site', 'is_gap')
