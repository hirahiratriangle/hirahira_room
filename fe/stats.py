"""正答率の集計と、苦手度の算出。

苦手度は素の正答率ではなく、事前分布（合格ライン相当の 0.6）へ寄せた
平滑化正答率から求める。1〜2問しか解いていない中分類が「正答率0%の
最重要苦手分野」として暴れるのを防ぐため。
"""

from django.db.models import Avg, Count, Max, Q

from .models import Attempt, Category, Question

# ベイズ平滑化のパラメータ。SMOOTHING_PRIOR は科目基準点（600/1000）に
# 対応させ、SMOOTHING_STRENGTH 問ぶんの「仮の解答」を上乗せして扱う。
SMOOTHING_PRIOR = 0.6
SMOOTHING_STRENGTH = 5

# 「苦手」と判定する平滑化正答率のしきい値と、判定に必要な最低解答数
WEAK_THRESHOLD = 0.6
WEAK_MIN_ATTEMPTS = 3


def smoothed_rate(correct, total):
    """平滑化した正答率。未解答なら事前分布そのもの。"""
    return (correct + SMOOTHING_STRENGTH * SMOOTHING_PRIOR) / (total + SMOOTHING_STRENGTH)


def category_stats(user, subject=None):
    """中分類ごとの成績を、要綱の中分類順に返す。"""
    attempts = Attempt.objects.filter(user=user)
    if subject:
        attempts = attempts.filter(question__subject=subject)

    aggregated = {
        row['category']: row
        for row in attempts.values('category').annotate(
            total=Count('id'),
            correct=Count('id', filter=Q(is_correct=True)),
            last_answered=Max('answered_at'),
        )
    }

    available = {
        row['category']: row['n']
        for row in Question.objects.active().for_subject(subject)
        .values('category').annotate(n=Count('id'))
    }

    rows = []
    for category in Category.objects.all():
        agg = aggregated.get(category.id, {})
        total = agg.get('total', 0)
        correct = agg.get('correct', 0)
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
            'last_answered': agg.get('last_answered'),
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
    attempts = Attempt.objects.filter(user=user)
    total = attempts.count()
    correct = attempts.filter(is_correct=True).count()

    recent = list(attempts.order_by('-answered_at')[:50])
    recent_correct = sum(1 for a in recent if a.is_correct)

    study_days = attempts.dates('answered_at', 'day').count()

    return {
        'total': total,
        'correct': correct,
        'wrong': total - correct,
        'rate': (correct / total) if total else None,
        'rate_percent': round(correct / total * 100) if total else None,
        'recent_total': len(recent),
        'recent_rate_percent': round(recent_correct / len(recent) * 100) if recent else None,
        'study_days': study_days,
        # 科目Aの基準点は600/1000点（IRT）。素点の目安として60%を置く。
        'pass_line_percent': 60,
    }


def daily_counts(user, days=14):
    """直近の日別解答数（学習の継続を見るため）。"""
    from datetime import timedelta

    from django.utils import timezone

    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    rows = (
        Attempt.objects.filter(user=user, answered_at__date__gte=start)
        .values('answered_at__date')
        .annotate(total=Count('id'), correct=Count('id', filter=Q(is_correct=True)))
    )
    by_date = {row['answered_at__date']: row for row in rows}

    result = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        row = by_date.get(day)
        result.append({
            'date': day,
            'total': row['total'] if row else 0,
            'correct': row['correct'] if row else 0,
        })
    return result
