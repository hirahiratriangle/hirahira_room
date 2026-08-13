import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import generic

from .forms import CountEventForm
from .models import CountEvent

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
