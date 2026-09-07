"""問題 JSON の検証と一括取り込み。

問題はアカウントごとに持ち、JSON の取り込みが唯一の登録経路になる。
取り込みは常に一括差し替え。既存の問題との一致・不一致は見ず、
ファイルの内容をそのままその人の問題集にする。

古い問題は削除するのでデータは増え続けない。解答履歴は問題を参照しない
作りにしてあるため、問題を消しても履歴と分野ごとの正答率・苦手分野の
判定はそのまま残る。
"""

import json
from pathlib import Path

from django.db import transaction

from .models import Category, Question

DATA_DIR = Path(__file__).resolve().parent / 'data'
LABELS = 'アイウエオカキクケコ'
MAX_CHOICES = len(LABELS)

BUNDLED_FILES = [
    'questions_tech_a.json',
    'questions_tech_b.json',
    'questions_management.json',
    'questions_strategy.json',
    'questions_subject_b.json',
]


class ImportError_(ValueError):
    """取り込めない JSON だったことを表す。利用者に見せる文言を持つ。"""


def parse(payload):
    """文字列を問題のリストにする。読めなければ理由を添えて失敗させる。"""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ImportError_('JSON として読めません：{}'.format(exc))
    if not isinstance(data, list):
        raise ImportError_('問題を並べた配列（[ ... ]）で渡してください。')
    if not data:
        raise ImportError_('問題が1件も含まれていません。')
    return data


def validate(records):
    """取り込む前に全件を検査する。1件でも駄目なら何も入れない。"""
    codes = set(Category.objects.values_list('code', flat=True))
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
    return records


@transaction.atomic
def replace_all(user, records):
    """その人の問題集を、渡された内容にそっくり入れ替える。

    古い問題は削除する。解答履歴は問題を参照しない作りにしてあるので、
    問題を消しても履歴と分野別の正答率は残る。
    テンプレートが生成した計算問題は JSON に無くて当然なので触らない。
    """
    removed, _ = (
        Question.objects
        .filter(owner=user, template__isnull=True)
        .delete()
    )

    categories = {c.code: c for c in Category.objects.all()}
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
    return len(created), removed


def bundled_records():
    """リポジトリに同梱している問題バンクを読み込む。"""
    records = []
    for name in BUNDLED_FILES:
        path = DATA_DIR / name
        if not path.exists():
            continue
        with path.open(encoding='utf-8') as fp:
            records.extend(json.load(fp))
    return records


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
