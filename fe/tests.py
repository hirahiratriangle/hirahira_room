"""FE 対策アプリのテスト。

このアプリで壊れると痛いのは次の3点なので、そこを重点的に確かめる。
  ・重点出題が本当に苦手分野へ寄るか（統計的に検証する）
  ・問題が ID ごとに分かれ、他人の問題が混ざらないか
  ・一括差し替えをしても解答履歴と分野別の正答率が残るか
"""

import json
import random
import html
import re

from accounts.models import CustomUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Sum
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .exam import EXAM, build_prompt
from .generators import REGISTRY, generate_question
from .importer import (ImportError_, needs_confirmation, parse, preview, sync,
                       validate)
from .learning import note_menu, pick_note
from .models import (Attempt, Category, CategoryProgress, DailyProgress, Learner,
                     LearningNote, LearningRound, PassReport,
                     Question, QuestionTemplate, StudySession)
from .selection import (REVIEW_DUE_GAP, _due_for_review,
                        _last_result_map, _recent_answer_times, pick_question)
from .stats import (WEAK_MIN_ATTEMPTS, category_options, category_stats,
                    overall_stats,
                    record_progress, site_summary, smoothed_rate, weak_categories)
from .views import SESSION_LEARNER


def seed_masters():
    """中分類と問題テンプレートだけを投入する（問題は入らない）。"""
    call_command('seed_fe', verbosity=0)


def make_learner(code, is_admin=False):
    return Learner.objects.create(code=code, is_admin=is_admin)


def enter(client, learner):
    """hirahira_room にログインし、その ID で入った状態にする。

    ID はアカウントと紐づかないので、ログインするアカウントは誰でもよい。
    """
    account, _ = CustomUser.objects.get_or_create(username='hirahira')
    client.force_login(account)
    session = client.session
    session[SESSION_LEARNER] = learner.pk
    session.save()


def dashboard(learner):
    return reverse('fe:dashboard', args=[learner.code])


def build_questions(per_category=4):
    """テスト用の問題を全分類に作る。

    アプリは問題を同梱しないので、テストも配布物に依存させない。
    分類は科目ごとに別なので、その分類の科目に合わせて作る。
    科目Bは本番同様6択にする。
    """
    records = []
    for category in Category.objects.all():
        for i in range(per_category):
            is_subject_b = category.subject == Question.SUBJECT_B
            size = 6 if is_subject_b else 4
            records.append({
                'category': category.code,
                'subject': category.subject,
                'topic': category.name,
                'difficulty': 2,
                'stem': '{}の問題{}'.format(category.name, i),
                'choices': [
                    '選択肢{}-{}-{}'.format(j, category.code, i) for j in range(size)
                ],
                'answer': 0,
                'explanation': '解説',
                'source': 'テスト用',
            })
    return sync(validate({'questions': records, 'notes': None}))


def build_notes(codes=(9, 11), topic=None):
    """テスト用の技術解説。問題とは別に、概念を説明した文章として持たせる。"""
    notes = []
    for code in codes:
        category = Category.objects.get(code=code)
        notes.append({
            'category': code,
            'topic': topic if topic is not None else category.name,
            'title': '{}のしくみ'.format(category.name),
            'body': '{}について、定義からしくみまでを説明した文章。'.format(category.name),
            'source': 'シラバス',
        })
    return notes


def answer(learner, question, correct):
    """1問解いた状態を作る（解答ログと理解度の両方を更新する）。"""
    attempt = Attempt.objects.create(
        learner=learner, question=question, category=question.category,
        selected_index=question.answer_index if correct else 0, is_correct=correct,
    )
    record_progress(learner, question, correct)
    return attempt


class GeneratorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_masters()
        cls.learner = make_learner('gen')

    def test_all_templates_generate_valid_questions(self):
        for template in QuestionTemplate.objects.all():
            with self.subTest(template=template.key):
                rng = random.Random(template.key)
                for _ in range(30):
                    question = generate_question(template, rng=rng)
                    self.assertIsNotNone(question)
                    self.assertEqual(len(question.choices), 4)
                    self.assertEqual(len(set(question.choices)), 4)
                    self.assertEqual(question.category_id, template.category_id)

    def test_same_parameters_reuse_the_same_question(self):
        template = QuestionTemplate.objects.get(key='calc-availability-mtbf')
        first = generate_question(template, rng=random.Random(1))
        second = generate_question(template, rng=random.Random(1))
        self.assertEqual(first.id, second.id, '同じ数値なら同じ問題を使い回す')

    def test_generated_questions_are_shared_by_every_id(self):
        """問題は全 ID の共有なので、同じ数値なら誰が引いても同じ1問になる。"""
        template = QuestionTemplate.objects.get(key='calc-availability-mtbf')
        generate_question(template, rng=random.Random(2))
        generate_question(template, rng=random.Random(2))
        self.assertEqual(Question.objects.filter(template=template).count(), 1)

    def test_registry_and_templates_match(self):
        self.assertEqual(
            set(REGISTRY), set(QuestionTemplate.objects.values_list('key', flat=True))
        )


class StatsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_masters()
        cls.learner = make_learner('stats')
        build_questions()

    def test_smoothing_pulls_small_samples_toward_the_prior(self):
        self.assertAlmostEqual(smoothed_rate(0, 1), 3.0 / 6.0)
        self.assertGreater(smoothed_rate(0, 1), 0.4)
        self.assertLess(smoothed_rate(0, 100), 0.05)

    def test_weak_categories_need_a_minimum_number_of_attempts(self):
        security = Category.objects.get(code=11)
        question = Question.objects.filter(category=security).first()
        answer(self.learner, question, correct=False)
        rows = category_stats(self.learner)
        self.assertEqual(weak_categories(rows), [], '1問だけでは苦手判定しない')

    def test_category_stats_counts_correctly(self):
        security = Category.objects.get(code=11)
        questions = list(Question.objects.filter(category=security)[:4])
        for index, question in enumerate(questions):
            answer(self.learner, question, correct=index == 0)
        row = next(r for r in category_stats(self.learner) if r['category'] == security)
        self.assertEqual(row['total'], 4)
        self.assertEqual(row['correct'], 1)
        self.assertEqual(row['rate_percent'], 25)
        self.assertTrue(row['is_weak'])

    def test_other_ids_answers_do_not_count(self):
        """問題は共有でも、成績は ID ごと。ほかの ID の解答は数えない。"""
        other = make_learner('stats_other')
        security = Category.objects.get(code=11)
        answer(other, Question.objects.filter(category=security).first(), correct=True)
        row = next(r for r in category_stats(self.learner) if r['category'] == security)
        self.assertEqual(row['total'], 0)
        self.assertEqual(overall_stats(self.learner)['total'], 0)


class SelectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_masters()
        cls.learner = make_learner('picker')
        build_questions()

    def _answer(self, category, correct, count):
        questions = list(Question.objects.filter(category=category)[:count])
        self.assertGreaterEqual(len(questions), 1)
        for index in range(count):
            answer(self.learner, questions[index % len(questions)], correct)

    def test_a_missed_question_is_not_repeated_immediately(self):
        """まちがえた直後に同じ問題を出さない。答えを覚えているだけになる。"""
        category = Category.objects.get(code=9)
        missed = Question.objects.filter(category=category).first()
        answer(self.learner, missed, correct=False)

        session = StudySession.objects.create(
            learner=self.learner, mode=StudySession.MODE_CATEGORY,
            subject=Question.SUBJECT_A, category=category,
        )
        random.seed(20260908)
        for _ in range(30):
            question, _reason = pick_question(self.learner, session)
            self.assertNotEqual(question.id, missed.id, '間をあけずに戻ってきた')

    def test_a_missed_question_comes_back_before_unseen_ones(self):
        """間隔があけば、未出題が残っていても復習が優先される。"""
        category = Category.objects.get(code=9)
        questions = list(Question.objects.filter(category=category))
        self.assertGreaterEqual(len(questions), 4)
        missed = questions[0]
        answer(self.learner, missed, correct=False)
        # ほかの問題を解いて間隔をあける
        for question in questions[1:]:
            answer(self.learner, question, correct=True)
        for _ in range(REVIEW_DUE_GAP):
            answer(self.learner, questions[1], correct=True)

        session = StudySession.objects.create(
            learner=self.learner, mode=StudySession.MODE_CATEGORY,
            subject=Question.SUBJECT_A, category=category,
        )
        random.seed(20260908)
        reasons = set()
        for _ in range(60):
            question, reason = pick_question(self.learner, session)
            if question.id == missed.id:
                reasons.add(reason)
        self.assertIn('まちがえた問題の復習', reasons, '間隔をあけても戻ってこなかった')

    def test_repeated_misses_shorten_the_interval(self):
        """何度も落とした問題ほど、短い間隔で戻す。"""
        category = Category.objects.get(code=9)
        questions = list(Question.objects.filter(category=category))
        once, twice = questions[0], questions[1]
        # 2回落とした方を先に、1回だけの方をあとに解く。
        # あとに解いたほうが間隔は短いので、間隔だけで並ぶなら once は戻らない。
        answer(self.learner, twice, correct=False)
        answer(self.learner, twice, correct=False)
        answer(self.learner, once, correct=False)
        for question in questions[2:]:
            answer(self.learner, question, correct=True)
        answer(self.learner, questions[2], correct=True)

        history = _last_result_map(self.learner, [once.id, twice.id])
        times = _recent_answer_times(self.learner)
        due = dict(
            (q.id, over) for q, over in
            _due_for_review([once, twice], history, times)
        )
        # 2回落とした方が先に期限を迎える
        self.assertIn(twice.id, due, '2回落とした問題は短い間隔で戻るべき')
        self.assertNotIn(once.id, due, '1回だけの問題はまだ間隔が足りない')

    def test_focus_mode_favours_weak_categories(self):
        database = Category.objects.get(code=9)
        network = Category.objects.get(code=10)
        self._answer(database, correct=False, count=10)
        self._answer(network, correct=True, count=10)

        session = StudySession.objects.create(
            learner=self.learner, mode=StudySession.MODE_FOCUS, subject=Question.SUBJECT_A
        )
        random.seed(20260906)
        counts = {}
        for _ in range(600):
            question, _reason = pick_question(self.learner, session)
            counts[question.category.code] = counts.get(question.category.code, 0) + 1

        self.assertGreater(
            counts.get(9, 0), counts.get(10, 0),
            '想定出題数が少なくても、苦手なデータベースの方が多く出るべき',
        )

    def test_random_mode_follows_the_exam_ratio(self):
        session = StudySession.objects.create(
            learner=self.learner, mode=StudySession.MODE_RANDOM, subject=Question.SUBJECT_A
        )
        random.seed(11)
        counts = {}
        for _ in range(800):
            question, _reason = pick_question(self.learner, session)
            counts[question.category.code] = counts.get(question.category.code, 0) + 1
        self.assertGreater(counts.get(11, 0), counts.get(23, 0) * 2)

    def test_category_mode_stays_in_the_selected_category(self):
        database = Category.objects.get(code=9)
        session = StudySession.objects.create(
            learner=self.learner, mode=StudySession.MODE_CATEGORY,
            subject=Question.SUBJECT_A, category=database,
        )
        for _ in range(30):
            question, _reason = pick_question(self.learner, session)
            self.assertEqual(question.category_id, database.id)

    def test_subject_b_mode_only_returns_subject_b_questions(self):
        session = StudySession.objects.create(
            learner=self.learner, mode=StudySession.MODE_FOCUS, subject=Question.SUBJECT_B
        )
        for _ in range(30):
            question, _reason = pick_question(self.learner, session)
            self.assertEqual(question.subject, Question.SUBJECT_B)
            self.assertEqual(question.category.subject, Question.SUBJECT_B)
            self.assertIn(question.category.code, range(101, 106))

    def test_review_mode_returns_previously_wrong_questions(self):
        database = Category.objects.get(code=9)
        questions = list(Question.objects.filter(category=database)[:3])
        for question in questions:
            answer(self.learner, question, correct=False)
        session = StudySession.objects.create(
            learner=self.learner, mode=StudySession.MODE_REVIEW, subject=Question.SUBJECT_A
        )
        wrong_ids = {q.id for q in questions}
        for _ in range(20):
            question, _reason = pick_question(self.learner, session)
            self.assertIn(question.id, wrong_ids)

    def test_recently_shown_questions_are_avoided(self):
        session = StudySession.objects.create(
            learner=self.learner, mode=StudySession.MODE_FOCUS, subject=Question.SUBJECT_A
        )
        first, _ = pick_question(self.learner, session)
        for _ in range(20):
            question, _reason = pick_question(self.learner, session, exclude_ids=[first.id])
            self.assertNotEqual(question.id, first.id)

    def test_every_id_is_served_from_the_shared_set(self):
        other = make_learner('picker_other')
        session = StudySession.objects.create(
            learner=other, mode=StudySession.MODE_FOCUS, subject=Question.SUBJECT_A
        )
        question, _reason = pick_question(other, session)
        self.assertIsNotNone(question)

    def test_no_questions_yields_nothing_instead_of_crashing(self):
        Question.objects.all().delete()
        empty = make_learner('picker_empty')
        session = StudySession.objects.create(
            learner=empty, mode=StudySession.MODE_FOCUS, subject=Question.SUBJECT_B
        )
        question, reason = pick_question(empty, session)
        self.assertIsNone(question)
        self.assertIsNone(reason)


class ViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_masters()
        cls.learner = make_learner('viewer')
        build_questions()

    def setUp(self):
        enter(self.client, self.learner)

    def test_unsupported_formats_are_stated_up_front(self):
        """対応していない問題形式の注意書きを、ダッシュボードと入口に畳まずに出す。"""
        for url in (dashboard(self.learner), reverse('fe:enter')):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode('utf-8')
                self.assertIn('このアプリが対応していない問題・問題形式', html)
                self.assertIn('図を見ないと解けない問題', html)
                self.assertIn('本番どおりの模擬試験', html)
                self.assertIn('記述・入力・並べ替え', html)

    def test_import_screen_states_what_cannot_be_rendered(self):
        """取り込み画面では、作る側が踏む制約も併せて示す。"""
        admin = make_learner('noticeadmin', is_admin=True)
        enter(self.client, admin)
        html = self.client.get(reverse('fe:manage_upload')).content.decode('utf-8')
        self.assertIn('このアプリで出せない問題', html)
        for word in ('図が本質の問題は作れません', '上付き', '組合せを1つの選択肢'):
            self.assertIn(word, html)

    def test_prompt_states_unsupported_formats(self):
        """利用者が AI に渡すプロンプトにも、扱えない形式を書いておく。"""
        from .exam import build_prompt
        prompt = build_prompt()
        self.assertIn('【このアプリが扱えないもの】', prompt)
        for word in ('図が必要な問題は作らない', '2のn乗', 'ルビ', '複数正解や部分点はない'):
            self.assertIn(word, prompt)

    def test_index_renders(self):
        response = self.client.get(dashboard(self.learner))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '基本情報技術者試験')

    def test_every_page_with_the_settings_form_offers_the_categories(self):
        """出題設定のフォームを置く画面は、分野の選択肢も渡すこと。

        フォームを include で共有しているので、渡し忘れても画面は出る。
        選択肢が「指定なし」だけの状態で出てしまい、気づきにくい。
        """
        total = Category.objects.count()
        for url in (dashboard(self.learner), reverse('fe:quiz')):
            with self.subTest(page=url):
                response = self.client.get(url)
                self.assertEqual(len(response.context['categories']), total)
                self.assertContains(response, 'セキュリティ')

    def test_category_options_carry_the_subject(self):
        """分類は科目ごとに別。画面で出し分けられるよう科目を添えている。"""
        options = {o['code']: o for o in category_options(self.learner)}
        self.assertEqual(options[11]['subject'], Question.SUBJECT_A, 'セキュリティは科目A')
        self.assertEqual(options[103]['subject'], Question.SUBJECT_B)
        self.assertFalse(
            set(range(1, 24)) & set(range(101, 106)),
            '科目Aと科目Bの分類番号は重ならない',
        )
        self.assertGreater(options[103]['question_count'], 0)

    def test_only_the_current_subject_is_rendered(self):
        """分類は科目ごとに別なので、最初から選んでいる科目のぶんだけ描く。

        全部を描いて JavaScript で隠す作りだと、JS が動くまでの一瞬、
        別の科目の分類が見えてしまう。
        """
        session, _ = StudySession.objects.get_or_create(learner=self.learner)
        for subject, expected in ((Question.SUBJECT_A, 23), (Question.SUBJECT_B, 5)):
            with self.subTest(subject=subject):
                session.subject = subject
                session.category = None
                session.save()
                html = self.client.get(dashboard(self.learner)).content.decode('utf-8')
                select = re.search(r'id="category".*?</select>', html, re.S).group(0)
                codes = [int(v) for v in re.findall(r'<option value="(\d+)"', select)]
                self.assertEqual(len(codes), expected)
                for code in codes:
                    self.assertEqual(
                        Category.objects.get(code=code).subject, subject,
                    )

    def test_the_category_field_is_locked_unless_the_mode_uses_it(self):
        """分野が効くのは「分野を指定」のときだけ。ほかのモードでは操作させない。

        選べるのに保存時に捨てられる状態だと、選んだつもりが効いていない。
        """
        session, _ = StudySession.objects.get_or_create(learner=self.learner)
        for mode, locked in (
            (StudySession.MODE_CATEGORY, False),
            (StudySession.MODE_FOCUS, True),
            (StudySession.MODE_REVIEW, True),
        ):
            with self.subTest(mode=mode):
                session.mode = mode
                session.save()
                html = self.client.get(dashboard(self.learner)).content.decode('utf-8')
                block = re.search(r'<select[^>]*id="category".*?>', html, re.S).group(0)
                self.assertEqual('disabled' in block, locked)

    def test_the_category_shows_only_when_the_mode_uses_it(self):
        """分野が効くのは「分野を指定」のときだけ。

        ほかのモードでも保存値を映していると、効いていない分野が選ばれた
        ままに見える。設定を送らない限り変わらないので、固定されたように映る。
        """
        session, _ = StudySession.objects.get_or_create(learner=self.learner)
        session.subject = Question.SUBJECT_A
        session.category = Category.objects.get(code=2)

        def selected():
            html = self.client.get(dashboard(self.learner)).content.decode('utf-8')
            block = re.search(r'id="category".*?</select>', html, re.S).group(0)
            found = re.findall(r'<option value="(\d*)"[^>]*selected', block)
            return found[0] if found else ''

        session.mode = StudySession.MODE_CATEGORY
        session.save()
        self.assertEqual(selected(), '2', '分野指定のときは選ばれて見える')

        session.mode = StudySession.MODE_FOCUS
        session.save()
        self.assertEqual(selected(), '', 'ほかのモードでは指定なしに見える')

    def test_saving_from_the_dashboard_comes_back_to_it(self):
        """ダッシュボードで設定を変えたら、その場に戻す。

        変えた瞬間に演習が始まると、何が保存されたのか確かめられない。
        ダッシュボードには「いまの設定で解く」が別にあるので、そちらで始める。
        """
        response = self.client.post(reverse('fe:quiz_settings'), {
            'mode': StudySession.MODE_RANDOM, 'subject': 'A', 'next': 'index',
        })
        self.assertRedirects(response, dashboard(self.learner))

        # 演習画面からなら、そのまま次の問題へ進む
        response = self.client.post(reverse('fe:quiz_settings'), {
            'mode': StudySession.MODE_RANDOM, 'subject': 'A',
        })
        self.assertRedirects(response, reverse('fe:quiz'))

    def test_the_dashboard_button_says_it_only_saves(self):
        html = self.client.get(dashboard(self.learner)).content.decode('utf-8')
        self.assertIn('設定を保存', html)
        self.assertNotIn('この設定で出題', html)

    def test_a_category_from_the_other_subject_is_refused(self):
        """科目に無い分野を指定されたら受け付けない。"""
        response = self.client.post(reverse('fe:quiz_settings'), {
            'mode': StudySession.MODE_CATEGORY, 'subject': 'A', 'category': 103,
        })
        self.assertEqual(response.status_code, 302)
        session = StudySession.objects.get(learner=self.learner)
        self.assertIsNone(session.category)
        self.assertEqual(session.mode, StudySession.MODE_FOCUS)

    def test_the_two_subjects_have_separate_categories(self):
        """科目Aだけの分類と科目Bだけの分類に分かれ、重なりが無いこと。"""
        a = set(Category.objects.filter(subject='A').values_list('code', flat=True))
        b = set(Category.objects.filter(subject='B').values_list('code', flat=True))
        self.assertEqual(len(a), 23)
        self.assertEqual(len(b), 5)
        self.assertFalse(a & b)
        self.assertEqual(
            sum(c.exam_weight for c in Category.objects.filter(subject='A')), 60,
            '科目Aの想定出題数の合計は本番の60問',
        )
        self.assertEqual(
            sum(c.exam_weight for c in Category.objects.filter(subject='B')), 20,
            '科目Bの想定出題数の合計は本番の20問',
        )

    def test_quiz_shows_a_question_and_grades_the_answer(self):
        response = self.client.get(reverse('fe:quiz'))
        self.assertEqual(response.status_code, 200)
        question = response.context['question']

        response = self.client.post(reverse('fe:quiz'), {
            'question_id': question.id, 'choice': question.answer_index,
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_correct'])

        attempt = Attempt.objects.get(learner=self.learner, question=question)
        self.assertTrue(attempt.is_correct)
        self.assertEqual(attempt.category_id, question.category_id)

    def test_wrong_answer_is_recorded_and_explained(self):
        response = self.client.get(reverse('fe:quiz'))
        question = response.context['question']
        wrong = (question.answer_index + 1) % len(question.choices)
        response = self.client.post(reverse('fe:quiz'), {
            'question_id': question.id, 'choice': wrong,
        })
        self.assertFalse(response.context['is_correct'])
        self.assertContains(response, '解説')
        self.assertFalse(Attempt.objects.get(learner=self.learner).is_correct)

    def test_double_submission_is_not_counted_twice(self):
        response = self.client.get(reverse('fe:quiz'))
        question = response.context['question']
        payload = {'question_id': question.id, 'choice': question.answer_index}
        self.client.post(reverse('fe:quiz'), payload)
        self.client.post(reverse('fe:quiz'), payload)
        self.assertEqual(Attempt.objects.filter(learner=self.learner).count(), 1)

    def test_settings_change_the_study_session(self):
        response = self.client.post(reverse('fe:quiz_settings'), {
            'mode': StudySession.MODE_CATEGORY, 'subject': 'A', 'category': 11,
        })
        self.assertRedirects(response, reverse('fe:quiz'))
        session = StudySession.objects.get(learner=self.learner)
        self.assertEqual(session.mode, StudySession.MODE_CATEGORY)
        self.assertEqual(session.category.code, 11)

    def test_stats_and_history_render(self):
        for url in (reverse('fe:stats'), reverse('fe:history')):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_login_is_not_required_to_study(self):
        """学習の画面は、hirahira_room にログインしていなくても使える。"""
        self.client.logout()
        self.assertEqual(self.client.get(reverse('fe:index')).status_code, 200)
        self.client.post(reverse('fe:index'), {'action': 'temporary'})
        for name in ('fe:index', 'fe:enter', 'fe:quiz', 'fe:learn_start',
                     'fe:stats', 'fe:history'):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_login_is_still_required_to_manage(self):
        """管理用の ID で入っていても、ログインしていなければ管理画面は開けない。"""
        self.client.logout()
        session = self.client.session
        session[SESSION_LEARNER] = make_learner('boss', is_admin=True).pk
        session.save()
        for name in ('fe:manage_list', 'fe:manage_upload', 'fe:manage_export'):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('account_login'), response['Location'])
        self.assertEqual(self.client.post(reverse('fe:manage_upload'), {}).status_code, 302)


class LearnerIdTests(TestCase):
    """本アプリの ID。入口で入り、ダッシュボードは /fe/<ID>/ で本人だけが開ける。"""

    @classmethod
    def setUpTestData(cls):
        seed_masters()
        cls.account = CustomUser.objects.create_user(username='someone', password='pass12345')

    def setUp(self):
        self.client.force_login(self.account)

    def _post(self, code, action):
        return self.client.post(reverse('fe:index'), {'code': code, 'action': action})

    def test_without_an_id_the_entrance_is_shown(self):
        response = self.client.get(reverse('fe:index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'この ID で入る')

    def test_top_page_offers_both_ways_to_start(self):
        """トップでは、履歴を残すかどうかを選べる。"""
        html = self.client.get(reverse('fe:index')).content.decode('utf-8')
        self.assertIn('履歴を残さずに始める', html)
        self.assertIn('履歴を残す（ID を登録）', html)

    def test_can_start_without_registering_an_id(self):
        """ID を登録せずに始められ、そのままダッシュボードが開く。"""
        response = self.client.post(reverse('fe:index'), {'action': 'temporary'})
        self.assertRedirects(response, reverse('fe:index'))
        learner = Learner.objects.get(code__isnull=True)
        self.assertTrue(learner.is_temporary)
        # 入口がそのままダッシュボードになる（URL に ID は出ない）
        html = self.client.get(reverse('fe:index')).content.decode('utf-8')
        self.assertIn('履歴なし', html)
        self.assertNotIn('履歴を残さずに始める', html)

    def test_temporary_use_can_answer_and_keeps_stats_while_in_use(self):
        """使っている間は、ふつうに解けて成績も積み上がる。"""
        build_questions()
        self.client.post(reverse('fe:index'), {'action': 'temporary'})
        learner = Learner.objects.get(code__isnull=True)
        question = Question.objects.first()
        answer(learner, question, correct=True)
        self.assertEqual(
            CategoryProgress.objects.get(learner=learner, category=question.category).answered, 1
        )
        self.assertEqual(self.client.get(reverse('fe:quiz')).status_code, 200)

    def test_registering_an_id_later_starts_from_zero(self):
        """履歴なしで使ったあとに ID を登録しても、それまでの解答は引き継がない。"""
        build_questions()
        self.client.post(reverse('fe:index'), {'action': 'temporary'})
        temp = Learner.objects.get(code__isnull=True)
        answer(temp, Question.objects.first(), correct=True)

        self._post('later_id', 'create')
        named = Learner.objects.get(code='later_id')
        self.assertEqual(CategoryProgress.objects.filter(learner=named).count(), 0)
        # 一時的な記録はそのまま残り、掃除の対象になる
        self.assertTrue(Learner.objects.filter(pk=temp.pk).exists())

    def test_temporary_use_is_excluded_from_the_site_summary(self):
        """消える記録は全体集計に数えない。"""
        build_questions()
        self.client.post(reverse('fe:index'), {'action': 'temporary'})
        temp = Learner.objects.get(code__isnull=True)
        answer(temp, Question.objects.first(), correct=True)
        self.assertEqual(site_summary()['total'], 0)

        named = make_learner('counted')
        answer(named, Question.objects.first(), correct=True)
        self.assertEqual(site_summary()['total'], 1)

    def test_temporary_use_cannot_report_a_pass(self):
        """履歴を残さない利用からは合格申告できない。"""
        self.client.post(reverse('fe:index'), {'action': 'temporary'})
        response = self.client.post(
            reverse('fe:pass_report'), {'passed_on': '2026-04-01'}
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(PassReport.objects.count(), 0)

    def test_purge_removes_only_stale_temporary_records(self):
        """掃除は、使われなくなった一時的な記録だけを消す。"""
        from django.utils import timezone
        fresh = Learner.objects.create(code=None)
        stale = Learner.objects.create(code=None)
        Learner.objects.filter(pk=stale.pk).update(
            last_seen_at=timezone.now() - timezone.timedelta(days=90)
        )
        named = make_learner('keep_me')

        call_command('purge_temp_learners', verbosity=0)
        self.assertFalse(Learner.objects.filter(pk=stale.pk).exists())
        self.assertTrue(Learner.objects.filter(pk=fresh.pk).exists())
        self.assertTrue(Learner.objects.filter(pk=named.pk).exists())

    def test_create_opens_the_new_dashboard(self):
        response = self._post('Hira_01', 'create')
        # 大文字小文字は区別せず、小文字にそろえる
        self.assertRedirects(response, '/fe/hira_01/')
        self.assertTrue(Learner.objects.filter(code='hira_01').exists())
        # 入ったあとは、入口を開いてもダッシュボードへ直行する
        self.assertRedirects(self.client.get(reverse('fe:index')), '/fe/hira_01/')

    def test_create_refuses_a_taken_or_bad_id(self):
        make_learner('taken')
        for code in ('taken', 'TAKEN', 'quiz', 'enter', 'ab', 'ひらがな', 'a b', 'x' * 31):
            with self.subTest(code=code):
                response = self._post(code, 'create')
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['form'].errors)
        self.assertEqual(Learner.objects.count(), 1)

    def test_enter_needs_an_existing_id(self):
        response = self._post('nobody', 'enter')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'その ID はまだありません')
        self.assertFalse(Learner.objects.exists())

    def test_anyone_with_the_id_can_enter(self):
        """合言葉は無い。ID を入力した人を本人として扱う。"""
        make_learner('shared')
        self.assertRedirects(self._post('shared', 'enter'), '/fe/shared/')

    def test_only_the_current_id_can_open_its_dashboard(self):
        mine = make_learner('mine')
        make_learner('theirs')
        enter(self.client, mine)
        self.assertEqual(self.client.get('/fe/mine/').status_code, 200)
        self.assertEqual(self.client.get('/fe/MINE/').status_code, 200)
        self.assertEqual(self.client.get('/fe/theirs/').status_code, 404)
        self.assertEqual(self.client.get('/fe/nobody/').status_code, 404)

    def test_without_an_id_other_pages_go_to_the_entrance(self):
        make_learner('someone')
        self.assertEqual(self.client.get('/fe/someone/').status_code, 302)
        for name in ('fe:quiz', 'fe:stats', 'fe:history', 'fe:learn_start', 'fe:manage_list'):
            with self.subTest(page=name):
                self.assertRedirects(self.client.get(reverse(name)), reverse('fe:index'))

    def test_the_enter_page_switches_ids_any_time(self):
        """入ったあとでも入口を開け、別の ID を入力すれば何度でも切り替わる。"""
        first, second = make_learner('first'), make_learner('second')
        enter(self.client, first)
        response = self.client.get(reverse('fe:enter'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'いまは ID「<strong>first</strong>」で入っています')

        self.assertRedirects(
            self.client.post(reverse('fe:enter'), {'code': 'second', 'action': 'enter'}),
            dashboard(second),
        )
        self.assertEqual(self.client.get(dashboard(first)).status_code, 404)
        self.assertRedirects(
            self.client.post(reverse('fe:enter'), {'code': 'first', 'action': 'enter'}),
            dashboard(first),
        )
        self.assertRedirects(
            self.client.post(reverse('fe:enter'), {'code': 'third', 'action': 'create'}),
            '/fe/third/',
        )

    def test_every_page_links_to_the_enter_page(self):
        learner = make_learner('linked')
        enter(self.client, learner)
        for url in (dashboard(learner), reverse('fe:stats'), reverse('fe:quiz')):
            with self.subTest(url=url):
                self.assertContains(self.client.get(url), reverse('fe:enter'))

    def test_ids_in_the_same_account_are_separate(self):
        """同じアカウントでも、ID が違えば成績は別。問題集は共有。"""
        first, second = make_learner('first'), make_learner('second')
        build_questions()
        answer(first, Question.objects.all().first(), correct=True)
        enter(self.client, second)
        response = self.client.get(dashboard(second))
        self.assertEqual(response.context['question_total'], Question.objects.count())
        self.assertEqual(response.context['overall']['total'], 0)

    def test_only_an_admin_id_can_manage_questions(self):
        build_questions()
        enter(self.client, make_learner('plain'))
        for name in ('fe:manage_list', 'fe:manage_upload', 'fe:manage_export'):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)
        response = self.client.post(reverse('fe:manage_upload'), {})
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(self.client.get(reverse('fe:stats')), '問題の管理')

        enter(self.client, make_learner('boss', is_admin=True))
        self.assertEqual(self.client.get(reverse('fe:manage_list')).status_code, 200)
        self.assertContains(self.client.get(reverse('fe:stats')), '問題の管理')


class ManageTests(TestCase):
    """問題の一括差し替え。JSON が唯一の登録経路になっている。"""

    @classmethod
    def setUpTestData(cls):
        seed_masters()
        cls.learner = make_learner('owner', is_admin=True)
        cls.other = make_learner('stranger')

    def setUp(self):
        enter(self.client, self.learner)

    def _upload(self, n=3, prefix='問題'):
        """取り込みは JSON ファイル1本だけなので、テストもファイルで投げる。"""
        return {'questions_file': SimpleUploadedFile(
            'q.json', self._payload(n, prefix).encode('utf-8'),
            content_type='application/json',
        )}

    def _post_upload(self, data):
        """取り込みを最後まで通す。削除が多くて確認が出たら、承認して続ける。"""
        response = self.client.post(reverse('fe:manage_upload'), data)
        if response.status_code == 200 and '削除が多いので' in response.content.decode('utf-8'):
            payload = re.search(
                r'name="payload" value="(.*?)">', response.content.decode('utf-8'), re.S
            ).group(1)
            response = self.client.post(reverse('fe:manage_upload'), {
                'confirm': '1', 'payload': html.unescape(payload),
            })
        return response

    def _json_upload(self, records):
        return {'questions_file': SimpleUploadedFile(
            'q.json', json.dumps(records, ensure_ascii=False).encode('utf-8'),
            content_type='application/json',
        )}

    def _upload_with_notes(self, n=3, codes=(9, 11), topic=None):
        return self._json_upload({
            'notes': build_notes(codes=codes, topic=topic),
            'questions': json.loads(self._payload(n)),
        })

    def _payload(self, n=3, prefix='問題'):
        return json.dumps([
            {
                'category': 11, 'subject': 'A', 'topic': '情報セキュリティ', 'difficulty': 2,
                'stem': '{}{}'.format(prefix, i),
                'choices': ['ア{}'.format(i), 'イ{}'.format(i), 'ウ{}'.format(i), 'エ{}'.format(i)],
                'answer': 0, 'explanation': '解説', 'source': 'テスト',
            }
            for i in range(n)
        ], ensure_ascii=False)

    def test_empty_state_is_shown_before_any_upload(self):
        response = self.client.get(reverse('fe:manage_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'まだ問題がありません')

    def test_upload_by_file_creates_the_question_set(self):
        response = self.client.post(reverse('fe:manage_upload'), self._upload(3))
        self.assertRedirects(response, reverse('fe:manage_list'))
        questions = Question.objects.all()
        self.assertEqual(questions.count(), 3)

    def test_pasting_is_not_offered(self):
        """取り込み経路はファイル1本。貼り付け欄は置かない。"""
        response = self.client.get(reverse('fe:manage_upload'))
        self.assertNotIn('payload', response.context['form'].fields)
        self.assertNotContains(response, 'id_payload')
        self.assertNotContains(response, '<textarea')

    def test_a_large_file_is_accepted(self):
        """解説を添えると 1MB を超えるので、その大きさで通ること。"""
        records = json.loads(self._payload(200, '大きな問題'))
        notes = [
            {
                'category': 11, 'topic': '情報セキュリティ',
                'title': '解説{}'.format(i), 'body': 'あ' * 8000, 'source': 'テスト',
            }
            for i in range(120)
        ]
        payload = json.dumps(
            {'notes': notes, 'questions': records}, ensure_ascii=False
        ).encode('utf-8')
        # 小分類ぶんの解説を厚く書いた実運用の大きさを想定する。
        self.assertGreater(len(payload), 2 * 1024 * 1024, '2MB を超える前提のテスト')

        response = self.client.post(reverse('fe:manage_upload'), {
            'questions_file': SimpleUploadedFile('big.json', payload,
                                         content_type='application/json'),
        })
        self.assertRedirects(response, reverse('fe:manage_list'))
        self.assertEqual(Question.objects.all().count(), 200)
        self.assertEqual(LearningNote.objects.all().count(), 120)

    def test_upload_is_required(self):
        response = self.client.post(reverse('fe:manage_upload'), {})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)

    def test_upload_deletes_the_old_set_and_its_logs(self):
        self._post_upload(self._upload(3, '旧'))
        old = list(Question.objects.all())
        old_ids = [q.id for q in old]
        answer(self.learner, old[0], correct=True)
        answer(self.other, old[1], correct=False)

        self._post_upload(self._upload(2, '新'))

        self.assertEqual(
            Question.objects.all().count(), 2,
            '古い問題は削除され、データが増え続けない',
        )
        self.assertFalse(Question.objects.filter(id__in=old_ids).exists())
        self.assertEqual(
            Attempt.objects.count(), 0,
            '全 ID の解答ログが問題と一緒に消える',
        )

    def test_understanding_survives_the_replacement(self):
        """問題を入れ替えても、分野ごとの理解度は残る。"""
        self.client.post(reverse('fe:manage_upload'), self._upload(3, '旧'))
        security = Category.objects.get(code=11)
        questions = list(Question.objects.all())
        answer(self.learner, questions[0], correct=True)
        answer(self.learner, questions[1], correct=False)

        self.client.post(reverse('fe:manage_upload'), self._upload(2, '新'))

        row = next(r for r in category_stats(self.learner) if r['category'] == security)
        self.assertEqual(row['total'], 2, '解答数は残る')
        self.assertEqual(row['correct'], 1, '正解数も残る')
        self.assertEqual(row['rate_percent'], 50)

        overall = overall_stats(self.learner)
        self.assertEqual(overall['total'], 2)
        self.assertEqual(overall['study_days'], 1, '学習した日数も残る')

    def test_progress_is_kept_per_account(self):
        build_questions()
        self.client.post(reverse('fe:manage_upload'), self._upload(2))
        mine = Question.objects.all().first()
        theirs = Question.objects.all().first()
        answer(self.learner, mine, correct=True)
        answer(self.other, theirs, correct=False)

        self.assertEqual(overall_stats(self.learner)['correct'], 1)
        self.assertEqual(overall_stats(self.other)['correct'], 0)
        self.assertEqual(
            CategoryProgress.objects.filter(learner=self.learner).count(), 1,
            '他人の解答が自分の理解度に混ざらない',
        )

    def test_upload_replaces_the_set_for_every_id(self):
        build_questions()
        self._post_upload(self._upload(2))
        self.assertEqual(
            Question.objects.filter(template__isnull=True).count(), 2,
            '問題集は共有なので、取り込むと全員の問題集が入れ替わる',
        )

    def test_broken_json_is_rejected_without_touching_anything(self):
        self.client.post(reverse('fe:manage_upload'), self._upload(2))
        before = list(Question.objects.all().values_list('id', flat=True))
        response = self.client.post(reverse('fe:manage_upload'), {'questions_file': SimpleUploadedFile(
            'q.json', b'{ not json', content_type='application/json')})
        self.assertEqual(response.status_code, 200)
        after = list(Question.objects.all().values_list('id', flat=True))
        self.assertEqual(before, after, '取り込みに失敗したら既存の問題は変わらない')

    def test_invalid_records_are_rejected_with_a_reason(self):
        cases = {
            '中分類が存在しない': [{'category': 99, 'stem': 'x', 'choices': ['a', 'b'], 'answer': 0}],
            '正解が範囲外': [{'category': 11, 'stem': 'x', 'choices': ['a', 'b'], 'answer': 5}],
            '選択肢が1つ': [{'category': 11, 'stem': 'x', 'choices': ['a'], 'answer': 0}],
            '選択肢が重複': [{'category': 11, 'stem': 'x', 'choices': ['a', 'a'], 'answer': 0}],
            '問題文が空': [{'category': 11, 'stem': '  ', 'choices': ['a', 'b'], 'answer': 0}],
        }
        for label, records in cases.items():
            with self.subTest(label=label):
                response = self.client.post(
                    reverse('fe:manage_upload'), self._json_upload(records))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['form'].errors)
                self.assertEqual(Question.objects.all().count(), 0)

    def test_export_round_trips_without_losing_notes(self):
        """書き出して取り込み直しても、問題も解説も失われないこと。

        取り込みは解説も一括で差し替えるので、書き出しに解説が入っていないと、
        往復しただけで教材が消える。
        """
        self.client.post(reverse('fe:manage_upload'), self._upload_with_notes(4))
        before_q = Question.objects.all().count()
        before_n = LearningNote.objects.all().count()
        self.assertEqual((before_q, before_n), (4, 2))

        response = self.client.get(reverse('fe:manage_export'))
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode('utf-8'))
        self.assertEqual(len(payload['questions']), 4)
        self.assertEqual(len(payload['notes']), 2)
        self.assertNotIn('key', payload['questions'][0], '書き出しにキーは含めない')

        response = self.client.post(
            reverse('fe:manage_upload'), self._json_upload(payload))
        self.assertRedirects(response, reverse('fe:manage_list'))
        self.assertEqual(Question.objects.all().count(), before_q)
        self.assertEqual(LearningNote.objects.all().count(), before_n)

    def test_export_can_be_limited_to_one_subject(self):
        self.client.post(reverse('fe:manage_upload'), self._upload_with_notes(3))
        b = Category.objects.filter(subject='B').first()
        LearningNote.objects.create(
            category=b, topic=b.name, title='科目Bの解説', body='本文',
        )
        payload = json.loads(
            self.client.get(reverse('fe:manage_export'), {'subject': 'A'})
            .content.decode('utf-8')
        )
        self.assertTrue(payload['notes'])
        for note in payload['notes']:
            self.assertLess(note['category'], 100, '科目Aの書き出しに科目Bの解説が混ざらない')

    def test_choice_labels_cover_a_long_answer_group(self):
        """選択肢が多くても記号が割り当たること。"""
        self.client.post(reverse('fe:manage_upload'), self._json_upload([{
            'category': 103, 'subject': 'B', 'stem': 'x',
            'choices': ['選択肢{}'.format(i) for i in range(9)], 'answer': 8,
            'explanation': '解説',
        }]))
        question = Question.objects.get(template__isnull=True)
        labels = [label for _, label, _ in question.labeled_choices()]
        self.assertEqual(labels, list('アイウエオカキクケ'))
        self.assertEqual(question.answer_label, 'ケ')

    def test_list_shows_the_shared_set(self):
        self.client.post(reverse('fe:manage_upload'), self._upload(3))
        response = self.client.get(reverse('fe:manage_list'))
        self.assertEqual(response.context['summary']['total'], 3)


class ImportNotesTests(TestCase):
    """技術解説つき JSON の取り込み。"""

    @classmethod
    def setUpTestData(cls):
        seed_masters()
        cls.learner = make_learner('reader')

    def test_object_form_imports_notes_and_questions(self):
        payload = {
            'notes': build_notes(),
            'questions': [{
                'category': 11, 'stem': 'x', 'choices': ['a', 'b', 'c', 'd'], 'answer': 0,
            }],
        }
        result = sync(validate(parse(json.dumps(payload, ensure_ascii=False))))
        self.assertEqual(result['questions']['added'], 1)
        self.assertEqual(result['notes']['added'], 2)
        self.assertEqual(LearningNote.objects.all().count(), 2)

    def test_plain_array_still_works(self):
        """解説を持たない、いままでの形の JSON もそのまま取り込める。"""
        payload = json.dumps(
            [{'category': 11, 'stem': 'x', 'choices': ['a', 'b'], 'answer': 0}],
            ensure_ascii=False,
        )
        result = sync(validate(parse(payload)))
        self.assertEqual(result['questions']['added'], 1)
        self.assertIsNone(result['notes'], '解説を含まないファイルは解説に触れない')

    def test_notes_only_file_is_accepted(self):
        """解説だけのファイルも取り込める。問題には触らない。"""
        build_questions(per_category=1)
        before = Question.objects.count()
        result = sync(validate(parse(json.dumps(
            {'notes': build_notes()}, ensure_ascii=False
        ))))
        self.assertEqual(result['notes']['added'], 2)
        self.assertIsNone(result['questions'], '問題を含まないファイルは問題に触れない')
        self.assertEqual(Question.objects.count(), before)

    def test_sync_keeps_unchanged_rows_and_their_history(self):
        """変わっていない問題は作り直さない。解答履歴が切れないため。"""
        build_questions(per_category=1)
        question = Question.objects.first()
        learner = make_learner('synced')
        answer(learner, question, correct=True)

        records = [{
            'category': q.category.code, 'subject': q.subject, 'topic': q.topic,
            'stem': q.stem, 'choices': q.choices, 'answer': q.answer_index,
            'explanation': q.explanation, 'difficulty': q.difficulty,
            'source': q.source,
        } for q in Question.objects.filter(template__isnull=True)]
        result = sync(validate({'questions': records, 'notes': None}))

        self.assertEqual(result['questions']['added'], 0)
        self.assertEqual(result['questions']['removed'], 0)
        self.assertEqual(result['questions']['unchanged'], len(records))
        self.assertTrue(Attempt.objects.filter(learner=learner).exists())
        self.assertEqual(Question.objects.filter(pk=question.pk).count(), 1)

    def test_sync_updates_in_place_and_removes_missing(self):
        """解説や正解だけ直したら更新、ファイルから消えたら削除。"""
        build_questions(per_category=2)
        total = Question.objects.filter(template__isnull=True).count()
        kept = Question.objects.filter(template__isnull=True).first()
        records = [{
            'category': kept.category.code, 'subject': kept.subject,
            'stem': kept.stem, 'choices': kept.choices, 'answer': kept.answer_index,
            'explanation': '書き直した解説',
        }]
        result = sync(validate({'questions': records, 'notes': None}))
        self.assertEqual(result['questions']['updated'], 1)
        self.assertEqual(result['questions']['removed'], total - 1)
        kept.refresh_from_db()
        self.assertEqual(kept.explanation, '書き直した解説')

    def test_both_files_can_be_uploaded_at_once(self):
        """問題と解説を別々の欄で選んで、1回で取り込める。"""
        admin = make_learner('twofiles', is_admin=True)
        enter(self.client, admin)
        questions = json.dumps({'questions': [
            {'category': 11, 'stem': 'まとめて取り込む問題',
             'choices': ['a', 'b', 'c', 'd'], 'answer': 0},
        ]}, ensure_ascii=False)
        notes = json.dumps({'notes': build_notes()}, ensure_ascii=False)

        response = self.client.post(reverse('fe:manage_upload'), {
            'questions_file': SimpleUploadedFile('q.json', questions.encode('utf-8')),
            'notes_file': SimpleUploadedFile('n.json', notes.encode('utf-8')),
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Question.objects.filter(template__isnull=True).count(), 1)
        self.assertEqual(LearningNote.objects.count(), 2)

    def test_notes_file_alone_is_accepted_from_the_screen(self):
        """解説の欄だけ選んでも取り込める。問題には触らない。"""
        build_questions(per_category=1)
        before = Question.objects.count()
        admin = make_learner('notesonly', is_admin=True)
        enter(self.client, admin)
        notes = json.dumps({'notes': build_notes()}, ensure_ascii=False)

        response = self.client.post(reverse('fe:manage_upload'), {
            'notes_file': SimpleUploadedFile('n.json', notes.encode('utf-8')),
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(LearningNote.objects.count(), 2)
        self.assertEqual(Question.objects.count(), before, '問題はそのまま')

    def test_upload_needs_at_least_one_file(self):
        """どちらも選ばずに送ったら、理由を添えて止める。"""
        admin = make_learner('nofile', is_admin=True)
        enter(self.client, admin)
        response = self.client.post(reverse('fe:manage_upload'), {})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'どちらか一方は選んでください')

    def test_same_side_in_both_files_is_rejected(self):
        """両方の欄に問題を入れたら、どちらを使うか決められないので弾く。"""
        admin = make_learner('bothq', is_admin=True)
        enter(self.client, admin)
        payload = json.dumps({'questions': [
            {'category': 11, 'stem': 'x', 'choices': ['a', 'b'], 'answer': 0},
        ]}, ensure_ascii=False).encode('utf-8')
        response = self.client.post(reverse('fe:manage_upload'), {
            'questions_file': SimpleUploadedFile('a.json', payload),
            'notes_file': SimpleUploadedFile('b.json', payload),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '両方に問題が入っています')

    def test_preview_counts_without_writing(self):
        """下見は数えるだけで、1件も書き込まない。"""
        build_questions(per_category=1)
        before = Question.objects.count()
        counts = preview(validate({
            'questions': [{
                'category': 11, 'subject': 'A', 'stem': '新しい問題',
                'choices': ['a', 'b', 'c', 'd'], 'answer': 0,
            }],
            'notes': None,
        }))
        self.assertEqual(counts['questions']['added'], 1)
        self.assertEqual(Question.objects.count(), before, '下見では書き込まない')

    def test_large_removal_needs_confirmation(self):
        """削除が既存の3割を超えたら、確定の前に止める。"""
        build_questions(per_category=1)
        total = Question.objects.filter(template__isnull=True).count()
        kept = Question.objects.filter(template__isnull=True).first()
        one = [{
            'category': kept.category.code, 'subject': kept.subject, 'stem': kept.stem,
            'choices': kept.choices, 'answer': kept.answer_index,
            'source': kept.source, 'topic': kept.topic,
        }]
        counts = preview(validate({'questions': one, 'notes': None}))
        self.assertEqual(counts['questions']['removed'], total - 1)
        self.assertTrue(needs_confirmation(counts))

        # 3割以下なら止めない
        few = Question.objects.filter(template__isnull=True)[:total - 1]
        records = [{
            'category': q.category.code, 'subject': q.subject, 'stem': q.stem,
            'choices': q.choices, 'answer': q.answer_index,
            'source': q.source, 'topic': q.topic,
        } for q in few]
        self.assertFalse(needs_confirmation(
            preview(validate({'questions': records, 'notes': None}))
        ))

    def test_upload_screen_stops_before_a_large_removal(self):
        """画面でも、削除が多いファイルは一度で取り込まれない。"""
        build_questions(per_category=1)
        admin = make_learner('uploader', is_admin=True)
        enter(self.client, admin)
        before = Question.objects.count()
        kept = Question.objects.filter(template__isnull=True).first()
        payload = json.dumps({'questions': [{
            'category': kept.category.code, 'subject': kept.subject, 'stem': kept.stem,
            'choices': kept.choices, 'answer': kept.answer_index,
        }]}, ensure_ascii=False)

        response = self.client.post(reverse('fe:manage_upload'), {
            'questions_file': SimpleUploadedFile('q.json', payload.encode('utf-8')),
        })
        self.assertEqual(response.status_code, 200, '遷移せず確認画面を出す')
        self.assertContains(response, '削除が多いので、一度止めました')
        self.assertEqual(Question.objects.count(), before, 'まだ消えていない')

        # 確認したうえで実行すると、取り込まれる
        response = self.client.post(reverse('fe:manage_upload'), {
            'confirm': '1', 'payload': payload,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Question.objects.filter(template__isnull=True).count(), 1)

    def test_duplicate_keys_in_one_file_are_rejected(self):
        """同じ鍵が1つのファイルに2件あると、どちらが勝つか不定なので止める。"""
        payload = json.dumps({'questions': [
            {'category': 11, 'stem': '同じ問題', 'choices': ['a', 'b'], 'answer': 0},
            {'category': 11, 'stem': '  同じ問題  ', 'choices': ['c', 'd'], 'answer': 1},
        ]}, ensure_ascii=False)
        with self.assertRaises(ImportError_):
            validate(parse(payload))

    def test_empty_side_is_rejected(self):
        """空の配列は、キーごと外すのと区別して弾く。取り違えの事故を防ぐため。"""
        for payload in ('{"questions": []}', '{"notes": []}', '{}'):
            with self.subTest(payload=payload):
                with self.assertRaises(ImportError_):
                    parse(payload)

    def test_questions_only_file_leaves_the_notes_alone(self):
        """問題だけのファイルを入れても、解説は消えない。

        問題と解説は更新の頻度が違うので別のファイルで渡せる。入っていない
        側に触ると、問題を1問足すだけで解説が全部消えることになる。
        """
        sync(validate(parse(json.dumps({
            'notes': build_notes(),
            'questions': [{'category': 11, 'stem': 'x', 'choices': ['a', 'b'], 'answer': 0}],
        }, ensure_ascii=False))))
        self.assertEqual(LearningNote.objects.all().count(), 2)

        sync(validate(parse(json.dumps(
            [{'category': 11, 'stem': 'y', 'choices': ['a', 'b'], 'answer': 0}],
            ensure_ascii=False,
        ))))
        self.assertEqual(LearningNote.objects.all().count(), 2, '解説はそのまま残る')

    def test_a_broken_note_is_rejected(self):
        cases = {
            '本文が空': [{'category': 11, 'title': 'x', 'body': '  '}],
            '見出しが無い': [{'category': 11, 'body': 'x'}],
            '中分類が無い': [{'category': 99, 'title': 'x', 'body': 'y'}],
        }
        for label, notes in cases.items():
            with self.subTest(label=label):
                payload = json.dumps({
                    'notes': notes,
                    'questions': [{
                        'category': 11, 'stem': 'x', 'choices': ['a', 'b'], 'answer': 0,
                    }],
                }, ensure_ascii=False)
                with self.assertRaises(ImportError_):
                    validate(parse(payload))
                self.assertEqual(Question.objects.all().count(), 0)


class LearningModeTests(TestCase):
    """学習モード。解説を読んでから、その範囲を解く。"""

    @classmethod
    def setUpTestData(cls):
        seed_masters()
        cls.learner = make_learner('learner')

    def setUp(self):
        enter(self.client, self.learner)

    @staticmethod
    def _checked_notes(response):
        """学習対象のうち、最初から選ばれている行の値。"""
        html = response.content.decode('utf-8')
        return [
            m.group(1) for m in re.finditer(
                r'name="note"\s+value="([^"]*)"[^>]*\bchecked\b', html
            )
        ]

    @staticmethod
    def _add_questions(category, topic, count):
        """小分類を指定して問題を足す。build_questions は中分類名を小分類にするため。"""
        return [
            Question.objects.create(
                subject=category.subject, category=category, topic=topic,
                stem='{}の問題{}'.format(topic, i),
                choices=['ア', 'イ', 'ウ', 'エ'], answer_index=0,
            )
            for i in range(count)
        ]

    def _prepare(self, topic=None):
        build_questions(per_category=12)
        LearningNote.objects.all().delete()
        for note in build_notes(codes=(9,), topic=topic):
            LearningNote.objects.create(
                category=Category.objects.get(code=note['category']),
                topic=note['topic'], title=note['title'],
                body=note['body'], source=note['source'],
            )

    def test_a_round_needs_a_note(self):
        """解説が無ければ始められない。管理用の ID なら取り込み画面へ案内する。"""
        build_questions()
        response = self.client.post(reverse('fe:learn_start'), {'minutes': 5})
        self.assertRedirects(response, reverse('fe:learn_start'))

        enter(self.client, make_learner('curator', is_admin=True))
        response = self.client.post(reverse('fe:learn_start'), {'minutes': 5})
        self.assertRedirects(response, reverse('fe:manage_upload'))
        self.assertEqual(LearningRound.objects.count(), 0)

    def test_reading_phase_shows_the_note_not_the_questions(self):
        """読む段階では、設問も選択肢も見せない。"""
        self._prepare()
        self.client.post(reverse('fe:learn_start'), {'minutes': 5})
        round_ = LearningRound.objects.get(learner=self.learner)
        self.assertEqual(round_.reading_seconds, 300)

        response = self.client.get(reverse('fe:learn_read', args=[round_.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, round_.note.title)
        for item in round_.items.all():
            self.assertNotContains(response, item.question.stem)

    def test_questions_come_from_the_note_s_category(self):
        self._prepare()
        self.client.post(reverse('fe:learn_start'), {'minutes': 5})
        round_ = LearningRound.objects.get(learner=self.learner)
        self.assertEqual(round_.total, LearningRound.QUESTION_COUNT)
        for item in round_.items.select_related('question'):
            self.assertEqual(
                item.question.category_id, round_.note.category_id,
                '読んだ解説と別の分野が出題された',
            )

    def test_only_questions_of_the_note_s_topic_are_asked(self):
        """出題は解説と同じ小分類だけ。足りなくても中分類からは補わない。"""
        self._prepare()
        database = Category.objects.get(code=9)
        note = LearningNote.objects.get(category=database)
        # 同じ中分類で、解説とは別の小分類の問題。混ざってはいけない。
        other = self._add_questions(database, '別の小分類', 5)
        mine = Question.objects.filter(category=database, topic=note.topic)

        self.client.post(reverse('fe:learn_start'), {'minutes': 5, 'note': note.pk})
        round_ = LearningRound.objects.get(learner=self.learner)
        asked = {item.question_id for item in round_.items.all()}
        self.assertTrue(asked <= set(mine.values_list('id', flat=True)))
        self.assertFalse(asked & {q.id for q in other})

    def test_a_round_is_short_when_the_topic_has_few_questions(self):
        """対応する問題が{}問に足りなければ、その数だけ出す。""".format(
            LearningRound.QUESTION_COUNT
        )
        Question.objects.all().delete()
        LearningNote.objects.all().delete()
        law = Category.objects.get(code=23)
        self._add_questions(law, '知的財産権', 3)
        note = LearningNote.objects.create(
            category=law, topic='知的財産権', title='著作権と産業財産権', body='本文',
        )

        self.client.post(reverse('fe:learn_start'), {'minutes': 5, 'note': note.pk})
        round_ = LearningRound.objects.get(learner=self.learner)
        self.assertEqual(round_.total, 3)

    def test_origin_label_tells_ipa_questions_from_original_ones(self):
        """出典の書き出しから、IPA の出題そのものか自作かを見分ける。"""
        cases = [
            ('令和6年度 科目A 問3', 'IPA公開問題（令和6年度）'),
            ('令和3年度 春期 午前 問2', 'IPA公開問題（令和3年度 春期）'),
            ('サンプル問題 科目A 問12', 'IPAサンプル問題'),
            ('シラバス Ver.8.0 中分類1 応用数学（3）数値解析', '自作'),
            ('試験要綱 科目B', '自作'),
            ('自作（令和6年度秋 午前 問5 を基に作成）', '自作'),
            ('', ''),
        ]
        for source, label in cases:
            self.assertEqual(Question(source=source).origin_label, label)

    def test_only_ipa_questions_call_their_source_a_citation(self):
        """「出典」と呼ぶのは IPA の出題を転記した問題だけ。自作は「出題範囲」。"""
        cases = [
            ('令和6年度 科目A 問3', '出典'),
            ('サンプル問題 科目A 問12', '出典'),
            ('シラバス Ver.8.0 中分類1 応用数学（3）数値解析', '出題範囲'),
            ('自作（令和6年度秋 午前 問5 を基に作成）', '出題範囲'),
        ]
        for source, heading in cases:
            self.assertEqual(Question(source=source).source_heading, heading)

    def test_learning_quiz_shows_the_source_after_answering(self):
        """学習モードでも、答えた後に出典と出題元の区別を出す。"""
        self._prepare()
        self.client.post(reverse('fe:learn_start'), {'minutes': 5})
        round_ = LearningRound.objects.get(learner=self.learner)
        self.client.post(reverse('fe:learn_read', args=[round_.pk]))

        item = round_.items.get(order=0)
        question = item.question
        question.source = 'シラバス 中分類1 テスト用の項目'
        question.save()

        before = self.client.get(reverse('fe:learn_quiz', args=[round_.pk]))
        self.assertNotContains(before, 'テスト用の項目')

        after = self.client.post(
            reverse('fe:learn_quiz', args=[round_.pk]),
            {'choice': question.answer_index},
        )
        self.assertContains(after, '出題範囲：シラバス 中分類1 テスト用の項目')
        self.assertContains(after, '自作')

    def test_answering_records_progress(self):
        """学習モードで解いた分も、分野ごとの理解度に積む。"""
        self._prepare()
        self.client.post(reverse('fe:learn_start'), {'minutes': 5})
        round_ = LearningRound.objects.get(learner=self.learner)
        self.client.post(reverse('fe:learn_read', args=[round_.pk]))

        item = round_.items.get(order=0)
        response = self.client.post(
            reverse('fe:learn_quiz', args=[round_.pk]),
            {'choice': item.question.answer_index},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_correct'])

        item.refresh_from_db()
        self.assertTrue(item.is_correct)
        self.assertEqual(Attempt.objects.filter(learner=self.learner).count(), 1)
        self.assertEqual(
            CategoryProgress.objects.filter(learner=self.learner).aggregate(
                n=Sum('answered'))['n'], 1,
        )

    def test_a_round_runs_to_the_result_screen(self):
        self._prepare()
        self.client.post(reverse('fe:learn_start'), {'minutes': 3})
        round_ = LearningRound.objects.get(learner=self.learner)
        self.client.post(reverse('fe:learn_read', args=[round_.pk]))

        for _ in range(round_.total):
            item = round_.items.get(order=round_.position)
            self.client.post(
                reverse('fe:learn_quiz', args=[round_.pk]),
                {'choice': item.question.answer_index},
            )
            self.client.post(reverse('fe:learn_next', args=[round_.pk]))
            round_.refresh_from_db()

        self.assertEqual(round_.phase, LearningRound.PHASE_DONE)
        self.assertEqual(round_.correct, round_.total)
        response = self.client.get(reverse('fe:learn_result', args=[round_.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['missed'], [])

    def test_the_result_lists_every_question_with_its_source(self):
        """結果には、正解した問題も含めて全問を出典つきで並べる。"""
        self._prepare()
        self.client.post(reverse('fe:learn_start'), {'minutes': 3})
        round_ = LearningRound.objects.get(learner=self.learner)
        self.client.post(reverse('fe:learn_read', args=[round_.pk]))

        for order in range(round_.total):
            item = round_.items.get(order=order)
            question = item.question
            question.source = 'シラバス 中分類1 テスト用の項目{}'.format(order)
            question.save()
            # 1問目だけまちがえ、残りは正解する
            choice = question.answer_index
            if order == 0:
                choice = (choice + 1) % len(question.choices)
            self.client.post(reverse('fe:learn_quiz', args=[round_.pk]), {'choice': choice})
            self.client.post(reverse('fe:learn_next', args=[round_.pk]))

        response = self.client.get(reverse('fe:learn_result', args=[round_.pk]))
        self.assertEqual(len(response.context['items']), round_.total)
        self.assertEqual(len(response.context['missed']), 1)
        for order in range(round_.total):
            self.assertContains(
                response, '出題範囲：シラバス 中分類1 テスト用の項目{}'.format(order)
            )
        self.assertContains(response, '不正解', count=1)
        self.assertContains(response, 'あなたの解答', count=1)

    def test_the_menu_groups_notes_by_category(self):
        """学習対象は、中分類でまとめて小分類（解説）を並べる。"""
        build_questions(per_category=12)
        LearningNote.objects.all().delete()
        for code, topic in ((9, 'データベース設計'), (9, 'データベース方式'), (11, '情報セキュリティ')):
            category = Category.objects.get(code=code)
            LearningNote.objects.create(
                category=category, topic=topic,
                title='{}の解説'.format(topic), body='本文', source='テスト',
            )

        response = self.client.get(reverse('fe:learn_start'))
        groups = response.context['groups']
        self.assertEqual([g['category'].code for g in groups], [9, 11])
        self.assertEqual(len(groups[0]['notes']), 2)
        self.assertEqual(
            [n['note'].topic for n in groups[0]['notes']],
            ['データベース方式', 'データベース設計'],
        )

    def test_notes_follow_the_syllabus_order(self):
        """中分類の中はシラバスの小分類の順。取り込んだ順でも文字順でもない。

        文字順にすると「プロジェクトのコスト」が「プロジェクトマネジメント」より
        先に来てしまい、シラバスとは逆になる。
        """
        build_questions(per_category=12)
        LearningNote.objects.all().delete()
        pm = Category.objects.get(code=14)
        # わざとシラバスと違う順で取り込む
        for topic in ('プロジェクトのコスト', 'プロジェクトマネジメント', 'プロジェクトの資源'):
            LearningNote.objects.create(
                category=pm, topic=topic, title='{}の解説'.format(topic), body='本文',
            )

        groups = note_menu(self.learner, Question.SUBJECT_A)
        self.assertEqual(
            [n['note'].topic for n in groups[0]['notes']],
            ['プロジェクトマネジメント', 'プロジェクトの資源', 'プロジェクトのコスト'],
        )

    def test_a_topic_outside_the_syllabus_goes_last(self):
        """シラバスに無い小分類は末尾に回す。並びの正はシラバスに置く。"""
        build_questions(per_category=12)
        LearningNote.objects.all().delete()
        law = Category.objects.get(code=23)
        for topic in ('独自の小分類', '標準化関連', '知的財産権'):
            LearningNote.objects.create(
                category=law, topic=topic, title='{}の解説'.format(topic), body='本文',
            )

        groups = note_menu(self.learner, Question.SUBJECT_A)
        self.assertEqual(
            [n['note'].topic for n in groups[0]['notes']],
            ['知的財産権', '標準化関連', '独自の小分類'],
        )

    def test_choosing_a_note_uses_that_note(self):
        """選んだ解説がそのまま読む対象になる。"""
        self._prepare()
        database = Category.objects.get(code=9)
        chosen = LearningNote.objects.create(
            category=database, topic='正規化',
            title='正規化の解説', body='本文', source='テスト',
        )
        self._add_questions(database, '正規化', 4)
        self.client.post(
            reverse('fe:learn_start'), {'minutes': 5, 'note': chosen.pk}
        )
        round_ = LearningRound.objects.get(learner=self.learner)
        self.assertEqual(round_.note_id, chosen.pk)
        for item in round_.items.select_related('question'):
            self.assertEqual(item.question.category_id, database.pk)

    def test_an_unknown_note_is_not_accepted(self):
        self._prepare()
        response = self.client.post(
            reverse('fe:learn_start'), {'minutes': 5, 'note': 999999}
        )
        self.assertRedirects(response, reverse('fe:learn_start'))
        self.assertEqual(LearningRound.objects.count(), 0)

    def test_a_note_without_questions_is_reported(self):
        """解説はあるが、その分野の問題が無いとき。"""
        LearningNote.objects.all().delete()
        note = LearningNote.objects.create(
            category=Category.objects.get(code=23),
            topic='法務', title='法務の解説', body='本文',
        )
        response = self.client.post(
            reverse('fe:learn_start'), {'minutes': 5, 'note': note.pk}
        )
        self.assertRedirects(response, reverse('fe:learn_start'))
        self.assertEqual(LearningRound.objects.count(), 0)

    def test_a_note_without_questions_is_never_picked_automatically(self):
        """おまかせは、出題できる問題を持つ解説だけから選ぶ。

        持たない解説を選ぶと、読ませたあとに出題できず、始められないため。
        """
        self._prepare()
        empty = LearningNote.objects.create(
            category=Category.objects.get(code=23),
            topic='出題のない小分類', title='法務の解説', body='本文',
        )
        for _ in range(20):
            self.assertNotEqual(pick_note(self.learner, Question.SUBJECT_A), empty)

    def test_the_menu_tells_how_many_questions_each_note_has(self):
        """解説ごとに出題できる問数を添える。0問の解説は選べないと分かるように。"""
        self._prepare()
        database = Category.objects.get(code=9)
        LearningNote.objects.create(
            category=database, topic='出題のない小分類',
            title='まだ問題のない解説', body='本文',
        )
        groups = {
            g['category'].code: g for g in note_menu(self.learner, Question.SUBJECT_A)
        }
        counts = {
            e['note'].title: e['questions'] for e in groups[9]['notes']
        }
        self.assertEqual(counts['まだ問題のない解説'], 0)
        self.assertEqual(counts['データベースのしくみ'], 12)

    def test_notes_are_offered_per_subject(self):
        """解説も科目で絞る。科目Aの回で科目Bの解説が出てはいけない。"""
        self._prepare()
        b = Category.objects.filter(subject='B').first()
        LearningNote.objects.create(
            category=b, topic=b.name,
            title='科目Bの解説', body='本文', source='テスト',
        )

        a_menu = note_menu(self.learner, Question.SUBJECT_A)
        b_menu = note_menu(self.learner, Question.SUBJECT_B)
        self.assertNotIn(b.id, [g['category'].id for g in a_menu])
        self.assertEqual([g['category'].id for g in b_menu], [b.id])

        for _ in range(20):
            note = pick_note(self.learner, Question.SUBJECT_A)
            self.assertEqual(note.category.subject, Question.SUBJECT_A)

    def test_a_note_from_the_other_subject_is_refused(self):
        self._prepare()
        b = Category.objects.filter(subject='B').first()
        theirs = LearningNote.objects.create(
            category=b, topic=b.name,
            title='科目Bの解説', body='本文',
        )
        response = self.client.post(
            reverse('fe:learn_start'), {'minutes': 5, 'note': theirs.pk}
        )
        self.assertRedirects(response, reverse('fe:learn_start'))
        self.assertEqual(LearningRound.objects.count(), 0)

    def _set_category(self, category):
        session, _ = StudySession.objects.get_or_create(learner=self.learner)
        session.mode = StudySession.MODE_CATEGORY
        session.category = category
        session.save()
        return session

    def test_the_dashboard_category_is_opened(self):
        """ダッシュボードで分野を指定していれば、その中分類を開いて見せる。

        開くだけで、解説はどれも選ばない。選ぶのは利用者。
        """
        self._prepare()
        self._set_category(Category.objects.get(code=9))

        response = self.client.get(reverse('fe:learn_start'))
        self.assertEqual(response.context['focus'].code, 9)
        self.assertEqual(self._checked_notes(response), [])

    def test_the_auto_pick_stays_inside_the_dashboard_category(self):
        """分野を指定していれば、おまかせもその分野の中から選ぶ。"""
        self._prepare()
        database = Category.objects.get(code=9)
        for code in (11, 23):
            other = Category.objects.get(code=code)
            LearningNote.objects.create(
                category=other, topic=other.name,
                title='{}の解説'.format(other.name), body='本文',
            )
        self._set_category(database)

        for _ in range(10):
            self.client.post(reverse('fe:learn_start'), {'minutes': 5})
            round_ = LearningRound.objects.get(learner=self.learner)
            self.assertEqual(round_.note.category_id, database.pk)

    def test_the_auto_pick_covers_every_field_without_a_category(self):
        """分野を指定していなければ、おまかせは全分野から選ぶ。"""
        self._prepare()
        for code in (11, 23):
            other = Category.objects.get(code=code)
            LearningNote.objects.create(
                category=other, topic=other.name,
                title='{}の解説'.format(other.name), body='本文',
            )
        seen = set()
        for _ in range(30):
            self.client.post(reverse('fe:learn_start'), {'minutes': 5})
            seen.add(LearningRound.objects.get(learner=self.learner).note.category.code)
        self.assertGreater(len(seen), 1)

    def test_a_category_without_notes_is_reported(self):
        """指定した分野に出題できる解説が無ければ、始めずに知らせる。"""
        self._prepare()
        self._set_category(Category.objects.get(code=23))

        response = self.client.post(reverse('fe:learn_start'), {'minutes': 5})
        self.assertRedirects(response, reverse('fe:learn_start'))
        self.assertEqual(LearningRound.objects.count(), 0)

    def test_nothing_is_opened_without_a_category(self):
        """分野を指定していなければ、どの中分類も開かず、何も選ばない。"""
        self._prepare()
        response = self.client.get(reverse('fe:learn_start'))
        self.assertIsNone(response.context['focus'])
        self.assertEqual(self._checked_notes(response), [])

    def test_a_category_of_the_other_subject_is_not_used(self):
        """科目を切り替えたあとの指定は引き継がない。その科目には無い分類のため。"""
        self._prepare()
        self._set_category(Category.objects.filter(subject='B').first())
        response = self.client.get(reverse('fe:learn_start'))
        self.assertIsNone(response.context['focus'])

        # おまかせの範囲も絞らない。絞ると科目Aでは1本も選べなくなる。
        self.client.post(reverse('fe:learn_start'), {'minutes': 5})
        round_ = LearningRound.objects.get(learner=self.learner)
        self.assertEqual(round_.note.category.subject, Question.SUBJECT_A)

    def test_a_round_can_be_abandoned_from_the_quiz(self):
        """解答の途中でやめて学習モードトップへ戻れる。ラウンドは残さない。"""
        self._prepare()
        self.client.post(reverse('fe:learn_start'), {'minutes': 5})
        round_ = LearningRound.objects.get(learner=self.learner)
        self.client.post(reverse('fe:learn_read', args=[round_.pk]))

        # 1問だけ解いてからやめる
        item = round_.items.get(order=0)
        self.client.post(
            reverse('fe:learn_quiz', args=[round_.pk]),
            {'choice': item.question.answer_index},
        )
        response = self.client.post(reverse('fe:learn_abandon', args=[round_.pk]))
        self.assertRedirects(response, reverse('fe:learn_start'))
        self.assertEqual(LearningRound.objects.count(), 0)

        # 解いたぶんの記録は残る。ラウンドとは別に積んでいるため。
        self.assertEqual(Attempt.objects.filter(learner=self.learner).count(), 1)
        self.assertEqual(
            CategoryProgress.objects.filter(learner=self.learner).aggregate(
                n=Sum('answered'))['n'], 1,
        )

    def test_a_finished_round_is_not_deleted(self):
        """終わったラウンドは結果を見返すためのものなので、中断では消さない。"""
        self._prepare()
        self.client.post(reverse('fe:learn_start'), {'minutes': 5})
        round_ = LearningRound.objects.get(learner=self.learner)
        round_.phase = LearningRound.PHASE_DONE
        round_.save(update_fields=['phase'])

        response = self.client.post(reverse('fe:learn_abandon', args=[round_.pk]))
        self.assertRedirects(response, reverse('fe:learn_result', args=[round_.pk]))
        self.assertEqual(LearningRound.objects.count(), 1)

    def test_starting_a_new_round_clears_the_left_over_one(self):
        """画面を離れて置き去りになったラウンドは、次を始めるときに片づける。"""
        self._prepare()
        self.client.post(reverse('fe:learn_start'), {'minutes': 5})
        left_over = LearningRound.objects.get(learner=self.learner)

        self.client.post(reverse('fe:learn_start'), {'minutes': 5})
        rounds = LearningRound.objects.filter(learner=self.learner)
        self.assertEqual(rounds.count(), 1)
        self.assertNotEqual(rounds.first().pk, left_over.pk)

    def test_a_finished_round_survives_the_next_one(self):
        """終わったラウンドは、次を始めても残る。結果を見返せなくなるため。"""
        self._prepare()
        self.client.post(reverse('fe:learn_start'), {'minutes': 5})
        done = LearningRound.objects.get(learner=self.learner)
        done.phase = LearningRound.PHASE_DONE
        done.save(update_fields=['phase'])

        self.client.post(reverse('fe:learn_start'), {'minutes': 5})
        self.assertEqual(LearningRound.objects.filter(learner=self.learner).count(), 2)
        self.assertTrue(LearningRound.objects.filter(pk=done.pk).exists())

    def test_another_account_cannot_abandon_the_round(self):
        self._prepare()
        self.client.post(reverse('fe:learn_start'), {'minutes': 5})
        round_ = LearningRound.objects.get(learner=self.learner)

        enter(self.client, make_learner('stranger3'))
        response = self.client.post(reverse('fe:learn_abandon', args=[round_.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(LearningRound.objects.filter(pk=round_.pk).exists())

    def test_another_account_cannot_open_the_round(self):
        self._prepare()
        self.client.post(reverse('fe:learn_start'), {'minutes': 5})
        round_ = LearningRound.objects.get(learner=self.learner)

        enter(self.client, make_learner('stranger2'))
        for name in ('fe:learn_read', 'fe:learn_quiz', 'fe:learn_result'):
            self.assertEqual(
                self.client.get(reverse(name, args=[round_.pk])).status_code, 404,
            )


class SiteSummaryTests(TestCase):
    """ダッシュボードの「みんなの集計」。全利用者の合計と、合格の申告。"""

    @classmethod
    def setUpTestData(cls):
        seed_masters()
        cls.alice = make_learner('alice')
        cls.bob = make_learner('bob')
        make_learner('idle')  # 1問も解いていない人は「学習した人」に数えない
        build_questions()
        build_questions()

    def setUp(self):
        enter(self.client, self.alice)

    def _question(self, learner, code):
        return Question.objects.filter(category__code=code).first()

    def test_counts_every_users_answers_by_category(self):
        answer(self.alice, self._question(self.alice, 11), True)
        answer(self.alice, self._question(self.alice, 11), False)
        answer(self.bob, self._question(self.bob, 11), True)
        answer(self.bob, self._question(self.bob, 101), False)

        summary = site_summary()
        self.assertEqual(summary['total'], 4)
        self.assertEqual(summary['correct'], 2)
        self.assertEqual(summary['learners'], 2)

        subject_a, subject_b = summary['subjects']
        security = next(r for r in subject_a['rows'] if r['category'].code == 11)
        self.assertEqual((security['total'], security['correct']), (3, 2))
        self.assertEqual(security['share_percent'], 100.0)
        self.assertEqual(subject_b['total'], 1)
        # 分野の内訳は科目Aだけに付く
        self.assertTrue(subject_a['fields'])
        self.assertEqual(subject_b['fields'], [])

    def test_total_survives_reimport(self):
        """のべ解答数は、問題を取り込み直しても減らない。

        取り込みは同期なので、変わっていない問題はそのまま残り、解答履歴も
        切れない。仮に問題が入れ替わっても、のべ数は消えない CategoryProgress
        から数えているので減らない。
        """
        answer(self.alice, self._question(self.alice, 11), True)
        build_questions()
        self.assertTrue(
            Attempt.objects.filter(learner=self.alice).exists(),
            '同じ内容を取り込み直しただけなら、解答履歴は残る',
        )
        self.assertEqual(site_summary()['total'], 1)

    def test_dashboard_shows_summary(self):
        answer(self.bob, self._question(self.bob, 11), True)
        response = self.client.get(dashboard(self.alice))
        self.assertContains(response, 'みんなの集計')
        self.assertEqual(response.context['summary']['total'], 1)
        # 他人の名前は出さない
        self.assertNotContains(response, 'bob')

    def test_report_and_withdraw_pass(self):
        url = reverse('fe:pass_report')
        response = self.client.post(url, {'passed_on': '2026-06-01'})
        self.assertRedirects(response, dashboard(self.alice) + '#summary')
        self.assertEqual(site_summary()['passers'], 1)

        # 申告し直しても1人は1件のまま
        self.client.post(url, {'passed_on': '2026-07-01'})
        self.assertEqual(PassReport.objects.count(), 1)
        self.assertEqual(str(PassReport.objects.get().passed_on), '2026-07-01')

        self.client.post(url, {'action': 'withdraw'})
        self.assertEqual(site_summary()['passers'], 0)

    def test_future_date_is_rejected(self):
        self.client.post(reverse('fe:pass_report'), {'passed_on': '2999-01-01'})
        self.assertFalse(PassReport.objects.exists())

    def test_pass_report_requires_login(self):
        self.client.logout()
        self.client.post(reverse('fe:pass_report'), {'passed_on': '2026-06-01'})
        self.assertFalse(PassReport.objects.exists())


class SeedCommandTests(TestCase):
    def test_seed_loads_masters_but_no_questions(self):
        """アプリは問題を同梱しないので、コマンドでも問題は入らない。"""
        seed_masters()
        self.assertEqual(Category.objects.filter(subject='A').count(), 23)
        self.assertEqual(Category.objects.filter(subject='B').count(), 5)
        self.assertEqual(QuestionTemplate.objects.count(), len(REGISTRY))
        self.assertEqual(Question.objects.count(), 0)


class ExamDefinitionTests(TestCase):
    """試験ごとにアプリを作る前提なので、対象試験と必要資料は1か所で定義する。"""

    @classmethod
    def setUpTestData(cls):
        seed_masters()

    def test_required_materials_are_declared(self):
        self.assertTrue(EXAM['name'])
        self.assertTrue(EXAM['url'])
        self.assertTrue(EXAM['materials'], '必要な資料が1つも書かれていない')
        for material in EXAM['materials']:
            with self.subTest(name=material.get('name')):
                self.assertTrue(material['name'])
                self.assertTrue(material['purpose'], '何に使う資料かを書く')

    def test_prompt_lists_the_required_materials(self):
        """資料を見ずに推測で作らせないよう、プロンプトに資料名を並べる。"""
        prompt = build_prompt()
        for material in EXAM['materials']:
            self.assertIn(material['name'], prompt)
        self.assertIn('推測で作らないでください', prompt)

    def test_prompt_lists_every_category(self):
        """分野を増やしてもプロンプトが追随すること。"""
        prompt = build_prompt()
        for category in Category.objects.all():
            with self.subTest(category=category.name):
                self.assertIn(category.name, prompt)

    def test_weights_add_up_to_the_real_exam(self):
        """想定出題数の合計は、本番の出題数と一致させる。

        1分野でも足し忘れると、重み付き抽選が本番の比率からずれる。
        """
        for subject, expected in ((Question.SUBJECT_A, 60), (Question.SUBJECT_B, 20)):
            with self.subTest(subject=subject):
                total = sum(
                    Category.objects.filter(subject=subject)
                    .values_list('exam_weight', flat=True)
                )
                self.assertEqual(total, expected)

    def test_every_category_can_be_drawn(self):
        """どの中分類にも最低1問の重みを置く。0 だと永久に出題されない。"""
        for category in Category.objects.all():
            with self.subTest(category=category.name):
                self.assertGreaterEqual(category.exam_weight, 1)

    def test_weights_follow_the_published_exams_not_syllabus_volume(self):
        """配分はシラバスの分量ではなく、公開問題の実測に沿わせる。

        小分類の数で按分すると実態と逆向きになる（セキュリティが薄く、
        プロジェクトマネジメントが厚くなる）。取り違えを防ぐために固定する。
        """
        weight = dict(Category.objects.values_list('code', 'exam_weight'))
        # セキュリティは本番で最も多く出る中分類
        self.assertEqual(weight[11], max(
            w for c, w in weight.items() if c < 100
        ))
        # プロジェクトマネジメントは小分類が11と最多だが、出題は少ない
        self.assertLess(weight[14], weight[11])

    def test_upload_screen_shows_the_materials(self):
        learner = make_learner('materials', is_admin=True)
        enter(self.client, learner)
        response = self.client.get(reverse('fe:manage_upload'))
        self.assertEqual(response.status_code, 200)
        for material in EXAM['materials']:
            self.assertContains(response, material['name'])
        self.assertContains(response, EXAM['url'])

    def test_materials_are_only_for_an_admin_id(self):
        """問題を作る資料の案内は、問題集を管理する管理用の ID にだけ出す。"""
        learner = make_learner('materials_plain')
        enter(self.client, learner)
        response = self.client.get(dashboard(learner))
        self.assertNotContains(response, '問題を作るのに必要な資料')
        self.assertContains(response, '問題集はまだ用意されていません')

    def test_dashboard_shows_the_materials(self):
        learner = make_learner('materials_dash', is_admin=True)
        enter(self.client, learner)
        response = self.client.get(dashboard(learner))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '問題を作るのに必要な資料')
        for material in EXAM['materials']:
            self.assertContains(response, material['name'])
