"""会社の職掌と経営KPI（案）。職掌ごとに「何に責任を持つか」と、その成果を測る数字を決める。

会社の立場が担う仕事を4つの職掌に分ける。

| 職掌 | 責任を持つこと | 見る数字 |
|---|---|---|
| 営業・事業企画 | 担える仕事の大きさを広げ、次に狙う案件を決める | リーダー枠を担える人の割合、質問リストの件数 |
| 人材開発 | 育成へ再投資する項目を決める | 成長した人の割合、層の薄い項目の数 |
| ナレッジ管理 | 現場の経験を会社の資産に変える | 公開の増分、業界カバー率 |
| 標準化 | 現場の教訓で物差しとテンプレートを育てる | 改訂提案の決定率・採用率 |

画面の順序は構想の因果に合わせる。人材開発から始まり、経営の結果は最後に置く。メンバーの成長が会社の成長を決めるので、
成長が原因で、売上は結果になる（憲章 VII）。「成長の寄与」で、階級が上がった分が標準売上にどれだけ効いたかを見る。

経営の結果として、標準売上・標準粗利率・稼働率を置く。金額は階級 × 人月から足し上げる（money.py）。
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
from datetime import date, timedelta

from django.utils import timezone

from . import grades, growth, kit, money, signals
from .metrics import THRESHOLDS, WEEKLY_RATE_MIN, site_metrics
from .models import (
    KNOWLEDGE_CANDIDATE, VISIBILITY_COMPANY, Assignment, Document, Goal, GoalEvidence, GradeAssignment, GradeReview, InterviewQuestion, KitProposal, Membership, MonthlyWork, Question, Site, Term,
    month_start, week_start,
)

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
    'support_rate': 80,     # 「読めない」に説明の時間を割り当てた割合（%）
    'feedback_rate': 80,    # まとめに返した割合（%）
    'reach_waiting': 2,     # 成長の確認待ちの件数（これ以下）
    'line_rate': 80,        # 裁量の線を書いた人の割合（%）
    'candidates': 3,        # 公開待ちの候補の件数（これ以下）
    'focus_rate': 50,       # 重点に向かう目標の割合（%）
    'matched_rate': 70,     # 次の目標に合うアサインの割合（%）
}
# レベル3以上がこの人数に満たない項目を「層が薄い」とする。1人だけだと、その人が抜けた現場で受けられなくなるため
THIN_PEOPLE = 2

# グラフの色。段階（レベル）は1色の濃淡、種類は色相を固定の順で使う。
# 色はどれも検査スクリプトを通した組み合わせ。コントラストが低い色があるので、表でも読めるようにする
ORDINAL_3 = ['#86b6ef', '#3987e5', '#0d366b']
ORDINAL_4 = ['#86b6ef', '#3987e5', '#256abf', '#0d366b']
CATEGORICAL = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948']

DEFINITIONS = [
    ('成長の寄与', '成長による標準売上', '標準売上 − 同じ人月を入った時の階級で計算した額（直近3か月）', '―', '人の成長が売上を押し上げているか'),
    ('成長の寄与', '階級が上がった人', '直近3か月に階級の見直しを採用した人数', '―', '成長を階級で報いているか'),
    ('経営の結果', '標準売上', '階級の標準単価 × 人月 を全社で足し上げた額（直近3か月）', f'前の3か月の{TARGETS["sales_growth"]}%以上', '事業が伸びているか'),
    ('経営の結果', '標準粗利率', '（標準売上 − 階級の標準原価 × 人月）÷ 標準売上', f'{TARGETS["gross_rate"]}%以上', '今の階級の構成で利益が残るか'),
    ('経営の結果', '稼働率', '今月、現場に入っている人数 ÷ 全社の在籍人数', f'{TARGETS["operating_rate"]}%以上', '次のアサインを急ぐ人がいるか'),
    ('営業・事業企画', 'リーダー枠を担える人の割合', 'ITSSレベル4相当以上の人数 ÷ 在籍人数（承認した成長から）',
     f'{TARGETS["leader_rate"]}%以上', '上の立場の案件を狙えるか'),
    ('営業・事業企画', '質問リストの件数', '営業・面談の質問リストの件数', f'{TARGETS["questions"]}件以上', '現場の教訓が入場前の確認に戻っているか'),
    ('人材開発', '成長した人の割合', '直近3か月に成長が承認された人数 ÷ 在籍人数', f'{TARGETS["growth_rate"]}%以上', '育成が止まっていないか'),
    ('人材開発', '重点に向かう目標', '承認前の目標のうち、会社の重点スキル項目に向かうもの', f'{TARGETS["focus_rate"]}%以上', '会社の重点がメンバーの目標に届いているか'),
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
    ('人の成長の確定', '作業を証拠にした成長', '承認した成長のうち、終えた作業を証拠にしたものの割合', '―', 'タスクの消化が成長につながっているか'),
    ('アサイン', '次の目標を持つ人の割合', '承認した成長に次の目標がある人 ÷ 成長が承認された人', f'{TARGETS["next_rate"]}%以上', '次のアサインの材料があるか'),
    ('アサイン', '目標に合うアサイン', '記録したアサインのうち、次の目標に合う現場へ入れたもの', f'{TARGETS["matched_rate"]}%以上', '成長のループが閉じているか'),
    ('アサイン', '稼働率', '今月、現場に入っている人数 ÷ 全社の在籍人数', f'{TARGETS["operating_rate"]}%以上', '現場へ動かす人がいるか'),
    ('約束の調整', '約束と実際のズレ', '動いている現場の、食い違った行の合計', f'{TARGETS["promise_gaps"]}件以下', '営業時の説明を直すか'),
    ('約束の調整', 'スコープ外の件数', '動いている現場の「スコープ外」の合計', f'{TARGETS["scope_out"]}件以下', '追加交渉や契約の見直しが要るか'),
]

# リーダーの職掌の指標。マネジメントが現場をまたいで手を打つのに対し、リーダーは1つの現場の中でメンバーを支える
LEADER_DEFINITIONS = [
    ('詰まりの解消', '確認事項の平均滞留日数', '未解決の確認事項の経過日数の平均', f'{THRESHOLDS["stall_days"]}日未満', '意思決定が止まっていないか'),
    ('詰まりの解消', '説明の時間を割り当てた割合', '未解決の「読めない」のうち、説明の時間が決まったもの', f'{TARGETS["support_rate"]}%以上', '読めないまま放っていないか'),
    ('メンバーの育成', 'まとめに返した割合', 'メンバーが出したまとめのうち、リーダーが返したもの', f'{TARGETS["feedback_rate"]}%以上', '理解の差を返せているか'),
    ('メンバーの育成', '成長の確認待ち', 'メンバーが確認を依頼し、まだ確認していない成長', f'{TARGETS["reach_waiting"]}件以下', '成長の確認を止めていないか'),
    ('メンバーの育成', '成長の証拠になった作業', 'メンバーが終えた作業のうち、成長の証拠に使われたものの割合', '―', 'タスクの消化が成長につながっているか'),
    ('チームの運営', '週報の提出率', '週報がある週の割合（現場単位）', f'{WEEKLY_RATE_MIN}%以上', '週次共有が回っているか'),
    ('チームの運営', '裁量の線を書いた人', 'リーダーを除くメンバーのうち、裁量の線を書いた人の割合', f'{TARGETS["line_rate"]}%以上', '決めてよい範囲を言葉にしたか'),
    ('知見の資産化', '公開待ちの候補', 'ナレッジ候補のまま、まだ公開していない記録', f'{TARGETS["candidates"]}件以下', '現場の経験を止めていないか'),
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
    """KPI の1マス。met は目標に届いたか（数えられないときは None で、達成とも未達とも出さない）。

    status はメンバーの画面だけで使う言葉（済み・これから など）。あると達成・未達の代わりに出す。
    メンバーの数字は本人が確かめるためのもので、採点に見えないよう未達の表示を使わない。
    """
    name: str
    value: str
    detail: str
    target: str
    met: bool | None
    status: str = ''


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
    # 会社の重点がメンバーの目標に届いているか。選ぶのは本人なので、画面では印を付けるだけで、ここで結果を見る
    focus = growth.focus_items()
    open_goals = Goal.objects.filter(approved_on__isnull=True, site__is_archived=False)
    toward = open_goals.filter(skill_item__in=focus).count()
    focus_rate = percent(toward, open_goals.count())
    return {
        'name': '人材開発', 'duty': '育成へ再投資する項目を決める',
        'tiles': [
            Tile('成長した人（直近3か月）', show_percent(rate), f'{current}人 ／ 前の3か月 {previous}人',
                 f'目標 {TARGETS["growth_rate"]}%', judge(rate, TARGETS['growth_rate'])),
            Tile('重点に向かう目標', show_percent(focus_rate), f'{toward}件 ／ 承認前 {open_goals.count()}件',
                 f'目標 {TARGETS["focus_rate"]}%', judge(focus_rate, TARGETS['focus_rate'])),
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


def growth_contribution_function(spans):
    """成長の寄与：人数が増えた分と、人が育って階級が上がった分を分けて見る"""
    shares = [money.growth_share(p.start, p.end) for p in spans]
    revenue, raised = shares[-1]
    latest = spans[-1]
    promoted = (GradeReview.objects.filter(status=GradeReview.STATUS_ADOPTED, decided_at__date__range=(latest.start, latest.end))
                .order_by().values('user').distinct().count())
    return {
        'name': '成長の寄与', 'duty': 'メンバーの成長が標準売上をどれだけ押し上げたかを見る',
        'tiles': [
            Tile('成長による標準売上（直近3か月）', yen(raised), f'標準売上の {show_percent(percent(raised, revenue))}', '', None),
            Tile('階級が上がった人（直近3か月）', f'{promoted}人', f'提案中 {GradeReview.objects.filter(status=GradeReview.STATUS_OPEN).count()}件', '', None),
        ],
        'chart_title': '標準売上の内訳（万円）',
        'chart': chart('kpi-growth', [p.label for p in spans],
                       [('入った時の階級の分', [round((r - g) / 10000) for r, g in shares]),
                        ('階級が上がった分', [round(g / 10000) for _, g in shares])], CATEGORICAL),
        'wide': True,
    }


def company_functions(today=None, group='industries'):
    """職掌ごとの KPI とグラフ（画面の並び順）。テンプレートには chart の値と、同じ値の表を渡す"""
    spans = periods(today)
    people = headcount()
    # 構想の因果の順：人が育つ → 担える仕事が広がる → 経験が資産になる → 物差しが育つ → 売上に表れる
    functions = [development_function(spans, people), sales_function(spans, people), knowledge_function(spans),
                 standard_function(), growth_contribution_function(spans), result_function(spans, today, group)]
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
    # タスクの消化が成長に届いているか。承認した成長のうち、終えた作業を証拠にしたもの
    recent = list(Goal.objects.filter(approved_on__range=(spans[-1].start, spans[-1].end)).prefetch_related('evidence'))
    tasks = set(Question.objects.filter(kind=Question.KIND_TASK, is_open=False).values_list('pk', flat=True))
    with_task = sum(1 for g in recent if any(e.record_type == 'issue' and e.record_id in tasks for e in g.evidence.all()))
    growth_confirmation = {
        'name': '人の成長の確定', 'duty': '成長を確かめて承認し、次の目標を示す。ひとり現場の確認も担う',
        'tiles': [
            Tile('承認待ちの平均日数', '―' if days is None else f'{days}日', f'承認待ち {len(waiting)}件',
                 f'目標 {TARGETS["approval_days"]}日以下', judge(days, TARGETS['approval_days'], lower_is_better=True)),
            Tile('承認した成長（直近3か月）', f'{approved[-1]}件', f'前の3か月 {approved[-2]}件', '', None),
            Tile('作業を証拠にした成長', show_percent(percent(with_task, len(recent))), f'{with_task}件 ／ {len(recent)}件', '', None),
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
    assigned = Assignment.objects.count()
    matched = Assignment.objects.filter(matched=True).count()
    matched_rate = percent(matched, assigned)
    assignment = {
        'name': 'アサイン', 'duty': '次の目標に合う現場へ人を動かす',
        'tiles': [
            Tile('次の目標を持つ人', show_percent(next_rate), f'{len(planned)}人 ／ {len(reached)}人',
                 f'目標 {TARGETS["next_rate"]}%', judge(next_rate, TARGETS['next_rate'])),
            Tile('目標に合うアサイン', show_percent(matched_rate), f'{matched}件 ／ {assigned}件',
                 f'目標 {TARGETS["matched_rate"]}%', judge(matched_rate, TARGETS['matched_rate'])),
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
    """マネジメントの画面の下に置く、会社の KPI。主役は自分の職掌なので、成長の寄与と標準売上のグラフだけを残す"""
    functions = for_role('management', today)
    for function in functions:
        if function['name'] not in ('成長の寄与', '経営の結果'):
            function['chart'] = None
    return functions


def task_evidence(queryset):
    """終えた作業のうち、成長の証拠に使われたもの (使われた件数, 終えた件数)。現場単位で数え、人ごとには返さない"""
    finished = list(queryset.filter(kind=Question.KIND_TASK, is_open=False).values_list('pk', flat=True))
    used = (GoalEvidence.objects.filter(record_type='issue', record_id__in=finished).order_by()
            .values('record_id').distinct().count())
    return used, len(finished)


LEADER_WEEKS = 8  # 週ごとのグラフで見る週の数


def leader_functions(site, user, today=None):
    """リーダーの4職掌。担当する現場1つについて、現場単位の値だけを返す。

    リーダーは1つの現場の中で、メンバーが止まらないように日々支える。人ごとの状況はリーダーのホームで見るので、
    ここは現場の集計にする。リーダーが1人の現場では現場の数字がリーダー個人の数字になるため、
    これはリーダー自身の振り返りに使い、マネジメントや会社には渡さない（憲章 II）。

    上の立場へのつながり：詰まりの解消とチームの運営はマネジメントの「現場の運営」に、メンバーの育成は「人の成長の確定」と
    会社の「人材開発」に、知見の資産化は会社の「ナレッジ管理」につながる。
    """
    today = today or timezone.localdate()
    metrics = site_metrics(site, today)
    this_week = week_start(today)
    weeks = [this_week - timedelta(weeks=n) for n in range(LEADER_WEEKS - 1, -1, -1)]
    labels = [f'{w.month}/{w.day}' for w in weeks]

    unreadable = list(site.questions.filter(unreadable=True, is_open=True))
    supported = sum(1 for q in unreadable if q.support_on)
    support_rate = percent(supported, len(unreadable))
    stall = metrics.stall_days
    closed = [timezone.localdate(t) for t in site.questions.filter(
        kind=Question.KIND_QUESTION, stage='', closed_at__isnull=False).values_list('closed_at', flat=True)]
    unblocking = {
        'name': '詰まりの解消', 'duty': '読めない資料や確認事項を、止まる前に解く',
        'tiles': [
            Tile('確認事項の平均滞留日数', '―' if stall is None else f'{stall}日', f'未解決 {metrics.open_questions}件',
                 f'目標 {THRESHOLDS["stall_days"]}日未満', None if stall is None else stall < THRESHOLDS['stall_days']),
            Tile('説明の時間を割り当てた割合', show_percent(support_rate), f'{supported}件 ／ 読めない {len(unreadable)}件',
                 f'目標 {TARGETS["support_rate"]}%', judge(support_rate, TARGETS['support_rate'])),
        ],
        'chart_title': '解決した確認事項（週ごと）',
        'chart': chart(f'lead-closed-{site.pk}', labels,
                       [('解決', [sum(1 for d in closed if week_start(d) == w) for w in weeks])], ORDINAL_3[1:]),
    }

    goals = list(site.goals.exclude(author=user))
    submitted = site.summaries.filter(submitted_at__isnull=False).exclude(author=user)
    answered = submitted.filter(feedback_at__isnull=False).count()
    feedback_rate = percent(answered, submitted.count())
    waiting = sum(1 for g in goals if g.stage == Goal.STAGE_SUBMITTED)
    stages = list(Goal.STAGE_LABELS.items())
    used, finished_tasks = task_evidence(site.questions.all())
    developing = {
        'name': 'メンバーの育成', 'duty': 'まとめに返し、成長を事実で確認する',
        'tiles': [
            Tile('まとめに返した割合', show_percent(feedback_rate), f'{answered}件 ／ 出された {submitted.count()}件',
                 f'目標 {TARGETS["feedback_rate"]}%', judge(feedback_rate, TARGETS['feedback_rate'])),
            Tile('成長の確認待ち', f'{waiting}件', 'メンバーが確認を依頼した成長',
                 f'目標 {TARGETS["reach_waiting"]}件以下', judge(waiting, TARGETS['reach_waiting'], lower_is_better=True)),
            Tile('成長の証拠になった作業', show_percent(percent(used, finished_tasks)), f'{used}件 ／ 終えた {finished_tasks}件', '', None),
        ],
        'chart_title': 'メンバーの成長の段階',
        'chart': chart(f'lead-stage-{site.pk}', [label for _, label in stages],
                       [('件数', [sum(1 for g in goals if g.stage == key) for key, _ in stages])], ORDINAL_3[1:], horizontal=True),
    }

    members = site.memberships.exclude(user=user)
    lined = members.filter(line_updated_at__isnull=False).count()
    line_rate = percent(lined, members.count())
    reports = site.documents.filter(kind=Document.KIND_WEEKLY, week_of__in=weeks).values_list('week_of', 'author_id').distinct()
    team = {
        'name': 'チームの運営', 'duty': '週次共有を回し、裁量の線を言葉にする',
        'tiles': [
            Tile('週報の提出率', show_percent(metrics.weekly_rate), f'直近{metrics.weekly_weeks}週',
                 f'目標 {WEEKLY_RATE_MIN}%', judge(metrics.weekly_rate, WEEKLY_RATE_MIN)),
            Tile('裁量の線を書いた人', show_percent(line_rate), f'{lined}人 ／ {members.count()}人',
                 f'目標 {TARGETS["line_rate"]}%', judge(line_rate, TARGETS['line_rate'])),
        ],
        'chart_title': '週報を出した人数（週ごと）',
        'chart': chart(f'lead-weekly-{site.pk}', labels,
                       [('人数', [sum(1 for week, _ in reports if week == w) for w in weeks])], ORDINAL_3[1:]),
    }

    candidates = (site.documents.filter(knowledge_status=KNOWLEDGE_CANDIDATE).count()
                  + site.terms.filter(knowledge_status=KNOWLEDGE_CANDIDATE).count())
    public_docs = site.documents.filter(visibility=VISIBILITY_COMPANY)
    published = [('用語', site.terms.filter(visibility=VISIBILITY_COMPANY).count()),
                 ('設計判断', public_docs.filter(kind=Document.KIND_DESIGN).count()),
                 ('振り返り', public_docs.filter(kind=Document.KIND_RETRO).count()),
                 ('面談メモ', public_docs.filter(kind=Document.KIND_PRE).count())]
    knowledge = {
        'name': '知見の資産化', 'duty': 'ナレッジ候補を一般化して会社へ公開する',
        'tiles': [
            Tile('公開待ちの候補', f'{candidates}件', 'ナレッジ候補のまま',
                 f'目標 {TARGETS["candidates"]}件以下', judge(candidates, TARGETS['candidates'], lower_is_better=True)),
            Tile('公開した記録', f'{sum(n for _, n in published)}件', 'この現場から会社へ', '', None),
        ],
        'chart_title': '公開した記録の種類',
        'chart': chart(f'lead-public-{site.pk}', [name for name, _ in published],
                       [('件数', [n for _, n in published])], ORDINAL_3[1:], horizontal=True),
    }

    functions = [unblocking, developing, team, knowledge]
    for function in functions:
        function['rows'] = table_rows(function['chart'])
    return functions


def state(done, label='済み', waiting='これから'):
    """メンバーの画面の状態。Tile の (met, status) を返す。まだのときも未達にはしない"""
    return (True, label) if done else (None, waiting)


def member_functions(site, user, today=None):
    """メンバーの4職掌。その人自身の状況を、その人にだけ返す。

    メンバーは担当の成果物をやり遂げ、そのために状況を早く見せる。ここの数字は評価のためではなく、本人が確かめるためのもの。
    個人は運営指標ではなく成長で見るので（憲章 II・V）、リーダー・マネジメント・会社の画面には渡さない。

    記録の量は数えない。用語や週報の件数を増やすほど良く見える数字は、書かされる道具になるため。
    「やったか」「どの段階か」だけを見て、目標の数値も置かない。

    上の立場へのつながり：現場の理解と状況の共有はリーダーの「詰まりの解消」「チームの運営」に、
    人月は標準売上の出発点に、自分の成長はリーダーの確認・マネジメントの承認・会社のスキル台帳につながる。
    """
    today = today or timezone.localdate()
    membership = site.memberships.get(user=user)
    this_week = week_start(today)
    weeks = [this_week - timedelta(weeks=n) for n in range(LEADER_WEEKS - 1, -1, -1)]

    issues = list(site.questions.filter(author=user).exclude(stage='').prefetch_related('items'))
    stages = []
    for key, label in kit.STAGE_LABELS.items():
        items = [i for q in issues if q.stage == key for i in q.items.all()]
        stages.append((label, sum(1 for i in items if i.is_done), sum(1 for i in items if not i.is_done)))
    done, left = sum(s[1] for s in stages), sum(s[2] for s in stages)
    summary = site.summaries.filter(author=user).first()
    submitted = bool(summary and summary.submitted_at)
    met, status = state(bool(done + left) and not left)
    summary_met, summary_status = state(submitted)
    understanding = {
        'name': '現場の理解', 'duty': '全体像と担当機能をつかみ、理解を形にする',
        'tiles': [
            Tile('入場時チェックリスト', show_percent(percent(done, done + left)), f'{done}項目 ／ {done + left}項目', '', met, status),
            Tile('1週目のまとめ', '出した' if submitted else 'まだ',
                 'リーダーの返しあり' if summary and summary.feedback_at else '', '', summary_met, summary_status),
        ],
        'chart_title': '段階ごとのチェック項目',
        'chart': chart(f'member-check-{site.pk}', [s[0] for s in stages],
                       [('済み', [s[1] for s in stages]), ('これから', [s[2] for s in stages])], CATEGORICAL, horizontal=True),
    }

    tasks = list(site.questions.filter(author=user, kind=Question.KIND_TASK, due_on__isnull=False))
    finished = [q for q in tasks if not q.is_open and q.done_on]
    on_time = sum(1 for q in finished if q.done_on <= q.due_on)
    overdue = sum(1 for q in tasks if q.is_open and q.due_on < today)
    late_met, late_status = state(not overdue, 'なし', '確かめる')
    # タスクの消化と成長は両輪。やり遂げた作業のうち、自分の目標の証拠に使ったものを見せる
    evidenced = (GoalEvidence.objects.filter(goal__author=user, goal__site=site, record_type='issue',
                                             record_id__in=[q.pk for q in finished]).order_by()
                 .values('record_id').distinct().count())
    evidence_met, evidence_status = state(evidenced)
    delivery = {
        'name': '担当の遂行', 'duty': '作業を予定どおりやり遂げ、成長の証拠にする。遅れは早く伝える',
        'tiles': [
            Tile('予定どおり終えた作業', show_percent(percent(on_time, len(finished))), f'{on_time}件 ／ 終えた {len(finished)}件', '', None),
            Tile('予定日を過ぎた作業', f'{overdue}件', '遅れの原因と見込みを書く', '', late_met, late_status),
            Tile('成長の証拠にした作業', f'{evidenced}件', f'終えた {len(finished)}件のうち', '', evidence_met, evidence_status),
        ],
        'chart_title': '自分の作業',
        'chart': chart(f'member-task-{site.pk}', ['予定どおり', '遅れて完了', '進行中', '予定日を過ぎた'],
                       [('件数', [on_time, len(finished) - on_time, len(tasks) - len(finished) - overdue, overdue])],
                       ORDINAL_3[1:], horizontal=True),
    }

    weekly_done = not signals.missing_last_weekly(membership)
    worked = MonthlyWork.objects.filter(membership=membership, month=month_start(today)).exists()
    detected = signals.detected_count(signals.signals_for(user, site))
    weekly_met, weekly_status = state(weekly_done)
    work_met, work_status = state(worked)
    sign_met, sign_status = state(not detected, 'なし', '確かめる')
    submitted_weeks = set(site.documents.filter(kind=Document.KIND_WEEKLY, author=user, week_of__in=weeks)
                          .values_list('week_of', flat=True))
    sharing = {
        'name': '状況の共有', 'duty': '読めないことや懸念を早く上げ、週報と人月を出す',
        'tiles': [
            Tile('先週の週報', '出した' if weekly_done else 'まだ', '', '', weekly_met, weekly_status),
            Tile('今月の人月', '入れた' if worked else 'まだ', '', '', work_met, work_status),
            Tile('今出ている危険信号', f'{detected}個', '', '', sign_met, sign_status),
        ],
        'chart_title': '週報を出した週',
        'chart': chart(f'member-weekly-{site.pk}', [f'{w.month}/{w.day}' for w in weeks],
                       [('週報', [1 if w in submitted_weeks else 0 for w in weeks])], ORDINAL_3[1:]),
    }

    goals = list(site.goals.filter(author=user))
    latest = goals[-1] if goals else None
    approved = bool(latest and latest.stage == Goal.STAGE_APPROVED)
    goal_met, goal_status = state(approved, '承認済み')
    planned = any(g.next_text for g in goals)
    next_met, next_status = state(planned)
    # 自分の成長が階級につながることを見せる。階級は会社が決めるので、状態は出さない
    assignment = GradeAssignment.objects.filter(user=user).select_related('grade').first()
    following = grades.next_grade(assignment.grade) if assignment else None
    ahead = f'次の{following.name}はレベル{following.required_level}で見直し' if following else ''
    labels = list(Goal.STAGE_LABELS.items())
    growing = {
        'name': '自分の成長', 'duty': '目標を立て、行動と証拠で成長を説明する',
        'tiles': [
            Tile('目標の段階', latest.stage_label if latest else '目標がまだ', latest.skill_item if latest else '', '', goal_met, goal_status),
            Tile('次の目標', 'ある' if planned else 'まだ', '', '', next_met, next_status),
            Tile('今の階級', assignment.grade.name if assignment else '―', ahead, '', None),
        ],
        'chart_title': '目標の段階',
        'chart': chart(f'member-stage-{site.pk}', [label for _, label in labels],
                       [('件数', [sum(1 for g in goals if g.stage == key) for key, _ in labels])], ORDINAL_3[1:], horizontal=True),
    }

    functions = [understanding, delivery, sharing, growing]
    for function in functions:
        function['rows'] = table_rows(function['chart'])
    return functions


def assignment_candidates(viewer):
    """アサインの候補：承認した成長に次の目標があり、まだアサインを記録していないもの。本人の分は本人に出さない"""
    goals = [g for g in growth.approved_reaches() if g.next_text and g.author != viewer]
    done = set(Assignment.objects.filter(goal__in=goals).values_list('goal_id', flat=True))
    return [g for g in goals if g.pk not in done]
