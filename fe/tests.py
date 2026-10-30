"""FE 対策アプリのテスト。

このアプリで壊れると痛いのは次の3点なので、そこを重点的に確かめる。
  ・重点出題が本当に苦手分野へ寄るか（統計的に検証する）
  ・問題が ID ごとに分かれ、他人の問題が混ざらないか
  ・一括差し替えをしても解答履歴と分野別の正答率が残るか
"""

import io
import json
import random
import tempfile
from pathlib import Path
from unittest import mock
import html
import re

from accounts.models import CustomUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Sum
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from django.urls import reverse

from . import speech
from .exam import EXAM, build_prompt
from .generators import REGISTRY, generate_question
from .importer import (ImportError_, needs_confirmation, parse, preview, sync,
                       validate)
from .learning import note_menu, pick_note
from .models import (Attempt, Category, CategoryProgress, DailyProgress, Learner,
                     LearningNote, LearningRound, OpenProgress, PassReport,
                     Question, QuestionTemplate, StudySession, XPost)
from .selection import (REVIEW_DUE_GAP, _due_for_review,
                        _last_result_map, _recent_answer_times, pick_question)
from .stats import (WEAK_MIN_ATTEMPTS, category_options, category_stats,
                    overall_stats,
                    record_progress, site_summary, smoothed_rate, weak_categories)
from django.core.signing import get_cookie_signer

from .views import LEARNER_COOKIE, LEARNER_COOKIE_SALT, SESSION_LEARNER



def put_learner(client, pk):
    """その ID で入った状態にする。セッションと、ブラウザを閉じたら消える Cookie の両方。"""
    session = client.session
    session[SESSION_LEARNER] = pk
    session.save()
    client.cookies[LEARNER_COOKIE] = get_cookie_signer(
        salt=LEARNER_COOKIE + LEARNER_COOKIE_SALT
    ).sign(str(pk))

def seed_masters():
    """中分類と問題テンプレートだけを投入する（問題は入らない）。"""
    call_command('seed_fe', verbosity=0)


def make_learner(code, is_admin=False):
    return Learner.objects.create(code=code, is_admin=is_admin)


def enter(client, learner):
    """hirahira_room にログインし、その ID で入った状態にする。

    ID はアカウントと紐づかないので、学習の画面はどのアカウントでも同じに動く。
    """
    # 管理画面はスタッフ権限のあるアカウントにしか開けないので、付けておく
    account, _ = CustomUser.objects.get_or_create(
        username='hirahira', defaults={'is_staff': True},
    )
    client.force_login(account)
    put_learner(client, learner.pk)


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

    def _session(self, mode, subject=Question.SUBJECT_A):
        session, _ = StudySession.objects.get_or_create(learner=self.learner)
        session.mode, session.subject, session.category = mode, subject, None
        session.save()
        return session

    def test_ipa_mode_serves_only_questions_published_by_ipa(self):
        """IPA公開問題のモードでは、自作も自動生成も出さない。"""
        category = Category.objects.get(code=4)
        sources = ['令和7年度 科目A 問2', 'サンプル問題 科目A 問8']
        for i, source in enumerate(sources):
            Question.objects.create(
                subject=Question.SUBJECT_A, category=category, topic=category.name,
                stem='IPA の問題{}'.format(i), choices=['あ', 'い', 'う', 'え'],
                answer_index=0, source=source,
            )
        session = self._session(StudySession.MODE_IPA)

        seen, exclude = set(), []
        for _ in range(12):
            question, _reason = pick_question(self.learner, session, exclude_ids=exclude)
            self.assertIsNotNone(question)
            self.assertIn(question.source, sources)
            self.assertFalse(question.is_generated)
            seen.add(question.source)
            exclude = [question.id]
        self.assertEqual(seen, set(sources))

    def test_restricting_mode_stays_inside_the_chosen_category(self):
        """IPA のモードで分野を選んだら、その分野の IPA の問題だけを出す。"""
        inside = Category.objects.get(code=4)
        outside = Category.objects.get(code=5)
        wanted = Question.objects.create(
            subject=Question.SUBJECT_A, category=inside, topic=inside.name,
            stem='分野の中の IPA の問題', choices=['あ', 'い', 'う', 'え'],
            answer_index=0, source='令和7年度 科目A 問2',
        )
        Question.objects.create(
            subject=Question.SUBJECT_A, category=outside, topic=outside.name,
            stem='別の分野の IPA の問題', choices=['あ', 'い', 'う', 'え'],
            answer_index=0, source='令和7年度 科目A 問3',
        )
        session = self._session(StudySession.MODE_IPA)
        session.category = inside
        session.save()

        for _ in range(6):
            question, _reason = pick_question(self.learner, session, exclude_ids=[wanted.id])
            self.assertEqual(question, wanted)

        # その分野に IPA の問題が無ければ、ほかへ広げずに「出せる問題なし」にする
        session.category = Category.objects.get(code=6)
        session.save()
        self.assertEqual(pick_question(self.learner, session), (None, None))

    def test_every_mode_stays_inside_the_chosen_category(self):
        """どのモードでも、分野を選んだらその分野の問題だけを出す。"""
        inside = Category.objects.get(code=4)
        for mode in (StudySession.MODE_FOCUS, StudySession.MODE_RANDOM,
                     StudySession.MODE_CATEGORY):
            with self.subTest(mode=mode):
                session = self._session(mode)
                session.category = inside
                session.save()
                exclude = []
                for _ in range(8):
                    question, _reason = pick_question(self.learner, session, exclude_ids=exclude)
                    self.assertIsNotNone(question)
                    self.assertEqual(question.category, inside)
                    exclude = [question.id]

    def test_weak_mode_without_weak_categories_serves_nothing(self):
        """苦手と判定した分野がまだ無ければ、おまかせに切り替えずに何も出さない。"""
        session = self._session(StudySession.MODE_WEAK)
        self.assertEqual(pick_question(self.learner, session), (None, None))

        # 苦手と判定された分野ができたら、その分野だけから出す。未着手の分野は出さない
        weak = Category.objects.get(code=4)
        self._answer(weak, False, WEAK_MIN_ATTEMPTS)
        for _ in range(8):
            question, _reason = pick_question(self.learner, session)
            self.assertEqual(question.category, weak)

    def test_review_without_misses_serves_nothing(self):
        """まちがえた問題が無ければ、おまかせに切り替えずに何も出さない。"""
        session = self._session(StudySession.MODE_REVIEW)
        self.assertEqual(pick_question(self.learner, session), (None, None))

        answer(self.learner, Question.objects.filter(category__code=4).first(), True)
        self.assertEqual(pick_question(self.learner, session), (None, None))

        missed = Question.objects.filter(category__code=5).first()
        answer(self.learner, missed, False)
        question, _reason = pick_question(self.learner, session, exclude_ids=[missed.id])
        self.assertEqual(question, missed)

    def test_weak_mode_serves_nothing_from_a_category_not_judged_weak(self):
        """苦手分野だけで分野を選んでも、苦手と判定していない分野からは出さない。"""
        inside = Category.objects.get(code=4)
        session = self._session(StudySession.MODE_WEAK)
        session.category = inside
        session.save()
        self.assertEqual(pick_question(self.learner, session), (None, None))

        # 苦手と判定されたら、その分野から出す
        self._answer(inside, False, WEAK_MIN_ATTEMPTS)
        for _ in range(6):
            question, _reason = pick_question(self.learner, session)
            self.assertEqual(question.category, inside)

    def test_review_serves_nothing_from_a_category_without_misses(self):
        """復習で分野を選び、その分野にまちがえた問題が無ければ、何も出さない。"""
        inside = Category.objects.get(code=4)
        answer(self.learner, Question.objects.filter(category=inside).first(), True)
        answer(self.learner, Question.objects.filter(category__code=5).first(), False)
        session = self._session(StudySession.MODE_REVIEW)
        session.category = inside
        session.save()
        self.assertEqual(pick_question(self.learner, session), (None, None))

    def test_review_in_a_category_serves_only_misses_from_it(self):
        """復習で分野を選んだら、その分野でまちがえた問題だけを復習する。"""
        inside = Category.objects.get(code=4)
        outside = Category.objects.get(code=5)
        missed_in = Question.objects.filter(category=inside).first()
        missed_out = Question.objects.filter(category=outside).first()
        answer(self.learner, missed_in, False)
        answer(self.learner, missed_out, False)

        session = self._session(StudySession.MODE_REVIEW)
        session.category = inside
        session.save()
        for _ in range(6):
            # 直前に出した問題として除外されていても、ほかへ広げずに同じ問題を出す
            question, reason = pick_question(self.learner, session, exclude_ids=[missed_in.id])
            self.assertEqual(question, missed_in)
            self.assertEqual(reason, '前回まちがえた問題')

    def test_ipa_mode_has_nothing_to_serve_without_ipa_questions(self):
        session = self._session(StudySession.MODE_IPA)
        self.assertEqual(pick_question(self.learner, session), (None, None))

    def test_calculation_is_told_from_the_shape_of_the_choices(self):
        """数量の形をした選択肢だけを計算問題とみなす。番号の付いた名前は当てない。"""
        stem = '稼働率 0.9 の装置 2 台を並列に接続したときの稼働率はどれか。'
        cases = [
            (['0.81', '0.90', '0.95', '0.99'], True),
            (['約 0.8', '約 2.3', '約 6.3', '約 18.9'], True),
            (['毎秒 25M バイト', '毎秒 100M バイト', '毎秒 400M バイト', '毎秒 4G バイト'], True),
            (['n(n−1)', 'n−1', 'n(n−1)/2', 'n の 2 乗'], True),
            (['RAID0', 'RAID1', 'RAID5', 'RAID6'], False),
            (['レベル1', 'レベル2', 'レベル3', 'レベル4'], False),
            (['第1正規形', '第2正規形', '第3正規形', '第4正規形'], False),
            (['2 台の装置に同じ内容を書き込む', '0.81', '0.90', '0.99'], False),
        ]
        for choices, expected in cases:
            self.assertEqual(
                Question(stem=stem, choices=choices).is_calculation, expected, choices
            )
        # 問題文に数字が無ければ、選択肢が数字でも計算問題とはみなさない
        self.assertFalse(
            Question(stem='適切なものはどれか。', choices=['1', '2', '3', '4']).is_calculation
        )

    def test_calc_mode_serves_only_calculation_questions(self):
        """計算問題のモードでは、数値で答える問題と自動生成だけを出す。"""
        category = Category.objects.get(code=4)
        Question.objects.create(
            subject=Question.SUBJECT_A, category=category, topic=category.name,
            stem='稼働率 0.9 の装置 2 台を直列に接続したときの稼働率はどれか。',
            choices=['0.81', '0.90', '0.95', '0.99'], answer_index=0, source='テスト用',
        )
        session = self._session(StudySession.MODE_CALC)

        exclude = []
        for _ in range(12):
            question, _reason = pick_question(self.learner, session, exclude_ids=exclude)
            self.assertIsNotNone(question)
            self.assertEqual(question.subject, Question.SUBJECT_A)
            self.assertTrue(question.is_calculation, question.stem)
            exclude = [question.id]

    def test_calc_mode_follows_the_chosen_subject(self):
        """科目Bを選んでいれば、科目Bの数値で答える問題だけを出す。自動生成は混ぜない。"""
        category = Category.objects.filter(subject=Question.SUBJECT_B).first()
        made = Question.objects.create(
            subject=Question.SUBJECT_B, category=category, topic=category.name,
            stem='関数 f(3) を呼び出したときの戻り値はどれか。',
            choices=['6', '7', '8', '9', '10', '11'], answer_index=0, source='テスト用',
        )
        session = self._session(StudySession.MODE_CALC, subject=Question.SUBJECT_B)
        for _ in range(6):
            question, _reason = pick_question(self.learner, session)
            self.assertEqual(question, made)

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

    def test_a_well_answered_category_is_not_listed_as_weak(self):
        """よく解けている分野は、何問解いていても苦手分野に並べない。"""
        good, bad = Category.objects.filter(subject='A')[:2]
        for category, results in ((good, [True] * 10), (bad, [False] * 4)):
            question = Question.objects.filter(category=category).first()
            for correct in results:
                answer(self.learner, question, correct)

        listed = [r['category'] for r in weak_categories(category_stats(self.learner))]
        self.assertEqual(listed, [bad])

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
                self.assertIn('図を見ないと解けない問題（自作の分）', html)
                self.assertIn('本番どおりの模擬試験', html)
                self.assertIn('記述・入力・並べ替え', html)

    def test_import_screen_states_what_cannot_be_rendered(self):
        """取り込み画面では、作る側が踏む制約も併せて示す。"""
        admin = make_learner('noticeadmin', is_admin=True)
        enter(self.client, admin)
        html = self.client.get(reverse('fe:manage_upload')).content.decode('utf-8')
        self.assertIn('このアプリで出せない問題', html)
        for word in ('図は公開問題を転記するときだけ使えます', '上付き', '組合せを1つの選択肢'):
            self.assertIn(word, html)

    def test_prompt_states_unsupported_formats(self):
        """利用者が AI に渡すプロンプトにも、扱えない形式を書いておく。"""
        from .exam import build_prompt
        prompt = build_prompt()
        self.assertIn('【このアプリが扱えないもの】', prompt)
        for word in ('図は公開問題を転記するときだけ使う', '2のn乗', 'ルビ', '複数正解や部分点はない'):
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

    def test_the_category_field_is_usable_in_every_mode(self):
        """分野はどの出題モードでも選べる。伏せるモードは無い。"""
        session, _ = StudySession.objects.get_or_create(learner=self.learner)
        for mode, _label in StudySession.MODE_CHOICES:
            with self.subTest(mode=mode):
                session.mode = mode
                session.save()
                html = self.client.get(dashboard(self.learner)).content.decode('utf-8')
                block = re.search(r'<select[^>]*id="category".*?>', html, re.S).group(0)
                self.assertNotIn('disabled', block)

    def test_the_saved_category_shows_in_every_mode(self):
        """保存してある分野は、どのモードでも選ばれて見える。

        どのモードでも分野が効くので、効いている分野をそのまま映す。
        """
        session, _ = StudySession.objects.get_or_create(learner=self.learner)
        session.subject = Question.SUBJECT_A
        session.category = Category.objects.get(code=2)

        def selected():
            html = self.client.get(dashboard(self.learner)).content.decode('utf-8')
            block = re.search(r'id="category".*?</select>', html, re.S).group(0)
            found = re.findall(r'<option value="(\d*)"[^>]*selected', block)
            return found[0] if found else ''

        for mode, _label in StudySession.MODE_CHOICES:
            with self.subTest(mode=mode):
                session.mode = mode
                session.save()
                self.assertEqual(selected(), '2')

        session.category = None
        session.save()
        self.assertEqual(selected(), '')

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
        put_learner(self.client, make_learner('boss', is_admin=True).pk)
        for name in ('fe:manage_list', 'fe:manage_upload'):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('account_login'), response['Location'])
        self.assertEqual(self.client.post(reverse('fe:manage_upload'), {}).status_code, 302)


class NewModeSettingsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_masters()
        cls.learner = make_learner('modes')
        build_questions()

    def setUp(self):
        enter(self.client, self.learner)

    def test_calc_mode_keeps_the_chosen_subject(self):
        """計算問題のモードでも、科目はAとBのどちらも選べる。"""
        for subject in ('B', 'A'):
            self.client.post(reverse('fe:quiz_settings'), {
                'mode': StudySession.MODE_CALC, 'subject': subject, 'next': 'index',
            })
            session = StudySession.objects.get(learner=self.learner)
            self.assertEqual(session.mode, StudySession.MODE_CALC)
            self.assertEqual(session.subject, subject)

    def test_ipa_mode_keeps_the_chosen_subject(self):
        self.client.post(reverse('fe:quiz_settings'), {
            'mode': StudySession.MODE_IPA, 'subject': 'B', 'next': 'index',
        })
        session = StudySession.objects.get(learner=self.learner)
        self.assertEqual(session.mode, StudySession.MODE_IPA)
        self.assertEqual(session.subject, Question.SUBJECT_B)

    def test_restricting_modes_keep_the_chosen_category(self):
        """対象を絞る2つのモードでは、分野も選べる。選ばなくてもよい。"""
        for mode in (StudySession.MODE_IPA, StudySession.MODE_CALC):
            self.client.post(reverse('fe:quiz_settings'), {
                'mode': mode, 'subject': 'A', 'category': '4', 'next': 'index',
            })
            session = StudySession.objects.get(learner=self.learner)
            self.assertEqual((session.mode, session.category.code), (mode, 4))

            self.client.post(reverse('fe:quiz_settings'), {
                'mode': mode, 'subject': 'A', 'category': '', 'next': 'index',
            })
            session = StudySession.objects.get(learner=self.learner)
            self.assertEqual((session.mode, session.category), (mode, None))

    def test_every_mode_keeps_the_chosen_category(self):
        """分野はどのモードでも保存される。選ばなければ保存されない。"""
        for mode, _label in StudySession.MODE_CHOICES:
            with self.subTest(mode=mode):
                self.client.post(reverse('fe:quiz_settings'), {
                    'mode': mode, 'subject': 'A', 'category': '4', 'next': 'index',
                })
                session = StudySession.objects.get(learner=self.learner)
                self.assertEqual((session.mode, session.category.code), (mode, 4))

        self.client.post(reverse('fe:quiz_settings'), {
            'mode': StudySession.MODE_FOCUS, 'subject': 'A', 'category': '', 'next': 'index',
        })
        self.assertIsNone(StudySession.objects.get(learner=self.learner).category)

    def test_category_counts_follow_the_mode(self):
        """分野の件数は、そのモードで出せる問題の数にする。"""
        category = Category.objects.get(code=4)
        Question.objects.create(
            subject=Question.SUBJECT_A, category=category, topic=category.name,
            stem='稼働率 0.9 の装置 2 台を直列に接続したときの稼働率はどれか。',
            choices=['0.81', '0.90', '0.95', '0.99'], answer_index=0,
            source='令和7年度 科目A 問9',
        )
        option = next(
            o for o in category_options(self.learner, StudySession.MODE_IPA) if o['code'] == 4
        )
        self.assertEqual((option['count'], option['selectable']), (1, True))
        self.assertEqual(option['by_mode'][StudySession.MODE_CALC][0], 1)
        self.assertGreater(option['question_count'], 1)

        self.client.post(reverse('fe:quiz_settings'), {
            'mode': StudySession.MODE_IPA, 'subject': 'A', 'category': '4', 'next': 'index',
        })
        response = self.client.get(dashboard(self.learner))
        self.assertContains(response, '{}（1問）'.format(category.label))

    def test_categories_with_nothing_to_serve_cannot_be_chosen(self):
        """そのモードで出せる問題が無い分野は、分野の欄で選べなくする。"""
        ipa_category = Category.objects.get(code=4)
        Question.objects.create(
            subject=Question.SUBJECT_A, category=ipa_category, topic=ipa_category.name,
            stem='IPA の問題', choices=['あ', 'い', 'う', 'え'], answer_index=0,
            source='令和7年度 科目A 問9',
        )
        missed = Question.objects.filter(category__code=5).first()
        answer(self.learner, missed, False)
        weak = Category.objects.get(code=6)
        for question in list(Question.objects.filter(category=weak)[:WEAK_MIN_ATTEMPTS]):
            answer(self.learner, question, False)

        def selectable(mode):
            return {
                o['code'] for o in category_options(self.learner, mode)
                if o['selectable'] and o['subject'] == Question.SUBJECT_A
            }

        self.assertEqual(selectable(StudySession.MODE_IPA), {4})
        self.assertEqual(selectable(StudySession.MODE_REVIEW), {5, 6})
        self.assertEqual(selectable(StudySession.MODE_WEAK), {6})
        self.assertEqual(len(selectable(StudySession.MODE_FOCUS)), 23)

        # 画面でも、選べない分野には disabled が付く
        session, _ = StudySession.objects.get_or_create(learner=self.learner)
        session.mode = StudySession.MODE_IPA
        session.save()
        html_text = self.client.get(dashboard(self.learner)).content.decode('utf-8')
        block = re.search(r'id="category".*?</select>', html_text, re.S).group(0)
        open_codes = re.findall(r'<option value="(\d+)"(?![^>]*disabled)', block)
        self.assertEqual(open_codes, ['4'])

    def test_both_modes_are_offered_on_the_dashboard(self):
        response = self.client.get(dashboard(self.learner))
        self.assertContains(response, 'IPA公開問題（過去問・サンプル）からおまかせ')
        self.assertContains(response, '計算問題をおまかせ')


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

    def test_the_entrance_says_how_the_records_are_used(self):
        """記録を本人の機能以外に何に使うかを、入口と切り替え画面に書いておく。"""
        for name in ('fe:index', 'fe:enter'):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertContains(response, 'ID ごとの成績が分からない形に集計して')
                self.assertContains(response, '運営は ID ごとの記録を見られます。')

    def test_the_id_is_forgotten_when_the_browser_is_closed(self):
        """入った ID は、ブラウザを閉じたら消える Cookie にも置く。消えたら入口に戻る。

        セッションはブラウザを閉じても残るので、それだけだと共用の端末で
        次の人が前の人の ID のまま入ってしまう。
        """
        Learner.objects.create(code='closer')
        response = self._post('closer', 'enter')
        cookie = response.cookies[LEARNER_COOKIE]
        self.assertEqual(cookie['max-age'], '')
        self.assertEqual(cookie['expires'], '')
        self.assertTrue(cookie['httponly'])
        self.assertRedirects(response, reverse('fe:dashboard', args=['closer']))

        # ブラウザを閉じると、期限の無い Cookie だけが消え、セッションは残る
        del self.client.cookies[LEARNER_COOKIE]
        response = self.client.get(reverse('fe:quiz'))
        self.assertRedirects(response, reverse('fe:index'))
        self.assertNotIn(SESSION_LEARNER, self.client.session)

    def test_starting_without_an_id_is_also_forgotten(self):
        """履歴を残さずに始めた場合も同じ。閉じたら入口からになる。"""
        response = self.client.post(reverse('fe:index'), {'action': 'temporary'})
        self.assertIn(LEARNER_COOKIE, response.cookies)
        self.assertEqual(self.client.get(reverse('fe:quiz')).status_code, 200)
        del self.client.cookies[LEARNER_COOKIE]
        self.assertRedirects(self.client.get(reverse('fe:quiz')), reverse('fe:index'))

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
        for name in ('fe:manage_list', 'fe:manage_upload'):
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
        """取り込みは JSON ファイルだけなので、テストもファイルで投げる。"""
        return {'questions_a_file': SimpleUploadedFile(
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

    def _json_upload(self, records, field='questions_a_file'):
        return {field: SimpleUploadedFile(
            'q.json', json.dumps(records, ensure_ascii=False).encode('utf-8'),
            content_type='application/json',
        )}

    def _upload_with_notes(self, n=3, codes=(9, 11), topic=None):
        return {
            **self._json_upload({'questions': json.loads(self._payload(n))}),
            **self._json_upload(
                {'notes': build_notes(codes=codes, topic=topic)}, 'notes_a_file'),
        }

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
        payload = json.dumps({'notes': notes}, ensure_ascii=False).encode('utf-8')
        # 小分類ぶんの解説を厚く書いた実運用の大きさを想定する。
        self.assertGreater(len(payload), 2 * 1024 * 1024, '2MB を超える前提のテスト')

        response = self.client.post(reverse('fe:manage_upload'), {
            **self._json_upload({'questions': records}),
            'notes_a_file': SimpleUploadedFile('big.json', payload,
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
            Question.objects.filter(template__isnull=True, subject='A').count(), 2,
            '問題集は共有なので、取り込むと全員の問題集が入れ替わる',
        )

    def test_broken_json_is_rejected_without_touching_anything(self):
        self.client.post(reverse('fe:manage_upload'), self._upload(2))
        before = list(Question.objects.all().values_list('id', flat=True))
        response = self.client.post(reverse('fe:manage_upload'), {'questions_a_file': SimpleUploadedFile(
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

    def test_export_is_not_offered(self):
        """正は手元の JSON なので、アプリから書き出す道は置かない。"""
        response = self.client.get(reverse('fe:manage_list'))
        self.assertNotContains(response, '書き出す')
        self.assertNotContains(response, 'manage/export')

    def test_choice_labels_cover_a_long_answer_group(self):
        """選択肢が多くても記号が割り当たること。"""
        self.client.post(reverse('fe:manage_upload'), self._json_upload([{
            'category': 103, 'subject': 'B', 'stem': 'x',
            'choices': ['選択肢{}'.format(i) for i in range(9)], 'answer': 8,
            'explanation': '解説',
        }], 'questions_b_file'))
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
        kept = Question.objects.filter(template__isnull=True).first()
        total = Question.objects.filter(template__isnull=True, subject=kept.subject).count()
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
            'questions_a_file': SimpleUploadedFile('q.json', questions.encode('utf-8')),
            'notes_a_file': SimpleUploadedFile('n.json', notes.encode('utf-8')),
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
            'notes_a_file': SimpleUploadedFile('n.json', notes.encode('utf-8')),
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(LearningNote.objects.count(), 2)
        self.assertEqual(Question.objects.count(), before, '問題はそのまま')

    def test_upload_needs_at_least_one_file(self):
        """どの欄も選ばずに送ったら、理由を添えて止める。"""
        admin = make_learner('nofile', is_admin=True)
        enter(self.client, admin)
        response = self.client.post(reverse('fe:manage_upload'), {})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '取り込むファイルを1つ以上選んでください')

    def test_a_file_in_the_wrong_slot_is_rejected(self):
        """欄と中身の側・科目が合わないファイルは、取り込む前に止める。"""
        build_questions(per_category=1)
        before = Question.objects.count()
        admin = make_learner('wrongslot', is_admin=True)
        enter(self.client, admin)
        a_questions = json.dumps({'questions': [
            {'category': 11, 'subject': 'A', 'stem': 'x', 'choices': ['a', 'b'], 'answer': 0},
        ]}, ensure_ascii=False).encode('utf-8')
        a_notes = json.dumps({'notes': build_notes()}, ensure_ascii=False).encode('utf-8')
        both = json.dumps({
            'questions': json.loads(a_questions)['questions'], 'notes': build_notes(),
        }, ensure_ascii=False).encode('utf-8')
        cases = {
            '科目Aの問題を科目Bの欄に': ('questions_b_file', a_questions, '科目Bではありません'),
            '科目Aの解説を科目Bの欄に': ('notes_b_file', a_notes, '科目Bではありません'),
            '解説を問題の欄に': ('questions_a_file', a_notes, '技術解説が入っています'),
            '問題を解説の欄に': ('notes_a_file', a_questions, '問題が入っています'),
            '両方入りのファイル': ('questions_a_file', both, '技術解説が入っています'),
        }
        for label, (field, payload, message) in cases.items():
            with self.subTest(label=label):
                response = self.client.post(reverse('fe:manage_upload'), {
                    field: SimpleUploadedFile('x.json', payload),
                })
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, message)
                self.assertEqual(Question.objects.count(), before, '何も変わらない')

    def test_one_subject_leaves_the_other_subject_alone(self):
        """科目Aのファイルだけを取り込んでも、科目Bの問題と解説は消えない。

        問題も解説も科目ごとに別のファイルにしているので、ファイルに出てこない
        科目まで同期の対象にすると、片方を取り込むたびにもう片方が全部消える。
        """
        build_questions(per_category=1)
        b_note = Category.objects.filter(subject='B').first()
        sync(validate({'questions': None, 'notes': build_notes() + [{
            'category': b_note.code, 'topic': b_note.name, 'title': '科目Bの解説', 'body': '本文',
        }]}))
        b_questions = Question.objects.filter(template__isnull=True, subject='B').count()
        self.assertTrue(b_questions)

        result = sync(validate({'questions': [
            {'category': 11, 'subject': 'A', 'stem': '科目Aだけ', 'choices': ['a', 'b'], 'answer': 0},
        ], 'notes': build_notes(codes=(9,))}))

        self.assertEqual(result['questions']['subjects'], ['A'])
        self.assertEqual(result['notes']['subjects'], ['A'])
        self.assertEqual(
            Question.objects.filter(template__isnull=True, subject='B').count(), b_questions,
            '科目Bの問題はそのまま',
        )
        self.assertEqual(
            Question.objects.filter(template__isnull=True, subject='A').count(), 1,
            '科目Aはファイルの内容に揃う',
        )
        self.assertTrue(LearningNote.objects.filter(title='科目Bの解説').exists(), '科目Bの解説も残る')
        self.assertEqual(LearningNote.objects.filter(category__subject='A').count(), 1)

    def test_four_files_can_be_uploaded_at_once(self):
        """4つの欄をまとめて選んで、1回で取り込める。"""
        admin = make_learner('fourfiles', is_admin=True)
        enter(self.client, admin)
        b = Category.objects.filter(subject='B').first()
        files = {
            'questions_a_file': {'questions': [
                {'category': 11, 'subject': 'A', 'stem': 'A問', 'choices': ['a', 'b'], 'answer': 0}]},
            'questions_b_file': {'questions': [
                {'category': b.code, 'subject': 'B', 'stem': 'B問', 'choices': ['a', 'b'], 'answer': 1}]},
            'notes_a_file': {'notes': build_notes()},
            'notes_b_file': {'notes': [
                {'category': b.code, 'topic': b.name, 'title': 'B解説', 'body': '本文'}]},
        }
        response = self.client.post(reverse('fe:manage_upload'), {
            field: SimpleUploadedFile(field + '.json', json.dumps(data, ensure_ascii=False).encode('utf-8'))
            for field, data in files.items()
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Question.objects.filter(template__isnull=True).count(), 2)
        self.assertEqual(LearningNote.objects.count(), 3)

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
        kept = Question.objects.filter(template__isnull=True).first()
        # 同期はファイルに出てくる科目の中だけなので、数えるのも同じ科目
        total = Question.objects.filter(template__isnull=True, subject=kept.subject).count()
        one = [{
            'category': kept.category.code, 'subject': kept.subject, 'stem': kept.stem,
            'choices': kept.choices, 'answer': kept.answer_index,
            'source': kept.source, 'topic': kept.topic,
        }]
        counts = preview(validate({'questions': one, 'notes': None}))
        self.assertEqual(counts['questions']['removed'], total - 1)
        self.assertTrue(needs_confirmation(counts))

        # 3割以下なら止めない
        few = Question.objects.filter(template__isnull=True, subject=kept.subject)[:total - 1]
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
            'questions_a_file': SimpleUploadedFile('q.json', payload.encode('utf-8')),
        })
        self.assertEqual(response.status_code, 200, '遷移せず確認画面を出す')
        self.assertContains(response, '削除が多いので、一度止めました')
        self.assertEqual(Question.objects.count(), before, 'まだ消えていない')

        # 確認したうえで実行すると、取り込まれる
        response = self.client.post(reverse('fe:manage_upload'), {
            'confirm': '1', 'payload': payload,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Question.objects.filter(template__isnull=True, subject=kept.subject).count(), 1)

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
            ('令和6年度 科目A 問3', 'IPA公開問題'),
            ('令和3年度 春期 午前 問2', 'IPA公開問題'),
            ('サンプル問題 科目A 問12', 'IPAサンプル問題'),
            ('シラバス Ver.8.0 中分類1 応用数学（3）数値解析', '自作'),
            ('試験要綱 科目B', '自作'),
            ('自作（令和6年度秋 午前 問5 を基に作成）', '自作'),
            ('', ''),
        ]
        for source, label in cases:
            self.assertEqual(Question(source=source).origin_label, label)

    def test_source_text_does_not_repeat_the_label(self):
        """ラベルと同じ言葉で始まる source は、その言葉を落として出す。"""
        cases = [
            ('サンプル問題 科目A 問12', '科目A 問12'),
            ('令和6年度 科目A 問3', '令和6年度 科目A 問3'),
            ('シラバス Ver.8.0 中分類1 応用数学（3）数値解析',
             'シラバス Ver.8.0 中分類1 応用数学（3）数値解析'),
        ]
        for source, text in cases:
            self.assertEqual(Question(source=source).source_text, text)

        template = QuestionTemplate.objects.first()
        generated = Question(
            template=template, source='テンプレート「{}」による自動生成'.format(template.title),
        )
        self.assertEqual(generated.source_text, '計算問題：{}'.format(template.title))

    def test_learning_quiz_shows_the_source_with_the_question(self):
        """学習モードでは、出典と出題元の区別を選択肢の下に出す。解説の側には出さない。"""
        self._prepare()
        self.client.post(reverse('fe:learn_start'), {'minutes': 5})
        round_ = LearningRound.objects.get(learner=self.learner)
        self.client.post(reverse('fe:learn_read', args=[round_.pk]))

        item = round_.items.get(order=0)
        question = item.question
        question.source = 'シラバス 中分類1 テスト用の項目'
        question.explanation = 'テスト用の解説'
        question.save()

        before = self.client.get(reverse('fe:learn_quiz', args=[round_.pk]))
        self.assertContains(before, '</span>シラバス 中分類1 テスト用の項目', count=1)
        body = before.content.decode()
        last_choice = body.rindex(html.escape(question.choices[-1]))
        self.assertLess(last_choice, body.index('テスト用の項目'))
        self.assertContains(before, '自作')

        after = self.client.post(
            reverse('fe:learn_quiz', args=[round_.pk]),
            {'choice': question.answer_index},
        )
        self.assertContains(after, '</span>シラバス 中分類1 テスト用の項目', count=1)
        body = after.content.decode()
        last_choice = body.rindex(html.escape(question.choices[-1]))
        self.assertLess(last_choice, body.index('テスト用の項目'))
        self.assertLess(body.index('テスト用の項目'), body.index('テスト用の解説'))

    def test_quiz_shows_the_source_with_the_question(self):
        """1問1答でも、出典は答える前から選択肢の下に出す。解説の側には出さない。"""
        self._prepare()
        # その場で作られる計算問題は出典が別なので、この確認では出さない
        QuestionTemplate.objects.update(is_active=False)
        Question.objects.update(source='シラバス 中分類1 テスト用の項目', explanation='テスト用の解説')

        before = self.client.get(reverse('fe:quiz'))
        question = before.context['question']
        self.assertContains(before, '</span>シラバス 中分類1 テスト用の項目', count=1)
        body = before.content.decode()
        last_choice = body.rindex(html.escape(question.choices[-1]))
        self.assertLess(last_choice, body.index('テスト用の項目'))

        after = self.client.post(reverse('fe:quiz'), {
            'question_id': question.id, 'choice': question.answer_index,
        })
        self.assertContains(after, '</span>シラバス 中分類1 テスト用の項目', count=1)
        body = after.content.decode()
        last_choice = body.rindex(html.escape(question.choices[-1]))
        self.assertLess(last_choice, body.index('テスト用の項目'))
        self.assertLess(body.index('テスト用の項目'), body.index('テスト用の解説'))

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
                response, '</span>シラバス 中分類1 テスト用の項目{}'.format(order)
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

    def test_notes_of_one_topic_are_read_together(self):
        """同じ小分類に解説が2本あれば、1行にまとめ、続けて読んでから解く。

        出題は小分類ごとなので、1本だけ読ませると読んでいない内容の問題が混ざる。
        """
        self._prepare(topic='データベース方式')
        category = Category.objects.get(code=9)
        first = LearningNote.objects.get(category=category)
        second = LearningNote.objects.create(
            category=category, topic='データベース方式',
            title='二本目の解説', body='二本目の本文', source='シラバス',
        )
        Question.objects.filter(category=category).update(topic='データベース方式')

        menu = self.client.get(reverse('fe:learn_start'))
        group = menu.context['groups'][0]
        self.assertEqual(group['note_count'], 2)
        self.assertEqual(len(group['notes']), 1)
        self.assertEqual(group['notes'][0]['notes'], [first, second])

        # 2本目を指定して始めても、読むのは2本とも。学習回数は小分類で数える。
        self.client.post(reverse('fe:learn_start'), {'minutes': 5, 'note': second.pk})
        round_ = LearningRound.objects.get(learner=self.learner)
        page = self.client.get(reverse('fe:learn_read', args=[round_.pk]))
        self.assertEqual(page.context['notes'], [first, second])
        body = page.content.decode()
        self.assertLess(body.index(first.title), body.index('二本目の解説'))
        self.assertContains(page, '二本目の本文')

        menu = self.client.get(reverse('fe:learn_start'))
        self.assertEqual(menu.context['groups'][0]['notes'][0]['rounds'], 1)

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

    def _finish_round(self, note):
        """その解説のラウンドを、最後まで解いて終わらせる。"""
        self.client.post(reverse('fe:learn_start'), {'minutes': 5, 'note': note.pk})
        round_ = LearningRound.objects.filter(learner=self.learner).first()
        self.client.post(reverse('fe:learn_read', args=[round_.pk]))
        for _ in range(round_.total):
            item = round_.items.get(order=round_.position)
            self.client.post(
                reverse('fe:learn_quiz', args=[round_.pk]),
                {'choice': item.question.answer_index},
            )
            self.client.post(reverse('fe:learn_next', args=[round_.pk]))
            round_.refresh_from_db()
        return round_

    @staticmethod
    def _opened_codes(response):
        """学習モードの一覧で開いている中分類の番号。"""
        return sorted(category.code for category in response.context['opened'])

    def test_the_dashboard_category_is_opened(self):
        """まだ学習していなければ、ダッシュボードで指定した分野を開く。選んだ状態にはしない。"""
        self._prepare()
        self._set_category(Category.objects.get(code=9))

        response = self.client.get(reverse('fe:learn_start'))
        self.assertEqual(self._opened_codes(response), [9])
        self.assertContains(response, 'fe-group border-bottom" open', count=1)
        self.assertEqual(self._checked_notes(response), [])

    def test_the_dashboard_category_follows_a_new_choice(self):
        """解き終えたあとで分野を指定し直せば、指定し直した分野も開く。

        前は直前に解き終えた中分類だけを開いていたので、一度コンピュータ構成要素を
        解き終えると、ダッシュボードで分野をどう選び直しても、学習モードのトップは
        そこだけが開いたまま変わらなかった。
        """
        self._prepare()
        hardware = Category.objects.get(code=3)
        note = LearningNote.objects.create(
            category=hardware, topic=hardware.name, title='構成要素の解説', body='本文',
        )
        security = Category.objects.get(code=11)
        LearningNote.objects.create(
            category=security, topic=security.name, title='セキュリティの解説', body='本文',
        )
        self._finish_round(note)
        response = self.client.get(reverse('fe:learn_start'))
        self.assertEqual(self._opened_codes(response), [3])

        for code in (9, 11, 9):
            self._set_category(Category.objects.get(code=code))
            response = self.client.get(reverse('fe:learn_start'))
            self.assertEqual(self._opened_codes(response), [3, code])
            self.assertContains(response, 'fe-group border-bottom" open', count=2)

        # 指定した分野を解き終えたなら、同じ中分類なので1つだけ開く
        self._set_category(hardware)
        response = self.client.get(reverse('fe:learn_start'))
        self.assertEqual(self._opened_codes(response), [3])
        self.assertContains(response, 'fe-group border-bottom" open', count=1)

    def test_the_last_studied_category_is_opened(self):
        """指定とは別の分野を解き終えたら、指定した分野と、解き終えた中分類の両方を開く。

        学習回数は小分類の行に出るので、閉じたままだと学習したことが見えない。
        指定した分野も閉じないので、ダッシュボードの指定と一覧の開く先がずれない。
        """
        self._prepare()
        security = Category.objects.get(code=11)
        note = LearningNote.objects.create(
            category=security, topic=security.name, title='セキュリティの解説', body='本文',
        )
        self._set_category(Category.objects.get(code=9))
        self._finish_round(note)

        response = self.client.get(reverse('fe:learn_start'))
        self.assertEqual(self._opened_codes(response), [9, 11])
        self.assertContains(response, 'fe-group border-bottom" open', count=2)
        self.assertEqual(self._checked_notes(response), [])

        # 途中のラウンドは数えない。終わったものだけが「直前に学習した」になる。
        self.client.post(
            reverse('fe:learn_start'),
            {'minutes': 5, 'note': LearningNote.objects.get(category__code=9).pk},
        )
        self._set_category(None)
        response = self.client.get(reverse('fe:learn_start'))
        self.assertEqual(self._opened_codes(response), [11])

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
        """まだ学習していなければ、どの中分類も開かず、何も選ばない。"""
        self._prepare()
        response = self.client.get(reverse('fe:learn_start'))
        self.assertEqual(response.context['opened'], [])
        self.assertEqual(self._checked_notes(response), [])

    def test_a_category_of_the_other_subject_is_not_used(self):
        """科目を切り替えたあとの指定は引き継がない。その科目には無い分類のため。"""
        self._prepare()
        self._set_category(Category.objects.filter(subject='B').first())
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


class ManageNeedsStaffTests(TestCase):
    """問題の管理は、管理用の ID に加えて、スタッフ権限のあるアカウントが要る。"""

    PAGES = ('fe:manage_list', 'fe:manage_upload')

    @classmethod
    def setUpTestData(cls):
        seed_masters()
        cls.admin_id = make_learner('gatekeeper', is_admin=True)

    def _enter_as(self, username, is_staff):
        account = CustomUser.objects.create_user(
            username=username, password='pass12345', is_staff=is_staff,
        )
        self.client.force_login(account)
        put_learner(self.client, self.admin_id.pk)

    def test_an_ordinary_account_cannot_manage_even_with_the_admin_id(self):
        """誰でも作れるアカウントでは、管理用の ID を知っていても開けない。"""
        self._enter_as('visitor', is_staff=False)
        for name in self.PAGES:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)
        self.assertEqual(
            self.client.post(reverse('fe:manage_upload'), {}).status_code, 403
        )

    def test_a_staff_account_can_manage_with_the_admin_id(self):
        self._enter_as('keeper', is_staff=True)
        for name in ('fe:manage_list', 'fe:manage_upload'):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)



def png_uri(width=1, extra=b''):
    """テスト用の小さな PNG を data URI にする。extra で大きさを水増しできる。"""
    import base64, struct, zlib

    def chunk(kind, data):
        body = kind + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body))

    raw = b''.join(b'\x00' + b'\xff' * width for _ in range(1))
    png = (b'\x89PNG\r\n\x1a\n'
           + chunk(b'IHDR', struct.pack('>IIBBBBB', width, 1, 8, 0, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(raw))
           + (chunk(b'tEXt', extra) if extra else b'')
           + chunk(b'IEND', b''))
    return 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')


class FigureTests(TestCase):
    """図のある問題。公開問題の図を PNG の data URI で持ち、画面に出す。"""

    @classmethod
    def setUpTestData(cls):
        seed_masters()

    def _record(self, **extra):
        record = {
            'category': 2, 'subject': 'A', 'topic': 'データ構造',
            'stem': '図の木構造は2分探索木である。', 'choices': ['ア案', 'イ案', 'ウ案', 'エ案'],
            'answer': 1, 'source': '令和7年度 科目A 問3',
        }
        record.update(extra)
        return record

    def _sync(self, *records):
        return sync(validate({'questions': list(records), 'notes': None}))

    def test_figures_are_imported_and_kept(self):
        figure = {'src': png_uri(), 'alt': '根が a の2分木'}
        self._sync(self._record(figures=[figure], choice_figures=[png_uri(), None, png_uri(2), None]))
        question = Question.objects.get(template__isnull=True)
        self.assertEqual(question.figures, [figure])
        self.assertIsNone(question.choice_figure(1), '図の無い選択肢は null のまま')
        self.assertEqual(question.answer_figure, None)
        self.assertEqual(question.choice_rows()[2][3], png_uri(2))

    def test_questions_without_figures_still_import(self):
        self._sync(self._record())
        question = Question.objects.get(template__isnull=True)
        self.assertEqual(question.figures, [])
        self.assertIsNone(question.choice_figures)

    def test_changing_a_figure_is_an_update_and_keeps_the_history(self):
        """図は鍵に入らない。図を差し替えても同じ問題のままで、解答履歴は切れない。"""
        self._sync(self._record(figures=[{'src': png_uri(), 'alt': '旧'}]))
        question = Question.objects.get(template__isnull=True)
        answer(make_learner('figure'), question, correct=True)

        result = self._sync(self._record(figures=[{'src': png_uri(3), 'alt': '新'}]))
        self.assertEqual(result['questions']['updated'], 1)
        self.assertEqual(result['questions']['removed'], 0)
        self.assertTrue(Attempt.objects.filter(question=question).exists())
        question.refresh_from_db()
        self.assertEqual(question.figures[0]['alt'], '新')

    def test_broken_figures_are_rejected(self):
        ok = {'src': png_uri(), 'alt': '図'}
        jpeg = 'data:image/jpeg;base64,' + png_uri().split(',', 1)[1]
        not_png = 'data:image/png;base64,' + __import__('base64').b64encode(b'GIF89a....').decode()
        cases = {
            '配列でない': {'figures': ok},
            'src が画像でない': {'figures': [{'src': 'javascript:alert(1)', 'alt': '図'}]},
            'PNG 以外の形式': {'figures': [{'src': jpeg, 'alt': '図'}]},
            '中身が PNG でない': {'figures': [{'src': not_png, 'alt': '図'}]},
            'base64 が壊れている': {'figures': [{'src': 'data:image/png;base64,@@@', 'alt': '図'}]},
            '大きすぎる': {'figures': [{'src': png_uri(extra=b'x' * (301 * 1024)), 'alt': '図'}]},
            'alt が空': {'figures': [{'src': png_uri(), 'alt': ' '}]},
            '余計なキー': {'figures': [dict(ok, title='x')]},
            '選択肢の図の数が違う': {'choice_figures': [png_uri()]},
            '選択肢の図が画像でない': {'choice_figures': ['x', None, None, None]},
        }
        for label, extra in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(ImportError_):
                    validate({'questions': [self._record(**extra)], 'notes': None})

    def test_source_longer_than_the_column_is_rejected(self):
        """SQLite は長さを見ないので、検査しないと手元で通って本番で落ちる。"""
        long = 'シラバス Ver.8.0 中分類2 データ構造' + '（1）項目' * 60
        with self.assertRaises(ImportError_):
            validate({'questions': [self._record(source=long)], 'notes': None})
        with self.assertRaises(ImportError_):
            validate({'questions': None, 'notes': [
                {'category': 2, 'topic': 'データ構造', 'title': 't', 'body': 'b', 'source': long}]})
        # 項目まで書いた転記の出典は通る
        validate({'questions': [self._record(
            source='令和7年度 科目A 問3（中分類2 データ構造（2）木構造）')], 'notes': None})

    def test_quiz_shows_the_figures(self):
        self._sync(self._record(
            figures=[{'src': png_uri(), 'alt': '根が a の2分木'}],
            choice_figures=[png_uri(4), None, None, None],
        ))
        # テンプレートが生成する計算問題が選ばれないよう、その問題だけにする
        Question.objects.filter(template__isnull=False).delete()
        enter(self.client, make_learner('seer'))
        response = self.client.get(reverse('fe:quiz'))
        self.assertContains(response, 'src="{}"'.format(png_uri()))
        self.assertContains(response, 'alt="根が a の2分木"')
        self.assertContains(response, 'src="{}"'.format(png_uri(4)))

        question = response.context['question']
        response = self.client.post(reverse('fe:quiz'), {
            'question_id': question.id, 'choice': question.answer_index,
        })
        self.assertContains(response, 'src="{}"'.format(png_uri()), msg_prefix='答えた後も図を出す')
        self.assertContains(response, 'src="{}"'.format(png_uri(4)))


class NoteAudioTests(TestCase):
    """技術解説の読み上げ。音声は解説ごとに一度だけ作り、読んでいる間は時間を止める。"""

    @classmethod
    def setUpTestData(cls):
        seed_masters()
        cls.learner = make_learner('listener')

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        override = override_settings(FE_AUDIO_ROOT=folder.name)
        override.enable()
        self.addCleanup(override.disable)
        self.root = Path(folder.name)
        enter(self.client, self.learner)

    def _start_round(self):
        build_questions(per_category=12)
        LearningNote.objects.all().delete()
        for note in build_notes(codes=(9,)):
            LearningNote.objects.create(
                category=Category.objects.get(code=note['category']),
                topic=note['topic'], title=note['title'],
                body=note['body'], source=note['source'],
            )
        self.client.post(reverse('fe:learn_start'), {'minutes': 5})
        return LearningRound.objects.get(learner=self.learner)

    def _put_audio(self, note, data=b'0123456789'):
        (self.root / speech.audio_name(note)).write_bytes(data)

    def test_spoken_text_reads_symbols_as_words(self):
        self.assertEqual(speech.spoken_text('n×(n−1)÷2'), 'nかける(nひく1)わる2')
        self.assertEqual(speech.spoken_text('相関係数は−1から1'), '相関係数はマイナス1から1')
        self.assertEqual(speech.spoken_text('30＋22、P＝10'), '30たす22、Pイコール10')
        self.assertEqual(speech.spoken_text('JavaやC++'), 'Javaやシープラスプラス')

    def test_long_text_is_split_at_paragraphs(self):
        text = '\n\n'.join(['あ' * 500, 'い' * 500, 'う' * 500])
        parts = speech.chunks(text)
        self.assertEqual(parts, ['あ' * 500 + '\n' + 'い' * 500, 'う' * 500])

    def test_name_changes_only_when_the_body_changes(self):
        note = LearningNote(title='見出し', body='本文')
        same = LearningNote(title='別の見出し', body='本文')
        edited = LearningNote(title='見出し', body='直した本文')
        self.assertEqual(speech.audio_name(note), speech.audio_name(same))
        self.assertNotEqual(speech.audio_name(note), speech.audio_name(edited))
        self.assertRegex(speech.audio_name(note), speech.NAME_PATTERN)

    def test_player_appears_only_when_the_audio_exists(self):
        round_ = self._start_round()
        url = reverse('fe:learn_read', args=[round_.pk])
        self.assertNotContains(self.client.get(url), '<audio')

        self._put_audio(round_.note)
        self.assertContains(
            self.client.get(url),
            reverse('fe:note_audio', args=[speech.audio_name(round_.note)]),
        )

    def test_audio_answers_range_requests(self):
        """iPhone の Safari は Range 要求で取りに来るので、206 で一部だけ返す。"""
        round_ = self._start_round()
        self._put_audio(round_.note)
        url = reverse('fe:note_audio', args=[speech.audio_name(round_.note)])

        whole = self.client.get(url)
        self.assertEqual(whole.status_code, 200)
        self.assertEqual(b''.join(whole.streaming_content), b'0123456789')
        self.assertEqual(whole['Accept-Ranges'], 'bytes')

        part = self.client.get(url, HTTP_RANGE='bytes=0-1')
        self.assertEqual(part.status_code, 206)
        self.assertEqual(part.content, b'01')
        self.assertEqual(part['Content-Range'], 'bytes 0-1/10')

        tail = self.client.get(url, HTTP_RANGE='bytes=-3')
        self.assertEqual(tail.content, b'789')
        self.assertEqual(self.client.get(url, HTTP_RANGE='bytes=4-').content, b'456789')
        self.assertEqual(self.client.get(url, HTTP_RANGE='bytes=20-').status_code, 416)

    def test_audio_rejects_other_names_and_strangers(self):
        self.assertEqual(
            self.client.get(reverse('fe:note_audio', args=['db.sqlite3'])).status_code, 404
        )
        self.assertEqual(
            self.client.get(reverse('fe:note_audio', args=['0' * 20 + '.mp3'])).status_code, 404
        )
        round_ = self._start_round()
        self._put_audio(round_.note)
        stranger = self.client_class()
        response = stranger.get(reverse('fe:note_audio', args=[speech.audio_name(round_.note)]))
        self.assertRedirects(response, reverse('fe:index'), fetch_redirect_response=False)

    def test_time_spent_listening_is_not_counted(self):
        round_ = self._start_round()
        now = timezone.now()
        LearningRound.objects.filter(pk=round_.pk).update(
            started_at=now - timezone.timedelta(seconds=200),
            paused_at=now - timezone.timedelta(seconds=150),
        )
        round_.refresh_from_db()
        # 200秒前に始め、150秒前から止めている。進んだのは50秒だけ
        self.assertEqual(round_.remaining_seconds(now), 250)
        round_.resume(now)
        self.assertEqual(round_.paused_seconds, 150)
        self.assertEqual(round_.remaining_seconds(now), 250)

    def test_timer_endpoint_pauses_and_resumes(self):
        round_ = self._start_round()
        url = reverse('fe:learn_timer', args=[round_.pk])
        response = self.client.post(url, {'state': 'pause'})
        self.assertIn('remaining', response.json())
        round_.refresh_from_db()
        self.assertIsNotNone(round_.paused_at)

        self.client.post(url, {'state': 'resume'})
        round_.refresh_from_db()
        self.assertIsNone(round_.paused_at)

    def test_reloading_while_paused_starts_the_timer_again(self):
        """開き直すと読み上げは止まっているので、止めたままにしない。"""
        round_ = self._start_round()
        LearningRound.objects.filter(pk=round_.pk).update(
            paused_at=timezone.now() - timezone.timedelta(seconds=30),
        )
        self.client.get(reverse('fe:learn_read', args=[round_.pk]))
        round_.refresh_from_db()
        self.assertIsNone(round_.paused_at)
        self.assertGreaterEqual(round_.paused_seconds, 30)

    def test_command_makes_only_missing_audio(self):
        round_ = self._start_round()
        notes = list(LearningNote.objects.all())
        self._put_audio(notes[0])
        calls = []

        def fake(text, key, region):
            calls.append(text)
            return b'mp3'

        with mock.patch.object(speech, 'synthesize', fake), \
                mock.patch.object(speech, 'REQUEST_INTERVAL', 0), \
                mock.patch.dict('os.environ', {'AZURE_SPEECH_KEY': 'k', 'AZURE_SPEECH_REGION': 'r'}):
            call_command('make_note_audio', stdout=io.StringIO())
        self.assertEqual(len(calls), len(notes) - 1)
        for note in notes:
            self.assertTrue(speech.has_audio(note))

        (self.root / ('f' * 20 + '.mp3')).write_bytes(b'old')
        call_command('make_note_audio', '--prune', stdout=io.StringIO())
        self.assertEqual(
            sorted(p.name for p in self.root.iterdir()),
            sorted(speech.audio_name(n) for n in notes),
        )

    def test_only_subject_a_notes_are_read_aloud(self):
        """科目Bの解説には音声を作らず、ファイルがあっても再生ボタンを出さない。"""
        round_ = self._start_round()
        b_category = Category.objects.filter(subject=Question.SUBJECT_B).first()
        b_note = LearningNote.objects.create(
            category=b_category, topic='擬似言語', title='科目Bの解説', body='科目Bの本文',
        )
        self.assertFalse(speech.speaks(b_note))
        self._put_audio(b_note)
        self.assertFalse(speech.has_audio(b_note))

        with mock.patch.object(speech, 'synthesize', return_value=b'mp3') as fake, \
                mock.patch.object(speech, 'REQUEST_INTERVAL', 0), \
                mock.patch.dict('os.environ', {'AZURE_SPEECH_KEY': 'k', 'AZURE_SPEECH_REGION': 'r'}):
            call_command('make_note_audio', stdout=io.StringIO())
        texts = [c.args[0] for c in fake.call_args_list]
        self.assertFalse(any('科目Bの本文' in t for t in texts))
        self.assertTrue(speech.has_audio(round_.note))

        LearningRound.objects.filter(pk=round_.pk).update(note=b_note)
        page = self.client.get(reverse('fe:learn_read', args=[round_.pk]))
        self.assertNotContains(page, '<audio')


class MailTests(TestCase):
    """問題メール。確認のメールを経た登録、メールのリンクからだけの解除、毎日の送り方。"""

    @classmethod
    def setUpTestData(cls):
        seed_masters()

    def setUp(self):
        from django.core import mail as outbox
        self.outbox = outbox
        self.learner = make_learner('mailer')
        enter(self.client, self.learner)

    def _sync(self, counts):
        """{(中分類番号, 小分類): 問数} の問題を作る。"""
        records = []
        for (code, topic), n in counts.items():
            category = Category.objects.get(code=code)
            for i in range(n):
                records.append({
                    'category': code, 'subject': category.subject, 'topic': topic,
                    'stem': '{}の問題{}'.format(topic, i),
                    'choices': ['選択肢{}-{}'.format(j, i) for j in range(6 if code > 100 else 4)],
                    'answer': 1, 'explanation': '{}の解説{}'.format(topic, i), 'source': '自作',
                })
        return sync(validate({'questions': records, 'notes': None}))

    def _subscribe(self, email='me@example.com', **fields):
        from .models import MailSubscription
        subscription, _ = MailSubscription.objects.get_or_create(learner=self.learner)
        subscription.email = email
        for key, value in fields.items():
            setattr(subscription, key, value)
        subscription.save()
        return subscription

    def _link(self, message):
        return re.search(r'http://\S+/fe/mail/\S+/', message.body).group(0).split('8000', 1)[1]

    # ---- 登録と解除 ----

    def test_address_is_saved_only_after_pressing_receive(self):
        from .models import MailSubscription
        self._sync({(11, '情報セキュリティ'): 3})
        response = self.client.post(reverse('fe:mail_settings'), {
            'email': 'me@example.com', 'marathon': 'on',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self.outbox.outbox), 1)
        self.assertEqual(self.outbox.outbox[0].to, ['me@example.com'])
        subscription = MailSubscription.objects.get(learner=self.learner)
        self.assertEqual(subscription.email, '')

        link = self._link(self.outbox.outbox[0])
        self.client.logout()  # 別の端末で開いても使える
        self.assertContains(self.client.get(link), '受け取る')
        subscription.refresh_from_db()
        self.assertEqual(subscription.email, '', '開いただけでは登録しない')
        self.assertContains(self.client.post(link), '登録しました')
        subscription.refresh_from_db()
        self.assertEqual(subscription.email, 'me@example.com')
        self.assertContains(self.client.post(link), 'このリンクは使えません')

    def test_old_confirm_link_dies_when_another_address_is_requested(self):
        self.client.post(reverse('fe:mail_settings'), {'email': 'a@example.com', 'marathon': 'on'})
        old = self._link(self.outbox.outbox[0])
        with override_settings(FE_MAIL_LINK_INTERVAL=0):
            self.client.post(reverse('fe:mail_settings'), {'email': 'b@example.com', 'marathon': 'on'})
        self.assertContains(self.client.post(old), 'このリンクは使えません')
        self.assertContains(self.client.post(self._link(self.outbox.outbox[1])), '登録しました')

    def test_confirmation_mails_are_throttled(self):
        self.client.post(reverse('fe:mail_settings'), {'email': 'a@example.com', 'marathon': 'on'})
        self.client.post(reverse('fe:mail_settings'), {'email': 'b@example.com', 'marathon': 'on'})
        self.assertEqual(len(self.outbox.outbox), 1)

    def test_registered_address_cannot_be_changed_from_the_screen(self):
        self._subscribe(marathon=True)
        self._sync({(11, '情報セキュリティ'): 3})
        self.client.post(reverse('fe:mail_settings'), {
            'email': 'attacker@example.com', 'training': 'on', 'topics': ['11|情報セキュリティ'],
        })
        subscription = self.learner.mail_subscription
        subscription.refresh_from_db()
        self.assertEqual(subscription.email, 'me@example.com')
        self.assertFalse(subscription.marathon)
        self.assertTrue(subscription.training)
        self.assertEqual(subscription.training_topics, [[11, '情報セキュリティ']])
        self.assertEqual(len(self.outbox.outbox), 0)

    def test_training_needs_a_topic(self):
        self.client.post(reverse('fe:mail_settings'), {'email': 'a@example.com', 'training': 'on'})
        self.assertEqual(len(self.outbox.outbox), 0)

    def test_stop_only_from_the_mailed_link(self):
        subscription = self._subscribe(marathon=True)
        self.client.post(reverse('fe:mail_settings'), {'action': 'send_stop'})
        self.assertEqual(self.outbox.outbox[0].to, ['me@example.com'])
        link = self._link(self.outbox.outbox[0])
        self.client.get(link)
        subscription.refresh_from_db()
        self.assertEqual(subscription.email, 'me@example.com', '開いただけでは解除しない')
        self.assertContains(self.client.post(link), '配信を止めました')
        subscription.refresh_from_db()
        self.assertEqual(subscription.email, '')

    def test_addresses_are_encrypted_in_the_db(self):
        from django.db import connection
        self.client.post(reverse('fe:mail_settings'), {'email': 'secret@example.com', 'marathon': 'on'})
        with connection.cursor() as cursor:
            cursor.execute('SELECT email_enc, pending_email_enc FROM fe_mailsubscription')
            rows = cursor.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], '')
        self.assertNotIn('secret', rows[0][1])
        self.assertEqual(self.learner.mail_subscription.pending_email, 'secret@example.com')

        self.client.post(self._link(self.outbox.outbox[0]))
        with connection.cursor() as cursor:
            cursor.execute('SELECT email_enc, pending_email_enc FROM fe_mailsubscription')
            email_enc, pending_enc = cursor.fetchone()
        self.assertNotIn('secret', email_enc)
        self.assertEqual(pending_enc, '')
        self.learner.mail_subscription.refresh_from_db()
        self.assertEqual(self.learner.mail_subscription.email, 'secret@example.com')

    def test_unreadable_address_counts_as_unregistered(self):
        """SECRET_KEY を替えて読めなくなった宛先には送らない。"""
        from .mail import send_daily
        self._sync({(11, '情報セキュリティ'): 3})
        self._subscribe(marathon=True)
        with override_settings(SECRET_KEY='another-key-' + 'x' * 40, SECRET_KEY_FALLBACKS=[]):
            self.assertEqual(self.learner.mail_subscription.email, '')
            self.assertEqual(send_daily()['marathon'], 0)

    def test_pending_address_is_purged_after_the_deadline(self):
        from .mail import purge_pending
        self.client.post(reverse('fe:mail_settings'), {'email': 'late@example.com', 'marathon': 'on'})
        subscription = self.learner.mail_subscription
        link = self._link(self.outbox.outbox[0])
        sent_at = subscription.link_sent_at
        self.assertEqual(purge_pending(sent_at + timezone.timedelta(minutes=59)), 0)
        self.assertEqual(purge_pending(sent_at + timezone.timedelta(minutes=61)), 1)
        subscription.refresh_from_db()
        self.assertEqual(subscription.pending_email, '')
        self.assertContains(self.client.post(link), 'このリンクは使えません')

    def test_dashboard_and_daily_run_purge_expired_pending(self):
        from .mail import send_daily
        from .models import MailSubscription
        self.client.post(reverse('fe:mail_settings'), {'email': 'late@example.com', 'marathon': 'on'})
        MailSubscription.objects.update(link_sent_at=timezone.now() - timezone.timedelta(hours=2))
        self.assertNotContains(self.client.get(dashboard(self.learner)), '確認のメールを送ってあります')
        self.assertEqual(MailSubscription.objects.exclude(pending_email_enc='').count(), 0)

        MailSubscription.objects.update(pending_email_enc='x', link_sent_at=None)
        send_daily()
        self.assertEqual(MailSubscription.objects.exclude(pending_email_enc='').count(), 0)

    def test_temporary_use_cannot_subscribe(self):
        temporary = Learner.objects.create(code=None)
        put_learner(self.client, temporary.pk)
        response = self.client.post(reverse('fe:mail_settings'), {'email': 'a@example.com', 'marathon': 'on'})
        self.assertEqual(response.status_code, 403)

    def test_dashboard_shows_the_picker(self):
        self._sync({(11, '情報セキュリティ'): 3, (101, 'アルゴリズム'): 2})
        response = self.client.get(dashboard(self.learner))
        self.assertContains(response, 'id="mail"')
        self.assertContains(response, 'value="11|情報セキュリティ"')
        self.assertContains(response, 'value="101|アルゴリズム"')

    # ---- 送り方 ----

    def test_marathon_goes_in_order_by_topic_and_wraps(self):
        from .mail import send_daily
        self._sync({(11, '情報セキュリティ'): 12, (2, 'データ構造'): 3})
        subscription = self._subscribe(marathon=True)
        day = timezone.localdate()

        sizes = []
        for i in range(4):
            send_daily(day + timezone.timedelta(days=i))
            sizes.append(self.outbox.outbox[-1].body.count('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n問'))
        # 中分類2（3問）→ 中分類11の10問 → 残り2問 → 最初に戻って中分類2
        self.assertEqual(sizes, [3, 10, 2, 3])
        self.assertIn('データ構造', self.outbox.outbox[0].subject)
        self.assertIn('1〜3問目／全15問・2周目', self.outbox.outbox[3].subject)
        subscription.refresh_from_db()
        self.assertEqual((subscription.marathon_position, subscription.marathon_laps), (3, 1))

    def test_same_day_is_sent_once(self):
        from .mail import send_daily
        self._sync({(11, '情報セキュリティ'): 3})
        self._subscribe(marathon=True)
        self.assertEqual(send_daily()['marathon'], 1)
        self.assertEqual(send_daily()['marathon'], 0)
        self.assertEqual(len(self.outbox.outbox), 1)

    def test_training_runs_through_picked_topics_and_marathon_in_parallel(self):
        from .mail import send_daily
        self._sync({(11, '情報セキュリティ'): 12, (2, 'データ構造'): 3, (9, 'データベース'): 4})
        self._subscribe(marathon=True, training=True,
                        training_topics=[[11, '情報セキュリティ'], [9, 'データベース']])
        day = timezone.localdate()
        result = send_daily(day)
        self.assertEqual((result['marathon'], result['training']), (1, 1))
        for i in range(1, 4):
            send_daily(day + timezone.timedelta(days=i))
        subjects = [m.subject for m in self.outbox.outbox if '特訓' in m.subject]
        self.assertIn('情報セキュリティ（1〜10問目／12問）', subjects[0])
        self.assertIn('情報セキュリティ（11〜12問目／12問）', subjects[1])
        self.assertIn('データベース（1〜4問目／4問）', subjects[2])
        self.assertIn('情報セキュリティ（1〜10問目／12問）', subjects[3])

    def test_mail_has_answers_my_rates_stop_link_and_inline_figures(self):
        from .mail import send_daily
        self._sync({(11, '情報セキュリティ'): 1})
        question = Question.objects.get(template__isnull=True)
        question.figures = [{'src': png_uri(), 'alt': '構成図'}]
        question.save()
        answer(self.learner, question, True)
        self._subscribe(marathon=True)
        send_daily()
        message = self.outbox.outbox[0]
        self.assertIn('正解 イ', message.body)
        self.assertIn('情報セキュリティの解説0', message.body)
        self.assertIn('100％（1/1）', message.body)
        self.assertIn('/fe/mail/stop/', message.body)
        html_body = message.alternatives[0][0]
        self.assertIn('cid:q{}-f0@fe.mail'.format(question.id), html_body)
        images = [part for part in message.attachments if getattr(part, 'get_content_type', None)]
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]['Content-ID'], '<q{}-f0@fe.mail>'.format(question.id))
        self.assertIn('Content-Type: multipart/related', message.message().as_string())

    def test_stopped_address_gets_nothing(self):
        from .mail import send_daily
        self._sync({(11, '情報セキュリティ'): 3})
        self._subscribe(email='', marathon=True)
        self.assertEqual(send_daily()['marathon'], 0)

    # ---- 起動口 ----

    def test_task_endpoint_needs_the_token(self):
        url = reverse('fe:send_mails')
        self.client.logout()
        with override_settings(FE_MAIL_TOKEN=''):
            self.assertEqual(self.client.post(url).status_code, 503)
        with override_settings(FE_MAIL_TOKEN='secret'):
            self.assertEqual(self.client.post(url, HTTP_X_FE_MAIL_TOKEN='wrong').status_code, 403)
            response = self.client.post(url, HTTP_X_FE_MAIL_TOKEN='secret')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {'marathon': 0, 'training': 0, 'failed': 0})


