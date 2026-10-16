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

from .models import (Attempt, Category, CategoryProgress, DailyProgress,
                     PassReport, Question)

# ベイズ平滑化のパラメータ。SMOOTHING_PRIOR は科目基準点（600/1000）に
# 対応させ、SMOOTHING_STRENGTH 問ぶんの「仮の解答」を上乗せして扱う。
SMOOTHING_PRIOR = 0.6
SMOOTHING_STRENGTH = 5

# 「苦手」と判定する平滑化正答率のしきい値と、判定に必要な最低解答数
WEAK_THRESHOLD = 0.6
WEAK_MIN_ATTEMPTS = 3

# 「直近の調子」を見る日数
RECENT_DAYS = 7


def record_progress(learner, question, is_correct):
    """解答を理解度に反映する。問題が消えても残る記録はここだけ。"""
    now = timezone.now()
    progress, _ = CategoryProgress.objects.get_or_create(
        learner=learner, category=question.category, subject=question.subject
    )
    CategoryProgress.objects.filter(pk=progress.pk).update(
        answered=F('answered') + 1,
        correct=F('correct') + (1 if is_correct else 0),
        last_answered_at=now,
    )

    daily, _ = DailyProgress.objects.get_or_create(
        learner=learner, date=timezone.localdate(now)
    )
    DailyProgress.objects.filter(pk=daily.pk).update(
        answered=F('answered') + 1,
        correct=F('correct') + (1 if is_correct else 0),
    )


def categories_for(subject=None):
    """その科目の分類。科目を指定しなければ全部返す。

    科目Aは中分類（1〜23）、科目Bは要綱が別に定める5項目（101〜105）で、
    重なりがない。科目を絞らずに並べると、解きようのない分類が混ざる。
    """
    queryset = Category.objects.all()
    if subject in (Category.SUBJECT_A, Category.SUBJECT_B):
        queryset = queryset.filter(subject=subject)
    return queryset


def smoothed_rate(correct, total):
    """平滑化した正答率。未解答なら事前分布そのもの。"""
    return (correct + SMOOTHING_STRENGTH * SMOOTHING_PRIOR) / (total + SMOOTHING_STRENGTH)


def category_stats(learner, subject=None):
    """中分類ごとの成績を、要綱の中分類順に返す。"""
    progress = CategoryProgress.objects.filter(learner=learner)
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
        for row in Question.objects.active().for_subject(subject)
        .values('category').annotate(n=Count('id'))
    }

    rows = []
    for category in categories_for(subject):
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


def _review_counts(learner):
    """中分類ごとの「直近の解答がまちがいだった問題」の数。復習モードで出せる数になる。"""
    last = {}
    for question_id, is_correct in (
        Attempt.objects.filter(learner=learner)
        .order_by('answered_at', 'id').values_list('question_id', 'is_correct')
    ):
        last[question_id] = is_correct
    missed = [question_id for question_id, is_correct in last.items() if not is_correct]
    if not missed:
        return {}
    counts = {}
    for row in (
        Question.objects.active().filter(id__in=missed)
        .values('category', 'subject').annotate(n=Count('id'))
    ):
        counts.setdefault(row['category'], {})[row['subject']] = row['n']
    return counts


def category_options(learner, mode=None):
    """出題設定の「分野」に並べる選択肢。

    分類は科目ごとに別なので、画面では科目に合わせて出し分ける。
    テンプレートに model を渡さず素の値にしてあるのは、そのまま
    json_script で JavaScript へ渡し、科目の切替に使うため。

    分野はどの出題モードでも選べるが、そのモードで出せる問題が無い分野は選べなくする。
    件数と選べるかどうかはモードごとに違うので、全モードぶんを by_mode に持ち、
    いまのモード（mode）の値を count と selectable に入れておく。
    """
    from .generators import REGISTRY
    from .models import QuestionTemplate, StudySession

    def by_category(queryset):
        counts = {}
        for row in queryset.values('category', 'subject').annotate(n=Count('id')):
            counts.setdefault(row['category'], {})[row['subject']] = row['n']
        return counts

    everything = by_category(Question.objects.active())
    ipa = by_category(Question.objects.active().from_ipa())
    review = _review_counts(learner)

    # 計算問題かどうかは形から見分けるので、データベースでは数えられない
    calc = {}
    for question in Question.objects.active().only(
        'category_id', 'subject', 'stem', 'choices', 'template_id'
    ):
        if question.is_calculation:
            by_subject = calc.setdefault(question.category_id, {})
            by_subject[question.subject] = by_subject.get(question.subject, 0) + 1

    # 計算問題のテンプレートがある中分類は、固定の問題が無くても新しい数値で出せる
    generated = {
        template.category_id
        for template in QuestionTemplate.objects.filter(is_active=True)
        if template.key in REGISTRY
    }
    weak = {row['category'].id for row in category_stats(learner) if row['is_weak']}

    options = []
    for category in Category.objects.all():
        def count(source):
            # その分類の科目ぶんだけ数える。取り込み前の古いデータが
            # 別の科目で残っていても、件数に混ざらないようにする。
            return source.get(category.id, {}).get(category.subject, 0)

        total = count(everything)
        makes = category.id in generated and category.subject == Question.SUBJECT_A
        general = [total, total > 0 or makes]
        by_mode = {
            StudySession.MODE_FOCUS: general,
            StudySession.MODE_RANDOM: general,
            StudySession.MODE_CATEGORY: general,
            StudySession.MODE_IPA: [count(ipa), count(ipa) > 0],
            StudySession.MODE_CALC: [count(calc), count(calc) > 0 or makes],
            # 苦手と判定した分野だけ。判定していない分野は、問題があっても選べない
            StudySession.MODE_WEAK: [total, category.id in weak and general[1]],
            StudySession.MODE_REVIEW: [count(review), count(review) > 0],
        }
        current = by_mode.get(mode, general)
        options.append({
            'code': category.code,
            'label': category.label,
            'subject': category.subject,
            'question_count': total,
            'by_mode': by_mode,
            'count': current[0],
            'selectable': current[1],
        })
    return options


