"""基本情報技術者試験（FE）対策アプリのモデル。

分析の単位は試験要綱の出題範囲の分類。科目Aは中分類（1〜23）、科目Bは
要綱が別に定める5項目（101〜105）で、両者は重ならない。苦手分野の把握・
重点出題はすべてこの粒度で行う。分類は Category、出題は Question、解答履歴は
Attempt が持つ。問題と技術解説は全 ID で共有し、成績は Learner（本アプリの
中の ID）ごとに持つ。ID は hirahira_room のアカウントとは紐づけない。計算問題は QuestionTemplate から
数値を振り直して Question を生成するため、固定問題とテンプレート問題を
同じ導線で扱える。
"""

import re

from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Count, Q
from django.utils import timezone

from .crypto import decrypt, encrypt

# 科目。要綱が科目ごとに別の出題範囲を定めているので、分類も科目を持つ。
SUBJECT_A = 'A'
SUBJECT_B = 'B'
SUBJECT_CHOICES = [(SUBJECT_A, '科目A'), (SUBJECT_B, '科目B')]


class Learner(models.Model):
    """成績を持つ単位。ID を登録した人と、登録せずに使う人の両方を表す。

    ID を登録すると code が入り、成績が残る。ダッシュボードは /fe/<ID>/ で開く。
    hirahira_room のアカウントとは紐づけない。ID は本人が決め、入力した人を
    本人として扱う（合言葉などの確認はしない）。大文字小文字は区別せず、
    小文字にそろえて持つ。

    code が空のものは「履歴を残さない」で使っている間の一時的な記録。
    ブラウザのセッションだけが参照を持ち、URL からはたどれない。苦手重点の
    出題と学習モードは成績の積み上げが要るので、使っている間は同じ仕組みで
    記録し、使われなくなったら purge_temp_learners で消す。

    問題集は全 ID で共有で、取り込み（差し替え）ができるのは管理用の ID だけ。
    管理用にするのは Django の管理画面から行う。
    """

    # 一時的な記録を消すまでの日数。触られなくなってからの日数で数える。
    TEMP_RETENTION_DAYS = 30

    code = models.CharField(
        verbose_name='ID', max_length=30, unique=True, null=True, blank=True,
        help_text='空のときは、履歴を残さずに使っている一時的な記録。',
        validators=[RegexValidator(
            r'^[a-z0-9_-]{3,30}$',
            'ID は半角の英小文字・数字・「_」「-」で、3〜30文字にしてください。',
        )],
    )
    is_admin = models.BooleanField(
        verbose_name='管理用', default=False,
        help_text='問題集の取り込みと、問題の一覧ができる。',
    )
    created_at = models.DateTimeField(verbose_name='作成日時', auto_now_add=True)
    # 一時的な記録をいつ消してよいかの判断に使う。画面を開くたびに更新する。
    last_seen_at = models.DateTimeField(verbose_name='最終利用日時', default=timezone.now)

    class Meta:
        verbose_name = verbose_name_plural = 'FE 学習者ID'
        ordering = ['code']

    def __str__(self):
        return self.code or '（履歴を残さない利用）'

    @property
    def is_temporary(self):
        """ID を登録していない、使っている間だけの記録か。"""
        return not self.code

    @property
    def label(self):
        """画面に出す呼び名。"""
        return self.code or '履歴なし'


class Category(models.Model):
    """試験要綱が定める出題範囲の分類。

    科目Aは中分類（1〜23）。科目Bは要綱が別に定める5項目（101〜105）で、
    科目Aの中分類とは別の軸になっている。番号が重ならないようにしてあるので、
    取り込みの JSON は番号だけで分類が一意に定まる。
    """

    SUBJECT_A = SUBJECT_A
    SUBJECT_B = SUBJECT_B
    SUBJECT_CHOICES = SUBJECT_CHOICES

    FIELD_TECHNOLOGY = 'T'
    FIELD_MANAGEMENT = 'M'
    FIELD_STRATEGY = 'S'
    FIELD_CHOICES = [
        (FIELD_TECHNOLOGY, 'テクノロジ系'),
        (FIELD_MANAGEMENT, 'マネジメント系'),
        (FIELD_STRATEGY, 'ストラテジ系'),
    ]

    code = models.PositiveSmallIntegerField(verbose_name='分類番号', unique=True)
    name = models.CharField(verbose_name='分類', max_length=40)
    subject = models.CharField(
        verbose_name='科目', max_length=1, choices=SUBJECT_CHOICES, default=SUBJECT_A
    )
    field = models.CharField(verbose_name='分野', max_length=1, choices=FIELD_CHOICES)
    major_code = models.PositiveSmallIntegerField(verbose_name='大分類番号')
    major_name = models.CharField(verbose_name='大分類', max_length=40)
    # 本番で何問出るかの想定値。科目Aは60問、科目Bは20問のうちの件数。
    # IPA は内訳を公表していないため、公開されたサンプルセットと
    # 令和5〜8年度の公開問題の傾向から割り当てている。
    exam_weight = models.PositiveSmallIntegerField(verbose_name='想定出題数')

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


