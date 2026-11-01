from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser


class IndexTests(TestCase):
    def test_opens_without_login(self):
        res = self.client.get(reverse('pvsys:index'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, '企業価値の源泉は人月。その時間を実りあるものにする。')

    def test_links_to_each_page(self):
        res = self.client.get(reverse('pvsys:index'))
        for name in ('genba:about', 'genba:concept', 'genba:demo', 'proposals:index', 'fe:about', 'fe:index', 'fe:stats',
                     'welfare_activity:home', 'welfare_activity:slides'):
            self.assertContains(res, f'href="{reverse(name)}"')

    def test_links_open_in_new_tab(self):
        res = self.client.get(reverse('pvsys:index'))
        self.assertContains(res, 'target="_blank" rel="noopener noreferrer"', count=9)


class UrlTests(TestCase):
    def test_pages_live_under_pvsys(self):
        self.assertEqual(reverse('genba:about'), '/pvsys/genba/about/')
        self.assertEqual(reverse('proposals:index'), '/pvsys/proposals/')
        self.assertEqual(reverse('welfare_activity:home'), '/pvsys/welfare/')
        self.assertEqual(reverse('fe:index'), '/pvsys/fe/')

    def test_old_urls_redirect_without_login(self):
        # プレゼンの資料に古い URL が載っている
        for old, new in (('/genba/about/', '/pvsys/genba/about/'),
                         ('/proposals/', '/pvsys/proposals/'),
                         ('/welfare/slides/', '/pvsys/welfare/slides/')):
            with self.subTest(old):
                res = self.client.get(old)
                self.assertRedirects(res, new, status_code=301)

    def test_redirect_keeps_query_string(self):
        res = self.client.get('/genba/kit/?site=1')
        self.assertRedirects(res, '/pvsys/genba/kit/?site=1', status_code=301, fetch_redirect_response=False)

    def test_fe_opens_at_both_urls(self):
        for url in ('/fe/', '/pvsys/fe/'):
            with self.subTest(url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_fe_links_stay_on_the_url_being_opened(self):
        self.assertContains(self.client.get('/fe/'), 'href="/fe/stats/"')
        self.assertContains(self.client.get('/pvsys/fe/'), 'href="/pvsys/fe/stats/"')

    def test_scheduled_calls_are_not_redirected_at_root_url(self):
        # Logic Apps と GitHub Actions は転送をたどらないので、転送せずビューに届ける
        for url in ('/fe/tasks/send-mails/', '/fe/tasks/post-x/'):
            with self.subTest(url):
                self.assertNotEqual(self.client.post(url).status_code, 301)


class HomeTests(TestCase):
    def test_home_gathers_the_pages_here(self):
        # genba・3つの構想・基本情報・福祉従事者は PVSYS にまとめたので、ホームには入り口だけを出す
        self.client.force_login(CustomUser.objects.create_user('me', 'me@example.com', 'pass12345'))
        res = self.client.get(reverse('home:home'))
        self.assertContains(res, f'href="{reverse("pvsys:index")}"')
        for name in ('genba:about', 'proposals:index', 'fe:index', 'welfare_activity:home'):
            self.assertNotContains(res, f'href="{reverse(name)}"')