def weak_categories(rows, limit=5):
    """苦手と判定した中分類を、苦手な順に返す。

    判定は is_weak（3問以上解いて、平滑化した正答率が 60％ 未満）に従う。
    解答数だけで選ぶと、全問正解の分野まで「苦手分野」に並んでしまう。
    """
    weak = [r for r in rows if r['is_weak']]
    weak.sort(key=lambda r: (r['smoothed'], -r['total']))
    return weak[:limit]


def field_stats(rows):
    """分野（預金／融資／内国為替／銀行取引関連法）単位の集計。"""
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
    for key, _ in Category.FIELD_CHOICES:
        bucket = buckets.get(key)
        if not bucket:
            continue
        total = bucket['total']
        bucket['rate'] = (bucket['correct'] / total) if total else None
        bucket['rate_percent'] = round(bucket['correct'] / total * 100) if total else None
        result.append(bucket)
    return result


def overall_stats(learner):
    """全体成績と、直近の調子。"""
    totals = CategoryProgress.objects.filter(learner=learner).aggregate(
        total=Sum('answered'), correct=Sum('correct')
    )
    total = totals['total'] or 0
    correct = totals['correct'] or 0

    since = timezone.localdate() - timedelta(days=RECENT_DAYS - 1)
    recent = DailyProgress.objects.filter(learner=learner, date__gte=since).aggregate(
        total=Sum('answered'), correct=Sum('correct')
    )
    recent_total = recent['total'] or 0
    recent_correct = recent['correct'] or 0

    study_days = DailyProgress.objects.filter(learner=learner, answered__gt=0).count()

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


def daily_counts(learner, days=14):
    """直近の日別解答数（学習の継続を見るため）。"""
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    by_date = {
        row.date: row
        for row in DailyProgress.objects.filter(learner=learner, date__gte=start)
    }
    return [
        {
            'date': start + timedelta(days=offset),
            'total': getattr(by_date.get(start + timedelta(days=offset)), 'answered', 0),
            'correct': getattr(by_date.get(start + timedelta(days=offset)), 'correct', 0),
        }
        for offset in range(days)
    ]


def site_summary():
    """全 ID を合わせた集計。のべ解答数・分類別の解答数・合格者数。

    解答ログ（Attempt）は問題の差し替えで消えるので、のべ数は
    消えない CategoryProgress から数える。ID ごとの成績が分かる値は返さない。

    履歴を残さない利用（ID のない記録）は数えない。いずれ消える記録なので、
    数えると、あとで総数が減って辻褄が合わなくなるため。
    """
    registered = CategoryProgress.objects.filter(learner__code__isnull=False)
    per_category = {
        row['category']: row
        for row in registered.values('category').annotate(
            total=Sum('answered'), correct=Sum('correct'),
        )
    }

    subjects = []
    for value, label in Question.SUBJECT_CHOICES:
        if not categories_for(value).exists():
            continue   # 法務3級は1科目だけ。分類の無い科目は集計に出さない
        rows = []
        for category in categories_for(value):
            agg = per_category.get(category.id, {})
            total = agg.get('total') or 0
            correct = agg.get('correct') or 0
            rows.append({
                'category': category,
                'total': total,
                'correct': correct,
                'rate_percent': round(correct / total * 100) if total else None,
            })
        subject_total = sum(r['total'] for r in rows)
        subject_correct = sum(r['correct'] for r in rows)
        for row in rows:
            row['share_percent'] = (
                round(row['total'] / subject_total * 100, 1) if subject_total else 0.0
            )
        subjects.append({
            'value': value,
            'label': label,
            'rows': rows,
            # 分野（預金・融資など）ごとの内訳
            'fields': field_stats(rows) if value == Question.SUBJECT_A else [],
            'total': subject_total,
            'correct': subject_correct,
            'rate_percent': (
                round(subject_correct / subject_total * 100) if subject_total else None
            ),
        })

    total = sum(s['total'] for s in subjects)
    correct = sum(s['correct'] for s in subjects)
    return {
        'total': total,
        'correct': correct,
        'rate_percent': round(correct / total * 100) if total else None,
        'learners': (
            registered.filter(answered__gt=0).values('learner').distinct().count()
        ),
        'passers': PassReport.objects.count(),
        'subjects': subjects,
    }
