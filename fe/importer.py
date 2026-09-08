"""問題 JSON の検証と一括取り込み。

問題はアカウントごとに持ち、JSON の取り込みが唯一の登録経路になる。
取り込みは常に一括差し替え。既存の問題との一致・不一致は見ず、
ファイルの内容をそのままその人の問題集にする。

古い問題は削除するのでデータは増え続けない。解答履歴も問題に紐づくので
一緒に消える。残すのは分野ごとの理解度のほうで、これは解答のたびに
別のテーブルへ積み上げているため、正答率と苦手分野の判定は続く。
"""

import json
from django.db import transaction

from .models import Category, LearningNote, Question

LABELS = 'アイウエオカキクケコ'
MAX_CHOICES = len(LABELS)

class ImportError_(ValueError):
    """取り込めない JSON だったことを表す。利用者に見せる文言を持つ。"""


def parse(payload):
    """文字列を取り込む中身にする。読めなければ理由を添えて失敗させる。

    受け付ける形は2つ。問題だけを並べた配列と、技術解説を添えた
    {"notes": [...], "questions": [...]} の形。
    """
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ImportError_('JSON として読めません：{}'.format(exc))

    if isinstance(data, list):
        questions, notes = data, []
    elif isinstance(data, dict):
        questions = data.get('questions')
        notes = data.get('notes') or []
        if not isinstance(questions, list):
            raise ImportError_('"questions" に問題の配列がありません。')
        if not isinstance(notes, list):
            raise ImportError_('"notes" は技術解説の配列にしてください。')
    else:
        raise ImportError_(
            '問題を並べた配列（[ ... ]）か、'
            '{"notes": [...], "questions": [...]} の形で渡してください。'
        )

    if not questions:
        raise ImportError_('問題が1件も含まれていません。')
    return {'questions': questions, 'notes': notes}


def validate_notes(records, codes):
    """技術解説を検査する。分野・見出し・本文がそろっていることだけ見る。"""
    for i, record in enumerate(records, start=1):
        where = '技術解説{}件目'.format(i)
        if not isinstance(record, dict):
            raise ImportError_('{}：オブジェクトではありません。'.format(where))
        if record.get('category') not in codes:
            raise ImportError_(
                '{}：中分類 {} は存在しません（1〜23 の番号で指定します）。'.format(
                    where, record.get('category')
                )
            )
        for field, label in (('title', '見出し'), ('body', '本文')):
            value = record.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ImportError_('{}：{}（{}）が空です。'.format(where, field, label))
    return records


def validate(payload):
    """取り込む前に全件を検査する。1件でも駄目なら何も入れない。"""
    codes = set(Category.objects.values_list('code', flat=True))
    records = payload['questions']
    validate_notes(payload['notes'], codes)
    for i, record in enumerate(records, start=1):
        where = '{}件目'.format(i)
        if not isinstance(record, dict):
            raise ImportError_('{}：オブジェクトではありません。'.format(where))

        for field in ('category', 'stem', 'choices', 'answer'):
            if field not in record:
                raise ImportError_('{}：必須項目「{}」がありません。'.format(where, field))

        if record['category'] not in codes:
            raise ImportError_(
                '{}：中分類 {} は存在しません（1〜23 の番号で指定します）。'.format(
                    where, record['category']
                )
            )

        stem = record['stem']
        if not isinstance(stem, str) or not stem.strip():
            raise ImportError_('{}：stem（問題文）が空です。'.format(where))

        choices = record['choices']
        if not isinstance(choices, list) or not 2 <= len(choices) <= MAX_CHOICES:
            raise ImportError_(
                '{}：choices は2〜{}個の配列にしてください。'.format(where, MAX_CHOICES)
            )
        if any(not isinstance(c, str) or not c.strip() for c in choices):
            raise ImportError_('{}：choices に空の選択肢があります。'.format(where))
        if len(set(choices)) != len(choices):
            raise ImportError_('{}：choices が重複しています。'.format(where))

        answer = record['answer']
        if isinstance(answer, bool) or not isinstance(answer, int):
            raise ImportError_('{}：answer は整数で指定してください。'.format(where))
        if not 0 <= answer < len(choices):
            raise ImportError_(
                '{}：answer は 0〜{} の範囲で指定してください（0 から数えます）。'.format(
                    where, len(choices) - 1
                )
            )

        subject = record.get('subject', Question.SUBJECT_A)
        if subject not in (Question.SUBJECT_A, Question.SUBJECT_B):
            raise ImportError_('{}：subject は "A" か "B" にしてください。'.format(where))

        if record.get('difficulty', 2) not in (1, 2, 3):
            raise ImportError_('{}：difficulty は 1〜3 にしてください。'.format(where))
    return payload


@transaction.atomic
def replace_all(user, payload):
    """その人の問題集と技術解説を、渡された内容にそっくり入れ替える。

    古い問題は削除し、それに紐づく解答履歴も一緒に消える。分野別の正答率は
    解答のたびに別途積み上げてあるので、問題が消えても残る。
    テンプレートが生成した計算問題は JSON に無くて当然なので触らない。
    """
    records = payload['questions']
    removed, _ = (
        Question.objects
        .filter(owner=user, template__isnull=True)
        .delete()
    )

    categories = {c.code: c for c in Category.objects.all()}

    # 技術解説も同じタイミングで入れ替える。問題だけの JSON を取り込んだ
    # ときに前の解説だけ残ると、問題と噛み合わない教材が居座るため。
    LearningNote.objects.filter(owner=user).delete()
    LearningNote.objects.bulk_create([
        LearningNote(
            owner=user,
            category=categories[note['category']],
            topic=note.get('topic', ''),
            title=note['title'],
            body=note['body'],
            source=note.get('source', ''),
        )
        for note in payload['notes']
    ])

    created = []
    for record in records:
        created.append(Question(
            owner=user,
            subject=record.get('subject', Question.SUBJECT_A),
            category=categories[record['category']],
            topic=record.get('topic', ''),
            stem=record['stem'],
            choices=record['choices'],
            answer_index=record['answer'],
            explanation=record.get('explanation', ''),
            difficulty=record.get('difficulty', 2),
            source=record.get('source', ''),
            is_active=True,
        ))
    Question.objects.bulk_create(created)
    return len(created), removed, len(payload['notes'])


def export_records(user, subject=None):
    """その人の出題対象を、取り込みと同じ形式で書き出す。"""
    queryset = Question.objects.filter(owner=user, template__isnull=True, is_active=True)
    if subject in (Question.SUBJECT_A, Question.SUBJECT_B):
        queryset = queryset.filter(subject=subject)
    return [
        {
            'category': q.category.code,
            'subject': q.subject,
            'topic': q.topic,
            'difficulty': q.difficulty,
            'stem': q.stem,
            'choices': q.choices,
            'answer': q.answer_index,
            'explanation': q.explanation,
            'source': q.source,
        }
        for q in queryset.select_related('category').order_by('category__code', 'id')
    ]
