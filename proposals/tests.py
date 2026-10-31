from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser

from . import concept_data


class IndexTests(TestCase):
    def test_all_slides_open_without_login(self):
        res = self.client.get(reverse('proposals:index'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context['total'], 6)
        for text in ('人と会社と社会をつなぐ、', '一人ひとりの成長が、', '商流を縦に上がる。それは genba だ。',
                     '基本情報を、全社の共通言語にする', '福祉従事者の勘を支える伴奏者をつくる', '行けるところまで、皆で行こう。',
                     '（WG一覧 → WG-03 の「活動ページ」）', concept_data.FOOTER):
            self.assertContains(res, text)
        self.assertContains(res, 'id="slide-6"')
        self.assertContains(res, 'id="deck-next"')
        self.assertContains(res, '#2E6DB4')

    def test_reading_text_under_each_slide(self):
        res = self.client.get(reverse('proposals:index'))
        self.assertContains(res, 'class="deck-reading"', count=6)
        for note in concept_data.NOTES:
            self.assertContains(res, note)

    def test_links_to_each_concept(self):
        res = self.client.get(reverse('proposals:index'))
        self.assertContains(res, f'href="https://hirahira-room.net{reverse("genba:about")}"')
        self.assertContains(res, f'href="https://hirahira-room.net{reverse("fe:index")}"')
        self.assertContains(res, 'href="https://funitclub.org/wg/"')

    def test_home_links_here(self):
        self.client.force_login(CustomUser.objects.create_user('me', 'me@example.com', 'pass12345'))
        self.assertContains(self.client.get(reverse('home:home')), f'href="{reverse("proposals:index")}"')
