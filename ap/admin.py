from django.contrib import admin

from .models import (Attempt, Category, CategoryProgress, DailyProgress,
                     Learner, PassReport, Question, QuestionTemplate, StudySession)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'field', 'major_name', 'exam_weight')
    list_filter = ('field',)
    ordering = ('code',)


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'subject', 'category', 'topic', 'difficulty')
    list_filter = ('subject', 'category', 'difficulty', 'is_active')
    search_fields = ('stem', 'topic')
    ordering = ('category__code', 'id')


@admin.register(QuestionTemplate)
class QuestionTemplateAdmin(admin.ModelAdmin):
    list_display = ('key', 'title', 'category', 'is_active')
    list_filter = ('category', 'is_active')


@admin.register(Attempt)
class AttemptAdmin(admin.ModelAdmin):
    list_display = ('learner', 'question', 'category', 'is_correct', 'answered_at')
    list_filter = ('is_correct', 'category')
    date_hierarchy = 'answered_at'


@admin.register(CategoryProgress)
class CategoryProgressAdmin(admin.ModelAdmin):
    list_display = ('learner', 'category', 'subject', 'answered', 'correct', 'last_answered_at')
    list_filter = ('subject', 'category')


@admin.register(DailyProgress)
class DailyProgressAdmin(admin.ModelAdmin):
    list_display = ('learner', 'date', 'answered', 'correct')
    date_hierarchy = 'date'


@admin.register(PassReport)
class PassReportAdmin(admin.ModelAdmin):
    list_display = ('learner', 'passed_on', 'reported_at')
    date_hierarchy = 'passed_on'


@admin.register(Learner)
class LearnerAdmin(admin.ModelAdmin):
    """ID の一覧。管理用の付け外しと、一時的な記録の確認に使う。

    code が空のものは「履歴を残さない」で使われている記録で、
    purge_temp_learners_ap が最終利用日時を見て消す。消える前にどれだけ
    溜まっているかを、ここで見られるようにしておく。
    """

    list_display = ('__str__', 'kind', 'is_admin', 'created_at', 'last_seen_at')
    list_editable = ('is_admin',)
    list_filter = ('is_admin', ('code', admin.EmptyFieldListFilter))
    search_fields = ('code',)
    ordering = ('-last_seen_at',)

    @admin.display(description='種別')
    def kind(self, obj):
        return '履歴なし' if obj.is_temporary else '登録'


admin.site.register(StudySession)
