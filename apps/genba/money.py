"""金額の積み上げ。標準売上と標準粗利を、階級 × 人月から足し上げる（憲章 VI）。

実際の単価・契約金額・給与は別のシステムの話で、genba には置かない。ここの金額は、会社が定める階級の
標準単価・標準原価で計算した値で、経営の傾向を見るためのもの。請求や精算には使わない。

積み上げの段階と、見る立場は次のとおり。

| 段階 | 見る立場 | 見えるもの |
|---|---|---|
| 人×現場×月（MonthlyWork） | メンバー | 現場の全員の階級・人月・標準売上。自分の人月を入れる |
| 現場 | リーダー | メンバーと同じ。標準原価と標準粗利は見ない |
| 全現場 | マネジメント | 現場ごとの標準売上・標準粗利 |
| 全社 | 会社 | 全社と業界ごとの標準売上・標準粗利。現場名の代わりに業界を出す |

どの段階の数字も rollup() の1回の走査で作るので、上の段階の合計は必ず下の段階の足し算と一致する。
上の段階で金額を別に入力する欄は作らない。

本人がまだ人月を入れていない月は、その人の分が数字に入らない。現場の画面に「未入力」と出す。

人ごとの合計は people に返すが、これは積み上げの確かめと本人の行のためのもの。
人を並べて比べる画面には使わない（憲章 II）。
"""
from dataclasses import dataclass
from datetime import timedelta

from .models import CompanyMonth, MonthlyWork, month_start


@dataclass
class Amount:
    revenue: int = 0
    cost: int = 0

    def add(self, revenue, cost):
        self.revenue += revenue
        self.cost += cost

    @property
    def gross(self):
        return self.revenue - self.cost

    @property
    def gross_rate(self):
        """粗利率（%）。売上が0なら数えられないので None"""
        return round(self.gross * 100 / self.revenue) if self.revenue else None


def rollup(start, end):
    """start〜end の月の金額を積み上げる。

    返す値：people（(現場id, 人id) ごと）、sites（現場名ごと）、industries（業界ごと）、total（全社）
    """
    result = {'people': {}, 'sites': {}, 'industries': {}, 'total': Amount()}
    works = MonthlyWork.objects.filter(month__gte=month_start(start), month__lte=end).select_related('membership__site', 'grade')
    for w in works:
        m = w.membership
        for group, key in (('people', (m.site_id, m.user_id)), ('sites', m.site.name), ('industries', m.site.industry)):
            result[group].setdefault(key, Amount()).add(w.revenue, w.cost)
        result['total'].add(w.revenue, w.cost)
    return result


def operating(day):
    """その月の稼働：(現場に入っている人数, 在籍人数, 稼働率%)。

    人月が 0 より大きい行のある人を数える（2つの現場に入っていても1人）。人を特定する値は返さない。
    在籍人数が入っていない月は、稼働率を求めない。
    """
    month = month_start(day)
    billed = (MonthlyWork.objects.filter(month=month, man_month__gt=0).order_by()
              .values('membership__user').distinct().count())
    record = CompanyMonth.objects.filter(month=month).first()
    if not record or not record.headcount:
        return billed, None, None
    return billed, record.headcount, round(billed * 100 / record.headcount)


def site_history(site, last_month, months):
    """現場の月ごとの標準売上（古い順）[{'month', 'revenue'}]。現場チームに見せるので原価は返さない"""
    rows = {}
    for w in MonthlyWork.objects.filter(membership__site=site, month__lte=last_month).select_related('grade'):
        rows[w.month] = rows.get(w.month, 0) + w.revenue
    series = []
    month = last_month
    for _ in range(months):
        series.append({'month': month, 'revenue': rows.get(month, 0)})
        month = month_start(month - timedelta(days=1))
    return series[::-1]


def growth_share(start, end):
    """start〜end の標準売上のうち、成長で階級が上がった分。(標準売上, 階級が上がった分) を返す。

    人ごとに、最初に記録された月の階級を「入った時の階級」とする。同じ人月を入った時の階級で計算した額との差が、
    階級が上がったことで増えた分になる。人数が増えた分と、人が育った分を分けて見るための数字。
    """
    first = {}
    for user_id, price in (MonthlyWork.objects.order_by('month').values_list('membership__user', 'grade__standard_price')):
        first.setdefault(user_id, price)
    revenue = raised = 0
    works = MonthlyWork.objects.filter(month__gte=month_start(start), month__lte=end).select_related('grade', 'membership')
    for w in works:
        revenue += w.revenue
        raised += w.revenue - round(first[w.membership.user_id] * w.man_month)
    return revenue, raised
