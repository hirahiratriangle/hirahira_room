from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser

from .models import CountEvent


class CountEventTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username='tester', email='tester@example.com', password='pass12345'
        )
        self.today = timezone.localdate()

    def _create(self, name, date):
        return CountEvent.objects.create(user=self.user, name=name, date=date)

    def test_upcoming_event_counts_down(self):
        event = self._create('試験', self.today + timedelta(days=10))
        self.assertTrue(event.is_upcoming)
        self.assertEqual(event.count_days, 10)
        self.assertEqual(event.count_label, '残り')

    def test_past_event_counts_up(self):
        event = self._create('入社日', self.today - timedelta(days=400))
        self.assertTrue(event.is_past)
        self.assertEqual(event.count_days, 400)
        self.assertEqual(event.count_label, '経過')
        self.assertEqual(event.years_passed, 1)

    def test_today_event(self):
        event = self._create('誕生日', self.today)
        self.assertTrue(event.is_today)
        self.assertEqual(event.count_days, 0)
        self.assertEqual(event.count_label, '当日')


class CountEventViewTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username='tester', email='tester@example.com', password='pass12345'
        )
        self.other = CustomUser.objects.create_user(
            username='other', email='other@example.com', password='pass12345'
        )
        self.today = timezone.localdate()

    def test_pages_require_login(self):
        # サイト全体が LoginRequiredMiddleware でログイン必須
        for name in ('countdown:index', 'countdown:event_list', 'countdown:event_create'):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 302)

    def test_index_visible_after_login(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('countdown:index')).status_code, 200)

    def test_create_and_list(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('countdown:event_create'), {
            'name': '旅行', 'date': (self.today + timedelta(days=3)).isoformat(), 'memo': '',
        })
        self.assertRedirects(response, reverse('countdown:event_list'))

        event = CountEvent.objects.get(name='旅行')
        self.assertEqual(event.user, self.user)

        response = self.client.get(reverse('countdown:event_list'))
        self.assertEqual(list(response.context['upcoming_events']), [event])
        self.assertEqual(list(response.context['past_events']), [])

    def test_other_user_cannot_edit(self):
        event = CountEvent.objects.create(user=self.user, name='入社日', date=self.today)
        self.client.force_login(self.other)
        response = self.client.get(reverse('countdown:event_update', kwargs={'pk': event.pk}))
        self.assertEqual(response.status_code, 403)
