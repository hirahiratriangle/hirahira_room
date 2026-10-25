from datetime import timedelta

from accounts.models import CustomUser
from django.db import models
from django.urls import reverse
from django.utils import timezone

from . import kit

# 公開範囲。現場チームの中は全公開。会社へは一般化レビューを通して出す（本人に範囲を選ばせない）。
VISIBILITY_TEAM = 'team'
VISIBILITY_COMPANY = 'company'
VISIBILITY_CHOICES = [
    (VISIBILITY_TEAM, '現場チーム'),
    (VISIBILITY_COMPANY, '会社全体'),
]

# 会社公開までの流れ: 現場チームの誰かが候補にする → リーダーが一般化した版を書いて公開する
KNOWLEDGE_NONE = 'none'
KNOWLEDGE_CANDIDATE = 'candidate'
KNOWLEDGE_PUBLISHED = 'published'
KNOWLEDGE_CHOICES = [
    (KNOWLEDGE_NONE, '―'),
    (KNOWLEDGE_CANDIDATE, 'ナレッジ候補'),
    (KNOWLEDGE_PUBLISHED, '会社公開'),
]


def week_start(day):
    """その日を含む週の月曜日"""
    return day - timedelta(days=day.weekday())


class Site(models.Model):
    """現場。1現場＝1リポジトリ（genba-*）。テンプレートから作る。"""

    name = models.CharField(verbose_name='現場名', max_length=60)
    repo_name = models.CharField(
        verbose_name='リポジトリ名', max_length=100, unique=True,
        help_text='例: genba-acme-order。プロトタイプでは名前だけ持つ（GitHub には作らない）。',
    )
    industry = models.CharField(
        verbose_name='業界', max_length=40,
        help_text='会社公開のとき、顧客名の代わりに表示する。',
    )
    description = models.TextField(verbose_name='概要', blank=True)
    started_on = models.DateField(verbose_name='入場日')
    template_version = models.CharField(verbose_name='テンプレートの版', max_length=20, default=kit.TEMPLATE_VERSION)
    is_archived = models.BooleanField(verbose_name='Archive 済み', default=False)
    created_at = models.DateTimeField(verbose_name='作成日時', auto_now_add=True)

    class Meta:
        verbose_name = verbose_name_plural = '現場'
        ordering = ['-started_on']

    def __str__(self):
        return self.name


