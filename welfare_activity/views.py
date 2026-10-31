import datetime

from django.contrib.auth.decorators import login_not_required
from django.http import Http404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import generic

from . import content, tickets
from .forms import IssueFilterForm


# サイトは既定で全画面がログイン必須（LoginRequiredMiddleware）。活動ページは活動の様子を外にも
# 見せるため、全画面を公開にする（各ビューの login_not_required）。書き込む画面は無い。
# チケットは会員が GitHub で直接書き、ここはそれを読んで出すだけ（tickets.py）。

# 並べ替えのキー → 並べる値。None（期日の無いものなど）は昇順でも降順でも後ろに回す
SORTABLE = {
    'id': lambda board: lambda t: t.number,
    'tracker': lambda board: lambda t: [v for v, _l, _a in tickets.TRACKERS].index(t.tracker),
    'status': lambda board: lambda t: board.status_order.index(t.status),
    'priority': lambda board: lambda t: (
        board.priority_order.index(t.priority) if t.priority else None),
    'subject': lambda board: lambda t: t.title,
    'due_date': lambda board: lambda t: t.due_date,
    'updated_at': lambda board: lambda t: t.updated_at,
}
DEFAULT_SORT = '-updated_at'

# 期日がこの日数以内のチケットを、トップの「期日が近いチケット」に出す
DUE_SOON_DAYS = 14


class PageMixin:
    """どの画面にも、WG の情報（content.py）とナビの現在地を渡す。"""

    section = None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = self.section
        context['wg'] = content.as_dict()
        return context


class BoardMixin(PageMixin):
    """GitHub から読んだチケット（tickets.Board）を board として渡す。"""

    def get_board(self):
        if not hasattr(self, '_board'):
            self._board = tickets.load()
        return self._board

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['board'] = self.get_board()
        return context


@method_decorator(login_not_required, name='dispatch')
class HomeView(BoardMixin, generic.TemplateView):
    """活動ページのトップ。WG の紹介・構想資料・チケットの様子をまとめて見せる。"""

    template_name = 'welfare_activity/home.html'
    section = 'home'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        board = self.get_board()
        today = timezone.localdate()
        open_tickets = board.open_tickets
        total = len(board.tickets)
        due_soon = [t for t in open_tickets if t.due_date
                    and t.due_date <= today + datetime.timedelta(days=DUE_SOON_DAYS)]
        context.update({
            'today': today,
            'total_count': total,
            'open_count': len(open_tickets),
            'closed_count': total - len(open_tickets),
            'due_soon': sorted(due_soon, key=lambda t: (t.due_date, t.number))[:10],
            'due_soon_days': DUE_SOON_DAYS,
            'recent_issues': sorted(board.tickets, key=lambda t: t.updated_at, reverse=True)[:5],
            'projects': board.groups,
        })
        return context


@method_decorator(login_not_required, name='dispatch')
class SlidesView(PageMixin, generic.TemplateView):
    """構想資料（スライド）。content.SLIDES_TITLE が空のうちは出さない。"""

    template_name = 'welfare_activity/slides.html'
    section = 'slides'

    def get(self, request, *args, **kwargs):
        if not content.SLIDES_TITLE:
            raise Http404('構想資料はまだありません')
        return super().get(request, *args, **kwargs)


@method_decorator(login_not_required, name='dispatch')
class IssueListView(BoardMixin, generic.ListView):
    """チケット一覧。絞り込みと並べ替えができる。"""

    template_name = 'welfare_activity/issue_list.html'
    context_object_name = 'issues'
    paginate_by = 25
    section = 'issues'

    def get_filter_form(self):
        data = self.request.GET.copy()
        data.setdefault('state', 'open')
        return IssueFilterForm(data, board=self.get_board())

    def get_queryset(self):
        board = self.get_board()
        items = list(board.tickets)
        form = self.get_filter_form()
        if form.is_valid():
            picked = form.cleaned_data
            if picked.get('state') == 'open':
                items = [t for t in items if t.is_open]
            elif picked.get('state') == 'closed':
                items = [t for t in items if not t.is_open]
            for name, attr in (('project', 'group_id'), ('tracker', 'tracker'),
                               ('status', 'status'), ('priority', 'priority')):
                if picked.get(name):
                    items = [t for t in items if getattr(t, attr) == picked[name]]
        return self.sort(items, board)

    def get_sort(self):
        key = self.request.GET.get('sort', DEFAULT_SORT)
        return key if key.lstrip('-') in SORTABLE else DEFAULT_SORT

    def sort(self, items, board):
        key = self.get_sort()
        descending = key.startswith('-')
        value = SORTABLE[key.lstrip('-')](board)
        # 同じ値のあいだは新しい番号から
        items = sorted(items, key=lambda t: t.number, reverse=True)
        with_value = [t for t in items if value(t) is not None]
        without = [t for t in items if value(t) is None]
        return sorted(with_value, key=value, reverse=descending) + without

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter_form'] = self.get_filter_form()
        context['sort'] = self.get_sort()
        query = self.request.GET.copy()
        query.pop('page', None)
        query.pop('sort', None)
        context['query_string'] = query.urlencode()
        return context


