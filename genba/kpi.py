"""会社の職掌と経営KPI（案）。職掌ごとに「何に責任を持つか」と、その成果を測る数字を決める。

会社の立場が担う仕事を4つの職掌に分ける。

| 職掌 | 責任を持つこと | 見る数字 |
|---|---|---|
| 営業・事業企画 | 担える仕事の大きさを広げ、次に狙う案件を決める | リーダー枠を担える人の割合、質問リストの件数 |
| 人材開発 | 育成へ再投資する項目を決める | 成長した人の割合、層の薄い項目の数 |
| ナレッジ管理 | 現場の経験を会社の資産に変える | 公開の増分、業界カバー率 |
| 標準化 | 現場の教訓で物差しとテンプレートを育てる | 改訂提案の決定率・採用率 |

4つの職掌の上に「経営の結果」として、標準売上・標準粗利率・稼働率を置く。金額は階級 × 人月から足し上げる（money.py）。
実際の売上・粗利は別のシステムの話で、ここの数字は傾向を見るための標準値。
4つの職掌の数字は、その手前で動く先行指標になる。リーダー枠を担える人が増えれば高い立場で受注でき、
質問リストが育てば営業時の説明と実態のズレが減り、利益に効く、という順につながる。

使うデータは、会社の立場が見てよいものだけ（憲章 IV の公開範囲の表）。

- 承認した成長（会社の「個人のスキル台帳」の行）。記入中・確認中の成長は数えない（憲章 V）
- 一般化して公開された記録。公開をやめたものは数えない
- 改訂の提案と質問リスト。もともと会社の立場が扱うもの
- 在籍人数と現場の業界。割合の分母にだけ使い、現場名・顧客名は返さない
- 階級 × 人月を足し上げた標準売上・標準粗利（会社には全社と業界ごと、マネジメントには現場ごと）

週報・Issue・約束と実際のような運営指標の元データは、会社の列が「―」なので使わない。
新しい入力欄も足さない。どの数字も既にある記録から数える（憲章 I）。

下の立場にも見せる数字は for_role() で絞る。メンバーとリーダーには、次の目標や育成に使える数字を全社の割合で渡し、
グラフと人数は渡さない。少人数の会社では、人数から誰のことか推測できるため。
金額はどの立場にも「自分の範囲の合計」だけを見せる。メンバーとリーダーは自分の現場、マネジメントは全現場、会社は全社。

人を並べて比べる数字は作らない。
目標はすべて案で、TARGETS を直せば変えられる。
"""
import calendar
from dataclasses import dataclass
from datetime import date

from django.utils import timezone

from . import growth, money
from .metrics import site_metrics
from .models import VISIBILITY_COMPANY, Document, Goal, InterviewQuestion, KitProposal, Membership, Site, Term

# 期間：3か月ごとに6つ。今月を含む区切りを最後にして、さかのぼる。
# 暦の四半期にしないのは、月初めの「今の四半期」が1か月分しかなく、前の期と比べられなくなるのを減らすため
PERIOD_MONTHS = 3
PERIOD_COUNT = 6

# 目標（案）
TARGETS = {
    'leader_rate': 30,      # リーダー枠を担える人の割合（%）
    'questions': 10,        # 質問リストの件数
    'growth_rate': 30,      # 直近3か月に成長が承認された人の割合（%）
    'thin_items': 3,        # 層の薄い項目の数（これ以下）
    'published': 3,         # 直近3か月に公開したナレッジの件数
    'industry_rate': 80,    # 業界カバー率（%）
    'decided_rate': 80,     # 改訂提案の決定率（%）
    'adopted_rate': 50,     # 改訂提案の採用率（%）
    'sales_growth': 100,    # 標準売上の前の3か月比（%）
    'gross_rate': 30,       # 標準粗利率（%）
    'operating_rate': 90,   # 稼働率（%）
    'alert_sites': 25,      # 兆候が出ている現場の割合（%・これ以下）
    'approval_days': 7,     # 承認待ちの平均日数（これ以下）
    'next_rate': 50,        # 次の目標を持つ人の割合（%）
    'promise_gaps': 3,      # 約束と実際のズレの件数（これ以下）
    'scope_out': 5,         # スコープ外の件数（これ以下）
}
# レベル3以上がこの人数に満たない項目を「層が薄い」とする。1人だけだと、その人が抜けた現場で受けられなくなるため
THIN_PEOPLE = 2