# source がこの書き出しなら、IPA が公開した問題（過去の公開問題・サンプル問題）の転記
IPA_PUBLISHED_PREFIXES = ('令和', '平成')
IPA_SAMPLE_PREFIX = 'サンプル問題'

# 計算問題の見分けに使う。選択肢から数字と記号を除いて残る文字（単位など）の上限と、
# 最初の数字より前に置ける文字（「約」「毎秒」など）の上限
CALCULATION_REMAINDER_MAX = 6
CALCULATION_PREFIX_MAX = 2
_DIGIT = re.compile(r'[0-9０-９]')
_NUMERIC_PART = re.compile(r'[0-9０-９\s.,，、\-−+×÷/^()（）%％:：=＝<>≦≧~～]')


class QuestionQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def from_ipa(self):
        """IPA が公開した問題の転記だけ。自作と自動生成は含めない。"""
        published = Q()
        for prefix in IPA_PUBLISHED_PREFIXES + (IPA_SAMPLE_PREFIX,):
            published |= Q(source__startswith=prefix)
        return self.filter(published, template__isnull=True)

    def for_subject(self, subject):
        if subject in (Question.SUBJECT_A, Question.SUBJECT_B):
            return self.filter(subject=subject)
        return self


class Question(models.Model):
    """1問1答で出題する問題。全 ID で共有する。"""

    SUBJECT_A = SUBJECT_A
    SUBJECT_B = SUBJECT_B
    SUBJECT_CHOICES = SUBJECT_CHOICES

    DIFFICULTY_CHOICES = [(1, '基本'), (2, '標準'), (3, '応用')]

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
    # 図は公開問題の冊子から切り出した PNG を、data URI のまま持つ。画像を別ファイルに
    # すると取り込みのたびに画像も運ぶことになり、JSON 4本で完結しなくなるため。
    # figures は問題文の下に並べる図（{"src", "alt"} の配列）、choice_figures は
    # 選択肢そのものが図の問題で、選択肢と同じ並びの data URI（図の無い選択肢は null）。
    figures = models.JSONField(verbose_name='図', default=list, blank=True)
    choice_figures = models.JSONField(verbose_name='選択肢の図', null=True, blank=True)
    answer_index = models.PositiveSmallIntegerField(verbose_name='正解の選択肢番号（0始まり）')
    explanation = models.TextField(verbose_name='解説', blank=True)
    difficulty = models.PositiveSmallIntegerField(
        verbose_name='難易度', choices=DIFFICULTY_CHOICES, default=2
    )
    # 出典にシラバスの項目まで書くので、転記の問題では長くなる
    source = models.CharField(verbose_name='出典・根拠', max_length=255, blank=True)


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
        indexes = [models.Index(fields=['subject', 'category'], name='fe_question_subj_cat_idx')]

    def __str__(self):
        return self.stem[:40]

    @property
    def is_generated(self):
        return self.template_id is not None

    @property
    def origin_label(self):
        """IPA の出題そのものか自作かを、出典の書き出しから見分けて返す。

        source の前にラベルとして置く。何年の出題かは source に書いてあるので、
        ここでは繰り返さない。
        """
        if self.is_generated:
            return '自動生成'
        source = self.source.strip()
        if source.startswith(IPA_PUBLISHED_PREFIXES):
            return 'IPA公開問題'
        if source.startswith(IPA_SAMPLE_PREFIX):
            return 'IPAサンプル問題'
        return '自作' if source else ''

    @property
    def is_calculation(self):
        """数値を求めて答える問題か。

        計算問題の印はデータに無いので、形から見分ける。問題文に数字があり、
        どの選択肢も数量の形をしているもの。「0.81」「約 2.3」「4G バイト」
        「n(n−1)/2」は当たる。文で答える選択肢は当たらない。「RAID5」「レベル3」
        「第2正規形」のように名前に番号が付いただけのものも、数字の前の語が長いか
        「第」で始まるので当たらない。テンプレートから作った問題は、すべて計算問題である。
        科目Bでは、プログラムを追って数値や配列を答える問題がこれに当たる。
        """
        if self.is_generated:
            return True
        if not self.choices or not _DIGIT.search(self.stem):
            return False
        return all(self._is_quantity(text) for text in self.choices)

    @staticmethod
    def _is_quantity(text):
        """選択肢1つが、数値に単位が付いた程度の形か。"""
        first = _DIGIT.search(text)
        if not first:
            return False
        prefix = _NUMERIC_PART.sub('', text[:first.start()])
        if len(prefix) > CALCULATION_PREFIX_MAX or prefix.startswith('第'):
            return False
        return len(_NUMERIC_PART.sub('', text)) <= CALCULATION_REMAINDER_MAX

    @property
    def source_text(self):
        """ラベルの後ろに置く source。ラベルと同じ言葉は繰り返さない。

        サンプル問題の source は「サンプル問題 科目A 問12」と書き出すので、
        「IPAサンプル問題」のラベルを付けるときは頭の「サンプル問題」を落とす。
        その場で作った計算問題は、source ではなくひな形の題名から
        「計算問題：伝送時間と伝送効率」と出す。source の「テンプレート」は
        アプリの中の言葉で、解いている人には何のことか伝わらない。
        """
        if self.is_generated:
            return '計算問題：{}'.format(self.template.title)
        source = self.source.strip()
        if self.origin_label == 'IPAサンプル問題':
            return source.removeprefix('サンプル問題').strip()
        return source

    @property
    def answer_label(self):
        return self.choice_label(self.answer_index)

    @property
    def answer_text(self):
        if 0 <= self.answer_index < len(self.choices):
            return self.choices[self.answer_index]
        return ''

    def choice_figure(self, index):
        """その選択肢の図（data URI）。図の無い選択肢なら None。"""
        figures = self.choice_figures or []
        return figures[index] if 0 <= index < len(figures) else None

    @property
    def answer_figure(self):
        return self.choice_figure(self.answer_index)

    def choice_rows(self):
        """(番号, 記号, 本文, 図) のリスト。labeled_choices に選択肢の図を添えたもの。"""
        return [
            (i, label, text, self.choice_figure(i))
            for i, label, text in self.labeled_choices()
        ]

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

    learner = models.ForeignKey(
        Learner, verbose_name='ID', on_delete=models.CASCADE,
        related_name='attempts',
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
            models.Index(fields=['learner', '-answered_at'], name='fe_attempt_learner_at_idx'),
            models.Index(fields=['learner', 'category'], name='fe_attempt_learner_cat_idx'),
        ]

    def __str__(self):
        return '{} {}'.format(self.learner, '○' if self.is_correct else '×')

    @property
    def selected_label(self):
        """選んだ選択肢の記号（ア／イ／…）。"""
        return Question.choice_label(self.selected_index)


