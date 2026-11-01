from django.test import TestCase
from django.urls import reverse

from . import concept_data


class IndexTests(TestCase):
    def test_all_slides_open_without_login(self):
        res = self.client.get(reverse('proposals:index'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context['total'], 7)
        for text in ('社内活動のご提案', '一人ひとりの成長が、', '人材の層を厚くする。それは genba だ。',
                     '基本情報を、全社の共通言語にする', '福祉従事者の勘を支える伴奏者をつくる', '行けるところまで、皆で行こう。',
                     concept_data.FOOTER):
            self.assertContains(res, text)
        self.assertContains(res, 'id="slide-7"')
        self.assertContains(res, 'id="deck-next"')
        self.assertContains(res, '#2E6DB4')

    def test_first_slide_has_closing_copy_and_no_links(self):
        res = self.client.get(reverse('proposals:index'))
        html = res.content.decode()
        self.assertContains(res, '多様な人こそ会社の礎。', count=2)
        first = html[html.index('id="slide-1"'):html.index('id="slide-2"')]
        self.assertIn('構想 1', first)
        self.assertNotIn('<a ', first)

    def test_second_slide_gathers_the_strategy(self):
        route = concept_data.SLIDES[1]
        self.assertEqual(route['kind'], 'route')
        res = self.client.get(reverse('proposals:index'))
        html = res.content.decode()
        self.assertContains(res, '商流を上げつつ、生成AI時代を勝ち抜く')
        lead = route['routes'][0]['text']
        self.assertContains(res, lead, count=1)
        self.assertLess(html.index('id="slide-2"'), html.index(lead))
        self.assertLess(html.index(lead), html.index('id="slide-3"'))

    def test_reading_text_under_each_slide(self):
        res = self.client.get(reverse('proposals:index'))
        self.assertContains(res, 'class="deck-reading"', count=7)
        for note in concept_data.NOTES:
            self.assertContains(res, note)

    def test_links_to_each_concept(self):
        res = self.client.get(reverse('proposals:index'))
        self.assertContains(res, f'href="{reverse("genba:about")}"')
        self.assertContains(res, f'href="{reverse("fe:about")}"')
        self.assertContains(res, f'href="{reverse("welfare_activity:slides")}"')
        self.assertNotContains(res, 'funitclub.org')

    def test_concept_links_open_in_new_tab(self):
        # 札のリンクは7枚目の3つだけ（1枚目はリンクなし）、構想のスライドの帯が3つ
        res = self.client.get(reverse('proposals:index'))
        self.assertContains(res, 'target="_blank" rel="noopener noreferrer"', count=6)
