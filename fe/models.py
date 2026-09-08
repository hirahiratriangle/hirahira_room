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

    def owned_by(self, user):
        """その利用者の問題。問題はアカウントごとに持ち、他人の分は混ざらない。"""
        return self.filter(owner=user)


class Question(models.Model):
    """1問1答で出題する問題。"""

    SUBJECT_A = 'A'
    SUBJECT_B = 'B'
    SUBJECT_CHOICES = [(SUBJECT_A, '科目A'), (SUBJECT_B, '科目B')]

    DIFFICULTY_CHOICES = [(1, '基本'), (2, '標準'), (3, '応用')]

    owner = models.ForeignKey(
        CustomUser, verbose_name='所有者', on_delete=models.CASCADE,
        related_name='fe_questions',
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
    updated_at = models.DateTimeField(verbose_name='更新日時', auto_now=True)

    objects = QuestionQuerySet.as_manager()

    class Meta:
        verbose_name = verbose_name_plural = 'FE 問題'
        ordering = ['category__code', 'id']
        indexes = [models.Index(fields=['owner', 'subject', 'category'])]

    def __str__(self):
        return self.stem[:40]

    @property
    def is_generated(self):
        return self.template_id is not None

    @property
    def answer_label(self):
        return self.choice_label(self.answer_index)

    @property
    def answer_text(self):
        if 0 <= self.answer_index < len(self.choices):
            return self.choices[self.answer_index]
        return ''

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
    """1問1答の詳細な解答ログ。

    出題の組み立て（未出題か、前回まちがえたか）に使う短期的な記録で、
    問題が取り込みで入れ替わると一緒に消える。
    分野ごとの理解度は CategoryProgress に別途積み上げているので、
    ここが消えても正答率や苦手分野の判定は失われない。
    """

    user = models.ForeignKey(
        CustomUser, verbose_name='ユーザー', on_delete=models.CASCADE, related_name='fe_attempts'
    )
    question = models.ForeignKey(
        Question, verbose_name='問題', on_delete=models.CASCADE, related_name='attempts'
    )
    category = models.ForeignKey(
        Category, verbose_name='中分類', on_delete=models.PROTECT, related_name='attempts'
    )
    selected_index = models.PositiveSmallIntegerField(verbose_name='選んだ選択肢')
    is_correct = models.BooleanField(verbose_name='正誤')
    elapsed_ms = models.PositiveIntegerField(verbose_name='所要時間(ms)', null=True, blank=True)
    answered_at = models.DateTimeField(verbose_name='解答日時', auto_now_add=True)

    class Meta:
        verbose_name = verbose_name_plural = 'FE 解答ログ'
        ordering = ['-answered_at']
        indexes = [
            models.Index(fields=['user', '-answered_at']),
            models.Index(fields=['user', 'category']),
        ]

    def __str__(self):
        return '{} {}'.format(self.user, '○' if self.is_correct else '×')

    @property
    def selected_label(self):
        """選んだ選択肢の記号（ア／イ／…）。"""
        return Question.choice_label(self.selected_index)


class CategoryProgress(models.Model):
    """分野ごとの理解度。問題を入れ替えても残る、この人の到達点。

    問題そのものは取り込みのたびに消えるが、「セキュリティを何問解いて
    何問正解したか」は残す。苦手分野の判定と重点出題はここを見る。
    """

    user = models.ForeignKey(
        CustomUser, verbose_name='ユーザー', on_delete=models.CASCADE,
        related_name='fe_progress',
    )
    category = models.ForeignKey(
        Category, verbose_name='中分類', on_delete=models.PROTECT, related_name='progress'
    )
    subject = models.CharField(
        verbose_name='科目', max_length=1, choices=Question.SUBJECT_CHOICES,
        default=Question.SUBJECT_A,
    )
    answered = models.PositiveIntegerField(verbose_name='解答数', default=0)
    correct = models.PositiveIntegerField(verbose_name='正解数', default=0)
    last_answered_at = models.DateTimeField(verbose_name='最終解答日時', null=True, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = 'FE 分野別の理解度'
        ordering = ['category__code']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'category', 'subject'], name='fe_unique_progress'
            ),
        ]

    def __str__(self):
        return '{} {} {}/{}'.format(
            self.user, self.category.name, self.correct, self.answered
        )


class DailyProgress(models.Model):
    """日ごとの解答数。学習の継続を見るための記録で、問題とは独立している。"""

    user = models.ForeignKey(
        CustomUser, verbose_name='ユーザー', on_delete=models.CASCADE,
        related_name='fe_daily_progress',
    )
    date = models.DateField(verbose_name='日付')
    answered = models.PositiveIntegerField(verbose_name='解答数', default=0)
    correct = models.PositiveIntegerField(verbose_name='正解数', default=0)

    class Meta:
        verbose_name = verbose_name_plural = 'FE 日別の学習記録'
        ordering = ['-date']
        constraints = [
            models.UniqueConstraint(fields=['user', 'date'], name='fe_unique_daily_progress'),
        ]

    def __str__(self):
        return '{} {} {}問'.format(self.user, self.date, self.answered)


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


