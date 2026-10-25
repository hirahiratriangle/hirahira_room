"""運営指標。すべて現場単位で集計し、個人を比べる数字は作らない。"""
from dataclasses import dataclass, field
from datetime import timedelta

from django.utils import timezone

from .models import Document, Question, week_start

# 週報の提出率を見る期間（週）
WEEKLY_WINDOW = 8

# 「手を打つか」の目安。超えたら画面で強調する。
THRESHOLDS = {
    'stall_days': 7,      # 確認事項の平均滞留日数
    'promise_gaps': 2,    # 約束と実際のズレ件数
    'scope_out': 3,       # スコープ外作業の件数
    'term_gaps': 5,       # 用語のズレ件数
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
    return m
