"""学習モードの組み立て。

「先に読んで、あとで解く」を1ラウンドにする。読むのは問題の解説文ではなく、
概念をひとまとまりで説明した技術解説（LearningNote）。解く段階で、その解説が
扱う範囲から出題する。

どの解説を読むかは苦手な分野から選ぶ。まだ読んでいないものを先に出す。

出題は読んだ解説が扱う小分類に限る。同じ中分類から補わないので、1ラウンドの
問数は解説によって変わる。
"""

import random

from django.db import transaction
from django.db.models import Count

from .models import LearningItem, LearningNote, LearningRound, Question
from .stats import category_stats


@transaction.atomic
def start_round(learner, session, minutes, count=None, note=None, category=None):
    """読む解説を1本選び、その解説の範囲から出題ぶんを取ってラウンドを作る。

    note を渡せばそれを読む。渡さなければ苦手な分野から選ぶ。category を渡すと
    その中分類の中から選ぶ。解説が無ければ始められない。問題と同じく、
    管理用の ID が JSON で取り込む。
    """
    count = count or LearningRound.QUESTION_COUNT

    if note is None:
        note = pick_note(learner, session.subject, category=category)
    elif note.category.subject != session.subject:
        # 別の科目の解説を指定されても、その科目の問題は出せない
        return None
    if note is None:
        return None

    questions = questions_for_note(learner, note, session.subject, count)
    if not questions:
        return None

    round_ = LearningRound.objects.create(
        learner=learner, note=note, subject=session.subject, reading_seconds=minutes * 60,
    )
    order = list(questions)
    random.shuffle(order)
    LearningItem.objects.bulk_create([
        LearningItem(round=round_, question=question, order=index)
        for index, question in enumerate(order)
    ])
    return round_


def pick_note(learner, subject, category=None):
    """次に読む解説を選ぶ。苦手な分野のものを優先する。

    同じ分野に解説が複数あるときは、まだ読んでいないものから出す。
    category を渡すと、その中分類の中だけから選ぶ。

    出題できる問題を持つ解説だけを候補にする。持たない解説を選ぶと、読ませた
    あとに出題できず、ラウンドを作れないため。
    """
    # 分類は科目ごとに別なので、解説も科目で絞る。絞らないと、科目Aの回で
    # 科目Bの解説を読まされ、そのあと出題できる問題が無いことになる。
    notes = LearningNote.objects.filter(category__subject=subject)
    if category is not None:
        notes = notes.filter(category=category)
    counts = question_counts(subject)
    notes = [
        note for note in notes.select_related('category')
        if counts.get((note.category_id, note.topic))
    ]
    if not notes:
        return None

    weakness = {
        row['category'].id: row['weakness']
        for row in category_stats(learner, subject=subject)
    }
    read_counts = {}
    for note in notes:
        read_counts[note.id] = note.rounds.filter(learner=learner).count()

    fewest = min(read_counts.values())
    fresh = [n for n in notes if read_counts[n.id] == fewest]

    weights = [max(0.05, weakness.get(n.category_id, 0.4)) for n in fresh]
    return random.choices(fresh, weights=weights, k=1)[0]


def question_counts(subject):
    """小分類ごとに、出題できる問題が何問あるか。(中分類ID, 小分類) をキーにする。

    出題は解説と同じ小分類に限るので、この数がそのまま1ラウンドの上限になる。
    """
    rows = (
        Question.objects.active().for_subject(subject)
        .values('category_id', 'topic').annotate(n=Count('id'))
    )
    return {(row['category_id'], row['topic']): row['n'] for row in rows}


def note_menu(learner, subject):
    """学習対象の一覧。中分類でまとめ、その下に小分類（解説）を並べる。

    どれを選ぶか決められるように、中分類の正答率と、解説ごとの学習回数、
    そして解説ごとに出題できる問数を添える。問数が 0 の解説は選べない。
    """
    notes = list(
        LearningNote.objects.filter(category__subject=subject)
        .select_related('category')
    )
    if not notes:
        return []

    stats = {row['category'].id: row for row in category_stats(learner, subject=subject)}
    counts = {
        row['note']: row['n']
        for row in LearningRound.objects.filter(learner=learner, note__isnull=False)
        .values('note').annotate(n=Count('id'))
    }

    available = question_counts(subject)

    groups = {}
    for note in notes:
        group = groups.setdefault(note.category_id, {
            'category': note.category,
            'stats': stats.get(note.category_id),
            'notes': [],
            'questions': 0,
        })
        questions = available.get((note.category_id, note.topic), 0)
        group['notes'].append({
            'note': note, 'rounds': counts.get(note.id, 0), 'questions': questions,
        })
        # 同じ小分類に解説が2本あっても問題は同じなので、重ねて数えない
        group['questions'] = max(group['questions'], questions)

    for group in groups.values():
        group['notes'].sort(key=lambda n: (n['note'].topic, n['note'].title))
    return sorted(groups.values(), key=lambda g: g['category'].code)


def questions_for_note(learner, note, subject, count):
    """解説が扱う範囲から出題する問題を選ぶ。

    解説と同じ小分類の問題だけを出す。足りなくても同じ中分類から補わない。
    補うと、読んでいない小分類の問題が混ざり、読んだ範囲が身についたのか
    どうかをラウンドの正答数で測れなくなるため。
    その代わり、1ラウンドの問数は count に届かないことがある。
    """
    pool = list(
        Question.objects.active().for_subject(subject)
        .filter(category=note.category, topic=note.topic)
        .select_related('category')
    )
    random.shuffle(pool)
    return pool[:count]


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
