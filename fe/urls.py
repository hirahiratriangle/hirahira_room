from django.urls import path

from . import views

app_name = 'fe'

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
    path('quiz/', views.QuizView.as_view(), name='quiz'),
    path('quiz/settings/', views.QuizSettingsView.as_view(), name='quiz_settings'),
    path('stats/', views.StatsView.as_view(), name='stats'),
    path('history/', views.HistoryView.as_view(), name='history'),
]
