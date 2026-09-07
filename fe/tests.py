"""FE 対策アプリのテスト。

このアプリで壊れると痛いのは次の3点なので、そこを重点的に確かめる。
  ・重点出題が本当に苦手分野へ寄るか（統計的に検証する）
  ・問題がアカウントごとに分かれ、他人の問題が混ざらないか
  ・一括差し替えをしても解答履歴と分野別の正答率が残るか
"""

import json
import random

from accounts.models import CustomUser
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .generators import REGISTRY, generate_question
from .importer import bundled_records, replace_all, validate
from .models import (Attempt, Category, CategoryProgress, DailyProgress,
                     Question, QuestionTemplate, StudySession)
from .selection import pick_question
from .stats import (WEAK_MIN_ATTEMPTS, category_stats, overall_stats,
                    record_progress, smoothed_rate, weak_categories)


def seed_masters():
    """中分類と問題テンプレートだけを投入する（問題は入らない）。"""
    call_command('seed_fe', verbosity=0)


def make_user(username):
    return CustomUser.objects.create_user(
        username=username, email='{}@example.com'.format(username), password='pass12345'
    )


def give_bundled_questions(user):
    """同梱の問題バンクをそのアカウントに取り込む。"""
    return replace_all(user, validate(bundled_records()))


def answer(user, question, correct):
    """1問解いた状態を作る（解答ログと理解度の両方を更新する）。"""
    attempt = Attempt.objects.create(
        user=user, question=question, category=question.category,
        selected_index=question.answer_index if correct else 0, is_correct=correct,
    )
    record_progress(user, question, correct)
    return attempt


class QuestionBankTests(TestCase):
    """同梱している問題バンクそのものの検査。"""

    @classmethod
    def setUpTestData(cls):
        seed_masters()
        cls.user = make_user('bank')
        give_bundled_questions(cls.user)

    def test_categories_cover_the_whole_syllabus(self):
        self.assertEqual(Category.objects.count(), 23)
        self.assertEqual(
            sum(Category.objects.values_list('exam_weight', flat=True)), 60,
            '科目Aの想定出題数の合計は60問でなければならない',
        )

    def test_bundled_bank_passes_validation(self):
        """同梱の JSON が、画面からの取り込みと同じ検証を通ること。"""
        self.assertTrue(validate(bundled_records()))

    def test_every_question_has_a_valid_answer(self):
        for question in Question.objects.filter(owner=self.user):
            with self.subTest(stem=question.stem[:30]):
                self.assertGreaterEqual(len(question.choices), 2)
                self.assertEqual(len(set(question.choices)), len(question.choices))
                self.assertTrue(0 <= question.answer_index < len(question.choices))
                self.assertTrue(question.explanation, '解説のない問題があってはならない')

    def test_every_category_has_questions(self):
        """出題できない中分類があると、重点出題がその分野で空回りする。"""
        for category in Category.objects.all():
            with self.subTest(category=category.name):
                has_question = Question.objects.filter(
                    owner=self.user, category=category
                ).exists()
                has_template = QuestionTemplate.objects.filter(category=category).exists()
                self.assertTrue(has_question or has_template)

    def test_subject_b_covers_the_published_breakdown(self):
        codes = set(
            Question.objects.filter(owner=self.user, subject=Question.SUBJECT_B)
            .values_list('category__code', flat=True)
        )
        self.assertEqual(codes, {2, 11})

    def test_choice_counts_match_the_real_exam(self):
        """科目Aは四肢択一。科目Bは要綱に択一の指定がなく、実際は6〜10択が普通。"""
        for q in Question.objects.filter(owner=self.user, subject=Question.SUBJECT_A):
            with self.subTest(stem=q.stem[:30]):
                self.assertEqual(len(q.choices), 4, '科目Aは常に四肢択一')
        for q in Question.objects.filter(owner=self.user, subject=Question.SUBJECT_B):
            with self.subTest(stem=q.stem[:30]):
                self.assertGreaterEqual(
                    len(q.choices), 5,
                    '科目Bを4択にすると本番より易しくなる（消去法が効きすぎる）',
                )
                self.assertLessEqual(len(q.choices), 10)

    def test_choice_labels_cover_the_longest_answer_group(self):
        longest = max(Question.objects.filter(owner=self.user), key=lambda q: len(q.choices))
        labels = [label for _, label, _ in longest.labeled_choices()]
        self.assertEqual(len(labels), len(longest.choices))
        self.assertNotIn('?', labels)
        self.assertEqual(labels[:5], list('アイウエオ'))


class GeneratorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_masters()
        cls.user = make_user('gen')

    def test_all_templates_generate_valid_questions(self):
        for template in QuestionTemplate.objects.all():
            with self.subTest(template=template.key):
                rng = random.Random(template.key)
                for _ in range(30):
                    question = generate_question(template, self.user, rng=rng)
                    self.assertIsNotNone(question)
                    self.assertEqual(len(question.choices), 4)
                    self.assertEqual(len(set(question.choices)), 4)
                    self.assertEqual(question.owner, self.user)
                    self.assertEqual(question.category_id, template.category_id)

    def test_same_parameters_reuse_the_same_question(self):
        template = QuestionTemplate.objects.get(key='calc-availability-mtbf')
        first = generate_question(template, self.user, rng=random.Random(1))
        second = generate_question(template, self.user, rng=random.Random(1))
        self.assertEqual(first.id, second.id, '同じ数値なら同じ問題を使い回す')

    def test_generated_questions_belong_to_each_account(self):
        other = make_user('gen_other')
        template = QuestionTemplate.objects.get(key='calc-availability-mtbf')
        mine = generate_question(template, self.user, rng=random.Random(2))
        theirs = generate_question(template, other, rng=random.Random(2))
        self.assertNotEqual(mine.id, theirs.id, '同じ数値でも人ごとに別の行になる')
        self.assertEqual(mine.params, theirs.params, '中身は同じ')

    def test_registry_and_templates_match(self):
        self.assertEqual(
            set(REGISTRY), set(QuestionTemplate.objects.values_list('key', flat=True))
        )


class StatsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_masters()
        cls.user = make_user('stats')
        give_bundled_questions(cls.user)

    def test_smoothing_pulls_small_samples_toward_the_prior(self):
        self.assertAlmostEqual(smoothed_rate(0, 1), 3.0 / 6.0)
        self.assertGreater(smoothed_rate(0, 1), 0.4)
        self.assertLess(smoothed_rate(0, 100), 0.05)

    def test_weak_categories_need_a_minimum_number_of_attempts(self):
        security = Category.objects.get(code=11)
        question = Question.objects.filter(owner=self.user, category=security).first()
        answer(self.user, question, correct=False)
        rows = category_stats(self.user)
        self.assertEqual(weak_categories(rows), [], '1問だけでは苦手判定しない')

    def test_category_stats_counts_correctly(self):
        security = Category.objects.get(code=11)
        questions = list(Question.objects.filter(owner=self.user, category=security)[:4])
        for index, question in enumerate(questions):
            answer(self.user, question, correct=index == 0)
        row = next(r for r in category_stats(self.user) if r['category'] == security)
        self.assertEqual(row['total'], 4)
        self.assertEqual(row['correct'], 1)
        self.assertEqual(row['rate_percent'], 25)
        self.assertTrue(row['is_weak'])

    def test_stats_count_only_the_users_own_questions(self):
        other = make_user('stats_other')
        rows_before = {r['category'].code: r['question_count'] for r in category_stats(self.user)}
        give_bundled_questions(other)
        rows_after = {r['category'].code: r['question_count'] for r in category_stats(self.user)}
        self.assertEqual(rows_before, rows_after, '他人の問題が出題可能数に混ざってはいけない')


class SelectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_masters()
        cls.user = make_user('picker')
        give_bundled_questions(cls.user)

    def _answer(self, category, correct, count):
        questions = list(Question.objects.filter(owner=self.user, category=category)[:count])
        self.assertGreaterEqual(len(questions), 1)
        for index in range(count):
            answer(self.user, questions[index % len(questions)], correct)

    def test_focus_mode_favours_weak_categories(self):
        database = Category.objects.get(code=9)
        network = Category.objects.get(code=10)
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
            '想定出題数が少なくても、苦手なデータベースの方が多く出るべき',
        )

    def test_random_mode_follows_the_exam_ratio(self):
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
        questions = list(Question.objects.filter(owner=self.user, category=database)[:3])
        for question in questions:
            answer(self.user, question, correct=False)
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

    def test_other_peoples_questions_are_never_served(self):
        other = make_user('picker_other')
        give_bundled_questions(other)
        session = StudySession.objects.create(
            user=self.user, mode=StudySession.MODE_FOCUS, subject=Question.SUBJECT_A
        )
        for _ in range(60):
            question, _reason = pick_question(self.user, session)
            self.assertEqual(question.owner_id, self.user.pk)

    def test_no_questions_yields_nothing_instead_of_crashing(self):
        empty = make_user('picker_empty')
        session = StudySession.objects.create(
            user=empty, mode=StudySession.MODE_FOCUS, subject=Question.SUBJECT_B
        )
        question, reason = pick_question(empty, session)
        self.assertIsNone(question)
        self.assertIsNone(reason)


class ViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_masters()
        cls.user = make_user('viewer')
        give_bundled_questions(cls.user)

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
            'question_id': question.id, 'choice': question.answer_index,
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_correct'])

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
        self.client.post(reverse('fe:quiz'), payload)
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
        for url in (reverse('fe:quiz'), reverse('fe:manage_list'),
                    reverse('fe:manage_upload')):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 302)


