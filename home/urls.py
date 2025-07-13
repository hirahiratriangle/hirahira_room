# home/urls.py

from django.urls import path
from .views import HomeView

app_name = 'home'

urlpatterns = [
    # クラスベースビューを as_view() でマッピング
    path('', HomeView.as_view(), name='home'),
]