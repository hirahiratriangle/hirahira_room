"""出題する1問を選ぶ。

考え方は2段階。まず「どの中分類から出すか」を重み付き抽選で決め、
次に「その中分類のどの問題を出すか」を解答履歴から決める。
中分類の重みは本番の出題比率（Category.exam_weight）を土台に、
苦手なほど大きく増幅する。本番で8問出るセキュリティを得意にしても、
1問しか出ない法務ばかり出題されては困るため、苦手度だけでは決めない。
"""

import random

from django.db.models import Max

from .generators import generate_question, generators_for_category
from .models import Attempt, Category, Question
from .stats import WEAK_MIN_ATTEMPTS, category_stats

# 苦手度による増幅の下限と上限。weakness=0（完璧）で 0.3 倍、
# weakness=1（全問不正解）で 2.3 倍。本番比率を壊さない範囲で傾ける。
FOCUS_BASE = 0.3
FOCUS_GAIN = 2.0

# まだ一度も解いていない中分類は、実力が不明なので優先的に触れさせる
UNTOUCHED_BONUS = 1.6

# テンプレート（計算問題）から新規生成する確率。未出題の固定問題が
# 尽きている場合はこの値によらず生成に倒す。
TEMPLATE_RATIO = 0.3


def _category_weights(user, subject, mode):
    """モードに応じた中分類ごとの抽選重みを返す。"""
    from .models import StudySession

    rows = category_stats(user, subject=subject)
    available = {r['category'].id: r for r in rows if r['question_count'] > 0}

    weights = {}
    for category_id, row in available.items():
        category = row['category']
        if subject == Question.SUBJECT_B:
            # 科目Bの内訳は要綱に明記（アルゴリズム16問／セキュリティ4問）
            from .data.categories import SUBJECT_B_WEIGHTS
            base = SUBJECT_B_WEIGHTS.get(category.code, 0)
            if not base:
                continue
        else:
            base = category.exam_weight

        if mode == StudySession.MODE_RANDOM:
            weight = float(base)
        else:
            weight = base * (FOCUS_BASE + FOCUS_GAIN * row['weakness'])
            if row['is_untouched']:
                weight *= UNTOUCHED_BONUS

        if mode == StudySession.MODE_WEAK and not (row['is_weak'] or row['is_untouched']):
            continue

        if weight > 0:
            weights[category_id] = weight

    return weights, {r['category'].id: r for r in rows}


def _weighted_choice(weights):
    if not weights:
        return None
    keys = list(weights)
    return random.choices(keys, weights=[weights[k] for k in keys], k=1)[0]


def _last_result_map(user, question_ids):
    """問題ごとの「直近の解答が正解だったか」と最終解答日時。"""
    rows = (
        Attempt.objects.filter(user=user, question_id__in=question_ids)
        .values('question_id')
        .annotate(last_at=Max('answered_at'))
    )
    last_at = {r['question_id']: r['last_at'] for r in rows}
    if not last_at:
        return {}

    latest = Attempt.objects.filter(
        user=user, question_id__in=list(last_at), answered_at__in=list(last_at.values())
    ).values('question_id', 'is_correct', 'answered_at')

    result = {}
    for row in latest:
        result[row['question_id']] = {
            'is_correct': row['is_correct'],
            'answered_at': row['answered_at'],
        }
    return result


def _pick_from_category(user, category, subject, exclude_ids):
    """中分類の中から1問を選ぶ。未出題 → 前回不正解 → 久しく解いていない順。"""
    questions = list(
        Question.objects.active().for_subject(subject).owned_by(user)
        .filter(category=category).exclude(id__in=exclude_ids)
    )
    # 計算問題のテンプレートは科目A用なので，科目Bのときは使わない
    generators = (
        generators_for_category(category)
        if subject != Question.SUBJECT_B else []
    )

    history = _last_result_map(user, [q.id for q in questions]) if questions else {}
    unseen = [q for q in questions if q.id not in history]
    wrong = [q for q in questions if history.get(q.id, {}).get('is_correct') is False]
    stale = [q for q in questions if history.get(q.id, {}).get('is_correct') is True]
    stale.sort(key=lambda q: history[q.id]['answered_at'])

    # 未出題の固定問題が無い中分類では、テンプレートがあれば新しい数値で作る
    if generators and (not unseen or random.random() < TEMPLATE_RATIO):
        question = generate_question(random.choice(generators), user)
        if question and question.id not in exclude_ids:
            return question, 'テンプレートから新しい数値で生成'

    if unseen:
        return random.choice(unseen), '未出題'
    if wrong:
        return random.choice(wrong), '前回まちがえた問題'
    if stale:
        # 最後に解いてから時間が経っているものから
        head = stale[: max(1, len(stale) // 3)]
        return random.choice(head), 'しばらく解いていない問題'
    return None, None


def _pick_review(user, subject, exclude_ids):
    """直近の解答が不正解だった問題だけを対象にする復習モード。"""
    answered_ids = list(
        Attempt.objects.filter(user=user)
        .values_list('question_id', flat=True).distinct()
    )
    if not answered_ids:
        return None, None

    history = _last_result_map(user, answered_ids)
    wrong_ids = [
        qid for qid, row in history.items()
        if not row['is_correct'] and qid not in exclude_ids
    ]
    if not wrong_ids:
        return None, None

    questions = list(
        Question.objects.active().for_subject(subject).owned_by(user)
        .filter(id__in=wrong_ids)
    )
    if not questions:
        return None, None
    # 苦手な中分類の問題を優先する
    rows = {r['category'].id: r for r in category_stats(user, subject=subject)}
    weights = [max(0.1, rows[q.category_id]['weakness']) for q in questions]
    return random.choices(questions, weights=weights, k=1)[0], '前回まちがえた問題'


def pick_question(user, session, exclude_ids=()):
    """出題する1問と、選んだ理由を返す。出せる問題が無ければ (None, None)。"""
    from .models import StudySession

    exclude_ids = list(exclude_ids)
    subject = session.subject
    mode = session.mode

    if mode == StudySession.MODE_CATEGORY and session.category_id:
        question, reason = _pick_from_category(
            user, session.category, subject, exclude_ids
        )
        if question:
            return question, reason
        # 指定分野を解き切ったら、除外を解いてもう一度
        return _pick_from_category(user, session.category, subject, [])

    if mode == StudySession.MODE_REVIEW:
        question, reason = _pick_review(user, subject, exclude_ids)
        if question:
            return question, reason
        # 復習対象が無ければ通常の重点出題にフォールバックする
        mode = StudySession.MODE_FOCUS

    weights, rows = _category_weights(user, subject, mode)
    if not weights and mode == StudySession.MODE_WEAK:
        # まだ苦手が定まっていない場合は重点出題に切り替える
        weights, rows = _category_weights(user, subject, StudySession.MODE_FOCUS)

    tried = set()
    while weights:
        category_id = _weighted_choice(weights)
        if category_id is None:
            break
        tried.add(category_id)
        category = rows[category_id]['category']
        question, reason = _pick_from_category(user, category, subject, exclude_ids)
        if question:
            if mode in (StudySession.MODE_FOCUS, StudySession.MODE_WEAK) and rows[category_id]['is_weak']:
                reason = '苦手分野のため重点出題'
            return question, reason
        weights.pop(category_id, None)

    # すべて出題済みなら除外を解いて選び直す
    if exclude_ids:
        return pick_question(user, session, exclude_ids=[])
    return None, None
