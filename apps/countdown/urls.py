from django.urls import path

from . import api, views

app_name = 'countdown'

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),

    # React の画面。URL は React にする前と同じにしてあり、どれも同じ入れ物を返す。
    # 何を描くかは React（frontend/countdown/src/EventsPage.tsx）がアドレスを見て決める。
    # create・update は一覧の上に入力パネルを開く。
    path('events/', views.AppView.as_view(), name='event_list'),
    path('events/create/', views.AppView.as_view(), name='event_create'),
    path('events/<int:pk>/update/', views.AppView.as_view(), name='event_update'),
    # 削除は一覧の「削除」ですぐ行い、トーストの「元に戻す」で取り消せる。
    # 以前の削除確認のページの URL は残し、開かれたら一覧へ戻す（古いブックマーク向け）
    path('events/<int:pk>/delete/', views.AppView.as_view(), name='event_delete'),

    # React が呼ぶ API
    path('api/events/', api.EventListCreateAPI.as_view(), name='api_events'),
    path('api/events/<int:pk>/', api.EventDetailAPI.as_view(), name='api_event'),

    # 定期実行（GitHub Actions）から叩く。トークン必須・POST のみ。
    path('tasks/send-reminders/', views.SendRemindersView.as_view(), name='send_reminders'),
]