class CategoryProgress(models.Model):
    """分野ごとの理解度。問題を入れ替えても残る、この人の到達点。

    問題そのものは取り込みのたびに消えるが、「セキュリティを何問解いて
    何問正解したか」は残す。苦手分野の判定と重点出題はここを見る。
    """

    learner = models.ForeignKey(
        Learner, verbose_name='ID', on_delete=models.CASCADE,
        related_name='progress',
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
                fields=['learner', 'category', 'subject'], name='fe_unique_progress'
            ),
        ]

    def __str__(self):
        return '{} {} {}/{}'.format(
            self.learner, self.category.name, self.correct, self.answered
        )


class DailyProgress(models.Model):
    """日ごとの解答数。学習の継続を見るための記録で、問題とは独立している。"""

    learner = models.ForeignKey(
        Learner, verbose_name='ID', on_delete=models.CASCADE,
        related_name='daily_progress',
    )
    date = models.DateField(verbose_name='日付')
    answered = models.PositiveIntegerField(verbose_name='解答数', default=0)
    correct = models.PositiveIntegerField(verbose_name='正解数', default=0)

    class Meta:
        verbose_name = verbose_name_plural = 'FE 日別の学習記録'
        ordering = ['-date']
        constraints = [
            models.UniqueConstraint(fields=['learner', 'date'], name='fe_unique_daily_progress'),
        ]

    def __str__(self):
        return '{} {} {}問'.format(self.learner, self.date, self.answered)


