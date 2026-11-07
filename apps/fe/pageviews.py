"""学習の画面のページビューを数える。

1回ごとの記録は残さず、日付・画面・ID の有無ごとの件数（PageViewCount）を
1つ増やすだけにする。IP アドレス・ID・ブラウザの情報は残さない。
検索エンジンなどの巡回ロボットは、ブラウザの種類の文字列（User-Agent）で見分けて数えない。
問題の管理（/fe/console/）は数えない。
"""

import datetime
import re

from django.contrib.auth.decorators import login_not_required
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.decorators import method_decorator

from .models import PageViewCount

# 数える画面。値は表の見出し。並びは表の列の並び
PAGES = {
    'index': 'ダッシュボード（ID なし）',
    'mypage': 'マイページ',
    'quiz': '1問1答',
    'learn_start': '学習モード 開始',
    'learn_read': '学習モード 読む',
    'learn_quiz': '学習モード 解く',
    'learn_result': '学習モード 結果',
    'stats': '分野別正答率',
    'history': '履歴',
    'guide': 'このアプリについて',
}

# 巡回ロボットの User-Agent に入る言葉。空の User-Agent も数えない
BOT_PATTERN = re.compile(
    r'bot|crawl|spider|slurp|scrape|fetch|preview|monitor|headless|python|curl|wget',
    re.IGNORECASE,
)


def is_bot(request):
    agent = request.META.get('HTTP_USER_AGENT', '')
    return not agent or bool(BOT_PATTERN.search(agent))


def record(request, page):
    """その日・その画面・ID の有無の件数を1つ増やす。"""
    if page not in PAGES or is_bot(request):
        return
    learner = getattr(request, 'fe_learner', None)
    key = {
        'date': timezone.localdate(),
        'page': page,
        'with_id': bool(learner and learner.code),
    }
    if PageViewCount.objects.filter(**key).update(count=F('count') + 1):
        return
    try:
        with transaction.atomic():
            PageViewCount.objects.create(count=1, **key)
    except IntegrityError:
        # 同時に最初の1件を作られたら、そちらに足す
        PageViewCount.objects.filter(**key).update(count=F('count') + 1)


@method_decorator(login_not_required, name='dispatch')
class CountPageViewMixin:
    """GET で画面を出せたときに、page_name の画面として1件数える。

    ID の有無を見るので、LearnerRequiredMixin より前（左）に置く。数えるのは学習の
    画面だけで、どれもログインは要らない。この dispatch がいちばん外側になるので、
    LearnerRequiredMixin と同じくログインを求めない印を付けておく。
    """

    page_name = None

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        if request.method == 'GET' and response.status_code == 200:
            record(request, self.page_name)
        return response


def summary(days=30):
    """直近 days 日の集計。日別（新しい日が上）と、期間の画面別の合計。"""
    today = timezone.localdate()
    dates = [today - datetime.timedelta(days=i) for i in range(days)]
    rows = PageViewCount.objects.filter(date__gte=dates[-1])

    by_day = {d: {page: 0 for page in PAGES} for d in dates}
    totals = {page: {'with_id': 0, 'without_id': 0} for page in PAGES}
    for row in rows:
        if row.page not in PAGES:
            continue
        by_day[row.date][row.page] += row.count
        totals[row.page]['with_id' if row.with_id else 'without_id'] += row.count

    return {
        'days': days,
        'pages': [{'name': name, 'label': label} for name, label in PAGES.items()],
        'daily': [
            {'date': d, 'counts': [by_day[d][p] for p in PAGES], 'total': sum(by_day[d].values())}
            for d in dates
        ],
        'totals': [
            {
                'label': label,
                'with_id': totals[name]['with_id'],
                'without_id': totals[name]['without_id'],
                'total': totals[name]['with_id'] + totals[name]['without_id'],
            }
            for name, label in PAGES.items()
        ],
        'grand_total': sum(t['with_id'] + t['without_id'] for t in totals.values()),
    }
