"""成長基軸：基本設計スキルセット（ITSS対応）の引き方と、目標・到達の目安。

記録のスキル項目は保存せず、記録の種類から求める（構想書スライド14）。
目安はあくまで目安で、本人が説明に使う。他人が記録で採点するためには使わない（憲章 V）。
"""
from . import skillset_data as data
from .models import Document, Question, Summary, Term

SKILL_ITEMS = [i['name'] for i in data.SKILL_ITEMS]

# 記録の種類 → (ITSS スキル項目, 基本設計の具体スキル)
AUTO_SKILLS = {
    ('doc', Document.KIND_BUSINESS): ('業務システム構築', '業務フローの把握と可視化'),
    ('doc', Document.KIND_CONTRACT): ('ネゴシエーション', '方針と責任範囲の合意'),
    ('doc', Document.KIND_STRUCTURE): ('ネゴシエーション', '方針と責任範囲の合意'),
    ('doc', Document.KIND_DESIGN): ('ソフトウェアエンジニアリング', '影響範囲分析'),
    ('term', None): ('業務システム構築', '業務ドメイン知識の獲得'),
    ('issue', 'unreadable'): ('コミュニケーション', '読めない資料の早期の共有'),
    ('issue', 'problem'): ('コンサルティング技法の活用', 'リスクの発見と提起'),
    ('issue', 'scope_out'): ('ネゴシエーション', '方針と責任範囲の合意'),
    ('issue', 'schedule'): ('プロジェクトマネジメント', '担当機能の設計スケジュール管理'),
    ('summary', None): ('知的資産管理活用', '理解の資料化と再利用'),
}

ROLE_LABELS = {'member': 'メンバー', 'leader': 'リーダー', 'owner': '責任者'}


def concrete_skills(item=None):
    return [s for s in data.CONCRETE_SKILLS if item is None or s['item'] == item]


def level_description(item, level):
    """スキル項目とレベル（2〜5）の具体記述。レベル1は全職種共通なので記述がない"""
    for row in data.LEVEL_DESCRIPTIONS:
        if item in row['items']:
            text = row['levels'].get(level, '')
            return '' if text == '—' else text
    return ''


def level_descriptions(item):
    return [(n, level_description(item, n)) for n in (2, 3, 4, 5)]


def _issue_key(q):
    if q.unreadable:
        return 'unreadable'
    if q.scope_out:
        return 'scope_out'
    if q.kind == Question.KIND_PROBLEM:
        return 'problem'
    if q.kind == Question.KIND_TASK and q.due_on:
        return 'schedule'
    return None


def record_type(record):
    return {Document: 'doc', Term: 'term', Question: 'issue', Summary: 'summary'}[type(record)]


def auto_skill(record):
    """記録に自動で付くスキル項目 (項目, 具体スキル)。付かなければ None"""
    rtype = record_type(record)
    if rtype == 'doc':
        key = (rtype, record.kind)
    elif rtype == 'issue':
        key = (rtype, _issue_key(record))
    else:
        key = (rtype, None)
    return AUTO_SKILLS.get(key)


def record_title(record):
    rtype = record_type(record)
    if rtype == 'doc':
        return f'{record.get_kind_display()}：{record.title}'
    if rtype == 'term':
        return f'用語集：{record.term}'
    if rtype == 'issue':
        labels = '・'.join(record.labels) or record.get_kind_display()
        return f'{labels}：{record.title}'
    return '1週目のまとめ'


def own_records(user, site):
    """その現場の本人の記録（証拠の候補）。週報は「目標に近づいた行動」を書いた週だけ"""
    records = [
        *site.documents.filter(author=user).exclude(kind=Document.KIND_WEEKLY, goal_action=''),
        *site.terms.filter(author=user),
        *site.questions.filter(author=user, stage='').exclude(kind=Question.KIND_TASK, due_on__isnull=True),
        *site.summaries.filter(author=user).exclude(submitted_at__isnull=True),
    ]
    return records


def candidates(goal):
    """証拠の候補。目標のスキル項目に自動で付いた記録を上に並べる"""
    chosen = {(e.record_type, e.record_id) for e in goal.evidence.all()}
    rows = []
    for r in own_records(goal.author, goal.site):
        skill = auto_skill(r)
        key = (record_type(r), r.pk)
        # 週報の「目標に近づいた行動」は、目標に向けて書いたものなので候補の上に出す
        toward_goal = key[0] == 'doc' and r.kind == Document.KIND_WEEKLY
        rows.append({
            'key': f'{key[0]}:{key[1]}', 'type': key[0], 'id': r.pk,
            'title': f'{record_title(r)}（{r.goal_action}）' if toward_goal else record_title(r),
            'skill': skill, 'matches': toward_goal or bool(skill and skill[0] == goal.skill_item),
            'chosen': key in chosen,
        })
    rows.sort(key=lambda row: (not row['matches'], not row['chosen'], row['title']))
    return rows


def ae_grade(reached_level, mentored):
    """スキルシートの評価A〜Eの目安（スキルセットの使い方1）"""
    if not reached_level:
        return ''
    if reached_level >= 4 or (reached_level == 3 and mentored):
        return 'A'
    if reached_level == 3:
        return 'B'
    return 'C'


def itss_equivalent(role, team_size, complexity_count):
    """ITSSレベル相当の目安（立場・サイズ・複雑性だけで見る。成功回数などは本人が確かめる）"""
    size = team_size or 0
    if role == 'owner' and (size >= 10 or (complexity_count >= 4 and size >= 3)):
        return 5
    if role in ('leader', 'owner') and size >= 3:
        return 4
    if role:
        return 3
    return None


def paste_text(goal, evidence_titles):
    """課会資料・スキルシートに貼る文章"""
    lines = ['【現参画PJを通しての目標】',
             f'{goal.skill_item}（レベル{goal.target_level}の記述）：{goal.target_text}']
    if goal.reached_level:
        desc = level_description(goal.skill_item, goal.reached_level)
        lines += ['', '【到達】', f'{goal.skill_item}：レベル{goal.reached_level}の記述「{desc}」に当たる。']
        if goal.explanation:
            lines.append(goal.explanation)
        if evidence_titles:
            lines.append('証拠（genba の記録）：' + '／'.join(evidence_titles))
        grade = ae_grade(goal.reached_level, goal.mentored)
        lines.append(f'スキルシート（基本設計）の評価の目安：{grade}')
    level = itss_equivalent(goal.role, goal.team_size, len(goal.complexity_numbers))
    if level:
        nums = '・'.join(str(n) for n in goal.complexity_numbers) or 'なし'
        lines.append(f'ITSSレベル{level}相当（立場：{ROLE_LABELS[goal.role]}、ピーク時要員：{goal.team_size or "―"}人、'
                     f'複雑性：{nums}）')
    return '\n'.join(lines)
