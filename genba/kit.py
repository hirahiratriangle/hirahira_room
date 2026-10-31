"""現場テンプレート（genba-template に当たるもの）。

プロトタイプでは GitHub のテンプレートリポジトリを作らず、構成をここに持つ。
現場に入った人ごとに、チェックリストがその現場の作業 Issue としてコピーされる。
元資料を改訂しても、作成済みの現場は変わらない（新しく作る現場から新しい版を使う）。
"""
from . import kit_data

TEMPLATE_VERSION = '2026-10'

# 職種。リポジトリは1人に1つのまま、職種でテンプレート（入場時チェックリスト）を分ける（spec 014）。
# プログラマーが SE になっても、リポジトリを作り直さず職種だけ変える。記録と成長はそのまま続く。
JOB_SE = 'se'
JOB_PROGRAMMER = 'programmer'
JOB_TYPES = [(JOB_SE, 'SE'), (JOB_PROGRAMMER, 'プログラマー')]

# プログラマーのテンプレート。元資料は上流編だけなので、製造編は上流に固有の項目を差し替えた案（spec 014）。
# 段階・小見出し・残す場所は SE と同じにして、進捗の見え方と危険信号の判定をそろえる。
PROGRAMMER_CHECKLIST_TITLE = '入場時チェックリスト製造編'
PROGRAMMER_CHECKLIST_INTRO = [
    '製造工程（詳細設計・製造・テスト）の現場に入るときは、最初の1週間で「担当機能と全体像を把握し、'
    '設計書とコードのつながりを自分の言葉で説明できる」ことが求められる。このチェックリストは、'
    '入場前・初日・1週目・2週目以降の順に、確認しておくことを並べたもの。',
    kit_data.CHECKLIST_INTRO[1],
]
PROGRAMMER_REPLACED_ITEMS = {
    '担当工程（要件定義／基本設計／詳細設計）と担当機能の範囲': '担当工程（詳細設計／製造／単体テスト／結合テスト）と担当機能の範囲',
    '成果物の種類と形式（設計書テンプレート、レビューの進め方）': '成果物の種類と形式（ソースコード、テスト仕様書と結果、コードレビューの進め方）',
    '既存設計書の矛盾・記載漏れをメモする': '担当機能の設計書を読み、実装で迷う箇所・矛盾・記載漏れをメモする',
    '設計書の指摘は、課題管理表など残る場所に記録する（口頭だけで終えない）': 'コードレビューや設計書の指摘は、課題管理表など残る場所に記録する（口頭だけで終えない）',
}
PROGRAMMER_ADDED_ITEMS = {
    'ドキュメントと環境': ['開発環境の作り方、ソースコードの置き場所とブランチの運用、コーディング規約'],
    '担当機能（3〜4日目）': ['開発環境で既存のコードを動かし、担当機能の処理を追う'],
}

# docs/ の区分（スライド7）。キーは Document.kind に使う。
DOC_SECTIONS = [
    ('pre', '00_参画前', '面談メモ、言葉のズレの確認'),
    ('contract', '01_契約・条件', '作業範囲と約束事項（金額は置かない）'),
    ('structure', '02_体制', '体制図、決裁者、会議体'),
    ('business', '03_業務理解', '業務フロー、用語集'),
    ('design', '04_設計', '設計の判断（案・決定・理由）'),
    ('minutes', '05_議事録', '会議ごとに1ファイル'),
    ('weekly', '06_週報', 'やったこと・分かったこと・詰まり・次'),
    ('retro', '07_振り返り', '約束と実際、目標への成長'),
]
DOC_SECTION_NOTES = {key: note for key, _, note in DOC_SECTIONS}

ISSUE_KINDS = [
    ('question', '確認事項', '誰かに確かめること。誰に聞くかを書く'),
    ('problem', '課題', '懸念・リスク。事実・解釈・未確認のことに分けて、影響と一緒に書く'),
    ('task', '作業', 'やること。予定日と完了日で進捗表に並ぶ'),
]
ISSUE_LABELS = [
    ('スコープ外', '契約にない作業・責任を頼まれたとき。その場で引き受けず、自社の課長に相談する'),
    ('読めない', 'どの資料のどこが、なぜ読めないか（用語／前提／図がない）。2日動かなければ質問へ'),
    ('ナレッジ候補', '現場をまたいで役立ちそうな記録。リーダーが一般化を確認してから会社に公開する'),
]
LIFECYCLE = ['テンプレートから作成', '記録', '振り返り', 'Archive']

