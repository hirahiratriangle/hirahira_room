"""問題 JSON の検証と一括取り込み。

問題は全 ID で共有し、JSON の取り込みが唯一の登録経路になる。
取り込めるのは管理用の ID だけ（画面側で制限する）。
取り込みは常に一括差し替え。既存の問題との一致・不一致は見ず、
ファイルの内容をそのまま全 ID の問題集にする。

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

    受け付ける形は、問題だけを並べた配列と、{"questions": [...]} だけ、
    {"notes": [...]} だけ、両方入りの4つ。

    問題と解説は更新の頻度が違う（問題は作問のたびに入れ替わるが、解説は
    ほぼ固定で、苦戦している分野だけ後から書き足す）ので、別々のファイルで
    渡せるようにしてある。**入っていない側は触らない。** そのため
    「空の配列が入っている」と「キーごと無い」を区別して持つ。
    """
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ImportError_('JSON として読めません：{}'.format(exc))

    if isinstance(data, list):
        questions, notes = data, None
    elif isinstance(data, dict):
        questions = data.get('questions')
        notes = data.get('notes')
        if questions is None and notes is None:
            raise ImportError_(
                '"questions" も "notes" もありません。'
                'どちらか一方だけのファイルでも取り込めます。'
            )
        if questions is not None and not isinstance(questions, list):
            raise ImportError_('"questions" は問題の配列にしてください。')
        if notes is not None and not isinstance(notes, list):
            raise ImportError_('"notes" は技術解説の配列にしてください。')
    else:
        raise ImportError_(
            '問題を並べた配列（[ ... ]）か、'
            '{"questions": [...]} / {"notes": [...]} の形で渡してください。'
        )

    if questions is not None and not questions:
        raise ImportError_('"questions" が空です。問題を入れるか、キーごと外してください。')
    if notes is not None and not notes:
        raise ImportError_('"notes" が空です。解説を入れるか、キーごと外してください。')
    return {'questions': questions, 'notes': notes}


def question_key(subject, category, stem):
    """問題を突き合わせる鍵。空白のゆれは同じものとして扱う。

    stem の文言を変えたら別の問題になる。選択肢・正解・解説・難易度・
    小分類・出典の変更は、同じ問題への更新として扱う。
    """
    return (subject, category, ' '.join(str(stem).split()))


def note_key(category, title):
    """技術解説を突き合わせる鍵。title を変えたら別の解説になる。"""
    return (category, ' '.join(str(title).split()))


def _reject_duplicate_keys(questions, notes):
    """ファイルの中で鍵が重複していたら止める。どちらが勝つか不定になるため。"""
    seen = set()
    for i, r in enumerate(questions, start=1):
        if not isinstance(r, dict):
            continue
        key = question_key(
            r.get('subject', Question.SUBJECT_A), r.get('category'), r.get('stem', '')
        )
        if key in seen:
            raise ImportError_(
                '{}件目：同じ問題がファイル内に2件あります（科目・中分類・問題文が同じ）。'
                .format(i)
            )
        seen.add(key)

    seen = set()
    for i, r in enumerate(notes, start=1):
        if not isinstance(r, dict):
            continue
        key = note_key(r.get('category'), r.get('title', ''))
        if key in seen:
            raise ImportError_(
                '技術解説{}件目：同じ解説がファイル内に2件あります（中分類・見出しが同じ）。'
                .format(i)
            )
        seen.add(key)


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
    """取り込む前に全件を検査する。1件でも駄目なら何も入れない。

    ファイルに入っていない側（None）は検査しない。取り込みでも触らない。
    """
    subjects = dict(Category.objects.values_list('code', 'subject'))
    codes = set(subjects)
    records = payload['questions'] or []
    validate_notes(payload['notes'] or [], codes)
    _reject_duplicate_keys(records, payload['notes'] or [])
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
        # 分類は科目ごとに別なので、番号と科目が食い違っていたら止める。
        # 通してしまうと、その科目では二度と出てこない問題になる。
        if subjects[record['category']] != subject:
            raise ImportError_(
                '{}：中分類 {} は{}の分類です。subject が "{}" になっています。'.format(
                    where, record['category'],
                    '科目A' if subjects[record['category']] == Question.SUBJECT_A else '科目B',
                    subject,
                )
            )

        if record.get('difficulty', 2) not in (1, 2, 3):
            raise ImportError_('{}：difficulty は 1〜3 にしてください。'.format(where))
    return payload


# 削除がこの割合を超えたら、確定の前に確認を1枚はさむ。
# 取り違えたファイルを選んだときに、気づかず全部消してしまうのを防ぐため。
CONFIRM_REMOVAL_RATIO = 0.3


def needs_confirmation(preview):
    """下見の結果が、確認をはさむべき内容か。

    削除が既存の3割を超えていたら、取り違えを疑って一度止める。
    """
    for counts in preview.values():
        if not counts:
            continue
        existing = counts['unchanged'] + counts['updated'] + counts['removed']
        if existing and counts['removed'] / existing > CONFIRM_REMOVAL_RATIO:
            return True
    return False


def preview(payload):
    """取り込まずに、追加・更新・変更なし・削除の件数だけを数える。

    確定の前に件数を見せるため。全差し替えなら「削除が全件」が常態で
    異常に気づけないが、同期なら削除の件数が取り違えの警報になる。
    """
    return sync(payload, apply=False)


