from . import skillset_data as data
from collections import Counter, defaultdict

from .models import Document, Goal, Question, Summary, Term

SKILL_ITEMS = [i['name'] for i in data.SKILL_ITEMS]

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
    ('feedback', None): ('リーダーシップ', '後進の指導'),
}

ROLE_LABELS = {'member': 'メンバー', 'leader': 'リーダー', 'owner': '責任者'}


def concrete_skills(item=None):
    return [s for s in data.CONCRETE_SKILLS if item is None or s['item'] == item]


def level_description(item, level):
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


class FeedbackGiven:

    def __init__(self, summary):
        self.summary, self.pk = summary, summary.pk


def record_type(record):
    if isinstance(record, FeedbackGiven):
        return 'feedback'
    return {Document: 'doc', Term: 'term', Question: 'issue', Summary: 'summary'}[type(record)]


def auto_skill(record):
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
    if rtype == 'feedback':
        return f'まとめへの返し：{record.summary.author.username} さん'
    return '1週目のまとめ'


def own_records(user, site):
    records = [
        *site.documents.filter(author=user).exclude(kind=Document.KIND_WEEKLY, goal_action=''),
        *site.terms.filter(author=user),
        *site.questions.filter(author=user, stage='').exclude(kind=Question.KIND_TASK, due_on__isnull=True),
        *site.summaries.filter(author=user).exclude(submitted_at__isnull=True),
        *(FeedbackGiven(s) for s in site.summaries.filter(feedback_by=user).select_related('author')),
    ]
    return records


def candidates(goal):
    chosen = {(e.record_type, e.record_id) for e in goal.evidence.all()}
    rows = []
    for r in own_records(goal.author, goal.site):
        skill = auto_skill(r)
        key = (record_type(r), r.pk)
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
    if not reached_level:
        return ''
    if reached_level >= 4 or (reached_level == 3 and mentored):
        return 'A'
    if reached_level == 3:
        return 'B'
    return 'C'


def itss_equivalent(role, team_size, complexity_count):
    size = team_size or 0
    if role == 'owner' and (size >= 10 or (complexity_count >= 4 and size >= 3)):
        return 5
    if role in ('leader', 'owner') and size >= 3:
        return 4
    if role:
        return 3
    return None


def paste_text(goal, evidence_titles):
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
    if goal.approved_on:
        lines.append(f'承認：レベル{goal.approved_level}・評価{goal.approved_grade}（{goal.approved_on:%Y/%m/%d}）')
    return '\n'.join(lines)


def approved_reaches():
    latest = {}
    for g in Goal.objects.filter(approved_on__isnull=False).select_related('author', 'site').order_by('approved_on', 'pk'):
        latest[(g.author_id, g.skill_item)] = g
    return list(latest.values())


def skill_distribution(reaches=None):
    reaches = approved_reaches() if reaches is None else reaches
    counts = defaultdict(Counter)
    for g in reaches:
        counts[g.skill_item][g.approved_level] += 1
    return [
        {'item': item, 'counts': [counts[item][n] for n in (2, 3, 4, 5)],
         'level3_up': sum(v for n, v in counts[item].items() if n >= 3)}
        for item in SKILL_ITEMS
    ]


def itss_distribution(reaches=None):
    reaches = approved_reaches() if reaches is None else reaches
    best = {}
    for g in reaches:
        level = itss_equivalent(g.role, g.team_size, len(g.complexity_numbers))
        if level:
            best[g.author_id] = max(best.get(g.author_id, 0), level)
    c = Counter(best.values())
    return [(n, c[n]) for n in (3, 4, 5)]


def complexity_experience(reaches=None):
    reaches = approved_reaches() if reaches is None else reaches
    people = defaultdict(set)
    for g in reaches:
        for n in g.complexity_numbers:
            people[n].add(g.author_id)
    return [(i, text, len(people[i])) for i, text in enumerate(data.COMPLEXITY, 1)]


def thin_items(distribution):
    low = min(row['level3_up'] for row in distribution)
    return [row['item'] for row in distribution if row['level3_up'] == low]


def skill_sheet(goals):
    lines = []
    for g in goals:
        lines.append(f'■ {g.skill_item}：レベル{g.approved_level}・評価{g.approved_grade}（{g.site.industry}）')
        if g.explanation:
            lines.append(g.explanation)
        titles = [e.title for e in g.evidence.all()]
        if titles:
            lines.append('証拠：' + '／'.join(titles))
    return '\n'.join(lines)


def ledger(reaches=None):
    reaches = approved_reaches() if reaches is None else reaches
    people = defaultdict(list)
    for g in reaches:
        people[g.author].append(g)
    rows = []
    for author, goals in sorted(people.items(), key=lambda kv: kv[0].username):
        levels = {g.skill_item: g.approved_level for g in goals}
        best = max((itss_equivalent(g.role, g.team_size, len(g.complexity_numbers)) or 0 for g in goals), default=0)
        rows.append({
            'author': author, 'goals': goals, 'levels': [levels.get(item) for item in SKILL_ITEMS],
            'itss': best or None, 'next': [g for g in goals if g.next_text], 'sheet': skill_sheet(goals),
        })
    return rows