# 記録を残す場所（チェックリストの項目から移る先）
RECORD_TARGETS = {
    'progress': '進捗表',
    'question': '確認事項 Issue',
    'unreadable': '「読めない」Issue',
    'problem': '課題 Issue',
    'summary': '1週目のまとめ',
    'goal': '目標（スキルセット）',
    'structure_gap': '体制のズレ',
    **{f'doc:{key}': label for key, label, _ in DOC_SECTIONS},
}

# 小見出しごとの残す場所
_SECTION_TARGETS = {
    '契約・商流': ['doc:contract'],
    '役割と期待値': ['doc:pre', 'goal'],
    '自社側の支援': ['doc:pre'],
    '体制と人': ['doc:structure'],
    'ドキュメントと環境': ['doc:structure'],
    '自分の進捗管理をはじめる': ['progress'],
    '初日に聞いておく一言': ['question'],
    '全体像（1〜2日目）': ['doc:business'],
    '担当機能（3〜4日目）': ['doc:design'],
    '「読めない」を放置しない': ['unreadable'],
    '理解の「まとめ」を出す（5日目）': ['summary'],
    '2週目以降': ['doc:weekly', 'problem'],
}

# スライド12にあり元資料にない項目。目標の中身は spec 002 で作る。
GOAL_ITEM = ('役割と期待値', '問われるスキル項目を選び、今のレベルの一つ上の具体記述を目標にする')

STAGE_LABELS = {
    'pre': '入場前',
    'day1': '初日',
    'week1': '1週目',
    'week2': '2週目以降',
}

# 危険信号のうち、記録から判定するもの（genba/signals.py）
DETECTED_SIGNS = {
    '3日経っても理解が進んでいない': 'unreadable_stuck',
    '指摘も指示も来ない': 'no_weekly',
    '理解したことが形になっていない': 'no_summary',
}
# 判定しない兆候の、移る先
SIGN_TARGETS = {
    '契約にない作業・責任を頼まれる': 'scope_out',
    '日々の指示者が体制図と違う': 'structure_gap',
}


def phrase(scene):
    """元資料 6章の言い回しを場面名で引く"""
    for p in kit_data.PHRASES:
        if p['scene'] == scene:
            return p['phrase']
    raise KeyError(scene)


def job_label(job_type):
    return dict(JOB_TYPES)[job_type]


def job_type_of(user):
    """その人の職種。リポジトリをまだ持たない人は SE のテンプレートで見る"""
    repository = getattr(user, 'genba_repository', None)
    return repository.job_type if repository else JOB_SE


def checklist_title(job_type):
    return PROGRAMMER_CHECKLIST_TITLE if job_type == JOB_PROGRAMMER else kit_data.CHECKLIST_TITLE


def checklist_intro(job_type):
    return PROGRAMMER_CHECKLIST_INTRO if job_type == JOB_PROGRAMMER else kit_data.CHECKLIST_INTRO


def checklist(job_type=JOB_SE):
    """段階 → 小見出し → 項目。残す場所と目標の項目を足したもの。職種で項目が変わる"""
    stages = []
    for stage in kit_data.CHECKLIST_STAGES:
        sections = []
        for section in stage['sections']:
            items = list(section['items'])
            if job_type == JOB_PROGRAMMER:
                items = [PROGRAMMER_REPLACED_ITEMS.get(item, item) for item in items]
                items += PROGRAMMER_ADDED_ITEMS.get(section['title'], [])
            if section['title'] == GOAL_ITEM[0]:
                items.append(GOAL_ITEM[1])
            targets = _SECTION_TARGETS[section['title']]
            sections.append({**section, 'items': items, 'targets': targets,
                             'target_names': [RECORD_TARGETS[t] for t in targets]})
        stages.append({**stage, 'label': STAGE_LABELS[stage['key']], 'sections': sections})
    return stages


def join_site(site, user, role, joined_on, duty='', job_type=None):
    """現場に入る。その人の職種の入場時チェックリストを作業 Issue として並べる。

    job_type を省くと、リポジトリの今の職種のまま入る（マネジメントのアサインなど、本人が選ばない入り方）。
    """
    from .models import Membership

    repository = ensure_repository(user, job_type)
    membership = Membership.objects.create(site=site, user=user, role=role, joined_on=joined_on, duty=duty)
    create_checklist(site, user, repository.job_type)
    return membership


