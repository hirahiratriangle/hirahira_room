from datetime import timedelta
from io import StringIO

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser

from .models import CountEvent, ReminderLog
from .notifications import send_reminders


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

    def test_create_with_notification(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('countdown:event_create'), {
            'name': '資格試験',
            'date': (self.today + timedelta(days=10)).isoformat(),
            'memo': '',
            'notify': 'on',
        })
        self.assertRedirects(response, reverse('countdown:event_list'))
        self.assertTrue(CountEvent.objects.get(name='資格試験').notify)

    def test_notification_is_off_by_default(self):
        self.client.force_login(self.user)
        self.client.post(reverse('countdown:event_create'), {
            'name': '通知なし',
            'date': (self.today + timedelta(days=10)).isoformat(),
            'memo': '',
        })
        self.assertFalse(CountEvent.objects.get(name='通知なし').notify)


class ReminderTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username='tester', email='tester@example.com', password='pass12345'
        )
        self.today = timezone.localdate()

    def _event(self, name, days_ahead, notify=True, user=None, memo=''):
        return CountEvent.objects.create(
            user=user or self.user,
            name=name,
            date=self.today + timedelta(days=days_ahead),
            memo=memo,
            notify=notify,
        )

    def test_sends_all_upcoming_events_in_one_mail(self):
        self._event('試験', 7)
        self._event('旅行', 0)
        self._event('締切', 100)

        result = send_reminders(today=self.today)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(result['users'], 1)
        self.assertEqual(result['events'], 3)

        body = mail.outbox[0].body
        for name in ('試験', '旅行', '締切'):
            self.assertIn(name, body)
        self.assertEqual(mail.outbox[0].to, ['tester@example.com'])
        self.assertIn('HirahiraRoom', mail.outbox[0].subject)
        self.assertIn('3件', mail.outbox[0].subject)

    def test_excludes_past_and_disabled_events(self):
        self._event('過ぎた出来事', -1)
        self._event('通知オフ', 3, notify=False)
        self._event('対象', 3)

        send_reminders(today=self.today)

        body = mail.outbox[0].body
        self.assertIn('対象', body)
        self.assertNotIn('過ぎた出来事', body)
        self.assertNotIn('通知オフ', body)

    def test_nothing_to_send_means_no_mail(self):
        self._event('通知オフ', 3, notify=False)

        result = send_reminders(today=self.today)

        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(result['users'], 0)

    def test_does_not_send_twice_in_a_day(self):
        self._event('試験', 7)

        send_reminders(today=self.today)
        send_reminders(today=self.today)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(ReminderLog.objects.count(), 1)

    def test_sends_again_next_day(self):
        self._event('試験', 7)

        send_reminders(today=self.today)
        send_reminders(today=self.today + timedelta(days=1))

        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(ReminderLog.objects.count(), 2)

    def test_mail_shows_remaining_days(self):
        self._event('試験', 7, memo='午前・午後とも受験')
        self._event('本日の予定', 0)

        send_reminders(today=self.today)

        body = mail.outbox[0].body
        self.assertIn('あと7日', body)
        self.assertIn('本日です', body)
        self.assertIn('午前・午後とも受験', body)

    def test_each_user_gets_own_mail(self):
        other = CustomUser.objects.create_user(
            username='other', email='other@example.com', password='pass12345'
        )
        self._event('自分の予定', 3)
        self._event('相手の予定', 3, user=other)

        result = send_reminders(today=self.today)

        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(result['users'], 2)
        by_to = {m.to[0]: m.body for m in mail.outbox}
        self.assertIn('自分の予定', by_to['tester@example.com'])
        self.assertNotIn('相手の予定', by_to['tester@example.com'])
        self.assertIn('相手の予定', by_to['other@example.com'])

    def test_skips_user_without_email(self):
        no_mail = CustomUser.objects.create_user(
            username='nomail', email='', password='pass12345'
        )
        self._event('試験', 7, user=no_mail)

        result = send_reminders(today=self.today)

        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(result['skipped_no_email'], 1)

    def test_dry_run_sends_nothing(self):
        self._event('試験', 7)

        result = send_reminders(today=self.today, dry_run=True)

        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(ReminderLog.objects.count(), 0)
        self.assertEqual(result['events'], 1)

    def test_management_command(self):
        self._event('試験', 7)
        out = StringIO()

        call_command('send_countdown_reminders', '--date', self.today.isoformat(), stdout=out)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('1人に 1 件', out.getvalue())

    def test_management_command_rejects_bad_date(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command('send_countdown_reminders', '--date', '2026/01/01')


@override_settings(COUNTDOWN_REMINDER_TOKEN='test-token')
class SendRemindersEndpointTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username='tester', email='tester@example.com', password='pass12345'
        )
        self.today = timezone.localdate()
        CountEvent.objects.create(
            user=self.user, name='試験', date=self.today + timedelta(days=7), notify=True,
        )
        self.url = reverse('countdown:send_reminders')

    def test_sends_with_valid_token(self):
        response = self.client.post(self.url, HTTP_X_REMINDER_TOKEN='test-token')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['events'], 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_rejects_wrong_token(self):
        response = self.client.post(self.url, HTTP_X_REMINDER_TOKEN='wrong')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(mail.outbox), 0)

    def test_rejects_missing_token(self):
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(mail.outbox), 0)

    def test_rejects_get(self):
        response = self.client.get(self.url, HTTP_X_REMINDER_TOKEN='test-token')

        self.assertEqual(response.status_code, 405)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(COUNTDOWN_REMINDER_TOKEN='')
    def test_disabled_without_token_setting(self):
        response = self.client.post(self.url, HTTP_X_REMINDER_TOKEN='test-token')

        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(mail.outbox), 0)
