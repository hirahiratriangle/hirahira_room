"""seed_genba の履歴データ（終わった現場と、そこで残した成長・ナレッジ・提案）を作る。

経営KPIのグラフは、1年半ほどの推移を見せる。いま動いている4つの現場は3か月ほどしか経っていないので、
それより前の成長やナレッジは、終わった現場（Archive 済み）のものとして作る。

- 現場に入っていた時期より前の日付の記録を作らないよう、終わった現場を2つ置く
- 切り替えられるメンバーとリーダー（kit.DEMO_PERSONAS）は入れない。ホームに古い現場が並び、筋書きが変わるため
- Archive 済みなので、マネジメントの運営指標には出ない。デモの説明ページの現場（kit.DEMO_TEAMS）にも出ない
- 成長は同じ人の後の承認で上書きされる（growth.approved_reaches）ので、低いレベルから順に並べ、伸びていく形にする
- 日付はすべて今日からの日数で決める。いつ流しても同じ形のグラフになる
- 月次の人月は、今の現場も終わった現場も、入場した月から入れる。標準売上と標準粗利の積み上げの元になる
- 在籍人数は、本物の利用者が入れた値があれば上書きしない。階級表の G1〜G4 はデモの値にそろえる
- 階級の見直しは、成長が階級に届く流れを見せる。採用済みの人は、それより前の月を元の階級で記録する
"""
from datetime import timedelta

from genba import growth, kit
from genba.models import (
    KNOWLEDGE_PUBLISHED, VISIBILITY_COMPANY,
    Assignment, CompanyMonth, Document, FocusSkill, Goal, GoalEvidence, Grade, GradeAssignment, GradeReview, InterviewQuestion, KitProposal, Membership, MonthlyWork, Site, Term,
    month_start,
)

# 階級表：(階級, 標準単価, 標準原価（万円・月額）, 必要なレベル)と、人の階級。架空の数字
GRADES = [('G1', 70, 48, 2), ('G2', 78, 54, 3), ('G3', 88, 62, 4), ('G4', 98, 70, 5)]
PEOPLE_GRADES = {'demo_kimura': 'G1', 'demo_sato': 'G2', 'demo_yamamoto': 'G2', 'demo_nakamura': 'G2', 'demo_watanabe': 'G1',
                 'demo_takahashi': 'G2', 'demo_ito': 'G2', 'demo_suzuki': 'G3', 'demo_kobayashi': 'G4', 'demo_tanaka': 'G4'}
# 成長で階級が上がった人：(本人, 元の階級, 元になった成長のスキル項目, 何日前に採用)。それより前の月は元の階級で記録する
PROMOTED = [('demo_takahashi', 'G1', 'ソフトウェアエンジニアリング', 190), ('demo_yamamoto', 'G1', 'テクノロジ', 120)]
FOCUS = ['業務分析', '知的資産管理活用', 'ネゴシエーション']   # 会社の重点スキル項目
# 過去のアサイン：(本人, 元になった成長のスキル項目, 入った現場, 次の目標に合うか, 何日前)
ASSIGNED = [('demo_tanaka', 'リーダーシップ', 'claims', True, 85), ('demo_suzuki', 'リーダーシップ', 'order', True, 70),
            ('demo_takahashi', 'ソフトウェアエンジニアリング', 'logistics', False, 56)]
PAST_ENDED = {'genba-demo-past-sales': 150, 'genba-demo-past-web': 110}   # 終わった現場が何日前に終わったか

# (キー, 現場名, 業界, 入場した日からの日数, リーダー, メンバー)
SITES = [
    ('past-sales', '販売管理パッケージ導入', '卸売業', 560, 'demo_tanaka', ['demo_ito', 'demo_watanabe', 'demo_sato']),
    ('past-web', '会員サイト刷新', 'サービス業', 340, 'demo_suzuki', ['demo_takahashi', 'demo_yamamoto']),
]

# (現場, 本人, スキル項目, 承認レベル, 評価, 立場, ピーク時の人数, 複雑性, 何日前に承認)
GOALS = [
    ('past-sales', 'demo_tanaka', 'プロジェクトマネジメント', 3, 'B', 'member', 4, '1', 500),
    ('past-sales', 'demo_ito', 'ネゴシエーション', 2, 'C', 'member', 4, '', 440),
    ('past-sales', 'demo_tanaka', 'リーダーシップ', 3, 'B', 'leader', 4, '1,4', 400),
    ('past-sales', 'demo_sato', '業務分析', 2, 'C', 'member', 4, '', 350),
    ('past-sales', 'demo_watanabe', 'テクノロジ', 3, 'B', 'member', 4, '', 300),
    ('past-web', 'demo_suzuki', 'コミュニケーション', 3, 'B', 'leader', 3, '2', 250),
    ('past-web', 'demo_takahashi', 'ソフトウェアエンジニアリング', 3, 'B', 'member', 3, '2', 200),
    ('past-sales', 'demo_ito', 'コミュニケーション', 3, 'B', 'leader', 4, '3', 170),
    ('past-web', 'demo_yamamoto', 'テクノロジ', 3, 'B', 'member', 3, '', 130),
    ('past-web', 'demo_suzuki', 'リーダーシップ', 3, 'A', 'leader', 3, '2', 110),
]

