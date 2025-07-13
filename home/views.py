# home/views.py

from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

class HomeView(LoginRequiredMixin, TemplateView):
    """
    ログイン済みユーザー向けホーム画面。
    未ログイン時は LOGIN_URL にリダイレクト。
    """
    template_name = 'home/home.html'