def ensure_repository(user, job_type=None):
    """メンバー1人に1つのリポジトリを用意する（構想書スライド2）。現場が変わっても同じリポジトリを使い続ける。

    job_type を渡すと、作るときはその職種で作り、既にあれば職種だけ変える。リポジトリは増えない。
    """
    from .models import MemberRepository

    repository, created = MemberRepository.objects.get_or_create(
        user=user, defaults={'name': f'genba-{user.username}', 'job_type': job_type or JOB_SE})
    if not created and job_type and repository.job_type != job_type:
        repository.job_type = job_type
        repository.save(update_fields=['job_type'])
    return repository


def create_checklist(site, user, job_type=JOB_SE):
    """その人のチェックリストを、作業 Issue とチェック項目として並べる。

    入場のときの職種で写すので、あとで職種を変えても、作成済みの現場のチェックリストは変わらない。
    """
    from .models import ChecklistItem, Question

    order = 0
    for stage in checklist(job_type):
        for section in stage['sections']:
            order += 1
            q = Question.objects.create(
                site=site, author=user, kind=Question.KIND_TASK,
                title=f'［{stage["label"]}］{section["title"]}',
                body='\n'.join(filter(None, [stage['aim'], section['note']])),
                stage=stage['key'], record_to=','.join(section['targets']), order=order,
            )
            ChecklistItem.objects.bulk_create(
                ChecklistItem(question=q, text=text, order=i) for i, text in enumerate(section['items'], 1)
            )


# 成長のつながり（構想書スライド6）：ITSS の達成度 → 個人の成長 → 会社の成長
GROWTH_LINKS = [
    ('責任性', 'メンバー → リーダー → 責任者', '担える立場が、要員の参画からチーム・工程の一括へ'),
    ('複雑性', '経験した複雑性の項目が増える', '受けられる案件の難しさが上がる'),
    ('サイズ', '率いるチームが3人から10人へ', '請けられるチームの規模が大きくなる'),
    ('プロフェッショナル貢献', '後進の指導、社内講師、論文', '育成が速くなり、ナレッジが資産になる'),
]
GROWTH_LOOP = ['個人の成長', '会社が担える仕事が広がる', '次の目標に合う現場へアサイン', '次の成長']
GROWTH_NOTE = ('人の成長と会社の成長は、ひとつのビジョンの両面。個人の成長は、そのまま会社が担える仕事の大きさになる。'
               '情報サービス業では、一人ひとりの成長が会社の商品力になる。')

ROLE_GROWTH = [
    ('メンバー', '担当成果物をやり遂げる', 'レベル2 → 3。担当機能の設計を独力で行う', '用語集、1週目のまとめ、設計メモ'),
    ('リーダー', 'チームの成果物に責任を持つ', 'レベル3 → 4。3人以上のチーム、後進の指導', 'レビューの記録、詰まりの解消、メンバーの成長'),
    ('マネジメント', '複数の現場と人の育成に責任を持つ', 'レベル4 → 5。社内への貢献、技術継承の実績', '運営指標の改善、約束とのズレの減少、リーダーの育成'),
    ('会社', '担える仕事の大きさを広げる', '担える立場・規模・複雑性。組織のスキル分布', '上流・リーダー枠での受注、物差しとチェックリストの改訂'),
]
ROLE_GROWTH_NOTE = ('成長はつながっている：メンバーの成長はリーダーの「後進の指導」の証拠に、リーダーの成長はマネジメントの'
                    '「育成」の証拠に、全員の成長は会社の受注力になる。')

# ビジョンの階層。上の2つは会社のホームページ、下の2つは構想プレゼン（表紙・スライド5）の言葉。言い換えない。
# 使命から順に、メンバーの目標まで同じ向きに届くようにする（憲章 VII）
VISION = [
    ('使命', 'ITの力で世の中をより便利にして、人の心を豊かにする'),
    ('経営理念', '実務的で付加価値創造型の企業であり続け、ITシステムを駆使して社会全体を豊かにする'),
    ('ビジョン', '会社の成長は、人から始まる。'),
    ('仕組み', '会社が人を支え、人の成長が会社を育てる'),
]

