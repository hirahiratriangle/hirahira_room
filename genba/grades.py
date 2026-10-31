"""階級の見直し。メンバーの成長を、階級と標準単価につなぐ。

会社の成長は人の成長から始まる、という構想を数字でも貫くための仕組み。流れは次のとおり。

1. マネジメントが成長を承認する（レベルが確定する）
2. 次の階級に必要なレベルへ届いた人が、マネジメントの画面に候補として出る
3. マネジメントが見直しを提案する
4. 会社が採用か見送りを決める。階級を定めるのは会社の役割なので、自動では上げない
5. 採用すると、本人が次に入れる月から新しい階級の標準単価で標準売上が計算される

人月の行はその月の階級を写して持つので、過去の月の標準売上は動かない。
見送った提案は同じ成長では候補に戻らない。次の成長が承認されると、また候補になる。
"""
from django.utils import timezone

from . import growth
from .models import Grade, GradeAssignment, GradeReview


def next_grade(grade):
    return Grade.objects.filter(order__gt=grade.order).first()


def best_growth():
    """人ごとの、いちばん高いレベルの承認した成長。同じレベルなら新しいほう"""
    best = {}
    for goal in growth.approved_reaches():
        current = best.get(goal.author_id)
        if not current or (goal.approved_level, goal.approved_on) >= (current.approved_level, current.approved_on):
            best[goal.author_id] = goal
    return best


def candidates(viewer=None):
    """階級の見直しの候補。提案中の人と、同じ成長で提案済みの人は出さない。本人の分は本人に出さない"""
    best = best_growth()
    waiting = set(GradeReview.objects.filter(status=GradeReview.STATUS_OPEN).values_list('user_id', flat=True))
    reviewed = set(GradeReview.objects.values_list('goal_id', flat=True))
    rows = []
    for assignment in GradeAssignment.objects.select_related('user', 'grade').order_by('user__username'):
        goal, proposed = best.get(assignment.user_id), next_grade(assignment.grade)
        if not goal or not proposed or goal.approved_level < proposed.required_level:
            continue
        if assignment.user_id in waiting or goal.pk in reviewed or assignment.user == viewer:
            continue
        rows.append({'user': assignment.user, 'current': assignment.grade, 'proposed': proposed, 'goal': goal})
    return rows


def propose(goal, by):
    """候補にある人の見直しを提案する。候補にない成長なら None を返す"""
    row = next((r for r in candidates(by) if r['goal'] == goal), None)
    if row is None:
        return None
    return GradeReview.objects.create(user=row['user'], goal=goal, current_grade=row['current'],
                                      proposed_grade=row['proposed'], proposed_by=by)


def decide(review, by, adopted):
    """会社が採用か見送りを決める。採用したら人の階級を変える"""
    review.status = GradeReview.STATUS_ADOPTED if adopted else GradeReview.STATUS_DECLINED
    review.decided_by, review.decided_at = by, timezone.now()
    review.save()
    if adopted:
        GradeAssignment.objects.update_or_create(user=review.user, defaults={'grade': review.proposed_grade})
