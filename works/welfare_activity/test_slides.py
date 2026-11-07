from django.test import TestCase
from django.urls import reverse


class SlidesViewTests(TestCase):
    """構想資料の中身。元の pptx から写したものが欠けていないか。"""

    def setUp(self):
        self.response = self.client.get(reverse('welfare_activity:slides'))
        self.html = self.response.content.decode()

    def test_slides_page_is_under_the_activity_page(self):
        self.assertEqual(reverse('welfare_activity:slides'), '/welfare/slides/')
        self.assertEqual(self.response.status_code, 200)
        self.assertTemplateUsed(self.response, 'welfare_activity/slides.html')

    def test_title_and_lead_are_shown(self):
        self.assertContains(self.response, '福祉従事者支援プラットフォーム構想 | HirahiraRoom')
        self.assertContains(self.response, 'Assessment &amp; Planning')
        self.assertContains(self.response, '仕事と知見が循環する基盤をつくる')

    def test_every_slide_has_a_section(self):
        """スライド 1 枚＝1 セクション。表紙を含めて 14 枚ぶん（元の pptx の 20 枚に、全文の資料から 2 枚を足した）。"""
        self.assertEqual(self.html.count('<section class="slide'), 14)
        for number in range(1, 15):
            self.assertContains(self.response, 'id="s{}"'.format(number))

    def test_each_slide_heading_is_shown(self):
        headings = [
            '福祉従事者にとって何が変わるか',
            '計画をつくる「ポスト」は福祉の各分野にある',
            'ITの目的を「効率化」から「創発」へ',
            '従事者の勘を支える伴奏者となるプラットフォームをつくる。',
            'LOOP：仕事を「回り続ける構造」として捉える',
            '蓄積するのは「データ」ではなく「知見」',
            '知見バンク＝知見を学習したモデル',
            '医療では始まっている：NYUTron',
            'アーキテクチャの考え方',
            '手持ちの機材で、一人から始められる',
            '成長する仕事の基盤をつくる。',
            '次に詰めるべき9の論点',
            'ITエンジニアの仕事の意義が変わる',
        ]
        for heading in headings:
            self.assertContains(self.response, heading)

    def test_each_slide_keeps_its_kicker(self):
        for kicker in ['構想資料', '出発点は従事者', '同じ構造の専門職',
                       '問題意識', '構想の核心', 'Assessment &amp; Planning Support Kit',
                       '知見とは', 'プラットフォームの中核', '先行事例',
                       'エンジニアリング', 'インフラ', '最大の設計思想', '論点', 'ITエンジニアの矜持']:
            self.assertContains(self.response, '<p class="kicker">{}</p>'.format(kicker))

    def test_has_no_monetization(self):
        """構想3は小さな事業体の社会貢献なので、収益と費用の試算は載せない。"""
        for text in ['広告', '収益', '万円', 'Indeed']:
            self.assertNotContains(self.response, text)

    def test_speaker_notes_are_shown_as_supplements(self):
        """発表者ノートがあるスライドは、補足として本文の下に出す。"""
        self.assertEqual(self.html.count('class="note-label"'), 11)
        self.assertContains(self.response, '主旋律を奏でるのは、いつも従事者自身です。')

    def test_tables_scroll_sideways_on_narrow_screens(self):
        self.assertEqual(self.html.count('<div class="table-wrap">'), 1)

    def test_is_mobile_friendly(self):
        self.assertContains(self.response, 'name="viewport"')

    def test_keeps_the_deck_palette(self):
        """配色は元 pptx の図形・表に指定された色から取る。"""
        for colour in ['#15313A', '#1B7F79', '#7CC4B8', '#E9A23B',
                       '#B5761A', '#EEF1F2', '#E4F1EF', '#FCF1DE', '#5B6B70']:
            self.assertContains(self.response, colour)

    def test_has_no_close_button(self):
        """別タブで開く資料なので、戻る操作や閉じる操作は置かない。"""
        self.assertNotContains(self.response, 'close-tab')

    def test_has_slide_navigation(self):
        """プレゼン風のページ送り。"""
        self.assertContains(self.response, 'id="prev"')
        self.assertContains(self.response, 'id="next"')
        self.assertContains(self.response, 'id="counter"')
        self.assertContains(self.response, 'id="dots"')
