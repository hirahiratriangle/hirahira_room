from django.urls import path

from . import views

app_name = 'pvsys'

# 入り口はログインが要る（LoginRequiredMiddleware）。リンク先の genba・3つの構想・基本情報・福祉従事者の活動ページは
# URL を知っていればログインなしで開ける
urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
]