# グラフの色。段階（レベル）は1色の濃淡、種類は色相を固定の順で使う。
# 色はどれも検査スクリプトを通した組み合わせ。コントラストが低い色があるので、表でも読めるようにする
ORDINAL_3 = ['#86b6ef', '#3987e5', '#0d366b']
ORDINAL_4 = ['#86b6ef', '#3987e5', '#256abf', '#0d366b']
CATEGORICAL = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948']

DEFINITIONS = [
    ('経営の結果', '標準売上', '階級の標準単価 × 人月 を全社で足し上げた額（直近3か月）', f'前の3か月の{TARGETS["sales_growth"]}%以上', '事業が伸びているか'),
    ('経営の結果', '標準粗利率', '（標準売上 − 階級の標準原価 × 人月）÷ 標準売上', f'{TARGETS["gross_rate"]}%以上', '今の階級の構成で利益が残るか'),
    ('経営の結果', '稼働率', '今月、現場に入っている人数 ÷ 全社の在籍人数', f'{TARGETS["operating_rate"]}%以上', '次のアサインを急ぐ人がいるか'),
    ('営業・事業企画', 'リーダー枠を担える人の割合', 'ITSSレベル4相当以上の人数 ÷ 在籍人数（承認した成長から）',
     f'{TARGETS["leader_rate"]}%以上', '上の立場の案件を狙えるか'),
    ('営業・事業企画', '質問リストの件数', '営業・面談の質問リストの件数', f'{TARGETS["questions"]}件以上', '現場の教訓が入場前の確認に戻っているか'),
    ('人材開発', '成長した人の割合', '直近3か月に成長が承認された人数 ÷ 在籍人数', f'{TARGETS["growth_rate"]}%以上', '育成が止まっていないか'),
    ('人材開発', '層の薄い項目', f'レベル3以上が{THIN_PEOPLE}人に満たないスキル項目の数', f'{TARGETS["thin_items"]}項目以下', 'どこへ育成を再投資するか'),
    ('ナレッジ管理', '公開したナレッジ', '直近3か月に一般化して公開した記録の件数', f'{TARGETS["published"]}件以上', '経験が資産になり続けているか'),
    ('ナレッジ管理', '業界カバー率', '公開した記録がある業界 ÷ 現場の業界', f'{TARGETS["industry_rate"]}%以上', '得意な業界の知見が残っているか'),
    ('標準化', '改訂提案の決定率', '採用か見送りを決めた提案 ÷ すべての提案', f'{TARGETS["decided_rate"]}%以上', '現場の声を止めていないか'),
    ('標準化', '改訂提案の採用率', '採用した提案 ÷ 決めた提案', f'{TARGETS["adopted_rate"]}%以上', '物差しが現場の教訓で育っているか'),
]

# マネジメントの職掌の指標。会社が物差しと方針を決め、マネジメントはその物差しで今の現場と人を動かす
MANAGEMENT_DEFINITIONS = [
    ('現場の運営', '兆候が出ている現場の割合', '運営指標の目安を超えた現場 ÷ 動いている現場', f'{TARGETS["alert_sites"]}%以下', 'どの現場に手を打つか'),
    ('人の成長の確定', '承認待ちの平均日数', '確認が済んでから（ひとり現場は依頼から）今日までの日数の平均', f'{TARGETS["approval_days"]}日以下', '成長の確定を止めていないか'),
    ('アサイン', '次の目標を持つ人の割合', '承認した成長に次の目標がある人 ÷ 成長が承認された人', f'{TARGETS["next_rate"]}%以上', '次のアサインの材料があるか'),
    ('アサイン', '稼働率', '今月、現場に入っている人数 ÷ 全社の在籍人数', f'{TARGETS["operating_rate"]}%以上', '現場へ動かす人がいるか'),
    ('約束の調整', '約束と実際のズレ', '動いている現場の、食い違った行の合計', f'{TARGETS["promise_gaps"]}件以下', '営業時の説明を直すか'),
    ('約束の調整', 'スコープ外の件数', '動いている現場の「スコープ外」の合計', f'{TARGETS["scope_out"]}件以下', '追加交渉や契約の見直しが要るか'),
]

# 使わない指標。金額は積み上げるが、人を比べる数字にはしない（憲章 II・VI）
EXCLUDED = '実際の単価・売上・粗利（別のシステムで見る）／個人別の標準売上・標準粗利の比較／個人別の稼働時間の比較。'


@dataclass
class Period:
    start: date
    end: date

    @property
    def label(self):
        return f'{self.start.year % 100}/{self.start.month}–{self.end.month}'


