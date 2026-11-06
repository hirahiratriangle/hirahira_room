from django.test import TestCase
from django.urls import reverse

from . import deck_data


class IndexTests(TestCase):
    def test_all_slides_open_without_login(self):
        res = self.client.get(reverse('wages:index'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context['total'], 17)
        self.assertContains(res, 'id="slide-17"')
        self.assertContains(res, 'id="deck-next"')
        for text in ('SE／PGの賃金と仕事の実質', 'どんな価値の対価か？', 'SE／PGの実質時給は11職種中4位', '仕事の中身を4つの職務特性で比べる',
                     '割高分の正体：雇用の制約を負わないことへの補償', '対価に含まれるもう一つのもの：成果物責任を負わない立場', '結論：得ている対価をどう使うか？', '分析の限界と出典'):
            self.assertContains(res, text)

    def test_no_speaker_notes(self):
        res = self.client.get(reverse('wages:index'))
        self.assertNotContains(res, 'deck-reading')
        self.assertNotContains(res, '本日は、客先常駐で働くSE／PGの賃金')

    def test_charts_keep_the_numbers(self):
        res = self.client.get(reverse('wages:index'))
        for text in ('2,566', '2,184', '1,652', '1,970円', '−0.09', '59.3', '32.0', 'T ＝ 50 ＋ 10 ×（x − μ）÷ σ'):
            self.assertContains(res, text)
        # SE／PG の棒・行・線だけをオレンジにする
        self.assertContains(res, 'bar-row is-self', count=1)
        self.assertContains(res, 'heat-name is-self', count=2)
        self.assertContains(res, 'class="is-self"', count=2)

    def test_both_se_pg_lines_meet_at_the_same_pay(self):
        se_lines = [line for line in deck_data.slope_lines() if line['self']]
        self.assertEqual(len(se_lines), 2)
        self.assertEqual(se_lines[0]['y2'], se_lines[1]['y2'])

    def test_no_company_name_and_no_links(self):
        res = self.client.get(reverse('wages:index'))
        self.assertNotContains(res, 'pvsys', html=False)
        self.assertNotContains(res, 'PVSYS')
        self.assertNotContains(res, 'target="_blank"')