@override_settings(FE_X_DRY_RUN=True, FE_X_KEEP_DAYS=30,
                   FE_SITE_URL='https://example.jp')
class XPostTests(TestCase):
    """X への投稿と、投稿ごとの解答ページ。ID なし・ログインなしで使う。"""

    @classmethod
    def setUpTestData(cls):
        seed_masters()

    def _question(self, stem='SSID の役割はどれか。', code=10, **fields):
        category = Category.objects.get(code=code)
        values = dict(
            subject=category.subject, category=category, stem=stem,
            choices=['周波数', '暗号鍵', 'ネットワークの名前', '製造番号'],
            answer_index=2, explanation='SSID はネットワークの名前。', source='自作',
        )
        values.update(fields)
        return Question.objects.create(**values)

    def _post(self, slot='morning', **fields):
        from . import x_post
        self._question(**fields)
        x_post.post_slot(slot)
        return XPost.objects.get(date=timezone.localdate(), slot=slot)

    # ---- 投稿できる問題と文面 ----

    def test_eligible_questions_are_fixed_subject_a(self):
        from . import x_post
        short = self._question()
        long = self._question(stem='長い' * 200)
        self._question(stem='科目Bの問題', code=101, choices=['ア', 'イ', 'ウ', 'エ', 'オ', 'カ'])
        template = QuestionTemplate.objects.first()
        self._question(stem='テンプレートの問題', template=template)
        self._question(stem='使わない問題', is_active=False)
        self.assertEqual(set(x_post.eligible_questions()), {short, long})

    def test_long_stem_is_cut_and_points_to_the_page(self):
        from . import x_post
        post = self._post(stem='長い問題文' * 80, source='令和7年度 科目A 問30 ／ シラバス')
        self.assertLessEqual(x_post.weight(post.text), x_post.LIMIT)
        self.assertIn('…\n（出典：令和7年度 基本情報技術者試験 科目A 問30）', post.text)
        self.assertIn('▶問題の続き・選択肢と解答はこちら', post.text)
        # 切るのは問題文だけで、URL とタグは残す。ページには問題文の全文を写す
        self.assertIn('https://example.jp/fe/x/{}/'.format(post.code), post.text)
        self.assertTrue(post.text.endswith('#基本情報技術者 #FE #IPA'))
        self.assertEqual(post.stem, '長い問題文' * 80)
        # 収まる範囲で、できるだけ長く残す
        self.assertGreater(x_post.weight(post.text), x_post.LIMIT - 12)

    def test_long_figure_question_mentions_the_figure(self):
        post = self._post(stem='長い' * 200, figures=[{'src': 'data:image/png;base64,AAAA', 'alt': '図'}])
        self.assertIn('▶問題の続き・図・選択肢と解答はこちら', post.text)

    def test_official_questions_come_about_one_in_three(self):
        from . import x_post
        official = self._question(stem='公開問題', source='令和7年度 科目A 問1')
        originals = [self._question(stem='オリジナル{}'.format(i)) for i in range(9)]
        pool = [official] + originals
        rng = random.Random(7)
        picks = [x_post.pick(pool, rng) for _ in range(900)]
        share = picks.count(official) / len(picks)
        self.assertAlmostEqual(share, 1 / 3, delta=0.05)

    def test_pick_falls_back_when_one_side_is_empty(self):
        from . import x_post
        official = self._question(stem='公開問題', source='令和7年度 科目A 問1')
        original = self._question(stem='オリジナル')
        rng = random.Random(1)
        self.assertEqual({x_post.pick([official], rng) for _ in range(20)}, {official})
        self.assertEqual({x_post.pick([original], rng) for _ in range(20)}, {original})

    def test_text_fits_the_limit_and_has_url_and_tags(self):
        from . import x_post
        post = self._post()
        self.assertLessEqual(x_post.weight(post.text), x_post.LIMIT)
        self.assertIn('https://example.jp/fe/x/{}/'.format(post.code), post.text)
        self.assertIn('#基本情報技術者 #FE #IPA', post.text)
        self.assertIn('▶選択肢と解答はこちら', post.text)
        # 選択肢と正解は投稿に入れない
        self.assertNotIn('ネットワークの名前', post.text)

    def test_figure_question_points_to_the_page(self):
        post = self._post(figures=[{'src': 'data:image/png;base64,AAAA', 'alt': '図'}])
        self.assertIn('▶図・選択肢と解答はこちら', post.text)

    def test_citation_follows_ipa_form(self):
        from .x_post import citation
        self.assertEqual(
            citation('令和7年度 科目A 問2 ／ シラバス Ver.8.0 中分類1 応用数学'),
            '出典：令和7年度 基本情報技術者試験 科目A 問2',
        )
        self.assertEqual(
            citation('サンプル問題 科目A 問12'),
            '出典：基本情報技術者試験 サンプル問題 科目A 問12',
        )
        self.assertEqual(citation('シラバス Ver.8.0 中分類10'), '')

    def test_source_line_names_syllabus_item_for_original_questions(self):
        from .x_post import source_line
        self.assertEqual(
            source_line('シラバス Ver.8.0 中分類10 ネットワーク（3）無線LAN'),
            '（オリジナル問題／シラバス Ver.8.0 中分類10 ネットワーク（3）無線LAN）',
        )
        self.assertEqual(
            source_line('令和7年度 科目A 問2 ／ シラバス Ver.8.0 中分類1 応用数学'),
            '（出典：令和7年度 基本情報技術者試験 科目A 問2）',
        )
        self.assertEqual(source_line('自作'), '（オリジナル問題）')

    def test_text_has_source_line(self):
        post = self._post(source='シラバス Ver.8.0 中分類10 ネットワーク（3）無線LAN')
        self.assertIn('（オリジナル問題／シラバス Ver.8.0 中分類10 ネットワーク（3）無線LAN）', post.text)

    def test_weight_counts_full_width_and_url(self):
        from .x_post import weight
        self.assertEqual(weight('abc'), 3)
        self.assertEqual(weight('あい'), 4)
        self.assertEqual(weight('https://example.jp/' + 'x' * 100), 23)

    # ---- 投稿の流れ ----

    def test_same_slot_is_posted_once(self):
        from . import x_post
        self._question()
        first = x_post.post_slot('morning')
        self.assertEqual(first['status'], 'posted')
        self.assertEqual(x_post.post_slot('morning')['status'], 'already')
        self.assertEqual(XPost.objects.count(), 1)

    def test_each_slot_gets_its_own_post(self):
        from . import x_post
        self._question()
        for slot in ('morning', 'noon', 'evening'):
            x_post.post_slot(slot)
        self.assertEqual(XPost.objects.count(), 3)
        self.assertEqual(len(set(XPost.objects.values_list('code', flat=True))), 3)

    def test_pick_is_uniform_random_with_repeats(self):
        """重みなしで毎回引くので、前に出た問題も出る。"""
        from . import x_post
        self._question()
        self._question(stem='DNS の役割はどれか。')
        rng = random.Random(1)
        stems = []
        for day in range(20):
            date = timezone.localdate() - timezone.timedelta(days=day)
            x_post.post_slot('morning', today=date, rng=rng)
            stems.append(XPost.objects.get(date=date, slot='morning').stem)
        self.assertEqual(len(set(stems)), 2)

    def test_failed_post_is_retried_with_the_same_question(self):
        from . import x_post
        self._question()
        self._question(stem='DNS の役割はどれか。')
        with mock.patch.object(x_post, 'send', side_effect=x_post.PostError('503')):
            with self.assertRaises(x_post.PostError):
                x_post.post_slot('noon')
        failed = XPost.objects.get()
        self.assertEqual(failed.status, XPost.STATUS_FAILED)
        result = x_post.post_slot('noon')
        self.assertEqual(result['status'], 'posted')
        retried = XPost.objects.get()
        self.assertEqual((retried.code, retried.stem), (failed.code, failed.stem))
        self.assertEqual(retried.error, '')

    def test_no_question_means_no_post(self):
        from . import x_post
        self.assertEqual(x_post.post_slot('morning'), {'status': 'empty'})
        self.assertFalse(XPost.objects.exists())

    def test_post_keeps_a_copy_of_the_question(self):
        """投稿のあとに問題を直しても消しても、解答ページは投稿のときのまま。"""
        post = self._post()
        Question.objects.all().delete()
        response = self.client.get(reverse('fe:x_answer', args=[post.code]))
        self.assertContains(response, 'SSID の役割はどれか。')
        self.assertContains(response, 'ネットワークの名前')

    def test_send_without_keys_fails_when_not_dry_run(self):
        from . import x_post
        with override_settings(FE_X_DRY_RUN=False, X_API_KEY='', X_API_SECRET='',
                               X_ACCESS_TOKEN='', X_ACCESS_SECRET=''):
            with self.assertRaises(x_post.PostError):
                x_post.send('text')

    # ---- 期限 ----

    def test_old_posts_are_deleted_after_keep_days(self):
        from . import x_post
        self._question()
        today = timezone.localdate()
        for days in (29, 30, 31):
            x_post.post_slot('morning', today=today - timezone.timedelta(days=days))
        self.assertEqual(x_post.purge_old(), 2)
        self.assertEqual(list(XPost.objects.values_list('date', flat=True)),
                         [today - timezone.timedelta(days=29)])

    def test_expired_post_shows_the_notice(self):
        from . import x_post
        self._question()
        x_post.post_slot('morning', today=timezone.localdate() - timezone.timedelta(days=30))
        post = XPost.objects.get()
        response = self.client.get(reverse('fe:x_answer', args=[post.code]))
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'この問題の公開は終わりました', status_code=404)
        self.assertNotContains(response, 'SSID', status_code=404)

    def test_unknown_code_shows_the_same_notice(self):
        response = self.client.get(reverse('fe:x_answer', args=['nothing1']))
        self.assertContains(response, 'この問題の公開は終わりました', status_code=404)
        self.assertContains(response, 'https://example.jp/fe/', status_code=404)

    def test_unposted_post_is_not_shown(self):
        from . import x_post
        self._question()
        with mock.patch.object(x_post, 'send', side_effect=x_post.PostError('x')):
            with self.assertRaises(x_post.PostError):
                x_post.post_slot('morning')
        post = XPost.objects.get()
        response = self.client.get(reverse('fe:x_answer', args=[post.code]))
        self.assertEqual(response.status_code, 404)

    # ---- 解答ページ ----

    def test_page_needs_no_login_or_id(self):
        post = self._post()
        self.client.logout()
        response = self.client.get(reverse('fe:x_answer', args=[post.code]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SSID の役割はどれか。')
        self.assertContains(response, 'noindex')
        # 答える前は正解も解説も出さない
        self.assertNotContains(response, 'SSID はネットワークの名前。')
        self.assertNotContains(response, '基本情報の対策アプリで')

    def test_wrong_answer_shows_result_explanation_and_guide(self):
        post = self._post()
        response = self.client.post(reverse('fe:x_answer', args=[post.code]), {'choice': 0})
        self.assertContains(response, '不正解')
        self.assertContains(response, '正解は ウ')
        self.assertContains(response, 'あなたの答え')
        self.assertContains(response, 'SSID はネットワークの名前。')
        self.assertContains(response, '学習モード')
        self.assertContains(response, 'https://example.jp/fe/')
        self.assertContains(response, 'オリジナル問題')

    def test_official_question_shows_citation(self):
        post = self._post(source='令和7年度 科目A 問2 ／ シラバス Ver.8.0 中分類10')
        response = self.client.post(reverse('fe:x_answer', args=[post.code]), {'choice': 2})
        self.assertContains(response, '○ 正解！')
        self.assertContains(response, '出典：令和7年度 基本情報技術者試験 科目A 問2')
        self.assertNotContains(response, 'シラバス Ver.8.0')

    def test_invalid_choice_asks_again(self):
        post = self._post()
        response = self.client.post(reverse('fe:x_answer', args=[post.code]), {'choice': 9})
        self.assertContains(response, '選択肢を選んでください。')
        self.assertFalse(OpenProgress.objects.exists())

    # ---- 全体の集計への加算 ----

    def test_answers_are_counted_once_per_browser(self):
        post = self._post()
        url = reverse('fe:x_answer', args=[post.code])
        self.client.post(url, {'choice': 2})
        self.client.post(url, {'choice': 0})
        progress = OpenProgress.objects.get()
        self.assertEqual((progress.answered, progress.correct), (1, 1))
        # 別のブラウザなら数える
        other = self.client_class()
        other.post(url, {'choice': 0})
        progress.refresh_from_db()
        self.assertEqual((progress.answered, progress.correct), (2, 1))

    def test_open_answers_are_added_to_site_summary_but_not_learners(self):
        post = self._post()
        self.client.post(reverse('fe:x_answer', args=[post.code]), {'choice': 2})
        summary = site_summary()
        self.assertEqual((summary['total'], summary['correct']), (1, 1))
        self.assertEqual(summary['learners'], 0)
        subject_a = summary['subjects'][0]
        network = next(r for r in subject_a['rows'] if r['category'].code == 10)
        self.assertEqual((network['total'], network['correct']), (1, 1))

    def test_open_answers_are_not_in_any_learners_record(self):
        learner = make_learner('someone')
        enter(self.client, learner)
        post = self._post()
        self.client.post(reverse('fe:x_answer', args=[post.code]), {'choice': 2})
        self.assertFalse(Attempt.objects.exists())
        self.assertFalse(CategoryProgress.objects.exists())

    # ---- 起動口 ----

    def test_task_endpoint_needs_token_and_slot(self):
        url = reverse('fe:post_x')
        self._question()
        with override_settings(FE_X_TOKEN=''):
            self.assertEqual(self.client.post(url, {'slot': 'morning'}).status_code, 503)
        with override_settings(FE_X_TOKEN='secret'):
            self.assertEqual(
                self.client.post(url, {'slot': 'morning'}, HTTP_X_FE_X_TOKEN='wrong').status_code, 403)
            self.assertEqual(
                self.client.post(url, {'slot': 'night'}, HTTP_X_FE_X_TOKEN='secret').status_code, 400)
            response = self.client.post(url, {'slot': 'morning'}, HTTP_X_FE_X_TOKEN='secret')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['status'], 'posted')
            again = self.client.post(url, {'slot': 'morning'}, HTTP_X_FE_X_TOKEN='secret')
            self.assertEqual(again.json()['status'], 'already')

    def test_task_endpoint_reports_failure(self):
        from . import x_post
        self._question()
        with override_settings(FE_X_TOKEN='secret'), \
                mock.patch.object(x_post, 'send', side_effect=x_post.PostError('429')):
            response = self.client.post(reverse('fe:post_x'), {'slot': 'noon'},
                                        HTTP_X_FE_X_TOKEN='secret')
        self.assertEqual(response.status_code, 502)
