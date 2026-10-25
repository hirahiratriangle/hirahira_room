"""成長基軸：基本設計スキルセット（ITSS対応）の引き方と、目標・到達の目安。

記録のスキル項目は保存せず、記録の種類から求める（構想書スライド14）。
評価A〜E・ITSSレベル相当は目安で、本人が記入に使い、マネジメントが承認で確定する（憲章 V）。
会社のスキル台帳と集計に使うのは、承認した到達だけ（構想書スライド20）。
"""
from . import skillset_data as data
from collections import Counter, defaultdict

from .models import Document, Goal, Question, Summary, Term

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
    ('feedback', None): ('リーダーシップ', '後進の指導'),
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


class FeedbackGiven:
    """リーダーがまとめに返した記録（リーダーの証拠：後進の指導）"""

    def __init__(self, summary):
        self.summary, self.pk = summary, summary.pk


def record_type(record):
    if isinstance(record, FeedbackGiven):
        return 'feedback'
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
    if rtype == 'feedback':
        return f'まとめへの返し：{record.summary.author.username} さん'
    return '1週目のまとめ'


def own_records(user, site):
    """その現場の本人の記録（証拠の候補）。週報は「目標に近づいた行動」を書いた週だけ"""
    records = [
        *site.documents.filter(author=user).exclude(kind=Document.KIND_WEEKLY, goal_action=''),
        *site.terms.filter(author=user),
        *site.questions.filter(author=user, stage='').exclude(kind=Question.KIND_TASK, due_on__isnull=True),
        *site.summaries.filter(author=user).exclude(submitted_at__isnull=True),
        *(FeedbackGiven(s) for s in site.summaries.filter(feedback_by=user).select_related('author')),
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
    # 承認したあとは、確定したレベルと評価を末尾に足す（目安と並べて、どちらが確定かを分かるようにする）
    if goal.approved_on:
        lines.append(f'承認：レベル{goal.approved_level}・評価{goal.approved_grade}（{goal.approved_on:%Y/%m/%d}）')
    return '\n'.join(lines)


def approved_reaches():
    """承認した到達を、本人×項目ごとに最新の1件にする。

    同じ人が同じ項目で何度も承認を受けたら（現場をまたいで伸びたら）、最後に承認したものが今の到達。
    台帳・スキル分布・ITSS相当・複雑性の集計は、すべてこの一覧から作る。
    """
    latest = {}
    for g in Goal.objects.filter(approved_on__isnull=False).select_related('author', 'site').order_by('approved_on', 'pk'):
        latest[(g.author_id, g.skill_item)] = g
    return list(latest.values())


def skill_distribution(reaches=None):
    """ITSS 11項目 × レベル2〜5 の人数 [{'item', 'counts': [n2, n3, n4, n5], 'level3_up'}]。

    数えるのは承認で確定したレベル（approved_level）。本人が書いた reached_level ではない。
    """
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
    """ITSS レベル相当の目安ごとの人数（本人ごとに一番高いもの）。次に狙える案件の立場・規模を見る"""
    reaches = approved_reaches() if reaches is None else reaches
    best = {}
    for g in reaches:
        level = itss_equivalent(g.role, g.team_size, len(g.complexity_numbers))
        if level:
            best[g.author_id] = max(best.get(g.author_id, 0), level)
    c = Counter(best.values())
    return [(n, c[n]) for n in (3, 4, 5)]


def complexity_experience(reaches=None):
    """経験した複雑性の項目ごとの人数。受けられる案件の難しさを見る"""
    reaches = approved_reaches() if reaches is None else reaches
    people = defaultdict(set)
    for g in reaches:
        for n in g.complexity_numbers:
            people[n].add(g.author_id)
    return [(i, text, len(people[i])) for i, text in enumerate(data.COMPLEXITY, 1)]


def thin_items(distribution):
    """レベル3以上の人数が一番少ない項目（育成への再投資の候補）"""
    low = min(row['level3_up'] for row in distribution)
    return [row['item'] for row in distribution if row['level3_up'] == low]


def skill_sheet(goals):
    """証拠つきのスキルシートの文章（構想書スライド20の2）。

    承認したレベル・評価に、本人の行動と証拠になった genba の記録の名前を添える。
    現場名の代わりに業界を出す（会社に見せるため。憲章 III）。
    """
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
    """個人のスキル台帳（構想書スライド20の1）。本人ごとに1行。

    levels：SKILL_ITEMS の並びで、承認したレベル（なければ None）
    itss  ：承認した到達から見た ITSS レベル相当の一番高いもの
    next  ：次の目標がある到達（目標に合う現場へのアサインに使う）
    sheet ：skill_sheet() の文章
    """
    reaches = approved_reaches() if reaches is None else reaches
    people = defaultdict(list)
    for g in reaches:
        people[g.author].append(g)
    rows = []
    for author, goals in sorted(people.items(), key=lambda kv: kv[0].username):
        levels = {g.skill_item: g.approved_level for g in goals}
        # 立場・規模・複雑性を書いていない到達は相当レベルが出ない（0 として扱い、全部 0 なら None）
        best = max((itss_equivalent(g.role, g.team_size, len(g.complexity_numbers)) or 0 for g in goals), default=0)
        rows.append({
            'author': author, 'goals': goals, 'levels': [levels.get(item) for item in SKILL_ITEMS],
            'itss': best or None, 'next': [g for g in goals if g.next_text], 'sheet': skill_sheet(goals),
        })
    return rows
