from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser


class IndexTests(TestCase):
    def setUp(self):
        self.client.force_login(CustomUser.objects.create_user('me', 'me@example.com', 'pass12345'))

    def test_requires_login(self):
        self.client.logout()
        res = self.client.get(reverse('works:index'))
        self.assertRedirects(res, f'{reverse("account_login")}?next=/works/', fetch_redirect_response=False)

    def test_opens_after_login(self):
        res = self.client.get(reverse('works:index'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, '企業価値の源泉は人月。その時間を実りあるものにする。')

    def test_links_to_each_page(self):
        res = self.client.get(reverse('works:index'))
        for name in ('genba:about', 'genba:concept', 'genba:demo', 'proposals:index', 'fe:about', 'fe:index', 'fe:stats',
                     'welfare_activity:home', 'welfare_activity:slides'):
            self.assertContains(res, f'href="{reverse(name)}"')

    def test_links_open_in_new_tab(self):
        res = self.client.get(reverse('works:index'))
        self.assertContains(res, 'target="_blank" rel="noopener noreferrer"', count=9)


class UrlTests(TestCase):
    def test_urls_do_not_show_the_company_name(self):
        self.assertEqual(reverse('works:index'), '/works/')
        self.assertEqual(reverse('genba:about'), '/genba/about/')
        self.assertEqual(reverse('proposals:index'), '/proposals/')
        self.assertEqual(reverse('welfare_activity:home'), '/welfare/')
        self.assertEqual(reverse('fe:index'), '/fe/')

    def test_old_pvsys_urls_are_gone(self):
        # 転送を置くと、社名の URL が生き続ける
        for url in ('/pvsys/', '/pvsys/genba/about/', '/pvsys/proposals/', '/pvsys/fe/', '/pvsys/welfare/slides/'):
            with self.subTest(url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_public_pages_do_not_show_the_company_name(self):
        for name in ('genba:about', 'genba:concept', 'genba:demo', 'proposals:index', 'fe:about',
                     'fe:index', 'fe:stats', 'welfare_activity:home', 'welfare_activity:slides'):
            with self.subTest(name):
                self.assertNotContains(self.client.get(reverse(name)), 'pvsys', html=False)

    def test_presentation_urls_open_without_login(self):
        # プレゼンの資料に載っている URL
        for url in ('/genba/about/', '/proposals/', '/welfare/slides/'):
            with self.subTest(url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_scheduled_calls_are_not_redirected_at_root_url(self):
        # Logic Apps と GitHub Actions は転送をたどらないので、転送せずビューに届ける
        for url in ('/fe/tasks/send-mails/', '/fe/tasks/post-x/'):
            with self.subTest(url):
                self.assertNotEqual(self.client.post(url).status_code, 301)


class HomeTests(TestCase):
    def test_home_gathers_the_pages_here(self):
        # genba・3つの構想・基本情報・福祉従事者は「取り組み」にまとめたので、ホームには入り口だけを出す
        self.client.force_login(CustomUser.objects.create_user('me', 'me@example.com', 'pass12345'))
        res = self.client.get(reverse('home:home'))
        self.assertContains(res, f'href="{reverse("works:index")}"')
        for name in ('genba:about', 'proposals:index', 'fe:index', 'welfare_activity:home'):
            self.assertNotContains(res, f'href="{reverse(name)}"')
