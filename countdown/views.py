import hmac
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View, generic
from django.views.decorators.csrf import csrf_exempt

from .forms import CountEventForm
from .models import CountEvent
from .notifications import send_reminders

logger = logging.getLogger(__name__)


class OnlyYouMixin(UserPassesTestMixin):
    raise_exception = True

    def test_func(self):
        event = get_object_or_404(CountEvent, pk=self.kwargs['pk'])
        return self.request.user == event.user


class IndexView(generic.TemplateView):
    """アプリの紹介ページ（ログイン不要）"""
    template_name = 'countdown/index.html'


class EventListView(LoginRequiredMixin, generic.ListView):
    """登録した出来事・予定の一覧。未来はカウントダウン、過去はカウントアップ。"""
    model = CountEvent
    template_name = 'countdown/event_list.html'
    context_object_name = 'events'

    def get_queryset(self):
        return CountEvent.objects.filter(user=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        events = list(context['events'])
        today = timezone.localdate()

        # 予定（当日を含む未来）は近い順、出来事（過去）は新しい順に並べる
        context['upcoming_events'] = sorted(
            [e for e in events if e.date >= today], key=lambda e: e.date
        )
        context['past_events'] = sorted(
            [e for e in events if e.date < today], key=lambda e: e.date, reverse=True
        )
        context['today'] = today
        return context


class EventCreateView(LoginRequiredMixin, generic.CreateView):
    model = CountEvent
    template_name = 'countdown/event_form.html'
    form_class = CountEventForm
    success_url = reverse_lazy('countdown:event_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_title'] = '新規登録'
        return context

    def form_valid(self, form):
        event = form.save(commit=False)
        event.user = self.request.user
        event.save()
        messages.success(self.request, '「{}」を登録しました。'.format(event.name))
        logger.info('CountEvent created by {}'.format(self.request.user))
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, '登録に失敗しました。')
        return super().form_invalid(form)


class EventUpdateView(LoginRequiredMixin, OnlyYouMixin, generic.UpdateView):
    model = CountEvent
    template_name = 'countdown/event_form.html'
    form_class = CountEventForm
    success_url = reverse_lazy('countdown:event_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_title'] = '編集'
        return context

    def form_valid(self, form):
        messages.success(self.request, '「{}」を更新しました。'.format(form.instance.name))
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, '更新に失敗しました。')
        return super().form_invalid(form)


class EventDeleteView(LoginRequiredMixin, OnlyYouMixin, generic.DeleteView):
    model = CountEvent
    template_name = 'countdown/event_delete.html'
    success_url = reverse_lazy('countdown:event_list')

    def form_valid(self, form):
        messages.success(self.request, '「{}」を削除しました。'.format(self.object.name))
        return super().form_valid(form)


@method_decorator(login_not_required, name='dispatch')
@method_decorator(csrf_exempt, name='dispatch')
class SendRemindersView(View):
    """リマインド送信を外部スケジューラから起動するためのエンドポイント。

    Azure App Service には cron が無いため、GitHub Actions の定期実行から
    POST で叩く。ログインの代わりに共有トークンで認証する。
    """

    def post(self, request):
        expected = getattr(settings, 'COUNTDOWN_REMINDER_TOKEN', '')
        if not expected:
            logger.error('COUNTDOWN_REMINDER_TOKEN が未設定のため実行できません。')
            return JsonResponse(
                {'detail': 'リマインド用トークンが未設定です。'}, status=503)

        provided = request.headers.get('X-Reminder-Token', '')
        if not hmac.compare_digest(provided, expected):
            logger.warning('リマインド起動が不正なトークンで試みられました。')
            return JsonResponse({'detail': '認証できません。'}, status=403)

        result = send_reminders()
        logger.info(
            'リマインドを実行しました（%s人 / %s件）', result['users'], result['events'])
        return JsonResponse({
            'users': result['users'],
            'events': result['events'],
            'failed': result['failed'],
            'skipped_no_email': result['skipped_no_email'],
        })