# (現場, 書いた人, 用語, 一般化した意味, 何日前に公開)
TERMS = [
    ('past-sales', 'demo_ito', '掛率', '定価に掛けて卸値を決める率。得意先の区分ごとに持つことが多い。', 470),
    ('past-sales', 'demo_sato', '帳合', '仕入先と販売先の取引の組み合わせ。', 380),
    ('past-web', 'demo_yamamoto', '会員ランク', '利用実績で決まる区分。特典と結びつくので、変更の時点を決めておく。', 230),
]

# (現場, 書いた人, 区分, 一般化した題, 一般化した本文, 何日前に公開)
DOCUMENTS = [
    ('past-sales', 'demo_tanaka', Document.KIND_DESIGN, '掛率の改定を履歴で持つ',
     '案: 上書き／履歴。決定: 適用開始日つきの履歴で持つ。理由: 過去の伝票を再計算できるようにする。', 420),
    ('past-sales', 'demo_ito', Document.KIND_PRE, '商流の確認（卸売のパッケージ導入）',
     '面談で「カスタマイズの範囲」と言われた内容が、帳票までを指していた。入場前に対象を一覧で確かめる。', 330),
    ('past-sales', 'demo_tanaka', Document.KIND_RETRO, 'パッケージの標準機能と業務の差の扱い',
     '約束: 標準機能で導入\n実際: 差分の業務が残った\n次に面談で確かめること: 差分を誰が判断するか', 260),
    ('past-web', 'demo_suzuki', Document.KIND_DESIGN, '退会後の会員データの持ち方',
     '案: 削除／匿名化。決定: 匿名化して集計に残す。理由: 売上の集計と個人情報の扱いを両立する。', 210),
    ('past-web', 'demo_yamamoto', Document.KIND_RETRO, '画面の確認者が途中で増えた',
     '約束: 確認者は1部署\n実際: 3部署に広がった\n次に面談で確かめること: 画面の決裁者', 120),
]

# (質問, 業界)
QUESTIONS = [
    ('カスタマイズの範囲に帳票は入るか', '卸売業'),
    ('標準機能との差分は誰が判断するか', '卸売業'),
    ('画面の決裁者は1部署か。増えるときの手順はあるか', 'サービス業'),
]

# (対象, 題, 提案, 状態, 書いた人, 何日前)
PROPOSALS = [
    ('checklist', '入場前に帳票の対象を一覧で確かめる', '「契約・商流」に「対象の帳票の一覧」を足す', 'adopted', 'demo_ito', 320),
    ('skillset', 'パッケージ導入の具体スキルを足す', '業務分析に「標準機能との差分の整理」を足す', 'adopted', 'demo_tanaka', 240),
    ('phrases', '確認者が増えたときの一言', '「確認の進め方を合わせたいので、決裁者を教えていただけますか」', 'declined', 'demo_yamamoto', 110),
]


def seed_history(cmd, users):
    """終わった現場と、そこから残った記録を作る。cmd は seed_genba の Command（日付の計算と、会社の人を使う）"""
    sites = {}
    for key, name, industry, started, leader, members in SITES:
        site = Site.objects.create(
            name=name, team_slug=f'genba-demo-{key}', industry=industry, is_archived=True,
            started_on=cmd.today - timedelta(days=started), description='終わった現場。',
        )
        sites[key] = site
        Membership.objects.create(site=site, user=users[leader], role=Membership.ROLE_LEADER,
                                  joined_on=site.started_on, duty='リーダー')
        for name in members:
            Membership.objects.create(site=site, user=users[name], joined_on=site.started_on, duty='設計')

    for key, author, item, level, grade, role, size, complexity, days in GOALS:
        Goal.objects.create(site=sites[key], author=users[author], skill_item=item, current_level=level - 1,
                            target_text=f'レベル{level}の記述に届く', reached_level=level, role=role, team_size=size,
                            complexity=complexity, explanation='終わった現場での成長。', created_at=cmd.ago(days + 60),
                            **cmd.approved(level, grade, confirmed_by=cmd.kacho, days=days))

    def public(key, title, body, days):
        leader = sites[key].memberships.get(role=Membership.ROLE_LEADER).user
        return dict(visibility=VISIBILITY_COMPANY, knowledge_status=KNOWLEDGE_PUBLISHED, public_title=title, public_body=body,
                    published_by=leader, published_at=cmd.ago(days))

    for key, author, term, meaning, days in TERMS:
        Term.objects.create(site=sites[key], author=users[author], term=term, meaning=meaning, created_at=cmd.ago(days + 7),
                            **public(key, term, meaning, days))
    for key, author, kind, title, body, days in DOCUMENTS:
        Document.objects.create(site=sites[key], author=users[author], kind=kind, title=title, body=body,
                                created_at=cmd.ago(days + 7), **public(key, title, body, days))
    for question, industry in QUESTIONS:
        InterviewQuestion.objects.create(question=question, industry=industry, author=users['demo_tanaka'])
    for target, title, proposal, status, author, days in PROPOSALS:
        KitProposal.objects.create(target=target, title=title, proposal=proposal, status=status, author=users[author],
                                   decided_by=cmd.honbu, created_at=cmd.ago(days))
    seed_work(cmd)