@transaction.atomic
def sync(payload, apply=True):
    """ファイルの内容に合わせて、問題と技術解説を追加・更新・削除する。

    apply=False なら数えるだけで書き込まない（下見）。

    全差し替えにしない。出題の選び方が問題ごとの解答履歴に乗っているため
    （未出題を先に出し、まちがえた問題は何問かはさんでから戻し、何度も
    落とした問題ほど間隔を詰める）、消して入れ直すと取り込んだ瞬間に全問が
    未出題へ戻り、復習の待ち行列も間隔計算の土台も消える。解説も同じで、
    学習モードのラウンドが指す解説が毎回切れる。

    **ファイルに入っていない側は触らない。** 問題だけのファイルを取り込んでも
    解説は残る。入っている側については、ファイルに無い行を消す。追記だけに
    すると、アプリの中身と手元のファイルがずれてどちらが正か分からなくなる。

    テンプレートが生成した計算問題は JSON に無くて当然なので触らない。

    返すのは件数の内訳。利用者に見せて、削除が異常に多くないかを確かめる。
    """
    categories = {c.code: c for c in Category.objects.all()}
    result = {}

    records = payload.get('questions')
    if records is None:
        result['questions'] = None
    else:
        existing = {
            question_key(q.subject, q.category.code, q.stem): q
            for q in Question.objects.filter(template__isnull=True)
            .select_related('category')
        }
        added = updated = unchanged = 0
        seen = set()
        to_create, to_update = [], []
        for r in records:
            subject = r.get('subject', Question.SUBJECT_A)
            key = question_key(subject, r['category'], r['stem'])
            seen.add(key)
            fields = dict(
                topic=r.get('topic', ''),
                choices=r['choices'],
                answer_index=r['answer'],
                explanation=r.get('explanation', ''),
                difficulty=r.get('difficulty', 2),
                source=r.get('source', ''),
                is_active=True,
            )
            current = existing.get(key)
            if current is None:
                to_create.append(Question(
                    subject=subject, category=categories[r['category']],
                    stem=r['stem'], **fields,
                ))
                added += 1
            elif any(getattr(current, f) != v for f, v in fields.items()):
                for f, v in fields.items():
                    setattr(current, f, v)
                to_update.append(current)
                updated += 1
            else:
                unchanged += 1
        stale = [q.pk for k, q in existing.items() if k not in seen]
        removed = len(stale)
        if apply:
            Question.objects.bulk_create(to_create)
            if to_update:
                Question.objects.bulk_update(to_update, list(fields))
            if stale:
                Question.objects.filter(pk__in=stale).delete()
        result['questions'] = {
            'added': added, 'updated': updated,
            'unchanged': unchanged, 'removed': removed,
        }

    notes = payload.get('notes')
    if notes is None:
        result['notes'] = None
    else:
        existing = {
            note_key(n.category.code, n.title): n
            for n in LearningNote.objects.select_related('category')
        }
        added = updated = unchanged = 0
        seen = set()
        to_create, to_update = [], []
        for r in notes:
            key = note_key(r['category'], r['title'])
            seen.add(key)
            fields = dict(
                topic=r.get('topic', ''), body=r['body'], source=r.get('source', ''),
            )
            current = existing.get(key)
            if current is None:
                to_create.append(LearningNote(
                    category=categories[r['category']], title=r['title'], **fields,
                ))
                added += 1
            elif any(getattr(current, f) != v for f, v in fields.items()):
                for f, v in fields.items():
                    setattr(current, f, v)
                to_update.append(current)
                updated += 1
            else:
                unchanged += 1
        stale = [n.pk for k, n in existing.items() if k not in seen]
        removed = len(stale)
        if apply:
            LearningNote.objects.bulk_create(to_create)
            if to_update:
                LearningNote.objects.bulk_update(to_update, list(fields))
            if stale:
                LearningNote.objects.filter(pk__in=stale).delete()
        result['notes'] = {
            'added': added, 'updated': updated,
            'unchanged': unchanged, 'removed': removed,
        }

    return result


def export_records(subject=None):
    """共有の問題と技術解説を、取り込みと同じ形式で書き出す。

    控えを取る用途なので、問題と解説の両方を1つにまとめて出す。
    取り込みは同期なので、これをそのまま戻せば書き出した時点の状態に戻る。

    subject を指定すると、その科目のぶんだけを出す。ただし取り込みは
    ファイルに無い行を消すため、片方の科目だけを戻すともう一方は消える。
    控えを取るなら指定しない。
    """
    questions = Question.objects.filter(template__isnull=True, is_active=True)
    notes = LearningNote.objects.all()
    if subject in (Question.SUBJECT_A, Question.SUBJECT_B):
        questions = questions.filter(subject=subject)
        # 分類が科目ごとに別なので、解説もその科目のものだけになる
        notes = notes.filter(category__subject=subject)

    return {
        'notes': [
            {
                'category': n.category.code,
                'topic': n.topic,
                'title': n.title,
                'body': n.body,
                'source': n.source,
            }
            for n in notes.select_related('category').order_by('category__code', 'id')
        ],
        'questions': [
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
            for q in questions.select_related('category').order_by('category__code', 'id')
        ],
    }
