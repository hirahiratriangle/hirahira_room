"""運営指標。すべて現場単位で集計し、個人を比べる数字は作らない（人の名前は出さず、件数・人数だけ）。"""
from dataclasses import dataclass, field
from datetime import timedelta

from django.utils import timezone

from .models import Document, Membership, Question, week_start
from .signals import WEEKLY_GAP_ESCALATE, weeks_missing_in_row

# 週報の提出率を見る期間（週）
WEEKLY_WINDOW = 8

# 「手を打つか」の目安。超えたら画面で強調する。
THRESHOLDS = {
    'stall_days': 7,      # 確認事項の平均滞留日数
    'promise_gaps': 2,    # 約束と実際のズレ件数
    'scope_out': 3,       # スコープ外作業の件数
    'term_gaps': 5,       # 用語のズレ件数
    'structure_gaps': 1,  # 体制のズレ件数
    'leader_concerns': 2,  # リーダーの未解決の懸念
    'weekly_gap_people': 1,  # 週報の途切れが続いている人数
}
WEEKLY_RATE_MIN = 75      # 週報の提出率（%）


@dataclass
class SiteMetrics:
    site: object
    stall_days: float | None
    open_questions: int
    promise_gaps: int
    scope_out: int
    term_gaps: int
    weekly_rate: int | None
    weekly_weeks: int
    structure_gaps: int = 0
    leader_concerns: int = 0
    leader_concerns_unreceived: int = 0
    weekly_gap_people: int = 0
    alerts: list = field(default_factory=list)

    @property
    def needs_attention(self):
        return bool(self.alerts)


def weekly_rate(site, today=None):
    """直近 WEEKLY_WINDOW 週（現場の開始週以降）のうち、週報がある週の割合（%）"""
    today = today or timezone.localdate()
    this_week = week_start(today)
    first_week = max(week_start(site.started_on), this_week - timedelta(weeks=WEEKLY_WINDOW - 1))
    weeks = [first_week + timedelta(weeks=i) for i in range((this_week - first_week).days // 7 + 1)]
    if not weeks:
        return None, 0
    submitted = set(
        site.documents.filter(kind=Document.KIND_WEEKLY, week_of__gte=first_week)
        .values_list('week_of', flat=True)
    )
    hit = sum(1 for w in weeks if w in submitted)
    return round(hit * 100 / len(weeks)), len(weeks)


def site_metrics(site, today=None):
    open_qs = list(site.questions.filter(kind=Question.KIND_QUESTION, is_open=True))
    stall = round(sum(q.days_open for q in open_qs) / len(open_qs), 1) if open_qs else None
    rate, weeks = weekly_rate(site, today)
    m = SiteMetrics(
        site=site,
        stall_days=stall,
        open_questions=len(open_qs),
        promise_gaps=site.promise_checks.filter(is_gap=True).count(),
        scope_out=site.questions.filter(scope_out=True).count(),
        term_gaps=sum(1 for t in site.terms.all() if t.has_gap),
        weekly_rate=rate,
        weekly_weeks=weeks,
        structure_gaps=site.structure_gaps.count(),
    )
    leaders = site.memberships.filter(role=Membership.ROLE_LEADER).values_list('user_id', flat=True)
    concerns = site.questions.filter(kind=Question.KIND_PROBLEM, is_open=True, author_id__in=leaders)
    m.leader_concerns = concerns.count()
    m.leader_concerns_unreceived = concerns.filter(received_at__isnull=True).count()
    # 名前は出さず、人数だけ（2週以上続いたらマネジメントにも届ける）
    m.weekly_gap_people = sum(
        1 for ms in site.memberships.select_related('user', 'site')
        if weeks_missing_in_row(ms, today) >= WEEKLY_GAP_ESCALATE
    )
    if stall is not None and stall >= THRESHOLDS['stall_days']:
        m.alerts.append('意思決定が止まっている可能性')
    if m.promise_gaps >= THRESHOLDS['promise_gaps']:
        m.alerts.append('営業時の説明と実態がずれている')
    if m.scope_out >= THRESHOLDS['scope_out']:
        m.alerts.append('追加交渉・契約見直しが要るかも')
    if m.term_gaps >= THRESHOLDS['term_gaps']:
        m.alerts.append('立ち上がり・引き継ぎに注意')
    if rate is not None and rate < WEEKLY_RATE_MIN:
        m.alerts.append('週報が滞っている（忙しさの兆候）')
    if m.structure_gaps >= THRESHOLDS['structure_gaps']:
        m.alerts.append('契約形態と指示系統が一致していない')
    if m.leader_concerns >= THRESHOLDS['leader_concerns']:
        m.alerts.append('リーダーが懸念を抱えている')
    if m.weekly_gap_people >= THRESHOLDS['weekly_gap_people']:
        m.alerts.append('「指摘も指示も来ない」状態が続いている人がいる')
    return m


# 指標の定義（案）と気づけること（構想書スライド18＋spec 004 で足したもの）
DEFINITIONS = [
    ('確認事項の滞留日数', '未解決の確認事項の経過日数の平均', '現場の意思決定が止まっている'),
    ('約束と実際のズレ', '振り返りの「約束と実際」で食い違った行の数', '営業時の説明と実態の乖離'),
    ('スコープ外の件数', '「スコープ外」ラベルの Issue 数', '追加交渉・契約見直しが要る'),
    ('体制のズレ', '実際の指示者・決裁者が体制図と違う件数', '契約形態と指示系統の不一致'),
    ('用語のズレ', '一般と意味が違う用語の数', '立ち上がりの難しさ'),
    ('週報の提出率', '週報がある週の割合（現場単位）', '運営が回っているか'),
    ('リーダーの懸念', '現場のリーダーが書いた未解決の懸念の数（うちメンバーがまだ受け取っていない数）', 'リーダーが一人で抱えている'),
    ('週報の途切れが続いている', '前週から2週以上続けて週報が出ていない人の人数（名前は出さない）', '「指摘も指示も来ない」状態が続いている'),
]
