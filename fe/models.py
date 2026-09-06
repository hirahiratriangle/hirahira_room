"""基本情報技術者試験（FE）対策アプリのモデル。

分析の単位は試験要綱の「中分類」（全23）。苦手分野の把握・重点出題は
すべてこの粒度で行う。中分類は Category、出題は Question、解答履歴は
Attempt が持つ。計算問題は QuestionTemplate から数値を振り直して
Question を生成するため、固定問題とテンプレート問題を同じ導線で扱える。
"""

from accounts.models import CustomUser
from django.db import models
from django.db.models import Count, Q


class Category(models.Model):
    """試験要綱の中分類（1〜23）。"""

    FIELD_TECHNOLOGY = 'T'
    FIELD_MANAGEMENT = 'M'
    FIELD_STRATEGY = 'S'
    FIELD_CHOICES = [
        (FIELD_TECHNOLOGY, 'テクノロジ系'),
        (FIELD_MANAGEMENT, 'マネジメント系'),
        (FIELD_STRATEGY, 'ストラテジ系'),
    ]

    code = models.PositiveSmallIntegerField(verbose_name='中分類番号', unique=True)
    name = models.CharField(verbose_name='中分類', max_length=40)
    field = models.CharField(verbose_name='分野', max_length=1, choices=FIELD_CHOICES)
    major_code = models.PositiveSmallIntegerField(verbose_name='大分類番号')
    major_name = models.CharField(verbose_name='大分類', max_length=40)
    # 科目A（60問）で何問出るかの想定値。公開されたサンプルセット60問の
    # 分野構成と、令和5〜8年度の公開問題の傾向から割り当てている。
    exam_weight = models.PositiveSmallIntegerField(verbose_name='科目A想定出題数')

    class Meta:
        verbose_name = verbose_name_plural = 'FE 中分類'
        ordering = ['code']

    def __str__(self):
        return '{}. {}'.format(self.code, self.name)

    @property
    def label(self):
        return '{}. {}'.format(self.code, self.name)


class QuestionTemplate(models.Model):
    """数値を振り直して何問でも作れる計算問題のひな形。

    実体の生成は fe.generators が担当し、生成結果は Question として
    永続化する（同じパラメータなら同じ Question を使い回す）。
    """

    key = models.CharField(verbose_name='テンプレートキー', max_length=50, unique=True)
    title = models.CharField(verbose_name='題材', max_length=100)
    category = models.ForeignKey(
        Category, verbose_name='中分類', on_delete=models.PROTECT, related_name='templates'
    )
    topic = models.CharField(verbose_name='小分類', max_length=60, blank=True)
    is_active = models.BooleanField(verbose_name='有効', default=True)

    class Meta:
        verbose_name = verbose_name_plural = 'FE 問題テンプレート'
        ordering = ['category__code', 'key']

    def __str__(self):
        return self.title


class QuestionQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def for_subject(self, subject):
        if subject in (Question.SUBJECT_A, Question.SUBJECT_B):
            return self.filter(subject=subject)
        return self


class Question(models.Model):
    """1問1答で出題する問題。"""

    SUBJECT_A = 'A'
    SUBJECT_B = 'B'
    SUBJECT_CHOICES = [(SUBJECT_A, '科目A'), (SUBJECT_B, '科目B')]

    DIFFICULTY_CHOICES = [(1, '基本'), (2, '標準'), (3, '応用')]

    key = models.CharField(
        verbose_name='問題キー', max_length=60, unique=True,
        help_text='再投入しても重複しないようにするための安定キー',
    )
    subject = models.CharField(
        verbose_name='科目', max_length=1, choices=SUBJECT_CHOICES, default=SUBJECT_A
    )
    category = models.ForeignKey(
        Category, verbose_name='中分類', on_delete=models.PROTECT, related_name='questions'
    )
    topic = models.CharField(verbose_name='小分類', max_length=60, blank=True)
    stem = models.TextField(verbose_name='問題文')
    # 選択肢のリスト。ア／イ／ウ／エ の記号は表示側で付けるので本文には含めない。
    choices = models.JSONField(verbose_name='選択肢')
    answer_index = models.PositiveSmallIntegerField(verbose_name='正解の選択肢番号（0始まり）')
    explanation = models.TextField(verbose_name='解説', blank=True)
    difficulty = models.PositiveSmallIntegerField(
        verbose_name='難易度', choices=DIFFICULTY_CHOICES, default=2
    )
    source = models.CharField(verbose_name='出典・根拠', max_length=120, blank=True)

    template = models.ForeignKey(
        QuestionTemplate, verbose_name='生成元テンプレート', on_delete=models.CASCADE,
        null=True, blank=True, related_name='questions',
    )
    params = models.JSONField(verbose_name='生成パラメータ', null=True, blank=True)

    is_active = models.BooleanField(verbose_name='有効', default=True)
    created_at = models.DateTimeField(verbose_name='作成日時', auto_now_add=True)

    objects = QuestionQuerySet.as_manager()

    class Meta:
        verbose_name = verbose_name_plural = 'FE 問題'
        ordering = ['category__code', 'key']
        indexes = [models.Index(fields=['subject', 'category'])]

    def __str__(self):
        return '[{}] {}'.format(self.key, self.stem[:30])

    @property
    def is_generated(self):
        return self.template_id is not None

    @property
    def answer_label(self):
        return self.choice_label(self.answer_index)

    @staticmethod
    def choice_label(index):
        labels = 'アイウエオカキクケコ'
        return labels[index] if 0 <= index < len(labels) else '?'

    def labeled_choices(self):
        """テンプレートで回しやすい (番号, 記号, 本文) のリスト。"""
        return [
            (i, self.choice_label(i), text) for i, text in enumerate(self.choices)
        ]


