# GitHub から読んだ Issue を、活動ページで見せるチケットの形にする。
#
# チケットは会員が GitHub で直接書く（活動ページは読むだけ）。項目は次から取る。
#   トラッカー   … ラベル（bug → バグ、enhancement → 機能、question → サポート）
#   ステータスなど … content.GITHUB_PROJECT の Project の項目（ステータス・優先度・開始日・
#                  期日・進捗率・プロジェクト）。Project が無ければ Issue の Open/Closed だけ
# 担当者と書いた人は出さない（GitHub のユーザー名を公開のページに出さないため）。
#
# 読んだものは 5 分キャッシュする。読み直しに失敗したら、とっておいた最後の分を出す。

import datetime
import logging
from dataclasses import dataclass, field
from functools import cached_property

from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from . import body, content, github

logger = logging.getLogger(__name__)

CACHE_SECONDS = 5 * 60          # この間は GitHub を読み直さない
KEEP_SECONDS = 24 * 60 * 60     # 読み直せなかったときに出す「最後に読めた分」をとっておく長さ
RETRY_SECONDS = 60              # 読み直せなかったあと、次に試すまでの間

# （値, 表示, そう読むラベル）。どれも付いていなければ「機能」
TRACKERS = [
    ('bug', 'バグ', {'bug', 'バグ'}),
    ('feature', '機能', {'enhancement', 'feature', '機能'}),
    ('support', 'サポート', {'question', 'support', 'サポート'}),
]
DEFAULT_TRACKER = 'feature'

# Project のテンプレートの選択肢。並びは Project の選択肢の順に従い、ここは Project が無いときの順
STATUSES = ['新規', '進行中', '解決', 'フィードバック', '終了', '却下']
DONE_STATUSES = {'解決', '終了', '却下'}  # これか、Issue が Closed なら完了
PRIORITIES = ['低め', '通常', '高め', '急いで']
STATUS_CSS = {'新規': 'new', '進行中': 'in_progress', '解決': 'resolved',
              'フィードバック': 'feedback', '終了': 'closed', '却下': 'rejected'}
PRIORITY_CSS = {'低め': 'low', '通常': 'normal', '高め': 'high', '急いで': 'urgent'}

# Project の項目の名前
F_STATUS = ('ステータス', 'Status')
F_PRIORITY = '優先度'
F_START = '開始日'
F_DUE = '期日'
F_RATIO = '進捗率'
F_PROJECT = 'プロジェクト'


@dataclass
class Ticket:
    number: int
    title: str
    url: str
    body_html: str
    is_closed: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime
    comment_count: int
    tracker: str
    status: str
    priority: str = ''
    start_date: datetime.date = None
    due_date: datetime.date = None
    done_ratio: int = None
    group_id: str = ''
    group_name: str = ''
    parent_number: int = None             # 親の Issue（sub-issue のとき）。WBS の階層に使う
    children: list = field(default_factory=list, compare=False, repr=False)  # 親子が輪でも比べ・表示で止まらない

    @property
    def is_open(self):
        return not self.is_closed and self.status not in DONE_STATUSES

    @property
    def tracker_label(self):
        return next(label for value, label, _ in TRACKERS if value == self.tracker)

    @property
    def status_css(self):
        return STATUS_CSS.get(self.status, 'other')

    @property
    def priority_css(self):
        return PRIORITY_CSS.get(self.priority, '')

    @cached_property
    def _clean_body(self):
        return body.clean(self.body_html)

    @property
    def body(self):
        """公開してよい形にした本文の HTML。"""
        return self._clean_body[0]

    @property
    def body_had_images(self):
        return self._clean_body[1]

    @property
    def child_total(self):
        return len(self.children)

    @property
    def child_closed(self):
        return sum(not c.is_open for c in self.children)

    @property
    def bar_start(self):
        """ガントチャートの横棒の始まり。開始日が無ければ期日の 1 日だけ。"""
        return self.start_date or self.due_date

    @property
    def bar_end(self):
        return self.due_date or self.start_date

    def get_absolute_url(self):
        return reverse('welfare_activity:issue_detail', args=[self.number])