@dataclass
class Tile:
    """KPI の1マス。met は目標に届いたか（数えられないときは None で、達成とも未達とも出さない）"""
    name: str
    value: str
    detail: str
    target: str
    met: bool | None


def periods(today=None):
    """古い順の期間の一覧。最後の期間は今月を含む（まだ終わっていなくても、今月末までを区切りにする）"""
    today = today or timezone.localdate()
    last = today.year * 12 + today.month - 1
    rows = []
    for back in range(PERIOD_COUNT - 1, -1, -1):
        end = last - back * PERIOD_MONTHS
        start = end - PERIOD_MONTHS + 1
        end_year, end_month = divmod(end, 12)
        start_year, start_month = divmod(start, 12)
        rows.append(Period(
            date(start_year, start_month + 1, 1),
            date(end_year, end_month + 1, calendar.monthrange(end_year, end_month + 1)[1]),
        ))
    return rows


def percent(part, whole):
    """割合（%）。分母が0なら数えられないので None"""
    return round(part * 100 / whole) if whole else None


def judge(value, target, lower_is_better=False):
    if value is None:
        return None
    return value <= target if lower_is_better else value >= target


def show_percent(value):
    return '―' if value is None else f'{value}%'


def headcount():
    """在籍人数：Archive していない現場に入っている人の数。割合の分母に使う"""
    return Membership.objects.filter(site__is_archived=False).values('user').distinct().count()


def chart(chart_id, labels, series, colors, horizontal=False):
    """グラフ1つ分。canvas へ JSON で渡し、同じ値を表でも出す（色だけに頼らないよう、表で読めるようにする）"""
    return {
        'id': chart_id, 'labels': labels, 'horizontal': horizontal,
        'series': [{'label': label, 'data': data, 'color': color} for (label, data), color in zip(series, colors)],
    }


def table_rows(c):
    """グラフの値を、1行＝ラベル1つの表にする"""
    return [{'label': label, 'values': [s['data'][i] for s in c['series']]} for i, label in enumerate(c['labels'])]


def sales_function(spans, people):
    """営業・事業企画：担える立場の推移と、入場前に確かめる質問の蓄積"""
    leaders = sum(n for level, n in growth.itss_distribution() if level >= 4)
    rate = percent(leaders, people)
    questions = InterviewQuestion.objects.count()
    # 各期間の終わりの時点の台帳を作り直す。同じ人の後の承認で上がったレベルは、その時点では数えない
    history = [dict(growth.itss_distribution(growth.approved_reaches(until=p.end))) for p in spans]
    return {
        'name': '営業・事業企画', 'duty': '担える仕事の大きさを広げ、次に狙う案件を決める',
        'tiles': [
            Tile('リーダー枠を担える人', show_percent(rate), f'{leaders}人 ／ 在籍 {people}人',
                 f'目標 {TARGETS["leader_rate"]}%', judge(rate, TARGETS['leader_rate'])),
            Tile('質問リスト', f'{questions}件', '営業・面談で確かめる質問',
                 f'目標 {TARGETS["questions"]}件', judge(questions, TARGETS['questions'])),
        ],
        'chart_title': '担える立場の人数（ITSSレベル相当）',
        'chart': chart('kpi-itss', [p.label for p in spans],
                       [(f'レベル{n}相当', [row[n] for row in history]) for n in (3, 4, 5)], ORDINAL_3),
    }


def development_function(spans, people):
    """人材開発：直近の期間に成長が承認された人と、スキル項目ごとの層の厚さ"""
    distribution = growth.skill_distribution()
    thin = [row['item'] for row in distribution if row['level3_up'] < THIN_PEOPLE]

    def grown(period):
        # 同じ人が複数の項目で承認されても1人と数える。今いない人は分母にいないので外す
        return (Goal.objects.filter(approved_on__range=(period.start, period.end),
                                    author__genba_memberships__site__is_archived=False)
                .order_by().values('author').distinct().count())

    current, previous = grown(spans[-1]), grown(spans[-2])
    rate = percent(current, people)
    return {
        'name': '人材開発', 'duty': '育成へ再投資する項目を決める',
        'tiles': [
            Tile('成長した人（直近3か月）', show_percent(rate), f'{current}人 ／ 前の3か月 {previous}人',
                 f'目標 {TARGETS["growth_rate"]}%', judge(rate, TARGETS['growth_rate'])),
            Tile('層の薄い項目', f'{len(thin)}項目', '、'.join(thin) or '―',
                 f'目標 {TARGETS["thin_items"]}項目以下', judge(len(thin), TARGETS['thin_items'], lower_is_better=True)),
        ],
        'chart_title': 'スキル項目ごとの人数（承認したレベル）',
        'chart': chart('kpi-skill', [row['item'] for row in distribution],
                       [(f'レベル{n}', [row['counts'][i] for row in distribution]) for i, n in enumerate((2, 3, 4, 5))],
                       ORDINAL_4, horizontal=True),
        'tall': True,
    }


