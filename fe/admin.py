from django.contrib import admin

from .models import (Attempt, Category, CategoryProgress, DailyProgress,
                     Question, QuestionTemplate, StudySession)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'field', 'major_name', 'exam_weight')
    list_filter = ('field',)
    ordering = ('code',)


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'owner', 'subject', 'category', 'topic', 'difficulty')
    list_filter = ('subject', 'category', 'difficulty', 'is_active')
    search_fields = ('stem', 'topic')
    ordering = ('category__code', 'id')


@admin.register(QuestionTemplate)
class QuestionTemplateAdmin(admin.ModelAdmin):
    list_display = ('key', 'title', 'category', 'is_active')
    list_filter = ('category', 'is_active')


@admin.register(Attempt)
class AttemptAdmin(admin.ModelAdmin):
    list_display = ('user', 'question', 'category', 'is_correct', 'answered_at')
    list_filter = ('is_correct', 'category')
    date_hierarchy = 'answered_at'


@admin.register(CategoryProgress)
class CategoryProgressAdmin(admin.ModelAdmin):
    list_display = ('user', 'category', 'subject', 'answered', 'correct', 'last_answered_at')
    list_filter = ('subject', 'category')


@admin.register(DailyProgress)
class DailyProgressAdmin(admin.ModelAdmin):
    list_display = ('user', 'date', 'answered', 'correct')
    date_hierarchy = 'date'


admin.site.register(StudySession)
