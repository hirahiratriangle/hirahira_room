"""FE 対策アプリのテスト。

重点出題が実際に苦手分野へ寄るかどうかは，このアプリの肝なので統計的に確かめる。
"""

import random

from accounts.models import CustomUser
from django.test import TestCase
from django.urls import reverse

from .generators import REGISTRY, generate_question, sync_templates
from .models import Attempt, Category, Question, QuestionTemplate, StudySession
from .selection import pick_question
from .stats import category_stats, smoothed_rate, weak_categories


def seed():
    from django.core.management import call_command
    call_command('seed_fe', verbosity=0)


class QuestionBankTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed()

    def test_categories_cover_the_whole_syllabus(self):
        self.assertEqual(Category.objects.count(), 23)
        self.assertEqual(
            sum(Category.objects.values_list('exam_weight', flat=True)), 60,
            '科目Aの想定出題数の合計は60問でなければならない',
        )

    def test_every_question_has_a_valid_answer(self):
        for question in Question.objects.all():
            with self.subTest(key=question.key):
                self.assertGreaterEqual(len(question.choices), 2)
                self.assertEqual(len(set(question.choices)), len(question.choices))
                self.assertTrue(0 <= question.answer_index < len(question.choices))
                self.assertTrue(question.explanation, '解説のない問題があってはならない')

    def test_every_category_has_questions(self):
        """出題できない中分類があると，重点出題がその分野を選び続けて空回りする。"""
        for category in Category.objects.all():
            with self.subTest(category=category.name):
                has_question = Question.objects.filter(category=category).exists()
                has_template = QuestionTemplate.objects.filter(category=category).exists()
                self.assertTrue(has_question or has_template)

    def test_subject_b_covers_the_published_breakdown(self):
        """科目Bはアルゴリズム16問・情報セキュリティ4問という要綱の内訳に対応する。"""
        codes = set(
            Question.objects.filter(subject=Question.SUBJECT_B)
            .values_list('category__code', flat=True)
        )
        self.assertEqual(codes, {2, 11})


class GeneratorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed()

    def test_all_templates_generate_valid_questions(self):
        for template in QuestionTemplate.objects.all():
            with self.subTest(template=template.key):
                rng = random.Random(template.key)
                for _ in range(30):
                    question = generate_question(template, rng=rng)
                    self.assertIsNotNone(question)
                    self.assertEqual(len(question.choices), 4)
                    self.assertEqual(len(set(question.choices)), 4)
                    self.assertIn(
                        question.choices[question.answer_index], question.choices
                    )
                    self.assertEqual(question.category_id, template.category_id)

    def test_same_parameters_reuse_the_same_question(self):
        template = QuestionTemplate.objects.get(key='calc-availability-mtbf')
        first = generate_question(template, rng=random.Random(1))
        second = generate_question(template, rng=random.Random(1))
        self.assertEqual(first.id, second.id, '同じ数値なら同じ問題を使い回す')

    def test_registry_and_templates_match(self):
        self.assertEqual(
            set(REGISTRY), set(QuestionTemplate.objects.values_list('key', flat=True))
        )


class StatsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed()
        cls.user = CustomUser.objects.create_user(
            username='tester', email='tester@example.com', password='pass12345'
        )

    def test_smoothing_pulls_small_samples_toward_the_prior(self):
        # 1問だけ落とした分野が「正答率0％」として扱われないこと
        self.assertAlmostEqual(smoothed_rate(0, 1), 3.0 / 6.0)
        self.assertGreater(smoothed_rate(0, 1), 0.4)
        # 十分な数を解けば実測値に近づく
        self.assertLess(smoothed_rate(0, 100), 0.05)

    def test_weak_categories_need_a_minimum_number_of_attempts(self):
        security = Category.objects.get(code=11)
        question = Question.objects.filter(category=security).first()
        Attempt.objects.create(
            user=self.user, question=question, category=security,
            selected_index=(question.answer_index + 1) % len(question.choices),
            is_correct=False,
        )
        rows = category_stats(self.user)
        self.assertEqual(weak_categories(rows), [], '1問だけでは苦手判定しない')

    def test_category_stats_counts_correctly(self):
        security = Category.objects.get(code=11)
        questions = list(Question.objects.filter(category=security)[:4])
        for index, question in enumerate(questions):
            Attempt.objects.create(
                user=self.user, question=question, category=security,
                selected_index=question.answer_index if index == 0 else -1 % 4,
                is_correct=index == 0,
            )
        row = next(r for r in category_stats(self.user) if r['category'] == security)
        self.assertEqual(row['total'], 4)
        self.assertEqual(row['correct'], 1)
        self.assertEqual(row['rate_percent'], 25)
        self.assertTrue(row['is_weak'])


class SelectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed()
        cls.user = CustomUser.objects.create_user(
            username='picker', email='picker@example.com', password='pass12345'
        )

    def _answer(self, category, correct, count):
        questions = list(Question.objects.filter(category=category)[:count])
        self.assertGreaterEqual(len(questions), 1)
        for index in range(count):
            question = questions[index % len(questions)]
            Attempt.objects.create(
                user=self.user, question=question, category=category,
                selected_index=question.answer_index if correct else 0,
                is_correct=correct,
            )

    def test_focus_mode_favours_weak_categories(self):
        """同じ想定出題数の2分野で，苦手な側が明確に多く出ること。"""
        database = Category.objects.get(code=9)      # データベース（想定4問）
        network = Category.objects.get(code=10)      # ネットワーク（想定5問）
        self._answer(database, correct=False, count=10)
        self._answer(network, correct=True, count=10)

        session = StudySession.objects.create(
            user=self.user, mode=StudySession.MODE_FOCUS, subject=Question.SUBJECT_A
        )
        random.seed(20260906)
        counts = {}
        for _ in range(600):
            question, _reason = pick_question(self.user, session)
            counts[question.category.code] = counts.get(question.category.code, 0) + 1

        self.assertGreater(
            counts.get(9, 0), counts.get(10, 0),
            '想定出題数が少なくても，苦手なデータベースの方が多く出るべき',
        )

    def test_random_mode_follows_the_exam_ratio(self):
        """本番比率モードでは，セキュリティ（8問）が法務（1問）より明確に多い。"""
        session = StudySession.objects.create(
            user=self.user, mode=StudySession.MODE_RANDOM, subject=Question.SUBJECT_A
        )
        random.seed(11)
        counts = {}
        for _ in range(800):
            question, _reason = pick_question(self.user, session)
            counts[question.category.code] = counts.get(question.category.code, 0) + 1
        self.assertGreater(counts.get(11, 0), counts.get(23, 0) * 2)

    def test_category_mode_stays_in_the_selected_category(self):
        database = Category.objects.get(code=9)
        session = StudySession.objects.create(
            user=self.user, mode=StudySession.MODE_CATEGORY,
            subject=Question.SUBJECT_A, category=database,
        )
        for _ in range(30):
            question, _reason = pick_question(self.user, session)
            self.assertEqual(question.category_id, database.id)

    def test_subject_b_mode_only_returns_subject_b_questions(self):
        session = StudySession.objects.create(
            user=self.user, mode=StudySession.MODE_FOCUS, subject=Question.SUBJECT_B
        )
        for _ in range(30):
            question, _reason = pick_question(self.user, session)
            self.assertEqual(question.subject, Question.SUBJECT_B)
            self.assertIn(question.category.code, (2, 11))

    def test_review_mode_returns_previously_wrong_questions(self):
        database = Category.objects.get(code=9)
        questions = list(Question.objects.filter(category=database)[:3])
        for question in questions:
            Attempt.objects.create(
                user=self.user, question=question, category=database,
                selected_index=0, is_correct=False,
            )
        session = StudySession.objects.create(
            user=self.user, mode=StudySession.MODE_REVIEW, subject=Question.SUBJECT_A
        )
        wrong_ids = {q.id for q in questions}
        for _ in range(20):
            question, _reason = pick_question(self.user, session)
            self.assertIn(question.id, wrong_ids)

    def test_recently_shown_questions_are_avoided(self):
        session = StudySession.objects.create(
            user=self.user, mode=StudySession.MODE_FOCUS, subject=Question.SUBJECT_A
        )
        first, _ = pick_question(self.user, session)
        for _ in range(20):
            question, _reason = pick_question(self.user, session, exclude_ids=[first.id])
            self.assertNotEqual(question.id, first.id)


class ViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed()
        cls.user = CustomUser.objects.create_user(
            username='viewer', email='viewer@example.com', password='pass12345'
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_index_renders(self):
        response = self.client.get(reverse('fe:index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '基本情報技術者試験')

    def test_quiz_shows_a_question_and_grades_the_answer(self):
        response = self.client.get(reverse('fe:quiz'))
        self.assertEqual(response.status_code, 200)
        question = response.context['question']

        response = self.client.post(reverse('fe:quiz'), {
            'question_id': question.id,
            'choice': question.answer_index,
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_correct'])
        self.assertContains(response, '正解')

        attempt = Attempt.objects.get(user=self.user, question=question)
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
        self.assertFalse(Attempt.objects.get(user=self.user).is_correct)

    def test_double_submission_is_not_counted_twice(self):
        response = self.client.get(reverse('fe:quiz'))
        question = response.context['question']
        payload = {'question_id': question.id, 'choice': question.answer_index}

        self.client.post(reverse('fe:quiz'), payload)
        self.client.post(reverse('fe:quiz'), payload)  # ブラウザの再送信を模す
        self.assertEqual(Attempt.objects.filter(user=self.user).count(), 1)

    def test_settings_change_the_study_session(self):
        response = self.client.post(reverse('fe:quiz_settings'), {
            'mode': StudySession.MODE_CATEGORY, 'subject': 'A', 'category': 11,
        })
        self.assertRedirects(response, reverse('fe:quiz'))
        session = StudySession.objects.get(user=self.user)
        self.assertEqual(session.mode, StudySession.MODE_CATEGORY)
        self.assertEqual(session.category.code, 11)

    def test_stats_and_history_render(self):
        for url in (reverse('fe:stats'), reverse('fe:history')):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_login_is_required(self):
        self.client.logout()
        response = self.client.get(reverse('fe:quiz'))
        self.assertEqual(response.status_code, 302)