class Attempt(models.Model):
    """1問1答の解答履歴。正答率と苦手分野はすべてここから集計する。"""

    user = models.ForeignKey(
        CustomUser, verbose_name='ユーザー', on_delete=models.CASCADE, related_name='fe_attempts'
    )
    question = models.ForeignKey(
        Question, verbose_name='問題', on_delete=models.CASCADE, related_name='attempts'
    )
    # 出題時点の中分類を写し取っておく（問題の分類を後で直しても履歴は壊れない）
    category = models.ForeignKey(
        Category, verbose_name='中分類', on_delete=models.PROTECT, related_name='attempts'
    )
    selected_index = models.PositiveSmallIntegerField(verbose_name='選んだ選択肢')
    is_correct = models.BooleanField(verbose_name='正誤')
    elapsed_ms = models.PositiveIntegerField(verbose_name='所要時間(ms)', null=True, blank=True)
    answered_at = models.DateTimeField(verbose_name='解答日時', auto_now_add=True)

    class Meta:
        verbose_name = verbose_name_plural = 'FE 解答履歴'
        ordering = ['-answered_at']
        indexes = [
            models.Index(fields=['user', '-answered_at']),
            models.Index(fields=['user', 'category']),
        ]

    def __str__(self):
        return '{} {} {}'.format(
            self.user, self.question.key, '○' if self.is_correct else '×'
        )

    @property
    def selected_label(self):
        """選んだ選択肢の記号（ア／イ／…）。"""
        return Question.choice_label(self.selected_index)


class StudySession(models.Model):
    """出題モードの選択を覚えておくための軽い状態。"""

    MODE_FOCUS = 'focus'
    MODE_RANDOM = 'random'
    MODE_WEAK = 'weak'
    MODE_REVIEW = 'review'
    MODE_CATEGORY = 'category'
    MODE_CHOICES = [
        (MODE_FOCUS, 'おまかせ（苦手を重点出題）'),
        (MODE_RANDOM, '本番の出題比率どおり'),
        (MODE_WEAK, '苦手分野だけ'),
        (MODE_REVIEW, '間違えた問題の復習'),
        (MODE_CATEGORY, '分野を指定'),
    ]

    user = models.OneToOneField(
        CustomUser, verbose_name='ユーザー', on_delete=models.CASCADE, related_name='fe_session'
    )
    mode = models.CharField(
        verbose_name='出題モード', max_length=10, choices=MODE_CHOICES, default=MODE_FOCUS
    )
    subject = models.CharField(
        verbose_name='科目', max_length=1, choices=Question.SUBJECT_CHOICES,
        default=Question.SUBJECT_A,
    )
    category = models.ForeignKey(
        Category, verbose_name='指定中分類', on_delete=models.SET_NULL, null=True, blank=True
    )
    updated_at = models.DateTimeField(verbose_name='更新日時', auto_now=True)

    class Meta:
        verbose_name = verbose_name_plural = 'FE 出題設定'

    def __str__(self):
        return '{} / {}'.format(self.user, self.get_mode_display())