def seed_work(cmd):
    """階級を決め、すべてのデモの現場に入場した月から現場が終わった月まで人月を入れる。在籍は現場に入っている人＋待機1人にする"""
    grades = {}
    for order, (name, price, cost, level) in enumerate(GRADES):
        grades[name] = Grade.objects.update_or_create(
            name=name, defaults={'order': order, 'standard_price': price * 10000, 'standard_cost': cost * 10000,
                                 'required_level': level})[0]
    raised = {user: (grades[old], cmd.today - timedelta(days=days)) for user, old, _, days in PROMOTED}
    people = {}
    for m in Membership.objects.filter(site__team_slug__startswith='genba-demo-').select_related('site', 'user'):
        kit.ensure_repository(m.user)
        grade = grades[PEOPLE_GRADES[m.user.username]]
        GradeAssignment.objects.update_or_create(user=m.user, defaults={'grade': grade})
        last = cmd.today - timedelta(days=PAST_ENDED.get(m.site.team_slug, 0))
        month = month_start(m.joined_on)
        while month <= last:
            old, since = raised.get(m.user.username, (None, None))
            MonthlyWork.objects.create(membership=m, month=month, grade=old if old and month < month_start(since) else grade)
            people.setdefault(month, set()).add(m.user_id)
            month = (month + timedelta(days=32)).replace(day=1)
    for month, users in people.items():
        CompanyMonth.objects.get_or_create(month=month, defaults={'headcount': len(users) + 1})
    seed_reviews(cmd, grades)
    seed_task_evidence()
    seed_vision(cmd)


def seed_reviews(cmd, grades):
    """階級の見直し：採用済み2件と、会社の決定を待っている1件（demo_ito）。demo_watanabe はマネジメントの候補に出る"""
    for user, old, item, days in PROMOTED:
        goal = Goal.objects.get(author__username=user, skill_item=item, site__is_archived=True)
        GradeReview.objects.create(user=goal.author, goal=goal, current_grade=grades[old], proposed_grade=grades[PEOPLE_GRADES[user]],
                                   status=GradeReview.STATUS_ADOPTED, proposed_by=cmd.kacho, decided_by=cmd.honbu,
                                   decided_at=cmd.ago(days), created_at=cmd.ago(days + 3))
    goal = Goal.objects.get(author__username='demo_ito', skill_item='ネゴシエーション', approved_level=4)
    GradeReview.objects.create(user=goal.author, goal=goal, current_grade=grades['G2'], proposed_grade=grades['G3'],
                               proposed_by=cmd.kacho, created_at=cmd.ago(5))


def seed_task_evidence():
    """動いている現場の目標に、本人が終えた作業を2件ずつ証拠として付ける。やり遂げた作業が成長につながる形を見せる"""
    for goal in Goal.objects.filter(site__team_slug__startswith='genba-demo-', site__is_archived=False):
        tasks = goal.site.questions.filter(author=goal.author, kind='task', is_open=False, due_on__isnull=False)[:2]
        for task in tasks:
            GoalEvidence.objects.get_or_create(goal=goal, record_type='issue', record_id=task.pk,
                                               defaults={'title': growth.record_title(task)[:150]})


def seed_vision(cmd):
    """会社の重点と、過去のアサイン。本物の利用者が重点を決めていれば上書きしない"""
    if not FocusSkill.objects.exists():
        FocusSkill.objects.bulk_create(FocusSkill(skill_item=item) for item in FOCUS)
    for user, item, key, matched, days in ASSIGNED:
        goal = Goal.objects.get(author__username=user, skill_item=item, site__is_archived=True)
        goal.next_text = '次の現場で、より大きな責任を担う'
        goal.save(update_fields=['next_text'])
        Assignment.objects.create(goal=goal, site=Site.objects.get(team_slug=f'genba-demo-{key}'), matched=matched,
                                  assigned_by=cmd.kacho, created_at=cmd.ago(days))