@dataclass
class Group:
    """Project の項目「プロジェクト」の選択肢 1 つ（Redmine のプロジェクトにあたる）。"""

    id: str
    name: str
    description: str
    tickets: list = field(default_factory=list)

    @property
    def total_count(self):
        return len(self.tickets)

    @property
    def open_count(self):
        return sum(t.is_open for t in self.tickets)

    @property
    def closed_count(self):
        return self.total_count - self.open_count

    def get_absolute_url(self):
        return reverse('welfare_activity:project_detail', args=[self.id])


@dataclass
class Board:
    """WG のチケットのすべて。state は ok・stale（最後に読めた分）・offline（鍵が無い）・error（読めない）。"""

    state: str
    tickets: list = field(default_factory=list)
    groups: list = field(default_factory=list)
    has_project: bool = False
    project_url: str = ''
    fetched_at: datetime.datetime = None
    status_order: list = field(default_factory=lambda: list(STATUSES))
    priority_order: list = field(default_factory=lambda: list(PRIORITIES))

    @property
    def has_data(self):
        return self.state in ('ok', 'stale')

    @property
    def repo_url(self):
        return f'https://github.com/{github.owner()}/{content.GITHUB_REPO}'

    @property
    def new_issue_url(self):
        return f'{self.repo_url}/issues/new/choose'

    @property
    def open_tickets(self):
        return [t for t in self.tickets if t.is_open]

    def ticket(self, number):
        return next((t for t in self.tickets if t.number == number), None)

    def group(self, group_id):
        return next((g for g in self.groups if g.id == group_id), None)

    def wbs_rows(self):
        """WBS の並び。[(チケット, 段), …]。親の下に子を番号順に並べる。

        親がこのリポジトリに無い（ほかのリポジトリの Issue・読んだ範囲の外）ものは一番上の段に置く。
        親子が輪になっていても止まるよう、一度出したものは二度出さない。
        """
        numbers = {t.number for t in self.tickets}
        roots = sorted((t for t in self.tickets if t.parent_number not in numbers),
                       key=lambda t: t.number)
        rows, seen = [], set()

        def walk(ticket, depth):
            if ticket.number in seen:
                return
            seen.add(ticket.number)
            rows.append((ticket, depth))
            for child in sorted(ticket.children, key=lambda t: t.number):
                walk(child, depth + 1)

        for ticket in roots:
            walk(ticket, 0)
        for ticket in sorted(self.tickets, key=lambda t: t.number):  # 輪の中だけにあったもの
            walk(ticket, 0)
        return rows


def _date(value):
    return datetime.date.fromisoformat(value) if value else None


def _datetime(value):
    return datetime.datetime.fromisoformat(value.replace('Z', '+00:00')) if value else None


def _tracker(labels):
    names = {label['name'].lower() for label in labels}
    for value, _label, aliases in TRACKERS:
        if names & aliases:
            return value
    return DEFAULT_TRACKER


def _fields(node, project_number):
    """Issue の、この Project での項目の値 {項目名: 値}。単一選択は (選択肢の ID, 名前)。"""
    for item in (node.get('projectItems') or {}).get('nodes', []):
        if (item.get('project') or {}).get('number') != project_number:
            continue
        values = {}
        for value in item['fieldValues']['nodes']:
            name = (value.get('field') or {}).get('name')
            if not name:
                continue
            if 'optionId' in value:
                values[name] = (value['optionId'], value['name'])
            elif 'date' in value:
                values[name] = value['date']
            elif 'number' in value:
                values[name] = value['number']
        return values
    return {}