@method_decorator(login_not_required, name='dispatch')
class IssueDetailView(BoardMixin, generic.TemplateView):
    template_name = 'welfare_activity/issue_detail.html'
    section = 'issues'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        board = self.get_board()
        issue = board.ticket(kwargs['number'])
        # 読めていないとき（board.has_data が偽）は、404 にせず「読めません」を出す
        if issue is None and board.has_data:
            raise Http404('そのチケットはありません')
        context['issue'] = issue
        return context


@method_decorator(login_not_required, name='dispatch')
class ProjectListView(BoardMixin, generic.TemplateView):
    template_name = 'welfare_activity/project_list.html'
    section = 'projects'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['projects'] = self.get_board().groups
        return context


@method_decorator(login_not_required, name='dispatch')
class ProjectDetailView(BoardMixin, generic.TemplateView):
    template_name = 'welfare_activity/project_detail.html'
    section = 'projects'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        board = self.get_board()
        project = board.group(kwargs['group_id'])
        if project is None and board.has_data:
            raise Http404('そのプロジェクトはありません')
        context['project'] = project
        if project:
            issues = sorted(project.tickets, key=lambda t: t.updated_at, reverse=True)
            context['recent_issues'] = issues[:10]
            context['by_tracker'] = [
                (label, sum(t.tracker == value for t in issues))
                for value, label, _ in tickets.TRACKERS
            ]
        return context


def _monday(day):
    return day - datetime.timedelta(days=day.weekday())


@method_decorator(login_not_required, name='dispatch')
class GanttView(BoardMixin, generic.TemplateView):
    """ガントチャート。開始日〜期日のあるチケットを横棒で並べる（Project の項目を使う）。"""

    template_name = 'welfare_activity/gantt.html'
    section = 'gantt'

    # 1 日あたりの幅の下限（px）。期間が長いときは横に送って見る
    DAY_PX = 10

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        board = self.get_board()
        if board.has_data and not board.has_project:
            raise Http404('この WG は Project を使っていません')
        today = timezone.localdate()
        items = sorted((t for t in board.tickets if t.bar_start),
                       key=lambda t: (t.bar_start, t.bar_end, t.number))
        context.update({'today': today, 'rows': [], 'months': [], 'weeks': []})
        if not items:
            return context

        # 表の幅：いちばん早い日〜いちばん遅い日（今日も入れる）を、月曜〜日曜の区切りまで広げる
        first = _monday(min([t.bar_start for t in items] + [today]))
        last = _monday(max([t.bar_end for t in items] + [today])) + datetime.timedelta(days=6)
        span = (last - first).days + 1

        def left(day):
            return round((day - first).days / span * 100, 3)

        rows = []
        for t in items:
            start, end = min(t.bar_start, t.bar_end), max(t.bar_start, t.bar_end)
            if not t.is_open:
                state = 'done'
            elif t.due_date and t.due_date < today:
                state = 'late'
            else:
                state = 'open'
            rows.append({
                'issue': t,
                'left': left(start),
                'width': round(((end - start).days + 1) / span * 100, 3),
                'state': state,
            })
        months, day = [], first.replace(day=1)
        while day <= last:
            if day >= first:
                months.append({'label': f'{day.year}年{day.month}月', 'left': left(day)})
            day = (day + datetime.timedelta(days=32)).replace(day=1)
        # 表の左端が月の途中なら、その月の名前も出す。次の月の区切りが近いと重なるので、そのときは出さない
        if not months or months[0]['left'] > 15:
            months.insert(0, {'label': f'{first.year}年{first.month}月', 'left': 0})
        context.update({
            'rows': rows,
            'months': months,
            'weeks': [{'label': f'{d.month}/{d.day}', 'left': left(d)}
                      for d in (first + datetime.timedelta(days=7 * i) for i in range(span // 7))],
            'today_left': round(((today - first).days + 0.5) / span * 100, 3),
            'min_width': min(span * self.DAY_PX, 4000),
        })
        return context


@method_decorator(login_not_required, name='dispatch')
class WbsView(BoardMixin, generic.TemplateView):
    """WBS。親子（sub-issue）の階層で並べ、親には子の完了の数を出す。"""

    template_name = 'welfare_activity/wbs.html'
    section = 'wbs'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['rows'] = self.get_board().wbs_rows()
        return context
