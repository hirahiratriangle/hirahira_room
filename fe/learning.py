"""学習モードの組み立て。

「先に読んで、あとで解く」を1ラウンドにする。読むのは問題の解説文ではなく、
概念をひとまとまりで説明した技術解説（LearningNote）。解く段階で、その解説が
扱う範囲から出題する。

どの解説を読むかは苦手な分野から選ぶ。まだ読んでいないものを先に出す。
"""

import random

from django.db import transaction

from .models import LearningItem, LearningNote, LearningRound, Question
from .stats import category_stats


@transaction.atomic
def start_round(user, session, minutes, count=None):
    """読む解説を1本選び、その分野から出題ぶんを取ってラウンドを作る。

    解説が無ければ始められない。問題と同じく、利用者が JSON で取り込む。
    """
    count = count or LearningRound.QUESTION_COUNT

    note = pick_note(user, session.subject)
    if note is None:
        return None

    questions = questions_for_note(user, note, session.subject, count)
    if not questions:
        return None

    round_ = LearningRound.objects.create(
        user=user, note=note, subject=session.subject, reading_seconds=minutes * 60,
    )
    order = list(questions)
    random.shuffle(order)
    LearningItem.objects.bulk_create([
        LearningItem(round=round_, question=question, order=index)
        for index, question in enumerate(order)
    ])
    return round_


def pick_note(user, subject):
    """次に読む解説を選ぶ。苦手な分野のものを優先する。

    同じ分野に解説が複数あるときは、まだ読んでいないものから出す。
    """
    notes = list(
        LearningNote.objects.filter(owner=user).select_related('category')
    )
    if not notes:
        return None

    weakness = {
        row['category'].id: row['weakness']
        for row in category_stats(user, subject=subject)
    }
    read_counts = {}
    for note in notes:
        read_counts[note.id] = note.rounds.filter(user=user).count()

    fewest = min(read_counts.values())
    fresh = [n for n in notes if read_counts[n.id] == fewest]

    weights = [max(0.05, weakness.get(n.category_id, 0.4)) for n in fresh]
    return random.choices(fresh, weights=weights, k=1)[0]


def questions_for_note(user, note, subject, count):
    """解説が扱う範囲から出題する問題を選ぶ。

    小分類が一致するものを優先し、足りなければ同じ中分類から補う。
    読んだ内容と出題がずれないようにするため。
    """
    pool = (
        Question.objects.active().for_subject(subject).owned_by(user)
        .filter(category=note.category).select_related('category')
    )
    same_topic = [q for q in pool if note.topic and q.topic == note.topic]
    rest = [q for q in pool if q not in same_topic]
    random.shuffle(same_topic)
    random.shuffle(rest)
    return (same_topic + rest)[:count]


def current_item(round_):
    """いま解くべき1問。解き終わっていれば None。"""
    return round_.items.select_related('question', 'question__category').filter(
        order=round_.position
    ).first()


def grade(item, selected):
    """1問を採点してラウンドを次に進める。"""
    item.selected_index = selected
    item.is_correct = selected == item.question.answer_index
    item.save(update_fields=['selected_index', 'is_correct'])
    return item.is_correct