def published_dates():
    """公開した記録の種類ごとの公開日。公開をやめた記録は visibility が戻るので入らない"""
    docs = Document.objects.filter(visibility=VISIBILITY_COMPANY)
    terms = Term.objects.filter(visibility=VISIBILITY_COMPANY)

    def days(queryset):
        return [timezone.localdate(t) for t in queryset.exclude(published_at=None).values_list('published_at', flat=True)]

    return [
        ('用語', days(terms)),
        ('設計判断', days(docs.filter(kind=Document.KIND_DESIGN))),
        ('振り返り', days(docs.filter(kind=Document.KIND_RETRO))),
        ('面談メモ', days(docs.filter(kind=Document.KIND_PRE))),
    ]


def knowledge_function(spans):
    """ナレッジ管理：公開の増分と、どの業界の経験が資産になっているか"""
    groups = published_dates()
    everything = [d for _, dates in groups for d in dates]
    latest = spans[-1]
    added = sum(1 for d in everything if latest.start <= d <= latest.end)
    industries = set(Site.objects.values_list('industry', flat=True))
    covered = set(Document.objects.filter(visibility=VISIBILITY_COMPANY).values_list('site__industry', flat=True))
    covered |= set(Term.objects.filter(visibility=VISIBILITY_COMPANY).values_list('site__industry', flat=True))
    rate = percent(len(covered & industries), len(industries))
    return {
        'name': 'ナレッジ管理', 'duty': '現場の経験を会社の資産に変える',
        'tiles': [
            Tile('公開したナレッジ（直近3か月）', f'{added}件', f'累計 {len(everything)}件',
                 f'目標 {TARGETS["published"]}件', judge(added, TARGETS['published'])),
            Tile('業界カバー率', show_percent(rate), f'{len(covered & industries)}業界 ／ {len(industries)}業界',
                 f'目標 {TARGETS["industry_rate"]}%', judge(rate, TARGETS['industry_rate'])),
        ],
        'chart_title': '公開したナレッジの累計',
        'chart': chart('kpi-knowledge', [p.label for p in spans],
                       [(name, [sum(1 for d in dates if d <= p.end) for p in spans]) for name, dates in groups], CATEGORICAL),
    }


def standard_function():
    """標準化：現場から上がった改訂の提案を、止めずに決めて、採り入れているか"""
    proposals = list(KitProposal.objects.values_list('target', 'status'))
    adopted = sum(1 for _, status in proposals if status == 'adopted')
    decided = sum(1 for _, status in proposals if status != 'open')
    decided_rate, adopted_rate = percent(decided, len(proposals)), percent(adopted, decided)
    targets = KitProposal.TARGET_CHOICES
    return {
        'name': '標準化', 'duty': '現場の教訓で物差しとテンプレートを育てる',
        'tiles': [
            Tile('改訂提案の決定率', show_percent(decided_rate), f'{decided}件 ／ 提案 {len(proposals)}件',
                 f'目標 {TARGETS["decided_rate"]}%', judge(decided_rate, TARGETS['decided_rate'])),
            Tile('改訂提案の採用率', show_percent(adopted_rate), f'{adopted}件 ／ 決定 {decided}件',
                 f'目標 {TARGETS["adopted_rate"]}%', judge(adopted_rate, TARGETS['adopted_rate'])),
        ],
        'chart_title': '改訂提案の状態',
        'chart': chart('kpi-proposal', [label for _, label in targets],
                       [(label, [proposals.count((key, status)) for key, _ in targets])
                        for status, label in (('adopted', '採用'), ('open', '提案中'), ('declined', '見送り'))],
                       CATEGORICAL, horizontal=True),
    }


def yen(amount):
    """画面に出す額（万円）"""
    return f'{round(amount / 10000):,}万円'


