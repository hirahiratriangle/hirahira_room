from django.urls import path

from . import views

app_name = 'works'

# 入り口はログインが要る（LoginRequiredMiddleware）。リンク先の3つの構想と SE／PGの賃金は
# URL を知っていればログインなしで開ける
urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
]