class ManageTests(TestCase):
    """問題の一括差し替え。JSON が唯一の登録経路になっている。"""

    @classmethod
    def setUpTestData(cls):
        seed_masters()
        cls.user = make_user('owner')
        cls.other = make_user('stranger')

    def setUp(self):
        self.client.force_login(self.user)

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

    def test_upload_by_pasting_creates_the_question_set(self):
        response = self.client.post(reverse('fe:manage_upload'), {
            'payload': self._payload(3),
        })
        self.assertRedirects(response, reverse('fe:manage_list'))
        questions = Question.objects.filter(owner=self.user)
        self.assertEqual(questions.count(), 3)
        self.assertTrue(all(q.owner_id == self.user.pk for q in questions))

    def test_upload_by_file_works(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        payload = self._payload(2).encode('utf-8')
        response = self.client.post(reverse('fe:manage_upload'), {
            'upload': SimpleUploadedFile('q.json', payload, content_type='application/json'),
        })
        self.assertRedirects(response, reverse('fe:manage_list'))
        self.assertEqual(Question.objects.filter(owner=self.user).count(), 2)

    def test_upload_deletes_the_old_set_and_its_logs(self):
        self.client.post(reverse('fe:manage_upload'), {'payload': self._payload(3, '旧')})
        old = list(Question.objects.filter(owner=self.user))
        old_ids = [q.id for q in old]
        answer(self.user, old[0], correct=True)

        self.client.post(reverse('fe:manage_upload'), {'payload': self._payload(2, '新')})

        self.assertEqual(
            Question.objects.filter(owner=self.user).count(), 2,
            '古い問題は削除され、データが増え続けない',
        )
        self.assertFalse(Question.objects.filter(id__in=old_ids).exists())
        self.assertEqual(
            Attempt.objects.filter(user=self.user).count(), 0,
            '解答ログは問題と一緒に消える',
        )

    def test_understanding_survives_the_replacement(self):
        """問題を入れ替えても、分野ごとの理解度は残る。"""
        self.client.post(reverse('fe:manage_upload'), {'payload': self._payload(3, '旧')})
        security = Category.objects.get(code=11)
        questions = list(Question.objects.filter(owner=self.user))
        answer(self.user, questions[0], correct=True)
        answer(self.user, questions[1], correct=False)

        self.client.post(reverse('fe:manage_upload'), {'payload': self._payload(2, '新')})

        row = next(r for r in category_stats(self.user) if r['category'] == security)
        self.assertEqual(row['total'], 2, '解答数は残る')
        self.assertEqual(row['correct'], 1, '正解数も残る')
        self.assertEqual(row['rate_percent'], 50)

        overall = overall_stats(self.user)
        self.assertEqual(overall['total'], 2)
        self.assertEqual(overall['study_days'], 1, '学習した日数も残る')

    def test_progress_is_kept_per_account(self):
        give_bundled_questions(self.other)
        self.client.post(reverse('fe:manage_upload'), {'payload': self._payload(2)})
        mine = Question.objects.filter(owner=self.user).first()
        theirs = Question.objects.filter(owner=self.other).first()
        answer(self.user, mine, correct=True)
        answer(self.other, theirs, correct=False)

        self.assertEqual(overall_stats(self.user)['correct'], 1)
        self.assertEqual(overall_stats(self.other)['correct'], 0)
        self.assertEqual(
            CategoryProgress.objects.filter(user=self.user).count(), 1,
            '他人の解答が自分の理解度に混ざらない',
        )

    def test_upload_does_not_touch_other_accounts(self):
        give_bundled_questions(self.other)
        before = Question.objects.filter(owner=self.other).count()
        self.client.post(reverse('fe:manage_upload'), {'payload': self._payload(2)})
        after = Question.objects.filter(owner=self.other).count()
        self.assertEqual(before, after, '他人の問題集は差し替えの影響を受けない')

    def test_broken_json_is_rejected_without_touching_anything(self):
        self.client.post(reverse('fe:manage_upload'), {'payload': self._payload(2)})
        before = list(Question.objects.filter(owner=self.user).values_list('id', flat=True))
        response = self.client.post(reverse('fe:manage_upload'), {'payload': '{ not json'})
        self.assertEqual(response.status_code, 200)
        after = list(Question.objects.filter(owner=self.user).values_list('id', flat=True))
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
                response = self.client.post(reverse('fe:manage_upload'), {
                    'payload': json.dumps(records, ensure_ascii=False),
                })
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['form'].errors)
                self.assertEqual(Question.objects.filter(owner=self.user).count(), 0)

    def test_bundled_import_fills_an_empty_account(self):
        response = self.client.post(reverse('fe:manage_bundled'))
        self.assertRedirects(response, reverse('fe:manage_list'))
        self.assertEqual(
            Question.objects.filter(owner=self.user).count(), len(bundled_records()),
        )

    def test_export_round_trips_through_upload(self):
        self.client.post(reverse('fe:manage_bundled'))
        response = self.client.get(reverse('fe:manage_export'))
        self.assertEqual(response.status_code, 200)
        records = json.loads(response.content.decode('utf-8'))
        self.assertEqual(len(records), len(bundled_records()))

        # 書き出したものをそのまま取り込み直せる
        response = self.client.post(reverse('fe:manage_upload'), {
            'payload': json.dumps(records, ensure_ascii=False),
        })
        self.assertRedirects(response, reverse('fe:manage_list'))
        self.assertEqual(Question.objects.filter(owner=self.user).count(), len(records))

    def test_list_shows_only_my_questions(self):
        give_bundled_questions(self.other)
        self.client.post(reverse('fe:manage_upload'), {'payload': self._payload(3)})
        response = self.client.get(reverse('fe:manage_list'))
        self.assertEqual(response.context['summary']['total'], 3)
        for question in response.context['questions']:
            self.assertEqual(question.owner_id, self.user.pk)


class SeedCommandTests(TestCase):
    def test_seed_without_user_only_loads_masters(self):
        seed_masters()
        self.assertEqual(Category.objects.count(), 23)
        self.assertEqual(QuestionTemplate.objects.count(), len(REGISTRY))
        self.assertEqual(Question.objects.count(), 0, '問題はアカウント指定なしでは入らない')

    def test_seed_with_user_fills_that_account(self):
        seed_masters()
        user = make_user('seeded')
        call_command('seed_fe', '--user', 'seeded', verbosity=0)
        self.assertEqual(
            Question.objects.filter(owner=user).count(), len(bundled_records()),
        )