class Membership(models.Model):
    """現場ごとの立場。マネジメントと会社は現場をまたぐので Django のグループで持つ。"""

    ROLE_MEMBER = 'member'
    ROLE_LEADER = 'leader'
    ROLE_CHOICES = [(ROLE_MEMBER, 'メンバー'), (ROLE_LEADER, 'リーダー')]

    site = models.ForeignKey(Site, verbose_name='現場', on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(CustomUser, verbose_name='ユーザー', on_delete=models.CASCADE, related_name='genba_memberships')
    role = models.CharField(verbose_name='立場', max_length=10, choices=ROLE_CHOICES, default=ROLE_MEMBER)
    duty = models.CharField(verbose_name='役割', max_length=60, blank=True)
    joined_on = models.DateField(verbose_name='入場日')
    # 裁量の線（本人とリーダーが一度言葉にする）
    line_self = models.TextField(verbose_name='自分で決める範囲', blank=True)
    line_consult = models.TextField(verbose_name='相談して決める範囲', blank=True)
    line_updated_at = models.DateTimeField(verbose_name='裁量の線を書いた日時', null=True, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = '現場メンバー'
        constraints = [models.UniqueConstraint(fields=['site', 'user'], name='genba_unique_membership')]

    def __str__(self):
        return f'{self.site} / {self.user} ({self.get_role_display()})'


class KnowledgeMixin(models.Model):
    """会社公開できる記録に共通の項目。会社に見えるのは一般化した版（public_*）だけ。"""

    visibility = models.CharField(
        verbose_name='公開範囲', max_length=10, choices=VISIBILITY_CHOICES, default=VISIBILITY_TEAM,
    )
    knowledge_status = models.CharField(
        verbose_name='公開レビュー', max_length=10, choices=KNOWLEDGE_CHOICES, default=KNOWLEDGE_NONE,
    )
    public_title = models.CharField(verbose_name='一般化した題', max_length=100, blank=True)
    public_body = models.TextField(verbose_name='一般化した本文', blank=True)
    published_by = models.ForeignKey(CustomUser, verbose_name='公開したリーダー', on_delete=models.PROTECT,
                                     null=True, blank=True, related_name='+')
    published_at = models.DateTimeField(verbose_name='公開した日時', null=True, blank=True)

    class Meta:
        abstract = True

    @property
    def is_public(self):
        return self.visibility == VISIBILITY_COMPANY


class Document(KnowledgeMixin):
    """docs/ 配下の文書。種類は docs の区分（00_参画前〜07_振り返り）"""

    KIND_PRE = 'pre'
    KIND_CONTRACT = 'contract'
    KIND_STRUCTURE = 'structure'
    KIND_BUSINESS = 'business'
    KIND_DESIGN = 'design'
    KIND_MINUTES = 'minutes'
    KIND_WEEKLY = 'weekly'
    KIND_RETRO = 'retro'
    KIND_CHOICES = [(key, label) for key, label, _ in kit.DOC_SECTIONS]
    # 構想書の「公開範囲」で、会社全体に一般化して出せるもの
    PUBLISHABLE_KINDS = {KIND_PRE, KIND_DESIGN, KIND_RETRO}

    site = models.ForeignKey(Site, verbose_name='現場', on_delete=models.CASCADE, related_name='documents')
    author = models.ForeignKey(CustomUser, verbose_name='書いた人', on_delete=models.PROTECT, related_name='genba_documents')
    kind = models.CharField(verbose_name='区分', max_length=10, choices=KIND_CHOICES)
    title = models.CharField(verbose_name='タイトル', max_length=100)
    body = models.TextField(verbose_name='本文', blank=True)
    # 週報だけ
    week_of = models.DateField(verbose_name='対象週', null=True, blank=True, help_text='週の月曜日にそろえる。')
    done = models.TextField(verbose_name='やったこと', blank=True)
    learned = models.TextField(verbose_name='分かったこと', blank=True)
    stuck = models.TextField(verbose_name='詰まっていること', blank=True, help_text='リーダーの画面に出る。')
    next_step = models.TextField(verbose_name='次にやること', blank=True)
    goal_action = models.TextField(verbose_name='目標に近づいた行動', blank=True, help_text='1行でよい（任意）。')
    # 振り返りだけ
    skill_item = models.CharField(
        verbose_name='スキル対応', max_length=100, blank=True,
        help_text='基本設計スキルセットの項目。',
    )
    created_at = models.DateTimeField(verbose_name='作成日時', default=timezone.now)
    updated_at = models.DateTimeField(verbose_name='更新日時', auto_now=True)

    class Meta:
        verbose_name = verbose_name_plural = '文書'
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if self.week_of:
            self.week_of = week_start(self.week_of)
        super().save(*args, **kwargs)

    @property
    def is_publishable(self):
        return self.kind in self.PUBLISHABLE_KINDS


class Term(KnowledgeMixin):
    """用語集の1語。分からない語は意味を空のまま載せてよい。"""

    site = models.ForeignKey(Site, verbose_name='現場', on_delete=models.CASCADE, related_name='terms')
    author = models.ForeignKey(CustomUser, verbose_name='書いた人', on_delete=models.PROTECT, related_name='genba_terms')
    term = models.CharField(verbose_name='用語', max_length=60)
    meaning = models.TextField(verbose_name='この現場での意味', blank=True, help_text='分からなければ空のままでよい。')
    general_difference = models.TextField(
        verbose_name='一般的な意味との違い', blank=True,
        help_text='書かれていれば「用語のズレ」として数える。',
    )
    created_at = models.DateTimeField(verbose_name='作成日時', default=timezone.now)

    is_publishable = True

    class Meta:
        verbose_name = verbose_name_plural = '用語'
        ordering = ['term']

    def __str__(self):
        return self.term

    @property
    def has_gap(self):
        return bool(self.general_difference.strip())


class Question(models.Model):
    """Issue（確認事項・課題・作業）。チェックリストの項目も作業 Issue として並ぶ。"""

    KIND_QUESTION = 'question'
    KIND_PROBLEM = 'problem'
    KIND_TASK = 'task'
    KIND_CHOICES = [(key, label) for key, label, _ in kit.ISSUE_KINDS]

    REASON_CHOICES = [('term', '用語'), ('premise', '前提'), ('figure', '図がない')]
    STAGE_CHOICES = list(kit.STAGE_LABELS.items())

    site = models.ForeignKey(Site, verbose_name='現場', on_delete=models.CASCADE, related_name='questions')
    author = models.ForeignKey(CustomUser, verbose_name='書いた人', on_delete=models.PROTECT, related_name='genba_questions')
    kind = models.CharField(verbose_name='種類', max_length=10, choices=KIND_CHOICES, default=KIND_QUESTION)
    title = models.CharField(verbose_name='タイトル', max_length=100)
    body = models.TextField(verbose_name='内容', blank=True)
    ask_to = models.CharField(verbose_name='誰に聞くか', max_length=60, blank=True)
    # ラベル
    scope_out = models.BooleanField(verbose_name='スコープ外', default=False)
    unreadable = models.BooleanField(verbose_name='読めない', default=False)
    # 読めない：どの資料の・どこが・なぜ
    source = models.CharField(verbose_name='どの資料', max_length=100, blank=True)
    location = models.CharField(verbose_name='どこが', max_length=100, blank=True)
    reason = models.CharField(verbose_name='なぜ読めないか', max_length=10, choices=REASON_CHOICES, blank=True)
    support_on = models.DateField(verbose_name='説明の時間', null=True, blank=True)
    support_note = models.CharField(verbose_name='説明のメモ', max_length=200, blank=True)
    # 課題：懸念の型
    fact = models.TextField(verbose_name='事実', blank=True)
    interpretation = models.TextField(verbose_name='自分の解釈', blank=True)
    unconfirmed = models.TextField(verbose_name='未確認のこと', blank=True)
    impact = models.TextField(verbose_name='影響', blank=True)
    received_at = models.DateTimeField(verbose_name='受け取った日時', null=True, blank=True)
    received_by = models.ForeignKey(CustomUser, verbose_name='受け取った人', on_delete=models.PROTECT,
                                    null=True, blank=True, related_name='genba_received_concerns')
    response = models.TextField(verbose_name='受け取った人の返事', blank=True)
    # 作業：進捗表
    due_on = models.DateField(verbose_name='予定日', null=True, blank=True)
    done_on = models.DateField(verbose_name='完了日', null=True, blank=True)
    delay_note = models.TextField(verbose_name='遅れの原因と追いつく見込み', blank=True)
    # チェックリストから作られた作業
    stage = models.CharField(verbose_name='チェックリストの段階', max_length=10, choices=STAGE_CHOICES, blank=True)
    record_to = models.CharField(verbose_name='残す場所', max_length=100, blank=True)
    order = models.PositiveIntegerField(verbose_name='並び順', default=0)

    is_open = models.BooleanField(verbose_name='未解決', default=True)
    created_at = models.DateTimeField(verbose_name='作成日時', default=timezone.now)
    closed_at = models.DateTimeField(verbose_name='解決日時', null=True, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = 'Issue'
        ordering = ['created_at']

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse('genba:issue_detail', args=[self.pk])

    @property
    def days_open(self):
        """作成からの経過日数。解決済みなら解決までの日数。"""
        end = self.closed_at if not self.is_open and self.closed_at else timezone.now()
        return (end - self.created_at).days

    @property
    def is_checklist(self):
        return bool(self.stage)

    @property
    def labels(self):
        return [name for name, on in (('スコープ外', self.scope_out), ('読めない', self.unreadable)) if on]

    @property
    def is_late(self):
        """予定日を過ぎて終わっていない、または予定日より遅れて終わった"""
        if not self.due_on:
            return False
        return (self.done_on or timezone.localdate()) > self.due_on

    def record_targets(self):
        """チェックリストの項目から移る先 [(名前, URL)]"""
        targets = []
        for code in filter(None, self.record_to.split(',')):
            targets.append((kit.RECORD_TARGETS[code], target_url(self.site_id, code)))
        return targets


def target_url(site_id, code):
    """残す場所のコードから URL を作る"""
    if code.startswith('doc:'):
        return reverse('genba:document_create', args=[site_id, code[4:]])
    if code in ('question', 'unreadable', 'problem', 'scope_out'):
        return reverse('genba:issue_create', args=[site_id, code])
    if code == 'summary':
        return reverse('genba:summary', args=[site_id])
    if code == 'goal':
        return reverse('genba:goal_create', args=[site_id])
    if code == 'structure_gap':
        return reverse('genba:structure_gap_create', args=[site_id])
    if code == 'progress':
        return reverse('genba:site_detail', args=[site_id]) + '?tab=progress'
    raise ValueError(code)


class ChecklistItem(models.Model):
    """作業 Issue の中のチェック項目"""

    question = models.ForeignKey(Question, verbose_name='Issue', on_delete=models.CASCADE, related_name='items')
    text = models.CharField(verbose_name='項目', max_length=200)
    order = models.PositiveIntegerField(verbose_name='並び順', default=0)
    is_done = models.BooleanField(verbose_name='済み', default=False)

    class Meta:
        verbose_name = verbose_name_plural = 'チェック項目'
        ordering = ['order']

    def __str__(self):
        return self.text


class Summary(models.Model):
    """1週目のまとめ。全体像・担当機能の整理は本人が書き、用語集・未解決の質問・読めない一覧は記録から並べる。"""

    site = models.ForeignKey(Site, verbose_name='現場', on_delete=models.CASCADE, related_name='summaries')
    author = models.ForeignKey(CustomUser, verbose_name='書いた人', on_delete=models.PROTECT, related_name='genba_summaries')
    overview = models.TextField(
        verbose_name='全体像', blank=True,
        help_text='システムの目的、利用者、業務の流れ。図があればリンクを書く。',
    )
    functions = models.TextField(
        verbose_name='担当機能の整理', blank=True,
        help_text='機能一覧の中の位置とつながる機能、入力・処理・出力・データ。',
    )
    submitted_at = models.DateTimeField(verbose_name='出した日時', null=True, blank=True)
    review_note = models.TextField(
        verbose_name='リーダーと確かめた結果', blank=True,
        help_text='期待値との差と、2週目の作業。',
    )
    feedback = models.TextField(
        verbose_name='リーダーからの返し', blank=True,
        help_text='スキルの具体記述に沿った差と、次の作業。',
    )
    feedback_by = models.ForeignKey(CustomUser, verbose_name='返したリーダー', on_delete=models.PROTECT,
                                    null=True, blank=True, related_name='genba_summary_feedbacks')
    feedback_at = models.DateTimeField(verbose_name='返した日時', null=True, blank=True)
    updated_at = models.DateTimeField(verbose_name='更新日時', auto_now=True)

    class Meta:
        verbose_name = verbose_name_plural = '1週目のまとめ'
        constraints = [models.UniqueConstraint(fields=['site', 'author'], name='genba_unique_summary')]

    def __str__(self):
        return f'{self.site} / {self.author}'


class StructureGap(models.Model):
    """体制のズレ：実際の指示者・決裁者が体制図と違う（危険信号「日々の指示者が体制図と違う」の記録）"""

    ROLE_CHOICES = [('instructor', '日々の指示者'), ('approver', '決裁者')]

    site = models.ForeignKey(Site, verbose_name='現場', on_delete=models.CASCADE, related_name='structure_gaps')
    author = models.ForeignKey(CustomUser, verbose_name='書いた人', on_delete=models.PROTECT,
                               related_name='genba_structure_gaps')
    role_kind = models.CharField(verbose_name='どちらが違うか', max_length=12, choices=ROLE_CHOICES)
    on_chart = models.CharField(verbose_name='体制図では', max_length=100)
    actual = models.CharField(verbose_name='実際は', max_length=100)
    note = models.TextField(verbose_name='実際の決裁の流れ（メモ）', blank=True)
    created_at = models.DateTimeField(verbose_name='作成日時', default=timezone.now)

    class Meta:
        verbose_name = verbose_name_plural = '体制のズレ'
        ordering = ['created_at']

    def __str__(self):
        return f'{self.get_role_kind_display()}：{self.on_chart} → {self.actual}'


class PromiseCheck(models.Model):
    """振り返りの「約束と実際」表の1行"""

    site = models.ForeignKey(Site, verbose_name='現場', on_delete=models.CASCADE, related_name='promise_checks')
    author = models.ForeignKey(CustomUser, verbose_name='書いた人', on_delete=models.PROTECT, related_name='genba_promise_checks')
    promise = models.CharField(verbose_name='約束（営業・面談時の説明）', max_length=200)
    actual = models.CharField(verbose_name='実際', max_length=200)
    is_gap = models.BooleanField(verbose_name='食い違っている', default=False)
    created_at = models.DateTimeField(verbose_name='作成日時', default=timezone.now)

    class Meta:
        verbose_name = verbose_name_plural = '約束と実際'
        ordering = ['created_at']

    def __str__(self):
        return self.promise


class Goal(models.Model):
    """現場ごとの成長の目標と到達（基本設計スキルセット）。

    目標も到達も現場チームに全文で見せる（構想書スライド9「目標と到達」の行）。
    到達は3段階で確定する（スライド22）。

    1. 記入（メンバー本人）：当てはまるレベル・行動・証拠を書き、確認を依頼する（submitted_at）
    2. 確認（同じ現場のリーダー）：その行動が事実か、どの責任・規模でやったかを確かめる（confirmed_*）
    3. 承認（マネジメント）：レベルと評価A〜Eを確定し、次の目標を示す（approved_*）

    確認するリーダーがいない現場（ひとり現場）では、マネジメントが確認と承認をまとめて行い、
    元請・顧客から聞き取った声を customer_voice に残す。
    承認した到達だけが、会社のスキル台帳・スキルシート・台帳の集計に入る（スライド20）。
    """

    ROLE_CHOICES = [('member', 'メンバー'), ('leader', 'リーダー'), ('owner', '責任者')]
    LEVEL_CHOICES = [(n, f'レベル{n}') for n in range(1, 6)]
    GRADE_CHOICES = [(g, g) for g in 'ABCDE']

    site = models.ForeignKey(Site, verbose_name='現場', on_delete=models.CASCADE, related_name='goals')
    author = models.ForeignKey(CustomUser, verbose_name='本人', on_delete=models.PROTECT, related_name='genba_goals')
    skill_item = models.CharField(verbose_name='スキル項目', max_length=40)
    concrete_skill = models.CharField(verbose_name='基本設計の具体スキル', max_length=60, blank=True)
    current_level = models.PositiveSmallIntegerField(verbose_name='今のレベル', choices=LEVEL_CHOICES[:4])
    target_text = models.TextField(verbose_name='目標（一つ上のレベルの具体記述）')
    preparation = models.TextField(verbose_name='入場前に準備すること', blank=True)
    # ---- 1. 記入（振り返りで本人が書く） ----
    reached_level = models.PositiveSmallIntegerField(
        verbose_name='当てはまるレベル', choices=LEVEL_CHOICES[1:], null=True, blank=True)
    explanation = models.TextField(verbose_name='実際にやった行動', blank=True)
    mentored = models.BooleanField(verbose_name='後進の指導をした', default=False)
    role = models.CharField(verbose_name='立場（責任性）', max_length=10, choices=ROLE_CHOICES, blank=True)
    team_size = models.PositiveIntegerField(verbose_name='ピーク時のチーム人数', null=True, blank=True)
    complexity = models.CharField(verbose_name='複雑性（該当する番号）', max_length=30, blank=True)
    # 次の現場へ：一つ上の列を次の目標にする。本人が書き、承認のときにマネジメントが示し直せる。
    # 承認した到達の次の目標は、会社の「目標に合う現場へアサイン」に並ぶ
    next_text = models.TextField(verbose_name='次の目標（一つ上の列）', blank=True)
    # 確認を依頼した日時。依頼したあとは、本人は書き換えられない（取り下げれば記入に戻る）
    submitted_at = models.DateTimeField(verbose_name='確認を依頼した日時', null=True, blank=True)
    # ---- 2. 確認（リーダー。ひとり現場ではマネジメント） ----
    confirmed_at = models.DateTimeField(verbose_name='確認した日時', null=True, blank=True)
    confirmed_by = models.ForeignKey(CustomUser, verbose_name='確認した人', on_delete=models.PROTECT,
                                     null=True, blank=True, related_name='genba_confirmed_goals')
    confirm_comment = models.TextField(verbose_name='確認のコメント', blank=True)
    # ひとり現場で、マネジメントが元請・顧客から聞き取った声（自社のリーダーが現場で見ていない分を補う）
    customer_voice = models.TextField(verbose_name='元請・顧客の声', blank=True)
    # ---- 3. 承認（マネジメント） ----
    approved_on = models.DateField(verbose_name='承認した日', null=True, blank=True)
    approved_by = models.ForeignKey(CustomUser, verbose_name='承認した人', on_delete=models.PROTECT,
                                    null=True, blank=True, related_name='genba_approved_goals')
    # 承認で確定したレベルと評価。本人の「当てはまるレベル」とは別に持ち、マネジメントが直せる
    approved_level = models.PositiveSmallIntegerField(
        verbose_name='レベル', choices=LEVEL_CHOICES[1:], null=True, blank=True)
    approved_grade = models.CharField(verbose_name='評価', max_length=1, choices=GRADE_CHOICES, blank=True)
    approval_comment = models.TextField(verbose_name='承認のコメント', blank=True)
    created_at = models.DateTimeField(verbose_name='作成日時', default=timezone.now)
    updated_at = models.DateTimeField(verbose_name='更新日時', auto_now=True)

    # 段階は日時の欄から求める（段階そのものは保存しない）
    STAGE_DRAFT, STAGE_SUBMITTED, STAGE_CONFIRMED, STAGE_APPROVED = '', 'submitted', 'confirmed', 'approved'
    STAGE_LABELS = {STAGE_DRAFT: '記入中', STAGE_SUBMITTED: '確認待ち', STAGE_CONFIRMED: '承認待ち', STAGE_APPROVED: '承認済み'}

    class Meta:
        verbose_name = verbose_name_plural = '目標と到達'
        ordering = ['created_at']

    def __str__(self):
        return f'{self.skill_item}：{self.target_text[:30]}'

    @property
    def target_level(self):
        return self.current_level + 1

    @property
    def stage(self):
        """いまの段階。後ろの段階の欄が埋まっていれば、そちらを優先する"""
        if self.approved_on:
            return self.STAGE_APPROVED
        if self.confirmed_at:
            return self.STAGE_CONFIRMED
        if self.submitted_at:
            return self.STAGE_SUBMITTED
        return self.STAGE_DRAFT

    @property
    def stage_label(self):
        return self.STAGE_LABELS[self.stage]

    def confirmers(self):
        """確認できる人：同じ現場のリーダー（本人は除く。自分の到達は自分で確認しない）"""
        return CustomUser.objects.filter(
            genba_memberships__site=self.site, genba_memberships__role=Membership.ROLE_LEADER).exclude(pk=self.author_id)

    @property
    def is_solo(self):
        """ひとり現場か。確認できるリーダーがいなければ、マネジメントが確認も行う（スライド22）"""
        return not self.confirmers().exists()

    def reset(self):
        """記入に戻す（差し戻し・取り下げ）。コメントは本人が読めるよう残す。保存は呼び出し側で行う"""
        self.submitted_at = self.confirmed_at = self.confirmed_by = None
        self.approved_on = self.approved_by = self.approved_level = None
        self.approved_grade = ''

    @property
    def complexity_numbers(self):
        return [int(n) for n in self.complexity.split(',') if n]


class GoalEvidence(models.Model):
    """到達の証拠に選んだ本人の記録"""

    TYPE_CHOICES = [('doc', '文書'), ('term', '用語'), ('issue', 'Issue'), ('summary', 'まとめ'), ('feedback', 'まとめへの返し')]

    goal = models.ForeignKey(Goal, verbose_name='目標', on_delete=models.CASCADE, related_name='evidence')
    record_type = models.CharField(verbose_name='記録の種類', max_length=10, choices=TYPE_CHOICES)
    record_id = models.PositiveIntegerField(verbose_name='記録のID')
    title = models.CharField(verbose_name='記録の名前', max_length=150)

    class Meta:
        verbose_name = verbose_name_plural = '到達の証拠'
        constraints = [models.UniqueConstraint(fields=['goal', 'record_type', 'record_id'], name='genba_unique_evidence')]

    def __str__(self):
        return self.title


class KitProposal(models.Model):
    """テンプレート・言い回し集・スキルセットの改訂の提案（誰でも出せ、会社の立場が決める）"""

    TARGET_CHOICES = [('checklist', '入場時チェックリスト'), ('phrases', '言い回し集'), ('skillset', '成長の物差し（スキルセット）')]
    STATUS_CHOICES = [('open', '提案中'), ('adopted', '採用（次の版で反映）'), ('declined', '見送り')]

    target = models.CharField(verbose_name='対象', max_length=10, choices=TARGET_CHOICES)
    title = models.CharField(verbose_name='題', max_length=100)
    proposal = models.TextField(verbose_name='提案（足す・直す内容）')
    reason = models.TextField(verbose_name='理由（現場での教訓）', blank=True)
    author = models.ForeignKey(CustomUser, verbose_name='提案した人', on_delete=models.PROTECT, related_name='+')
    status = models.CharField(verbose_name='状態', max_length=10, choices=STATUS_CHOICES, default='open')
    decided_by = models.ForeignKey(CustomUser, verbose_name='決めた人', on_delete=models.PROTECT,
                                   null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(verbose_name='作成日時', default=timezone.now)

    class Meta:
        verbose_name = verbose_name_plural = '改訂の提案'
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class InterviewQuestion(models.Model):
    """営業・面談の質問リスト：振り返りの「約束と実際」から、入場前に確かめる項目を増やす"""

    question = models.CharField(verbose_name='入場前に確かめる質問', max_length=200)
    industry = models.CharField(verbose_name='業界（任意）', max_length=40, blank=True)
    source = models.ForeignKey(Document, verbose_name='元になった振り返り', on_delete=models.SET_NULL,
                               null=True, blank=True, related_name='+')
    author = models.ForeignKey(CustomUser, verbose_name='足した人', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(verbose_name='作成日時', default=timezone.now)

    class Meta:
        verbose_name = verbose_name_plural = '営業・面談の質問リスト'
        ordering = ['created_at']

    def __str__(self):
        return self.question
