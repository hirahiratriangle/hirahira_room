"""FE 対策アプリのテスト。

このアプリで壊れると痛いのは次の3点なので、そこを重点的に確かめる。
  ・重点出題が本当に苦手分野へ寄るか（統計的に検証する）
  ・問題がアカウントごとに分かれ、他人の問題が混ざらないか
  ・一括差し替えをしても解答履歴と分野別の正答率が残るか
"""

import json
import random

from accounts.models import CustomUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Sum
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .exam import EXAM, build_prompt
from .generators import REGISTRY, generate_question
from .importer import ImportError_, parse, replace_all, validate
from .models import (Attempt, Category, CategoryProgress, DailyProgress,
                     LearningNote, LearningRound,
                     Question, QuestionTemplate, StudySession)
from .selection import (REVIEW_DUE_GAP, _due_for_review,
                        _last_result_map, _recent_answer_times, pick_question)
from .stats import (WEAK_MIN_ATTEMPTS, category_stats, overall_stats,
                    record_progress, smoothed_rate, weak_categories)


def seed_masters():
    """中分類と問題テンプレートだけを投入する（問題は入らない）。"""
    call_command('seed_fe', verbosity=0)


def make_user(username):
    return CustomUser.objects.create_user(
        username=username, email='{}@example.com'.format(username), password='pass12345'
    )