def _status(node, values):
    for name in F_STATUS:
        if name in values:
            return values[name][1]
    if node['state'] == 'OPEN':
        return '新規'
    return '終了' if node.get('stateReason') in (None, 'COMPLETED') else '却下'


def _options(project, names):
    if isinstance(names, str):
        names = (names,)
    for f in project['fields']['nodes']:
        if f.get('name') in names:
            return f['options']
    return []


def build(data, state, fetched_at=None):
    """github.fetch の結果から Board を作る。data が None なら空の Board。"""
    board = Board(state=state, fetched_at=fetched_at)
    if data is None:
        return board
    project = data.get('project')
    number = content.GITHUB_PROJECT
    if project:
        board.has_project = True
        board.project_url = project.get('url', '')
        board.status_order = [o['name'] for o in _options(project, F_STATUS)] or board.status_order
        board.priority_order = [o['name'] for o in _options(project, F_PRIORITY)] or board.priority_order
        board.groups = [Group(o['id'], o['name'], o.get('description') or '')
                        for o in _options(project, F_PROJECT)]
    groups = {g.id: g for g in board.groups}

    for node in data['issues']:
        values = _fields(node, number) if project else {}
        ratio = values.get(F_RATIO)
        group_id, group_name = values.get(F_PROJECT, ('', ''))
        ticket = Ticket(
            number=node['number'],
            title=node['title'],
            url=node['url'],
            body_html=node.get('bodyHTML') or '',
            is_closed=node['state'] == 'CLOSED',
            created_at=_datetime(node['createdAt']),
            updated_at=_datetime(node['updatedAt']),
            comment_count=node['comments']['totalCount'],
            tracker=_tracker(node['labels']['nodes']),
            status=_status(node, values),
            priority=values.get(F_PRIORITY, ('', ''))[1],
            start_date=_date(values.get(F_START)),
            due_date=_date(values.get(F_DUE)),
            done_ratio=max(0, min(100, int(ratio))) if ratio is not None else None,
            group_id=group_id,
            group_name=group_name,
            parent_number=(node.get('parent') or {}).get('number'),
        )
        board.tickets.append(ticket)
        if group_id in groups:
            groups[group_id].tickets.append(ticket)

    by_number = {t.number: t for t in board.tickets}
    for ticket in board.tickets:
        parent = by_number.get(ticket.parent_number)
        if parent is not None and parent is not ticket:
            parent.children.append(ticket)

    # 選択肢に無い値（Project から消した選択肢など）も、並べ替えで落ちないよう末尾に足す
    for ticket in board.tickets:
        if ticket.status not in board.status_order:
            board.status_order.append(ticket.status)
        if ticket.priority and ticket.priority not in board.priority_order:
            board.priority_order.append(ticket.priority)
    return board


def _cache_key():
    return f'welfare_activity:github:{content.GITHUB_REPO}:{content.GITHUB_PROJECT}'


def load():
    """チケットを読む。5 分以内に読んだものがあればそれを使う。"""
    key = _cache_key()
    saved = cache.get(key)
    if saved is None:
        try:
            data = github.fetch(content.GITHUB_REPO, content.GITHUB_PROJECT)
        except github.NotConfigured:
            return Board(state='offline')
        except github.GitHubError as e:
            logger.warning('活動ページのチケットを GitHub から読めませんでした: %s', e)
            saved = cache.get(key + ':last')
            saved = {**saved, 'stale': True} if saved else {'data': None, 'stale': True}
            cache.set(key, saved, RETRY_SECONDS)
        else:
            saved = {'data': data, 'fetched_at': timezone.now().isoformat(), 'stale': False}
            cache.set(key, saved, CACHE_SECONDS)
            cache.set(key + ':last', saved, KEEP_SECONDS)

    if saved['data'] is None:
        return Board(state='error')
    fetched_at = _datetime(saved.get('fetched_at'))
    return build(saved['data'], 'stale' if saved['stale'] else 'ok', fetched_at)
