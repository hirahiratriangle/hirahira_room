from django.contrib import admin

from .models import Attempt, Category, Question, QuestionTemplate, StudySession


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'field', 'major_name', 'exam_weight')
    list_filter = ('field',)
    ordering = ('code',)


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ('key', 'subject', 'category', 'topic', 'difficulty', 'is_active')
    list_filter = ('subject', 'category', 'difficulty', 'is_active')
    search_fields = ('key', 'stem', 'topic')
    ordering = ('key',)


@admin.register(QuestionTemplate)
class QuestionTemplateAdmin(admin.ModelAdmin):
    list_display = ('key', 'title', 'category', 'is_active')
    list_filter = ('category', 'is_active')


@admin.register(Attempt)
class AttemptAdmin(admin.ModelAdmin):
    list_display = ('user', 'question', 'category', 'is_correct', 'answered_at')
    list_filter = ('is_correct', 'category')
    date_hierarchy = 'answered_at'


admin.site.register(StudySession)
