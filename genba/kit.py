"""現場テンプレート（genba-template に当たるもの）。

プロトタイプでは GitHub のテンプレートリポジトリを作らず、構成をここに持つ。
現場に入った人ごとに、チェックリストがその現場の作業 Issue としてコピーされる。
元資料を改訂しても、作成済みの現場は変わらない（新しく作る現場から新しい版を使う）。
"""
from . import kit_data

TEMPLATE_VERSION = '2026-10'

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


def checklist():
    """段階 → 小見出し → 項目。残す場所と目標の項目を足したもの"""
    stages = []
    for stage in kit_data.CHECKLIST_STAGES:
        sections = []
        for section in stage['sections']:
            items = list(section['items'])
            if section['title'] == GOAL_ITEM[0]:
                items.append(GOAL_ITEM[1])
            targets = _SECTION_TARGETS[section['title']]
            sections.append({**section, 'items': items, 'targets': targets,
                             'target_names': [RECORD_TARGETS[t] for t in targets]})
        stages.append({**stage, 'label': STAGE_LABELS[stage['key']], 'sections': sections})
    return stages


def join_site(site, user, role, joined_on, duty=''):
    """現場に入る。その人の入場時チェックリストを作業 Issue として並べる"""
    from .models import Membership

    membership = Membership.objects.create(site=site, user=user, role=role, joined_on=joined_on, duty=duty)
    create_checklist(site, user)
    return membership


def create_checklist(site, user):
    """その人のチェックリストを、作業 Issue とチェック項目として並べる"""
    from .models import ChecklistItem, Question

    order = 0
    for stage in checklist():
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
