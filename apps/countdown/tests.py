from datetime import timedelta
from io import StringIO

from django.core import mail
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
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


class CountEventPageTests(TestCase):
    """画面の入れ物（React を読み込むページ）。中身は React が描くので、ここでは開けるかだけを見る。"""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username='tester', email='tester@example.com', password='pass12345'
        )

    def test_pages_require_login(self):
        # サイト全体が LoginRequiredMiddleware でログイン必須
        for name in ('countdown:index', 'countdown:event_list', 'countdown:event_create'):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 302)

    def test_index_visible_after_login(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('countdown:index')).status_code, 200)

    def test_every_screen_url_returns_the_react_shell(self):
        # URL は React にする前と同じ。どれを開いても同じ入れ物を返す
        self.client.force_login(self.user)
        urls = [
            reverse('countdown:event_list'),
            reverse('countdown:event_create'),
            reverse('countdown:event_update', kwargs={'pk': 1}),
            reverse('countdown:event_delete', kwargs={'pk': 1}),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'id="countdown-root"')
                self.assertContains(response, 'countdown/app/app.js')
                # 書き込みに使う CSRF トークンの Cookie を配る
                self.assertIn('csrftoken', response.cookies)


class CountEventApiTests(TestCase):
    """React が呼ぶ API（apps/countdown/api.py）。"""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username='tester', email='tester@example.com', password='pass12345'
        )
        self.other = CustomUser.objects.create_user(
            username='other', email='other@example.com', password='pass12345'
        )
        self.today = timezone.localdate()
        self.list_url = reverse('countdown:api_events')

    def detail_url(self, event):
        return reverse('countdown:api_event', kwargs={'pk': event.pk})

    def post(self, data):
        return self.client.post(self.list_url, data, content_type='application/json')

    def test_requires_login_with_401_json(self):
        # ログイン画面への転送（302）ではなく、React が読める 401 を返す
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 401)
        self.assertIn('detail', response.json())

    def test_create_and_list(self):
        self.client.force_login(self.user)
        response = self.post({
            'name': '旅行', 'date': (self.today + timedelta(days=3)).isoformat(), 'memo': '',
        })
        self.assertEqual(response.status_code, 201)

        event = CountEvent.objects.get(name='旅行')
        self.assertEqual(event.user, self.user)

        data = self.client.get(self.list_url).json()
        self.assertEqual(data['today'], self.today.isoformat())
        self.assertEqual([e['id'] for e in data['upcoming']], [event.pk])
        self.assertEqual(data['past'], [])

    def test_list_is_split_and_ordered(self):
        # これからの予定（当日を含む）は近い順、過ぎた出来事は新しい順
        self.client.force_login(self.user)
        for name, days in (('遠い予定', 30), ('当日', 0), ('近い予定', 3), ('昔', -400), ('最近', -2)):
            CountEvent.objects.create(user=self.user, name=name, date=self.today + timedelta(days=days))

        data = self.client.get(self.list_url).json()
        self.assertEqual([e['name'] for e in data['upcoming']], ['当日', '近い予定', '遠い予定'])
        self.assertEqual([e['name'] for e in data['past']], ['最近', '昔'])

    def test_counts_are_computed_on_the_server(self):
        self.client.force_login(self.user)
        CountEvent.objects.create(user=self.user, name='試験', date=self.today + timedelta(days=10))
        CountEvent.objects.create(user=self.user, name='入社日', date=self.today - timedelta(days=400))

        data = self.client.get(self.list_url).json()
        exam = data['upcoming'][0]
        self.assertEqual(
            (exam['days_delta'], exam['count_days'], exam['count_label'], exam['weeks'], exam['extra_days']),
            (10, 10, '残り', 1, 3),
        )
        joined = data['past'][0]
        self.assertEqual(
            (joined['count_days'], joined['count_label'], joined['years_passed']),
            (400, '経過', 1),
        )
        # メモが無いときも null ではなく空文字で返す
        self.assertEqual(exam['memo'], '')

    def test_only_own_events_are_listed(self):
        CountEvent.objects.create(user=self.other, name='相手の予定', date=self.today)
        self.client.force_login(self.user)
        data = self.client.get(self.list_url).json()
        self.assertEqual(data['upcoming'] + data['past'], [])

    def test_other_user_cannot_see_edit_or_delete(self):
        # 他人の予定は「無い」と答える（あるかどうかも漏らさない）
        event = CountEvent.objects.create(user=self.user, name='入社日', date=self.today)
        self.client.force_login(self.other)
        url = self.detail_url(event)

        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(
            self.client.patch(url, {'name': '書きかえ'}, content_type='application/json').status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 404)

        event.refresh_from_db()
        self.assertEqual(event.name, '入社日')

    def test_update_and_delete_own_event(self):
        event = CountEvent.objects.create(user=self.user, name='試験', date=self.today)
        self.client.force_login(self.user)
        url = self.detail_url(event)

        response = self.client.patch(url, {'name': '本試験', 'notify': True}, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        event.refresh_from_db()
        self.assertEqual((event.name, event.notify), ('本試験', True))

        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertFalse(CountEvent.objects.filter(pk=event.pk).exists())

    def test_put_is_not_accepted(self):
        event = CountEvent.objects.create(user=self.user, name='試験', date=self.today)
        self.client.force_login(self.user)
        response = self.client.put(
            self.detail_url(event),
            {'name': '試験', 'date': self.today.isoformat(), 'memo': '', 'notify': False},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 405)

    def test_create_with_notification(self):
        self.client.force_login(self.user)
        self.post({
            'name': '資格試験', 'date': (self.today + timedelta(days=10)).isoformat(),
            'memo': '', 'notify': True,
        })
        self.assertTrue(CountEvent.objects.get(name='資格試験').notify)

    def test_notification_is_off_by_default(self):
        self.client.force_login(self.user)
        self.post({'name': '通知なし', 'date': (self.today + timedelta(days=10)).isoformat()})
        self.assertFalse(CountEvent.objects.get(name='通知なし').notify)

    def test_invalid_input_returns_field_errors(self):
        # React は欄ごとのメッセージを、その欄の下に出す
        self.client.force_login(self.user)
        response = self.post({'name': '', 'date': '2026/01/01'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(set(response.json()), {'name', 'date'})
        self.assertFalse(CountEvent.objects.exists())

    def test_user_cannot_be_set_from_the_request(self):
        # 持ち主はログイン中の本人に決まる。送られてきた user は無視する
        self.client.force_login(self.user)
        self.post({'name': '旅行', 'date': self.today.isoformat(), 'user': self.other.pk})
        self.assertEqual(CountEvent.objects.get(name='旅行').user, self.user)

    def test_writes_require_csrf_token(self):
        # 本物のブラウザと同じく CSRF を確かめるクライアントで送る
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        data = {'name': '旅行', 'date': self.today.isoformat()}

        response = client.post(self.list_url, data, content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(CountEvent.objects.exists())

        # 入れ物のページが配る Cookie の値を X-CSRFToken に載せれば通る（React と同じ送り方）
        token = client.get(reverse('countdown:event_list')).cookies['csrftoken'].value
        response = client.post(
            self.list_url, data, content_type='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 201)


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