# ---- 案内ページ（/pvsys/genba/about/）：hirahira-room から最初に開くページ。文言は構想書（スライド1・3・4）のまま ----

ABOUT_TITLE = '会社の成長は、人から始まる。'
ABOUT_LEAD = 'genba — 人の成長と会社の成長をつなぐプラットフォーム'
ABOUT_MESSAGE = '現場の経験を記録し、人と会社がひとつのビジョンに向かって成長する。'
ABOUT_ISSUES = [
    ('入場直後に迷う', '上流の現場では、最初の1週間で担当機能と全体像を把握し、理解を成果物にして見せることが求められる。'
                      '何を確かめればよいかは人に依存している。'),
    ('現場の状況が上に届かない', '指摘がないことを「問題ない」と受け取り、自社にも「問題なし」と報告してしまう。'
                                '詰まりや約束とのズレが、表に出るころには手遅れになる。'),
    ('知見も成長も、会社に残らない', '用語の意味、設計の判断、入場時の教訓は個人の手元に散る。'
                                  '何ができるようになったかも、スキルシートに一行で残るだけ。'),
]
ABOUT_ROLES = [
    ('fa-user', 'メンバー：成長と支援kit', '現場で働く。チェックリスト・目標設定・記録'),
    ('fa-user-friends', 'リーダー：メンバーを支える', 'チームを率いる。詰まりの発見、成長の確認'),
    ('fa-chart-bar', 'マネジメント：運営指標', '複数の現場を見る。現場単位の指標、成長の承認'),
    ('fa-building', '会社：ナレッジ資産・スキル台帳', '全社で持つ。公開されたナレッジと、個人の成長'),
]

# ---- デモの説明ページ（/pvsys/genba/demo/）：案内ページと genba 本体のあいだに挟む ----
# 現場と人の一覧はデモデータ（seed_genba）から読む。ここに持つのは、データにない説明だけ

DEMO_PREFIX = 'genba-demo-'  # デモの現場チーム名の頭。seed_genba もこれを使う
DEMO_ORGANIZATION = '架空の SI 会社です。チームが2つあり、それぞれのメンバーが顧客の現場に入っています。'
# 訪問者が切り替える4つの立場（ペルソナ）。立場ごとに別の人がいる（ひとりが全部を持つのは現実的でないため）。
# key：セッションに持つ値 ／ username：その立場の人 ／ place・point：説明ページの文言 ／ home：入ったときに開く画面
DEMO_PERSONAS = [
    {'key': 'member', 'icon': 'fa-user', 'label': 'メンバー', 'username': 'demo_kimura', 'place': '保険金請求 基本設計',
     'point': '入場から6日目。読めない資料があり、危険信号が出ている。自分の職掌の状況も見られる', 'home': 'genba:member_home'},
    {'key': 'leader', 'icon': 'fa-user-friends', 'label': 'リーダー', 'username': 'demo_kobayashi',
     'place': '受発注システム刷新', 'point': 'メンバーの詰まりと、成長の確認待ちが届いている', 'home': 'genba:leader_home'},
    {'key': 'management', 'icon': 'fa-chart-bar', 'label': 'マネジメント', 'username': 'demo_kacho', 'place': 'すべての現場',
     'point': '4つの職掌とKPI、現場単位の運営指標、成長の承認待ち', 'home': 'genba:management'},
    {'key': 'company', 'icon': 'fa-building', 'label': '会社', 'username': 'demo_honbu', 'place': '全社',
     'point': '職掌と経営KPI、ナレッジ資産の残高と活用、個人のスキル台帳', 'home': 'genba:company'},
]
DEMO_DEFAULT_PERSONA = 'member'  # 立場を選ばずに本体を開いた未ログインの人は、メンバーとして見る
# チームと、そのチームの現場（キーは現場チーム名の DEMO_PREFIX より後ろ）・現場の今の状況
DEMO_TEAMS = [
    ('チーム1', [
        ('order', '入場から10週。確定後の取消が決まらず、画面の設計が止まっている'),
        ('logistics', '自社のリーダーがいない現場。成長はマネジメントが元請の声を聞いて確かめた'),
    ]),
    ('チーム2', [
        ('claims', '12週目。周辺作業がスコープ外のまま依頼され、範囲を交渉している'),
        ('stock', '自社のリーダーがいない現場。週報が滞り、成長の確認を依頼している'),
    ]),
]