def build_questions(user, per_category=4):
    """テスト用の問題を全中分類に作る。

    アプリは問題を同梱しないので、テストも配布物に依存させない。
    科目Bは要綱の内訳に合わせて中分類2と11にだけ置き、本番同様6択にする。
    """
    records = []
    for category in Category.objects.all():
        for i in range(per_category):
            is_subject_b = category.code in (2, 11) and i == 0
            size = 6 if is_subject_b else 4
            records.append({
                'category': category.code,
                'subject': Question.SUBJECT_B if is_subject_b else Question.SUBJECT_A,
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
    return replace_all(user, validate({'questions': records, 'notes': []}))


def build_notes(user, codes=(9, 11), topic=None):
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


def answer(user, question, correct):
    """1問解いた状態を作る（解答ログと理解度の両方を更新する）。"""
    attempt = Attempt.objects.create(
        user=user, question=question, category=question.category,
        selected_index=question.answer_index if correct else 0, is_correct=correct,
    )
    record_progress(user, question, correct)
    return attempt


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
        build_questions(cls.user)

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
        build_questions(other)
        rows_after = {r['category'].code: r['question_count'] for r in category_stats(self.user)}
        self.assertEqual(rows_before, rows_after, '他人の問題が出題可能数に混ざってはいけない')


class SelectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_masters()
        cls.user = make_user('picker')
        build_questions(cls.user)

    def _answer(self, category, correct, count):
        questions = list(Question.objects.filter(owner=self.user, category=category)[:count])
        self.assertGreaterEqual(len(questions), 1)
        for index in range(count):
            answer(self.user, questions[index % len(questions)], correct)

    def test_a_missed_question_is_not_repeated_immediately(self):
        """まちがえた直後に同じ問題を出さない。答えを覚えているだけになる。"""
        category = Category.objects.get(code=9)
        missed = Question.objects.filter(owner=self.user, category=category).first()
        answer(self.user, missed, correct=False)

        session = StudySession.objects.create(
            user=self.user, mode=StudySession.MODE_CATEGORY,
            subject=Question.SUBJECT_A, category=category,
        )
        random.seed(20260908)
        for _ in range(30):
            question, _reason = pick_question(self.user, session)
            self.assertNotEqual(question.id, missed.id, '間をあけずに戻ってきた')

    def test_a_missed_question_comes_back_before_unseen_ones(self):
        """間隔があけば、未出題が残っていても復習が優先される。"""
        category = Category.objects.get(code=9)
        questions = list(Question.objects.filter(owner=self.user, category=category))
        self.assertGreaterEqual(len(questions), 4)
        missed = questions[0]
        answer(self.user, missed, correct=False)
        # ほかの問題を解いて間隔をあける
        for question in questions[1:]:
            answer(self.user, question, correct=True)
        for _ in range(REVIEW_DUE_GAP):
            answer(self.user, questions[1], correct=True)

        session = StudySession.objects.create(
            user=self.user, mode=StudySession.MODE_CATEGORY,
            subject=Question.SUBJECT_A, category=category,
        )
        random.seed(20260908)
        reasons = set()
        for _ in range(60):
            question, reason = pick_question(self.user, session)
            if question.id == missed.id:
                reasons.add(reason)
        self.assertIn('まちがえた問題の復習', reasons, '間隔をあけても戻ってこなかった')

    def test_repeated_misses_shorten_the_interval(self):
        """何度も落とした問題ほど、短い間隔で戻す。"""
        category = Category.objects.get(code=9)
        questions = list(Question.objects.filter(owner=self.user, category=category))
        once, twice = questions[0], questions[1]
        # 2回落とした方を先に、1回だけの方をあとに解く。
        # あとに解いたほうが間隔は短いので、間隔だけで並ぶなら once は戻らない。
        answer(self.user, twice, correct=False)
        answer(self.user, twice, correct=False)
        answer(self.user, once, correct=False)
        for question in questions[2:]:
            answer(self.user, question, correct=True)
        answer(self.user, questions[2], correct=True)

        history = _last_result_map(self.user, [once.id, twice.id])
        times = _recent_answer_times(self.user)
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
        build_questions(other)
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
        build_questions(cls.user)

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

    def _upload(self, n=3, prefix='問題'):
        """取り込みは JSON ファイル1本だけなので、テストもファイルで投げる。"""
        return {'upload': SimpleUploadedFile(
            'q.json', self._payload(n, prefix).encode('utf-8'),
            content_type='application/json',
        )}

    def _json_upload(self, records):
        return {'upload': SimpleUploadedFile(
            'q.json', json.dumps(records, ensure_ascii=False).encode('utf-8'),
            content_type='application/json',
        )}

    def _upload_with_notes(self, n=3, codes=(9, 11), topic=None):
        return self._json_upload({
            'notes': build_notes(self.user, codes=codes, topic=topic),
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
        questions = Question.objects.filter(owner=self.user)
        self.assertEqual(questions.count(), 3)
        self.assertTrue(all(q.owner_id == self.user.pk for q in questions))

    def test_pasting_is_not_offered(self):
        """取り込み経路はファイル1本。貼り付け欄は置かない。"""
        response = self.client.get(reverse('fe:manage_upload'))
        self.assertNotIn('payload', response.context['form'].fields)
        self.assertNotContains(response, 'id_payload')
        self.assertNotContains(response, '<textarea')

    def test_upload_is_required(self):
        response = self.client.post(reverse('fe:manage_upload'), {})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)

    def test_upload_deletes_the_old_set_and_its_logs(self):
        self.client.post(reverse('fe:manage_upload'), self._upload(3, '旧'))
        old = list(Question.objects.filter(owner=self.user))
        old_ids = [q.id for q in old]
        answer(self.user, old[0], correct=True)

        self.client.post(reverse('fe:manage_upload'), self._upload(2, '新'))

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
        self.client.post(reverse('fe:manage_upload'), self._upload(3, '旧'))
        security = Category.objects.get(code=11)
        questions = list(Question.objects.filter(owner=self.user))
        answer(self.user, questions[0], correct=True)
        answer(self.user, questions[1], correct=False)

        self.client.post(reverse('fe:manage_upload'), self._upload(2, '新'))

        row = next(r for r in category_stats(self.user) if r['category'] == security)
        self.assertEqual(row['total'], 2, '解答数は残る')
        self.assertEqual(row['correct'], 1, '正解数も残る')
        self.assertEqual(row['rate_percent'], 50)

        overall = overall_stats(self.user)
        self.assertEqual(overall['total'], 2)
        self.assertEqual(overall['study_days'], 1, '学習した日数も残る')

    def test_progress_is_kept_per_account(self):
        build_questions(self.other)
        self.client.post(reverse('fe:manage_upload'), self._upload(2))
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
        build_questions(self.other)
        before = Question.objects.filter(owner=self.other).count()
        self.client.post(reverse('fe:manage_upload'), self._upload(2))
        after = Question.objects.filter(owner=self.other).count()
        self.assertEqual(before, after, '他人の問題集は差し替えの影響を受けない')

    def test_broken_json_is_rejected_without_touching_anything(self):
        self.client.post(reverse('fe:manage_upload'), self._upload(2))
        before = list(Question.objects.filter(owner=self.user).values_list('id', flat=True))
        response = self.client.post(reverse('fe:manage_upload'), {'upload': SimpleUploadedFile(
            'q.json', b'{ not json', content_type='application/json')})
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
                response = self.client.post(
                    reverse('fe:manage_upload'), self._json_upload(records))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['form'].errors)
                self.assertEqual(Question.objects.filter(owner=self.user).count(), 0)

    def test_export_round_trips_through_upload(self):
        self.client.post(reverse('fe:manage_upload'), self._upload(4))
        response = self.client.get(reverse('fe:manage_export'))
        self.assertEqual(response.status_code, 200)
        records = json.loads(response.content.decode('utf-8'))
        self.assertEqual(len(records), 4)
        self.assertNotIn('key', records[0], '書き出しにキーは含めない')

        # 書き出したものをそのまま取り込み直せる
        response = self.client.post(
            reverse('fe:manage_upload'), self._json_upload(records))
        self.assertRedirects(response, reverse('fe:manage_list'))
        self.assertEqual(Question.objects.filter(owner=self.user).count(), len(records))

    def test_choice_labels_cover_a_long_answer_group(self):
        """選択肢が多くても記号が割り当たること。"""
        self.client.post(reverse('fe:manage_upload'), self._json_upload([{
            'category': 2, 'subject': 'B', 'stem': 'x',
            'choices': ['選択肢{}'.format(i) for i in range(9)], 'answer': 8,
            'explanation': '解説',
        }]))
        question = Question.objects.get(owner=self.user)
        labels = [label for _, label, _ in question.labeled_choices()]
        self.assertEqual(labels, list('アイウエオカキクケ'))
        self.assertEqual(question.answer_label, 'ケ')

    def test_list_shows_only_my_questions(self):
        build_questions(self.other)
        self.client.post(reverse('fe:manage_upload'), self._upload(3))
        response = self.client.get(reverse('fe:manage_list'))
        self.assertEqual(response.context['summary']['total'], 3)
        for question in response.context['questions']:
            self.assertEqual(question.owner_id, self.user.pk)


class ImportNotesTests(TestCase):
    """技術解説つき JSON の取り込み。"""

    @classmethod
    def setUpTestData(cls):
        seed_masters()
        cls.user = make_user('reader')

    def test_object_form_imports_notes_and_questions(self):
        payload = {
            'notes': build_notes(self.user),
            'questions': [{
                'category': 11, 'stem': 'x', 'choices': ['a', 'b', 'c', 'd'], 'answer': 0,
            }],
        }
        created, _removed, notes = replace_all(self.user, validate(parse(
            json.dumps(payload, ensure_ascii=False)
        )))
        self.assertEqual(created, 1)
        self.assertEqual(notes, 2)
        self.assertEqual(LearningNote.objects.filter(owner=self.user).count(), 2)

    def test_plain_array_still_works(self):
        """解説を持たない、いままでの形の JSON もそのまま取り込める。"""
        payload = json.dumps(
            [{'category': 11, 'stem': 'x', 'choices': ['a', 'b'], 'answer': 0}],
            ensure_ascii=False,
        )
        created, _removed, notes = replace_all(self.user, validate(parse(payload)))
        self.assertEqual((created, notes), (1, 0))

    def test_notes_are_replaced_with_the_questions(self):
        """問題だけの JSON を入れたら、前の解説も消える。"""
        replace_all(self.user, validate(parse(json.dumps({
            'notes': build_notes(self.user),
            'questions': [{'category': 11, 'stem': 'x', 'choices': ['a', 'b'], 'answer': 0}],
        }, ensure_ascii=False))))
        self.assertEqual(LearningNote.objects.filter(owner=self.user).count(), 2)

        replace_all(self.user, validate(parse(json.dumps(
            [{'category': 11, 'stem': 'y', 'choices': ['a', 'b'], 'answer': 0}],
            ensure_ascii=False,
        ))))
        self.assertEqual(
            LearningNote.objects.filter(owner=self.user).count(), 0,
            '問題と噛み合わない解説が残らない',
        )

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
                self.assertEqual(Question.objects.filter(owner=self.user).count(), 0)


class LearningModeTests(TestCase):
    """学習モード。解説を読んでから、その範囲を解く。"""

    @classmethod
    def setUpTestData(cls):
        seed_masters()
        cls.user = make_user('learner')

    def setUp(self):
        self.client.force_login(self.user)

    def _prepare(self, topic=None):
        build_questions(self.user, per_category=12)
        LearningNote.objects.filter(owner=self.user).delete()
        for note in build_notes(self.user, codes=(9,), topic=topic):
            LearningNote.objects.create(
                owner=self.user,
                category=Category.objects.get(code=note['category']),
                topic=note['topic'], title=note['title'],
                body=note['body'], source=note['source'],
            )

    def test_a_round_needs_a_note(self):
        """解説が無ければ始められない。取り込み画面へ案内する。"""
        build_questions(self.user)
        response = self.client.post(reverse('fe:learn_start'), {'minutes': 5})
        self.assertRedirects(response, reverse('fe:manage_upload'))
        self.assertEqual(LearningRound.objects.count(), 0)

    def test_reading_phase_shows_the_note_not_the_questions(self):
        """読む段階では、設問も選択肢も見せない。"""
        self._prepare()
        self.client.post(reverse('fe:learn_start'), {'minutes': 5})
        round_ = LearningRound.objects.get(user=self.user)
        self.assertEqual(round_.reading_seconds, 300)

        response = self.client.get(reverse('fe:learn_read', args=[round_.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, round_.note.title)
        for item in round_.items.all():
            self.assertNotContains(response, item.question.stem)

    def test_questions_come_from_the_note_s_category(self):
        self._prepare()
        self.client.post(reverse('fe:learn_start'), {'minutes': 5})
        round_ = LearningRound.objects.get(user=self.user)
        self.assertEqual(round_.total, LearningRound.QUESTION_COUNT)
        for item in round_.items.select_related('question'):
            self.assertEqual(
                item.question.category_id, round_.note.category_id,
                '読んだ解説と別の分野が出題された',
            )

    def test_answering_records_progress(self):
        """学習モードで解いた分も、分野ごとの理解度に積む。"""
        self._prepare()
        self.client.post(reverse('fe:learn_start'), {'minutes': 5})
        round_ = LearningRound.objects.get(user=self.user)
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
        self.assertEqual(Attempt.objects.filter(user=self.user).count(), 1)
        self.assertEqual(
            CategoryProgress.objects.filter(user=self.user).aggregate(
                n=Sum('answered'))['n'], 1,
        )

    def test_a_round_runs_to_the_result_screen(self):
        self._prepare()
        self.client.post(reverse('fe:learn_start'), {'minutes': 3})
        round_ = LearningRound.objects.get(user=self.user)
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

    def test_another_account_cannot_open_the_round(self):
        self._prepare()
        self.client.post(reverse('fe:learn_start'), {'minutes': 5})
        round_ = LearningRound.objects.get(user=self.user)

        self.client.force_login(make_user('stranger2'))
        for name in ('fe:learn_read', 'fe:learn_quiz', 'fe:learn_result'):
            self.assertEqual(
                self.client.get(reverse(name, args=[round_.pk])).status_code, 404,
            )


class SeedCommandTests(TestCase):
    def test_seed_loads_masters_but_no_questions(self):
        """アプリは問題を同梱しないので、コマンドでも問題は入らない。"""
        seed_masters()
        self.assertEqual(Category.objects.count(), 23)
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

    def test_upload_screen_shows_the_materials(self):
        user = make_user('materials')
        self.client.force_login(user)
        response = self.client.get(reverse('fe:manage_upload'))
        self.assertEqual(response.status_code, 200)
        for material in EXAM['materials']:
            self.assertContains(response, material['name'])
        self.assertContains(response, EXAM['url'])

    def test_dashboard_shows_the_materials(self):
        user = make_user('materials_dash')
        self.client.force_login(user)
        response = self.client.get(reverse('fe:index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '問題を作るのに必要な資料')
        for material in EXAM['materials']:
            self.assertContains(response, material['name'])
