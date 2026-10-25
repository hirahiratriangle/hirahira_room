"""危険信号（入場時チェックリスト上流編 5章）。

本人の記録だけを見て判定し、本人のホームと現場のページにだけ出す（他の立場には出さない）。
"""
from dataclasses import dataclass
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from . import kit, kit_data
from .models import Document, Membership, Summary, target_url, week_start

UNREADABLE_ASK_DAYS = 2     # 一覧を持って質問の時間をもらう目安
UNREADABLE_STUCK_DAYS = 3   # 「3日経っても理解が進んでいない」
SUMMARY_DAYS = 5            # 「理解の『まとめ』を出す（5日目）」
SUMMARY_WINDOW_DAYS = 28    # まとめの危険信号は入場期（4週）だけ出す


@dataclass
class Signal:
    sign: str
    action: str
    detected: bool | None   # None は判定しない（案内だけ）
    detail: str = ''
    link: tuple | None = None   # (名前, URL)


def signals_for(user, site, today=None):
    today = today or timezone.localdate()
    membership = Membership.objects.filter(user=user, site=site).first()
    joined_on = membership.joined_on if membership else site.started_on
    checks = {
        'unreadable_stuck': _unreadable_stuck(user, site),
        'no_weekly': _no_weekly(user, site, joined_on, today),
        'no_summary': _no_summary(user, site, joined_on, today),
    }
    result = []
    for s in kit_data.DANGER_SIGNS:
        code = kit.DETECTED_SIGNS.get(s['sign'])
        detected, detail, link = checks[code] if code else (None, '', None)
        if code is None and s['sign'] in kit.SIGN_TARGETS:
            target = kit.SIGN_TARGETS[s['sign']]
            name = 'スコープ外 Issue' if target == 'scope_out' else kit.RECORD_TARGETS[target]
            link = (name, target_url(site.pk, target))
        result.append(Signal(s['sign'], s['action'], detected, detail, link))
    return result


def _unreadable_stuck(user, site):
    since = timezone.now() - timedelta(days=UNREADABLE_STUCK_DAYS)
    stuck = site.questions.filter(author=user, unreadable=True, is_open=True, created_at__lte=since)
    n = stuck.count()
    if not n:
        return False, '', None
    url = reverse('genba:site_detail', args=[site.pk]) + '?tab=unreadable'
    return True, f'{UNREADABLE_STUCK_DAYS}日以上止まっている「読めない」が{n}件', ('「読めない」一覧', url)


def _no_weekly(user, site, joined_on, today):
    last_week = week_start(today) - timedelta(weeks=1)
    if week_start(joined_on) > last_week:
        return False, '', None   # 入場して1週たっていない
    if site.documents.filter(author=user, kind=Document.KIND_WEEKLY, week_of=last_week).exists():
        return False, '', None
    return True, f'{last_week.month}/{last_week.day}の週の週報が出ていない', ('週報を書く', target_url(site.pk, 'doc:weekly'))


def _no_summary(user, site, joined_on, today):
    days = (today - joined_on).days
    if not SUMMARY_DAYS <= days <= SUMMARY_WINDOW_DAYS:
        return False, '', None
    if Summary.objects.filter(site=site, author=user, submitted_at__isnull=False).exists():
        return False, '', None
    return True, f'入場から{days}日、まとめをまだ出していない', ('まとめ', target_url(site.pk, 'summary'))


def missing_last_weekly(membership, today=None):
    """前週の週報が出ていない（リーダー画面の「週報が途切れている」）"""
    today = today or timezone.localdate()
    return _no_weekly(membership.user, membership.site, membership.joined_on, today)[0]


def unreadable_needs_question(question):
    """2日経っても進まない「読めない」は、一覧を持って質問の時間をもらう"""
    return question.unreadable and question.is_open and question.days_open >= UNREADABLE_ASK_DAYS


def detected_count(signals):
    return sum(1 for s in signals if s.detected)