class LearningNote(models.Model):
    """技術解説。問題の解説文とは別に書かれた、概念をひとまとまりで説明する文章。

    問題は「解けるか」を測るものなので、断片的にしか説明できない。
    先にこちらを読んでから解くために、独立した教材として持つ。
    問題と同じくアカウントごとで、JSON の一括差し替えで入れ替える。
    """

    owner = models.ForeignKey(
        CustomUser, verbose_name='所有者', on_delete=models.CASCADE, related_name='fe_notes',
    )
    category = models.ForeignKey(
        Category, verbose_name='中分類', on_delete=models.PROTECT, related_name='notes'
    )
    topic = models.CharField(verbose_name='小分類', max_length=60, blank=True)
    title = models.CharField(verbose_name='見出し', max_length=120)
    body = models.TextField(verbose_name='本文')
    source = models.CharField(verbose_name='出典', max_length=120, blank=True)
    created_at = models.DateTimeField(verbose_name='作成日時', auto_now_add=True)

    class Meta:
        verbose_name = verbose_name_plural = 'FE 技術解説'
        ordering = ['category__code', 'id']

    def __str__(self):
        return self.title


class LearningRound(models.Model):
    """学習モードの1ラウンド。「読む → 解く」を1セットにした記録。

    先に技術解説をまとめて読み、そのあと同じ範囲を出題して定着を測る。
    1問1答の演習と違い、読む時間と解く問題の並びを最初に決め打ちにする。
    """

    PHASE_READING = 'reading'
    PHASE_QUIZ = 'quiz'
    PHASE_DONE = 'done'
    PHASE_CHOICES = [
        (PHASE_READING, '解説を読む'),
        (PHASE_QUIZ, '解答する'),
        (PHASE_DONE, '終了'),
    ]

    # 提示時間の選択肢（分）。10問ぶんの解説を読み切れる幅で用意する。
    MINUTE_CHOICES = [3, 5, 10, 15]
    DEFAULT_MINUTES = 5
    QUESTION_COUNT = 10

    user = models.ForeignKey(
        CustomUser, verbose_name='ユーザー', on_delete=models.CASCADE,
        related_name='fe_rounds',
    )
    note = models.ForeignKey(
        LearningNote, verbose_name='読んだ解説', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='rounds',
    )
    subject = models.CharField(
        verbose_name='科目', max_length=1, choices=Question.SUBJECT_CHOICES,
        default=Question.SUBJECT_A,
    )
    reading_seconds = models.PositiveIntegerField(verbose_name='提示時間(秒)')
    phase = models.CharField(
        verbose_name='段階', max_length=10, choices=PHASE_CHOICES, default=PHASE_READING
    )
    position = models.PositiveSmallIntegerField(verbose_name='解答中の位置', default=0)
    started_at = models.DateTimeField(verbose_name='開始日時', auto_now_add=True)
    finished_at = models.DateTimeField(verbose_name='終了日時', null=True, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = 'FE 学習ラウンド'
        ordering = ['-started_at']

    def __str__(self):
        return '{} {:%Y-%m-%d %H:%M}'.format(self.user, self.started_at)

    @property
    def total(self):
        return self.items.count()

    @property
    def correct(self):
        return self.items.filter(is_correct=True).count()

    @property
    def rate_percent(self):
        total = self.items.filter(is_correct__isnull=False).count()
        if not total:
            return None
        return round(self.correct / total * 100)


class LearningItem(models.Model):
    """学習ラウンドで扱う1問。読む段階では教材、解く段階では設問になる。"""

    round = models.ForeignKey(
        LearningRound, verbose_name='ラウンド', on_delete=models.CASCADE,
        related_name='items',
    )
    question = models.ForeignKey(
        Question, verbose_name='問題', on_delete=models.CASCADE, related_name='learning_items'
    )
    order = models.PositiveSmallIntegerField(verbose_name='出題順')
    selected_index = models.PositiveSmallIntegerField(
        verbose_name='選んだ選択肢', null=True, blank=True
    )
    is_correct = models.BooleanField(verbose_name='正誤', null=True, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = 'FE 学習ラウンドの問題'
        ordering = ['order']
        constraints = [
            models.UniqueConstraint(fields=['round', 'order'], name='fe_unique_round_order'),
        ]

    def __str__(self):
        return '{} {}問目'.format(self.round_id, self.order + 1)

    @property
    def selected_label(self):
        if self.selected_index is None:
            return ''
        return Question.choice_label(self.selected_index)
