from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class SiteHeaderTests(TestCase):
    def test_home_has_no_site_header(self):
        # サイト共通の黒いナビは廃止した。固定ナビの分の余白も残さない
        self.client.force_login(get_user_model().objects.create_user('me', 'me@example.com', 'pass12345'))
        res = self.client.get(reverse('home:home'))
        self.assertEqual(res.status_code, 200)
        self.assertNotContains(res, 'navbar')
        self.assertNotContains(res, 'HIRAHIRAROOM')
        self.assertNotContains(res, 'padding-top: 5rem')
