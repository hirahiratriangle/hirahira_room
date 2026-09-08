from django.urls import path

from . import views

app_name = 'fe'

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
    path('quiz/', views.QuizView.as_view(), name='quiz'),
    path('quiz/settings/', views.QuizSettingsView.as_view(), name='quiz_settings'),
    # 学習モード（先に解説を読み、そのあと同じ範囲を解く）
    path('learn/', views.LearnStartView.as_view(), name='learn_start'),
    path('learn/<int:pk>/read/', views.LearnReadView.as_view(), name='learn_read'),
    path('learn/<int:pk>/quiz/', views.LearnQuizView.as_view(), name='learn_quiz'),
    path('learn/<int:pk>/next/', views.LearnNextView.as_view(), name='learn_next'),
    path('learn/<int:pk>/result/', views.LearnResultView.as_view(), name='learn_result'),

    path('stats/', views.StatsView.as_view(), name='stats'),
    path('history/', views.HistoryView.as_view(), name='history'),

    # 問題の管理（自分の問題のみ。登録は JSON の一括差し替えで行う）
    path('manage/', views.ManageListView.as_view(), name='manage_list'),
    path('manage/upload/', views.QuestionUploadView.as_view(), name='manage_upload'),
    path('manage/export/', views.QuestionExportView.as_view(), name='manage_export'),
]