class StudySession(models.Model):
    """出題モードの選択を覚えておくための軽い状態。"""

    MODE_FOCUS = 'focus'
    MODE_RANDOM = 'random'
    MODE_WEAK = 'weak'
    MODE_REVIEW = 'review'
    MODE_CATEGORY = 'category'
    MODE_IPA = 'ipa'
    MODE_CALC = 'calc'
    MODE_CHOICES = [
        (MODE_FOCUS, 'おまかせ（苦手を重点出題）'),
        (MODE_IPA, 'IPA公開問題（過去問・サンプル）からおまかせ'),
        (MODE_CALC, '計算問題をおまかせ'),
        (MODE_RANDOM, '本番の出題比率どおり'),
        (MODE_WEAK, '苦手分野だけ'),
        (MODE_REVIEW, '間違えた問題の復習'),
        (MODE_CATEGORY, '分野を指定'),
    ]

    learner = models.OneToOneField(
        Learner, verbose_name='ID', on_delete=models.CASCADE,
        related_name='study_session',
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
        return '{} / {}'.format(self.learner, self.get_mode_display())


class LearningNote(models.Model):
    """技術解説。問題の解説文とは別に書かれた、概念をひとまとまりで説明する文章。

    問題は「解けるか」を測るものなので、断片的にしか説明できない。
    先にこちらを読んでから解くために、独立した教材として持つ。
    問題と同じく全 ID で共有し、JSON の一括差し替えで入れ替える。
    """

    category = models.ForeignKey(
        Category, verbose_name='中分類', on_delete=models.PROTECT, related_name='notes'
    )
    topic = models.CharField(verbose_name='小分類', max_length=60, blank=True)
    title = models.CharField(verbose_name='見出し', max_length=120)
    body = models.TextField(verbose_name='本文')
    # 扱っているシラバスの項目を全部書くので、長くなる
    source = models.CharField(verbose_name='出典', max_length=255, blank=True)
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

    learner = models.ForeignKey(
        Learner, verbose_name='ID', on_delete=models.CASCADE,
        related_name='rounds',
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
    # 読み上げている間は読む時間を止める。止めていた合計と、いま止めている始まり。
    paused_seconds = models.PositiveIntegerField(verbose_name='止めていた秒数', default=0)
    paused_at = models.DateTimeField(verbose_name='止めた日時', null=True, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = 'FE 学習ラウンド'
        ordering = ['-started_at']

    def __str__(self):
        return '{} {:%Y-%m-%d %H:%M}'.format(self.learner, self.started_at)

    @property
    def notes(self):
        """このラウンドで読む解説。同じ小分類の解説は、まとめて1つの範囲として読む。

        出題は小分類ごとなので、読む単位も小分類に合わせる。小分類に解説が
        2本あるのに1本だけ読ませると、読んでいない内容の問題が混ざる。
        並びは取り込んだ順で、JSON の中の並びがそのまま読む順になる。
        """
        if self.note is None:
            return []
        return list(
            LearningNote.objects.filter(category=self.note.category, topic=self.note.topic)
            .select_related('category').order_by('id')
        )

    def remaining_seconds(self, now=None):
        """読む時間の残り。開始時刻から数え、止めていた時間は差し引く。"""
        now = now or timezone.now()
        paused = self.paused_seconds
        if self.paused_at is not None:
            paused += (now - self.paused_at).total_seconds()
        elapsed = (now - self.started_at).total_seconds() - paused
        return max(0, int(self.reading_seconds - elapsed))

    def pause(self, now=None):
        """読む時間を止める。止まっていれば何もしない。"""
        if self.paused_at is None:
            self.paused_at = now or timezone.now()
            self.save(update_fields=['paused_at'])

    def resume(self, now=None):
        """読む時間を動かす。止めていた時間を合計に足す。"""
        if self.paused_at is not None:
            now = now or timezone.now()
            self.paused_seconds += int((now - self.paused_at).total_seconds())
            self.paused_at = None
            self.save(update_fields=['paused_seconds', 'paused_at'])

    @property
    def title(self):
        """読んだ解説の見出し。2本あれば並べる。"""
        return '／'.join(note.title for note in self.notes)

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


class MailSubscription(models.Model):
    """毎日0時に届く問題メールの登録。ID ごとに1件で、送り方は fe.mail が受け持つ。

    ID には合言葉がないので、アドレスは確認のメールのリンクを開いて「受け取る」を
    押したものだけを email に入れる。それまでは pending_email に置き、確認の期限
    （FE_MAIL_CONFIRM_MAX_AGE）を過ぎたら消す（fe.mail.purge_pending）。登録したあとは
    画面からアドレスを変えられず、解除もメールのリンクからだけできる。

    アドレスは DB に暗号化して持つ（fe.crypto）。読み書きは email と pending_email を通す。

    マラソンと特訓は並行して両方選べる。両方なら1日2通になる。
    """

    learner = models.OneToOneField(
        Learner, verbose_name='ID', on_delete=models.CASCADE,
        related_name='mail_subscription',
    )
    # 確認を済ませたアドレス（暗号化）。空なら未登録（または解除済み）
    email_enc = models.TextField(verbose_name='配信先（暗号化）', blank=True, editable=False)
    pending_email_enc = models.TextField(verbose_name='確認待ちのアドレス（暗号化）', blank=True, editable=False)
    # 確認・解除のメールを続けて送らせないための控え。確認待ちのアドレスを置いた日時も兼ねる
    # （確認待ちがあるのは、確認のメールを送ってから「受け取る」が押されるまでの間だけ）
    link_sent_at = models.DateTimeField(verbose_name='確認・解除のメールを送った日時', null=True, blank=True)

    # マラソン：全問を、はじめから順番に送る。位置は全問の並び（fe.mail.marathon_questions）の何番目か。
    # 問題の取り込みで並びが変わっても、位置はそのまま使う。
    marathon = models.BooleanField(verbose_name='マラソン', default=False)
    marathon_position = models.PositiveIntegerField(verbose_name='マラソンの次の位置', default=0)
    marathon_laps = models.PositiveIntegerField(verbose_name='マラソンの周回数', default=0)
    marathon_sent_on = models.DateField(verbose_name='マラソンを最後に送った日', null=True, blank=True)

    # 特訓：選んだ小分類を順に、1通に1つずつ送る。[[中分類番号, 小分類], ...]
    training = models.BooleanField(verbose_name='特訓', default=False)
    training_topics = models.JSONField(verbose_name='特訓の小分類', default=list, blank=True)
    training_index = models.PositiveIntegerField(verbose_name='特訓の次の小分類', default=0)
    training_offset = models.PositiveIntegerField(verbose_name='特訓の小分類の中の次の位置', default=0)
    training_sent_on = models.DateField(verbose_name='特訓を最後に送った日', null=True, blank=True)

    created_at = models.DateTimeField(verbose_name='作成日時', auto_now_add=True)

    class Meta:
        verbose_name = verbose_name_plural = 'FE メール配信'

    def __str__(self):
        return '{} {}'.format(self.learner, '登録済み' if self.email else '未登録')

    @property
    def email(self):
        return decrypt(self.email_enc)

    @email.setter
    def email(self, value):
        self.email_enc = encrypt(value)

    @property
    def pending_email(self):
        return decrypt(self.pending_email_enc)

    @pending_email.setter
    def pending_email(self, value):
        self.pending_email_enc = encrypt(value)

    @property
    def masked_email(self):
        """画面に出す配信先。ID を知っている人なら誰でも見られるので、伏せて出す。"""
        email = self.email
        local, _, domain = email.partition('@')
        return '{}***@{}'.format(local[:1], domain) if email else ''


class PassReport(models.Model):
    """本番に合格したという本人の申告。集計ページの「合格者数」はこれを数える。

    アプリには本番の結果を確かめる手段がないので、自己申告をそのまま信じる。
    1人1件。取り消せば行ごと消し、数えなくなる。
    """

    learner = models.OneToOneField(
        Learner, verbose_name='ID', on_delete=models.CASCADE,
        related_name='pass_report',
    )
    passed_on = models.DateField(verbose_name='合格した試験の受験日')
    reported_at = models.DateTimeField(verbose_name='申告日時', auto_now_add=True)

    class Meta:
        verbose_name = verbose_name_plural = 'FE 合格の申告'
        ordering = ['-passed_on']

    def __str__(self):
        return '{} {}'.format(self.learner, self.passed_on)


class XPost(models.Model):
    """X に投稿した1問と、その解答ページ（/fe/x/<code>/）。fe.x_post が受け持つ。

    投稿のときに問題の中身を写して持ち、解答ページはこの写しから描く。あとで問題を
    直しても、取り込みで消えても、X に出した問題とページの中身は食い違わない。
    投稿から X_POST_KEEP_DAYS 日たったら行ごと消す（fe.x_post.purge_old）。消えた投稿や
    存在しない code のページには、同じ案内ページを出す。

    1日3回（朝・昼・夜）で、日付と時間帯の組は1つだけ。定期実行が遅れて二重に
    呼ばれても、二重には投稿しない。
    """

    SLOT_MORNING = 'morning'
    SLOT_NOON = 'noon'
    SLOT_EVENING = 'evening'
    SLOT_CHOICES = [(SLOT_MORNING, '朝'), (SLOT_NOON, '昼'), (SLOT_EVENING, '夜')]

    STATUS_PENDING = 'pending'
    STATUS_POSTED = 'posted'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [(STATUS_PENDING, '投稿前'), (STATUS_POSTED, '投稿済み'), (STATUS_FAILED, '失敗')]

    date = models.DateField(verbose_name='日付')
    slot = models.CharField(verbose_name='時間帯', max_length=10, choices=SLOT_CHOICES)
    # 解答ページの URL に使う記号。連番にすると、番号をずらして過去の投稿を
    # たどれてしまうので、ランダムにする。
    code = models.CharField(verbose_name='URL の記号', max_length=16, unique=True)

    # 投稿したときの問題の写し
    subject = models.CharField(verbose_name='科目', max_length=1, choices=SUBJECT_CHOICES)
    category = models.ForeignKey(
        Category, verbose_name='中分類', on_delete=models.PROTECT, related_name='x_posts'
    )
    stem = models.TextField(verbose_name='問題文')
    choices = models.JSONField(verbose_name='選択肢')
    figures = models.JSONField(verbose_name='図', default=list, blank=True)
    choice_figures = models.JSONField(verbose_name='選択肢の図', null=True, blank=True)
    answer_index = models.PositiveSmallIntegerField(verbose_name='正解の選択肢番号（0始まり）')
    explanation = models.TextField(verbose_name='解説', blank=True)
    source = models.CharField(verbose_name='出典・根拠', max_length=255, blank=True)

    text = models.TextField(verbose_name='投稿の文面')
    status = models.CharField(
        verbose_name='状態', max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING
    )
    tweet_id = models.CharField(verbose_name='X の投稿 ID', max_length=32, blank=True)
    error = models.TextField(verbose_name='失敗の内容', blank=True)
    created_at = models.DateTimeField(verbose_name='作成日時', auto_now_add=True)
    posted_at = models.DateTimeField(verbose_name='投稿日時', null=True, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = 'FE X の投稿'
        ordering = ['-date', '-created_at']
        constraints = [
            models.UniqueConstraint(fields=['date', 'slot'], name='fe_unique_x_post_slot'),
        ]

    def __str__(self):
        return '{} {} {}'.format(self.date, self.get_slot_display(), self.stem[:30])

    def choice_figure(self, index):
        figures = self.choice_figures or []
        return figures[index] if 0 <= index < len(figures) else None

    def choice_rows(self):
        """(番号, 記号, 本文, 図) のリスト。Question.choice_rows と同じ形。"""
        return [
            (i, Question.choice_label(i), text, self.choice_figure(i))
            for i, text in enumerate(self.choices)
        ]

    @property
    def answer_label(self):
        return Question.choice_label(self.answer_index)

    @property
    def is_official(self):
        return self.source.strip().startswith(IPA_PUBLISHED_PREFIXES + (IPA_SAMPLE_PREFIX,))


class OpenProgress(models.Model):
    """ID なしで答えた解答の数え上げ。X の解答ページで答えた分がここに積もる。

    誰の解答かは持たない。全 ID を合わせた集計（fe.stats.site_summary）の、
    中分類ごとの解答数・正解数に足す。CategoryProgress と同じく問題とは
    切り離して持つので、問題を取り込み直しても数字は消えない。
    """

    category = models.ForeignKey(
        Category, verbose_name='中分類', on_delete=models.PROTECT, related_name='open_progress'
    )
    subject = models.CharField(verbose_name='科目', max_length=1, choices=SUBJECT_CHOICES)
    answered = models.PositiveIntegerField(verbose_name='解答数', default=0)
    correct = models.PositiveIntegerField(verbose_name='正解数', default=0)

    class Meta:
        verbose_name = verbose_name_plural = 'FE ID なしの解答数'
        ordering = ['category__code']
        constraints = [
            models.UniqueConstraint(fields=['category', 'subject'], name='fe_unique_open_progress'),
        ]

    def __str__(self):
        return '{} {}/{}'.format(self.category.name, self.correct, self.answered)