def result_function(spans, today, group):
    """経営の結果。group は積み上げの内訳：会社は業界ごと（industries）、マネジメントは現場ごと（sites）"""
    current, previous = money.rollup(spans[-1].start, spans[-1].end), money.rollup(spans[-2].start, spans[-2].end)
    growth_rate = percent(current['total'].revenue, previous['total'].revenue)
    gross = current['total'].gross_rate
    billed, enrolled, operating = money.operating(today or timezone.localdate())
    history = [money.rollup(p.start, p.end)[group] for p in spans]
    names = sorted({name for row in history for name in row})
    return {
        'name': '経営の結果', 'duty': '標準売上と標準粗利を、階級 × 人月から足し上げて見る',
        'tiles': [
            Tile('標準売上（直近3か月）', yen(current['total'].revenue), f'前の3か月 {yen(previous["total"].revenue)}',
                 f'目標 前の3か月の{TARGETS["sales_growth"]}%', judge(growth_rate, TARGETS['sales_growth'])),
            Tile('標準粗利率（直近3か月）', show_percent(gross), f'標準粗利 {yen(current["total"].gross)}',
                 f'目標 {TARGETS["gross_rate"]}%', judge(gross, TARGETS['gross_rate'])),
            Tile('稼働率（今月）', show_percent(operating), f'{billed}人 ／ 在籍 {enrolled or "―"}人',
                 f'目標 {TARGETS["operating_rate"]}%', judge(operating, TARGETS['operating_rate'])),
        ],
        'chart_title': '標準売上の積み上げ（万円）',
        'chart': chart('kpi-sales', [p.label for p in spans],
                       [(name, [round(row[name].revenue / 10000) if name in row else 0 for row in history]) for name in names],
                       CATEGORICAL),
        'wide': True,
    }


def company_functions(today=None, group='industries'):
    """職掌ごとの KPI とグラフ（画面の並び順）。テンプレートには chart の値と、同じ値の表を渡す"""
    spans = periods(today)
    people = headcount()
    functions = [result_function(spans, today, group), sales_function(spans, people),
                 development_function(spans, people), knowledge_function(spans), standard_function()]
    for function in functions:
        function['rows'] = table_rows(function['chart'])
    return functions


# メンバーとリーダーに見せる KPI。名前の変更に気づけるよう、Tile の name をそのまま使う。
# 成長した人の割合は、メンバーには見せない（自分だけ遅れているという圧力になりやすいため）
SHARED_TILES = {
    'member': {'リーダー枠を担える人', '質問リスト', '層の薄い項目', '公開したナレッジ（直近3か月）', '業界カバー率',
               '改訂提案の決定率', '改訂提案の採用率'},
}
SHARED_TILES['leader'] = SHARED_TILES['member'] | {'成長した人（直近3か月）'}
# 項目名は目標選びに使うので残す。ほかの detail は人数なので外す
KEEP_DETAIL = {'層の薄い項目'}


def for_role(role, today=None):
    """立場ごとの KPI。マネジメントは現場ごとの積み上げまで全部。リーダーとメンバーは全社の割合だけで、グラフと金額はない"""
    if role == 'management':
        return company_functions(today, group='sites')
    functions = []
    for function in company_functions(today):
        tiles = [t for t in function['tiles'] if t.name in SHARED_TILES[role]]
        if tiles:
            for tile in tiles:
                if tile.name not in KEEP_DETAIL:
                    tile.detail = ''
            functions.append({'name': function['name'], 'duty': function['duty'], 'tiles': tiles, 'chart': None})
    return functions


def waiting_approvals():
    """マネジメントの承認を待っている成長：確認が済んだもの、またはひとり現場の確認待ち"""
    goals = Goal.objects.filter(approved_on__isnull=True, submitted_at__isnull=False).select_related('site')
    return [g for g in goals if g.confirmed_at or g.is_solo]


