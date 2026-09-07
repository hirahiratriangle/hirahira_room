"""理解度の記録と集計。

問題は取り込みのたびに入れ替わって消えるので、成績を問題や解答ログから
集計すると、差し替えのたびに実力の記録まで失われてしまう。
そこで解答するたびに「分野ごとの理解度」と「日ごとの学習量」を積み上げ、
集計はそちらから行う。問題が消えても、この人の到達点は残る。

苦手度は素の正答率ではなく、事前分布（合格ライン相当の 0.6）へ寄せた
平滑化正答率から求める。1〜2問しか解いていない中分類が「正答率0%の
最重要苦手分野」として暴れるのを防ぐため。
"""

from datetime import timedelta

from django.db.models import Count, F, Sum
from django.utils import timezone

from .models import Category, CategoryProgress, DailyProgress, Question

# ベイズ平滑化のパラメータ。SMOOTHING_PRIOR は科目基準点（600/1000）に
# 対応させ、SMOOTHING_STRENGTH 問ぶんの「仮の解答」を上乗せして扱う。
SMOOTHING_PRIOR = 0.6
SMOOTHING_STRENGTH = 5

# 「苦手」と判定する平滑化正答率のしきい値と、判定に必要な最低解答数
WEAK_THRESHOLD = 0.6
WEAK_MIN_ATTEMPTS = 3

# 「直近の調子」を見る日数
RECENT_DAYS = 7


def record_progress(user, question, is_correct):
    """解答を理解度に反映する。問題が消えても残る記録はここだけ。"""
    now = timezone.now()
    progress, _ = CategoryProgress.objects.get_or_create(
        user=user, category=question.category, subject=question.subject
    )
    CategoryProgress.objects.filter(pk=progress.pk).update(
        answered=F('answered') + 1,
        correct=F('correct') + (1 if is_correct else 0),
        last_answered_at=now,
    )

    daily, _ = DailyProgress.objects.get_or_create(
        user=user, date=timezone.localdate(now)
    )
    DailyProgress.objects.filter(pk=daily.pk).update(
        answered=F('answered') + 1,
        correct=F('correct') + (1 if is_correct else 0),
    )


def smoothed_rate(correct, total):
    """平滑化した正答率。未解答なら事前分布そのもの。"""
    return (correct + SMOOTHING_STRENGTH * SMOOTHING_PRIOR) / (total + SMOOTHING_STRENGTH)


def category_stats(user, subject=None):
    """中分類ごとの成績を、要綱の中分類順に返す。"""
    progress = CategoryProgress.objects.filter(user=user)
    if subject in (Question.SUBJECT_A, Question.SUBJECT_B):
        progress = progress.filter(subject=subject)

    aggregated = {}
    for row in progress.values('category').annotate(
        total=Sum('answered'), correct=Sum('correct'),
    ):
        aggregated[row['category']] = row
    last_answered = {
        row['category']: row['last']
        for row in progress.values('category').annotate(last=F('last_answered_at'))
    }

    available = {
        row['category']: row['n']
        for row in Question.objects.active().for_subject(subject).owned_by(user)
        .values('category').annotate(n=Count('id'))
    }

    rows = []
    for category in Category.objects.all():
        agg = aggregated.get(category.id, {})
        total = agg.get('total') or 0
        correct = agg.get('correct') or 0
        smooth = smoothed_rate(correct, total)
        rows.append({
            'category': category,
            'total': total,
            'correct': correct,
            'wrong': total - correct,
            'rate': (correct / total) if total else None,
            'rate_percent': round(correct / total * 100) if total else None,
            'smoothed': smooth,
            'weakness': 1.0 - smooth,
            'last_answered': last_answered.get(category.id),
            'question_count': available.get(category.id, 0),
            'is_weak': total >= WEAK_MIN_ATTEMPTS and smooth < WEAK_THRESHOLD,
            'is_untouched': total == 0,
        })
    return rows


def weak_categories(rows, limit=5):
    """苦手な順に中分類を返す。解答実績のあるものだけが対象。"""
    answered = [r for r in rows if r['total'] >= WEAK_MIN_ATTEMPTS]
    answered.sort(key=lambda r: (r['smoothed'], -r['total']))
    return answered[:limit]


def field_stats(rows):
    """分野（テクノロジ系／マネジメント系／ストラテジ系）単位の集計。"""
    buckets = {}
    for row in rows:
        key = row['category'].field
        bucket = buckets.setdefault(key, {
            'field': key,
            'field_label': row['category'].get_field_display(),
            'total': 0,
            'correct': 0,
            'weight': 0,
        })
        bucket['total'] += row['total']
        bucket['correct'] += row['correct']
        bucket['weight'] += row['category'].exam_weight

    result = []
    for key in (Category.FIELD_TECHNOLOGY, Category.FIELD_MANAGEMENT, Category.FIELD_STRATEGY):
        bucket = buckets.get(key)
        if not bucket:
            continue
        total = bucket['total']
        bucket['rate'] = (bucket['correct'] / total) if total else None
        bucket['rate_percent'] = round(bucket['correct'] / total * 100) if total else None
        result.append(bucket)
    return result


def overall_stats(user):
    """全体成績と、直近の調子。"""
    totals = CategoryProgress.objects.filter(user=user).aggregate(
        total=Sum('answered'), correct=Sum('correct')
    )
    total = totals['total'] or 0
    correct = totals['correct'] or 0

    since = timezone.localdate() - timedelta(days=RECENT_DAYS - 1)
    recent = DailyProgress.objects.filter(user=user, date__gte=since).aggregate(
        total=Sum('answered'), correct=Sum('correct')
    )
    recent_total = recent['total'] or 0
    recent_correct = recent['correct'] or 0

    study_days = DailyProgress.objects.filter(user=user, answered__gt=0).count()

    return {
        'total': total,
        'correct': correct,
        'wrong': total - correct,
        'rate': (correct / total) if total else None,
        'rate_percent': round(correct / total * 100) if total else None,
        'recent_days': RECENT_DAYS,
        'recent_total': recent_total,
        'recent_rate_percent': (
            round(recent_correct / recent_total * 100) if recent_total else None
        ),
        'study_days': study_days,
        # 科目Aの基準点は600/1000点（IRT）。素点の目安として60%を置く。
        'pass_line_percent': 60,
    }


def daily_counts(user, days=14):
    """直近の日別解答数（学習の継続を見るため）。"""
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    by_date = {
        row.date: row
        for row in DailyProgress.objects.filter(user=user, date__gte=start)
    }
    return [
        {
            'date': start + timedelta(days=offset),
            'total': getattr(by_date.get(start + timedelta(days=offset)), 'answered', 0),
            'correct': getattr(by_date.get(start + timedelta(days=offset)), 'correct', 0),
        }
        for offset in range(days)
    ]
