from django.urls import path

from . import views

app_name = 'countdown'

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
    path('events/', views.EventListView.as_view(), name='event_list'),
    path('events/create/', views.EventCreateView.as_view(), name='event_create'),
    path('events/<int:pk>/update/', views.EventUpdateView.as_view(), name='event_update'),
    path('events/<int:pk>/delete/', views.EventDeleteView.as_view(), name='event_delete'),
    # 定期実行（GitHub Actions）から叩く。トークン必須・POST のみ。
    path('tasks/send-reminders/', views.SendRemindersView.as_view(), name='send_reminders'),
]