def management_functions(today=None):
    """マネジメントの4職掌。会社の KPI が全社の方向を見るのに対し、ここは今の現場と人を動かすための数字を返す。

    すべて既にある記録から数える。現場ごとの値か、人数と割合だけで、人を並べて比べる数字は返さない（憲章 II）。
    マネジメントの結果は会社の KPI の元になる。承認した成長はリーダー枠の割合と層の薄い項目に、
    アサインは稼働率と標準売上に、約束の調整は質問リストと改訂の提案につながる。
    """
    spans = periods(today)
    metrics = sorted((site_metrics(s, today) for s in Site.objects.filter(is_archived=False)), key=lambda m: m.site.name)
    names = [m.site.name for m in metrics]

    alerted = sum(1 for m in metrics if m.alerts)
    alert_rate = percent(alerted, len(metrics))
    operating_sites = {
        'name': '現場の運営', 'duty': '兆候を見つけ、手を打つ現場を決める',
        'tiles': [
            Tile('兆候が出ている現場', show_percent(alert_rate), f'{alerted}現場 ／ {len(metrics)}現場',
                 f'目標 {TARGETS["alert_sites"]}%以下', judge(alert_rate, TARGETS['alert_sites'], lower_is_better=True)),
            Tile('出ている兆候', f'{sum(len(m.alerts) for m in metrics)}件', '動いている現場の合計', '', None),
        ],
        'chart_title': '現場ごとの兆候の数',
        'chart': chart('mgmt-alerts', names, [('兆候', [len(m.alerts) for m in metrics])], ORDINAL_3[1:], horizontal=True),
    }

    waiting = waiting_approvals()
    now = timezone.now()
    days = round(sum((now - (g.confirmed_at or g.submitted_at)).days for g in waiting) / len(waiting), 1) if waiting else None
    approved = [Goal.objects.filter(approved_on__range=(p.start, p.end)).count() for p in spans]
    growth_confirmation = {
        'name': '人の成長の確定', 'duty': '成長を確かめて承認し、次の目標を示す。ひとり現場の確認も担う',
        'tiles': [
            Tile('承認待ちの平均日数', '―' if days is None else f'{days}日', f'承認待ち {len(waiting)}件',
                 f'目標 {TARGETS["approval_days"]}日以下', judge(days, TARGETS['approval_days'], lower_is_better=True)),
            Tile('承認した成長（直近3か月）', f'{approved[-1]}件', f'前の3か月 {approved[-2]}件', '', None),
        ],
        'chart_title': '承認した成長の件数',
        'chart': chart('mgmt-approved', [p.label for p in spans], [('承認', approved)], ORDINAL_3[1:]),
    }

    reaches = growth.approved_reaches()
    reached = {g.author_id for g in reaches}
    planned = {g.author_id for g in reaches if g.next_text}
    next_rate = percent(len(planned), len(reached))
    billed, enrolled, operating = money.operating(today or timezone.localdate())
    history = [money.operating(p.end) for p in spans]
    assignment = {
        'name': 'アサイン', 'duty': '次の目標に合う現場へ人を動かす',
        'tiles': [
            Tile('次の目標を持つ人', show_percent(next_rate), f'{len(planned)}人 ／ {len(reached)}人',
                 f'目標 {TARGETS["next_rate"]}%', judge(next_rate, TARGETS['next_rate'])),
            Tile('稼働率（今月）', show_percent(operating), f'{billed}人 ／ 在籍 {enrolled or "―"}人',
                 f'目標 {TARGETS["operating_rate"]}%', judge(operating, TARGETS['operating_rate'])),
        ],
        'chart_title': '現場に入っている人と待機の人数（各期の最後の月）',
        'chart': chart('mgmt-assign', [p.label for p in spans],
                       [('現場', [row[0] for row in history]),
                        ('待機', [max((row[1] or 0) - row[0], 0) for row in history])], CATEGORICAL),
    }

    gaps, scope_out = sum(m.promise_gaps for m in metrics), sum(m.scope_out for m in metrics)
    promises = {
        'name': '約束の調整', 'duty': '営業時の約束と現場の実態のズレを、顧客や営業と直す',
        'tiles': [
            Tile('約束と実際のズレ', f'{gaps}件', '動いている現場の合計',
                 f'目標 {TARGETS["promise_gaps"]}件以下', judge(gaps, TARGETS['promise_gaps'], lower_is_better=True)),
            Tile('スコープ外', f'{scope_out}件', '動いている現場の合計',
                 f'目標 {TARGETS["scope_out"]}件以下', judge(scope_out, TARGETS['scope_out'], lower_is_better=True)),
        ],
        'chart_title': '現場ごとの件数',
        'chart': chart('mgmt-promises', names,
                       [('約束と実際のズレ', [m.promise_gaps for m in metrics]), ('スコープ外', [m.scope_out for m in metrics]),
                        ('体制のズレ', [m.structure_gaps for m in metrics])], CATEGORICAL, horizontal=True),
    }

    functions = [operating_sites, growth_confirmation, assignment, promises]
    for function in functions:
        function['rows'] = table_rows(function['chart'])
    return functions


def management_reference(today=None):
    """マネジメントの画面の下に置く、会社の KPI。主役は自分の職掌なので、標準売上の積み上げのグラフだけを残す"""
    functions = for_role('management', today)
    for function in functions[1:]:
        function['chart'] = None
    return functions
