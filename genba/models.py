from datetime import timedelta

from accounts.models import CustomUser
from django.db import models
from django.utils import timezone

# 公開範囲。「自分だけ」の下書きはリポジトリ（＝このアプリ）の外に置くので、ここには持たない。
VISIBILITY_TEAM = 'team'
VISIBILITY_COMPANY = 'company'
VISIBILITY_CHOICES = [
    (VISIBILITY_TEAM, '現場チーム'),
    (VISIBILITY_COMPANY, '会社全体'),
]

# 会社公開までの流れ: 本人が候補にする → リーダーが一般化を確認 → 本人が公開に切り替える
KNOWLEDGE_NONE = 'none'
KNOWLEDGE_CANDIDATE = 'candidate'
KNOWLEDGE_REVIEWED = 'reviewed'
KNOWLEDGE_CHOICES = [
    (KNOWLEDGE_NONE, '―'),
    (KNOWLEDGE_CANDIDATE, 'ナレッジ候補'),
    (KNOWLEDGE_REVIEWED, '一般化確認済み'),
]


def week_start(day):
    """その日を含む週の月曜日"""
    return day - timedelta(days=day.weekday())


class Site(models.Model):
    """現場。1現場＝1リポジトリ（genba-*）"""

    name = models.CharField(verbose_name='現場名', max_length=60)
    repo_name = models.CharField(
        verbose_name='リポジトリ名', max_length=100, unique=True,
        help_text='例: genba-acme-order。GitHub 連携は未実装で、今は名前だけ持つ。',
    )
    industry = models.CharField(
        verbose_name='業界', max_length=40,
        help_text='会社公開のとき、顧客名の代わりに表示する。',
    )
    description = models.TextField(verbose_name='概要', blank=True)
    started_on = models.DateField(verbose_name='開始日')
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

    class Meta:
        verbose_name = verbose_name_plural = '現場メンバー'
        constraints = [models.UniqueConstraint(fields=['site', 'user'], name='genba_unique_membership')]

    def __str__(self):
        return f'{self.site} / {self.user} ({self.get_role_display()})'


class KnowledgeMixin(models.Model):
    """会社公開できる記録に共通の項目"""

    visibility = models.CharField(
        verbose_name='公開範囲', max_length=10, choices=VISIBILITY_CHOICES, default=VISIBILITY_TEAM,
    )
    knowledge_status = models.CharField(
        verbose_name='公開レビュー', max_length=10, choices=KNOWLEDGE_CHOICES, default=KNOWLEDGE_NONE,
    )

    class Meta:
        abstract = True

    @property
    def is_public(self):
        return self.visibility == VISIBILITY_COMPANY


class Document(KnowledgeMixin):
    """docs/ 配下の文書（面談メモ・設計メモ・議事録・週報・振り返りなど）"""

    KIND_INTERVIEW = 'interview'
    KIND_SCOPE = 'scope'
    KIND_STRUCTURE = 'structure'
    KIND_DESIGN = 'design'
    KIND_MINUTES = 'minutes'
    KIND_WEEKLY = 'weekly'
    KIND_RETRO = 'retro'
    KIND_CHOICES = [
        (KIND_INTERVIEW, '面談メモ・言葉のズレ'),
        (KIND_SCOPE, '作業範囲・約束事項'),
        (KIND_STRUCTURE, '体制・会議体'),
        (KIND_DESIGN, '設計メモ'),
        (KIND_MINUTES, '議事録'),
        (KIND_WEEKLY, '週報'),
        (KIND_RETRO, '振り返り'),
    ]
    # 構想書の「データと公開範囲」で、会社全体に出せるもの
    PUBLISHABLE_KINDS = {KIND_INTERVIEW, KIND_DESIGN, KIND_RETRO}

    site = models.ForeignKey(Site, verbose_name='現場', on_delete=models.CASCADE, related_name='documents')
    author = models.ForeignKey(CustomUser, verbose_name='書いた人', on_delete=models.PROTECT, related_name='genba_documents')
    kind = models.CharField(verbose_name='種類', max_length=10, choices=KIND_CHOICES)
    title = models.CharField(verbose_name='タイトル', max_length=100)
    body = models.TextField(verbose_name='本文', blank=True)
    week_of = models.DateField(verbose_name='対象週', null=True, blank=True, help_text='週報のみ。週の月曜日にそろえる。')
    risks = models.TextField(verbose_name='課題・リスク', blank=True, help_text='週報のみ。リーダー画面に出る。')
    skill_item = models.CharField(
        verbose_name='スキル対応', max_length=100, blank=True,
        help_text='振り返りのみ。基本設計スキルセットの項目。',
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
    """用語集の1語"""

    site = models.ForeignKey(Site, verbose_name='現場', on_delete=models.CASCADE, related_name='terms')
    author = models.ForeignKey(CustomUser, verbose_name='書いた人', on_delete=models.PROTECT, related_name='genba_terms')
    term = models.CharField(verbose_name='用語', max_length=60)
    meaning = models.TextField(verbose_name='この現場での意味')
    general_difference = models.TextField(
        verbose_name='一般的な意味との違い', blank=True,
        help_text='書かれていれば「用語のズレ」として数える。',
    )
    created_at = models.DateTimeField(verbose_name='作成日時', default=timezone.now)

    class Meta:
        verbose_name = verbose_name_plural = '用語'
        ordering = ['term']

    def __str__(self):
        return self.term

    @property
    def has_gap(self):
        return bool(self.general_difference.strip())

    is_publishable = True


class Question(models.Model):
    """Issue（確認事項・課題・作業）"""

    KIND_QUESTION = 'question'
    KIND_PROBLEM = 'problem'
    KIND_TASK = 'task'
    KIND_CHOICES = [(KIND_QUESTION, '確認事項'), (KIND_PROBLEM, '課題'), (KIND_TASK, '作業')]

    site = models.ForeignKey(Site, verbose_name='現場', on_delete=models.CASCADE, related_name='questions')
    author = models.ForeignKey(CustomUser, verbose_name='書いた人', on_delete=models.PROTECT, related_name='genba_questions')
    kind = models.CharField(verbose_name='種類', max_length=10, choices=KIND_CHOICES, default=KIND_QUESTION)
    title = models.CharField(verbose_name='タイトル', max_length=100)
    body = models.TextField(verbose_name='内容', blank=True)
    ask_to = models.CharField(verbose_name='誰に聞くか', max_length=60, blank=True)
    scope_out = models.BooleanField(verbose_name='スコープ外', default=False)
    is_open = models.BooleanField(verbose_name='未解決', default=True)
    created_at = models.DateTimeField(verbose_name='作成日時', default=timezone.now)
    closed_at = models.DateTimeField(verbose_name='解決日時', null=True, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = '確認事項・課題'
        ordering = ['created_at']

    def __str__(self):
        return self.title

    @property
    def days_open(self):
        """作成からの経過日数。解決済みなら解決までの日数。"""
        end = self.closed_at if not self.is_open and self.closed_at else timezone.now()
        return (end - self.created_at).days


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
