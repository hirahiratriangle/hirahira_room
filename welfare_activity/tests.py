import datetime
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django.views import View

from accounts.models import CustomUser

from . import body, content, github, tickets, views

PROJECT_NUMBER = 7
DAY = datetime.timedelta(days=1)


def field_value(name, value):
    """Project の項目の値（GitHub の応答の形）。単一選択は (選択肢の ID, 名前) で渡す。"""
    if isinstance(value, tuple):
        return {'optionId': value[0], 'name': value[1], 'field': {'name': name}}
    if isinstance(value, datetime.date):
        return {'date': value.isoformat(), 'field': {'name': name}}
    return {'number': value, 'field': {'name': name}}


def issue(number, title, *, state='OPEN', reason=None, labels=(), fields=None,
          updated=None, body_html='', comments=0, parent=None):
    """GitHub の Issue 1 件（github.ISSUES_QUERY の応答の形）。"""
    updated = updated or timezone.now() - number * datetime.timedelta(hours=1)
    node = {
        'number': number, 'title': title,
        'url': f'https://github.com/funITclub/wg-x/issues/{number}',
        'bodyHTML': body_html, 'state': state, 'stateReason': reason,
        'createdAt': '2026-10-01T00:00:00Z',
        'updatedAt': updated.isoformat(), 'closedAt': None,
        'comments': {'totalCount': comments},
        'labels': {'nodes': [{'name': name} for name in labels]},
        'parent': {'number': parent} if parent else None,
    }
    if fields is not None:
        node['projectItems'] = {'nodes': [
            {'project': {'number': 99}, 'fieldValues': {'nodes': [
                field_value('ステータス', ('x', '終了'))]}},  # ほかの Project の値は読まない
            {'project': {'number': PROJECT_NUMBER}, 'fieldValues': {'nodes': [
                {}] + [field_value(k, v) for k, v in fields.items()]}},
        ]}
    return node


def options(*names):
    return [{'id': f'opt-{name}', 'name': name, 'description': ''} for name in names]


PROJECT = {
    'title': 'WG の活動', 'url': 'https://github.com/orgs/funITclub/projects/7',
    'fields': {'nodes': [
        {},
        {'name': 'Status', 'options': options(*tickets.STATUSES)},
        {'name': '優先度', 'options': options(*tickets.PRIORITIES)},
        {'name': 'プロジェクト', 'options': [
            {'id': 'opt-site', 'name': 'サンプルサイト', 'description': '公開する Web サイト'},
            {'id': 'opt-tools', 'name': '社内ツール', 'description': ''},
        ]},
    ]},
}


def status(name):
    return ('opt-' + name, name)


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class BoardTestCase(TestCase):
    """GitHub には繋がず、github.fetch を差し替える。"""

    project_number = None

    def setUp(self):
        cache.clear()
        self.data = {'issues': self.make_issues(), 'project': None}
        patcher = mock.patch.object(github, 'fetch', side_effect=lambda *a: self.data)
        self.fetch = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(content, 'GITHUB_PROJECT', self.project_number)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_issues(self):
        return [
            issue(1, 'ログイン画面を作る', labels=['enhancement']),
            issue(2, '古いリンクを直す', state='CLOSED', labels=['question']),
            issue(3, '表示が崩れる', labels=['bug']),
        ]


class ProjectBoardTestCase(BoardTestCase):
    """Project（ステータス・優先度・期日など）を使っている WG。"""

    project_number = PROJECT_NUMBER

    def setUp(self):
        super().setUp()
        self.data['project'] = PROJECT

    def make_issues(self):
        return [
            issue(1, 'ログイン画面を作る', labels=['enhancement'], fields={
                'Status': status('進行中'), '優先度': status('高め'), '進捗率': 40,
                'プロジェクト': ('opt-site', 'サンプルサイト')}),
            issue(2, '古いリンクを直す', labels=['question'], fields={
                'Status': status('解決'), 'プロジェクト': ('opt-tools', '社内ツール')}),
            issue(3, '表示が崩れる', labels=['bug'], fields={}),
        ]


class TicketsTests(BoardTestCase):
    """GitHub の Issue をチケットとして読む（Project なし）。"""

    def test_trackers_come_from_labels(self):
        board = tickets.load()
        self.assertEqual({t.number: t.tracker for t in board.tickets},
                         {1: 'feature', 2: 'support', 3: 'bug'})

    def test_japanese_labels_and_no_label(self):
        self.data['issues'] = [issue(1, 'a', labels=['バグ']), issue(2, 'b')]
        board = tickets.load()
        self.assertEqual([t.tracker_label for t in board.tickets], ['バグ', '機能'])

    def test_status_follows_open_and_closed(self):
        self.data['issues'] = [
            issue(1, 'a'), issue(2, 'b', state='CLOSED', reason='COMPLETED'),
            issue(3, 'c', state='CLOSED', reason='NOT_PLANNED')]
        board = tickets.load()
        self.assertEqual([t.status for t in board.tickets], ['新規', '終了', '却下'])
        self.assertEqual([t.is_open for t in board.tickets], [True, False, False])
        self.assertFalse(board.has_project)

    def test_project_fields_are_not_asked_for(self):
        tickets.load()
        self.fetch.assert_called_once_with(content.GITHUB_REPO, None)

    def test_cached_for_a_while(self):
        tickets.load()
        tickets.load()
        self.assertEqual(self.fetch.call_count, 1)

    def test_last_good_copy_is_shown_when_github_fails(self):
        tickets.load()
        cache.delete(tickets._cache_key())  # 5 分たった
        self.fetch.side_effect = github.GitHubError('落ちている')
        with self.assertLogs(tickets.logger, 'WARNING'):
            board = tickets.load()
        self.assertEqual(board.state, 'stale')
        self.assertEqual(len(board.tickets), 3)
        # 失敗のあとしばらくは、開くたびに GitHub を待たせない
        tickets.load()
        self.assertEqual(self.fetch.call_count, 2)

    def test_error_without_a_last_copy(self):
        self.fetch.side_effect = github.GitHubError('落ちている')
        with self.assertLogs(tickets.logger, 'WARNING'):
            board = tickets.load()
        self.assertEqual(board.state, 'error')
        self.assertEqual(board.tickets, [])

    def test_offline_without_a_key(self):
        self.fetch.side_effect = github.NotConfigured('鍵がありません')
        self.assertEqual(tickets.load().state, 'offline')


class ProjectTicketsTests(ProjectBoardTestCase):
    def test_fields_come_from_the_project(self):
        board = tickets.load()
        first = board.ticket(1)
        self.assertEqual((first.status, first.priority, first.done_ratio, first.group_name),
                         ('進行中', '高め', 40, 'サンプルサイト'))
        self.assertTrue(board.has_project)
        self.fetch.assert_called_once_with(content.GITHUB_REPO, PROJECT_NUMBER)

    def test_resolved_counts_as_done_even_if_open(self):
        self.assertFalse(tickets.load().ticket(2).is_open)

    def test_other_projects_are_ignored(self):
        self.assertEqual(tickets.load().ticket(3).status, '新規')

    def test_groups_come_from_the_options(self):
        groups = tickets.load().groups
        self.assertEqual([(g.name, g.total_count, g.open_count) for g in groups],
                         [('サンプルサイト', 1, 1), ('社内ツール', 1, 0)])

    def test_dates_and_ratio(self):
        today = timezone.localdate()
        self.data['issues'] = [issue(1, 'a', fields={
            '開始日': today, '期日': today + DAY, '進捗率': 150})]
        ticket = tickets.load().ticket(1)
        self.assertEqual((ticket.start_date, ticket.due_date, ticket.done_ratio),
                         (today, today + DAY, 100))


class BodyTests(SimpleTestCase):
    """本文は GitHub が描いた HTML を、公開してよい形に削ってから出す。"""

    def test_scripts_and_handlers_are_removed(self):
        html, _ = body.clean('<p onclick="x()">本文</p><script>alert(1)</script>'
                             '<iframe src="https://example.com"></iframe><a href="javascript:x()">j</a>')
        self.assertEqual(html, '<p>本文</p><a target="_blank" rel="noopener noreferrer nofollow">j</a>')

    def test_links_open_in_a_new_tab(self):
        html, _ = body.clean('<a href="https://example.com/">外</a>')
        self.assertEqual(html, '<a href="https://example.com/" target="_blank" '
                               'rel="noopener noreferrer nofollow">外</a>')

    def test_images_are_dropped_with_their_signed_links(self):
        src = 'https://private-user-images.githubusercontent.com/1/a.png?jwt=secret'
        html, had_images = body.clean(f'<p><a href="{src}"><img src="{src}"></a></p>')
        self.assertTrue(had_images)
        self.assertNotIn('jwt', html)
        self.assertNotIn('<img', html)

    def test_mentions_hide_the_user_name(self):
        html, _ = body.clean('<p><a class="user-mention notranslate" data-hovercard-type="user" '
                             'href="https://github.com/someone">@someone</a> さんへ</p>')
        self.assertEqual(html, '<p>@メンバー さんへ</p>')

    def test_task_lists_become_marks(self):
        html, _ = body.clean(
            '<ul><li><input type="checkbox" class="task-list-item-checkbox" disabled checked> 済み</li>'
            '<li><input type="checkbox" class="task-list-item-checkbox" disabled> まだ</li></ul>')
        self.assertEqual(html, '<ul><li>☑ 済み</li><li>☐ まだ</li></ul>')

    def test_tables_and_code_stay(self):
        source = '<table><tr><td>1</td></tr></table><pre><code>x = 1</code></pre>'
        self.assertIn('<td>1</td>', body.clean(source)[0])
        self.assertIn('<pre><code>x = 1</code></pre>', body.clean(source)[0])


class IssueListTests(BoardTestCase):
    def get(self, **params):
        return self.client.get(reverse('welfare_activity:issue_list'), params)

    def test_list_is_shown(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'welfare_activity/issue_list.html')

    def test_open_issues_only_by_default(self):
        response = self.get()
        self.assertContains(response, 'ログイン画面を作る')
        self.assertNotContains(response, '古いリンクを直す')

    def test_closed_state_shows_closed_issues(self):
        response = self.get(state='closed')
        self.assertContains(response, '古いリンクを直す')
        self.assertNotContains(response, 'ログイン画面を作る')

    def test_all_state_shows_everything(self):
        response = self.get(state='all')
        self.assertContains(response, 'ログイン画面を作る')
        self.assertContains(response, '古いリンクを直す')

    def test_filter_by_tracker(self):
        response = self.get(state='all', tracker='bug')
        self.assertEqual([t.number for t in response.context['issues']], [3])

    def test_sorting_by_subject(self):
        response = self.get(state='all', sort='subject')
        titles = [t.title for t in response.context['issues']]
        self.assertEqual(titles, sorted(titles))

    def test_newest_first_by_default(self):
        numbers = [t.number for t in self.get(state='all').context['issues']]
        self.assertEqual(numbers, [1, 2, 3])  # 番号が小さいほど最近更新した（make_issues）

    def test_unknown_sort_key_falls_back(self):
        response = self.get(sort='nope')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['sort'], '-updated_at')

    def test_links_to_github_for_new_tickets(self):
        self.assertContains(
            self.get(),
            f'href="https://github.com/funITclub/{content.GITHUB_REPO}/issues/new/choose" '
            'target="_blank" rel="noopener noreferrer"')

    def test_no_project_columns_without_a_project(self):
        response = self.get()
        self.assertNotContains(response, '優先度')
        self.assertNotContains(response, '>プロジェクト<')
        self.assertNotIn('priority', response.context['filter_form'].fields)

    def test_paginated(self):
        self.data['issues'] = [issue(n, f'チケット{n}') for n in range(1, 31)]
        response = self.get()
        self.assertEqual(len(response.context['issues']), 25)
        self.assertContains(response, '1 / 2 ページ（全 30 件）')

    def test_shows_a_notice_when_github_fails(self):
        self.fetch.side_effect = github.GitHubError('落ちている')
        with self.assertLogs(tickets.logger, 'WARNING'):
            response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'いま GitHub からチケットを読めません')
        self.assertNotContains(response, '条件に合うチケットはありません')


class ProjectIssueListTests(ProjectBoardTestCase):
    def get(self, **params):
        return self.client.get(reverse('welfare_activity:issue_list'), params)

    def test_filter_by_project(self):
        response = self.get(state='all', project='opt-tools')
        self.assertEqual([t.number for t in response.context['issues']], [2])

    def test_filter_by_priority(self):
        response = self.get(priority='高め')
        self.assertEqual([t.number for t in response.context['issues']], [1])

    def test_sorting_by_priority_follows_the_options(self):
        """優先度は文字列の順ではなく、Project の選択肢の順（低め→通常→高め→急いで）に並ぶ。"""
        self.data['issues'] += [
            issue(4, 'd', fields={'優先度': status('急いで')}),
            issue(5, 'e', fields={'優先度': status('低め')})]
        response = self.get(state='all', sort='priority')
        self.assertEqual([t.priority for t in response.context['issues']],
                         ['低め', '高め', '急いで', '', ''])

    def test_sorting_by_status_follows_the_options(self):
        response = self.get(state='all', sort='status')
        self.assertEqual([t.status for t in response.context['issues']], ['新規', '進行中', '解決'])

    def test_project_columns_and_board_link(self):
        response = self.get()
        self.assertContains(response, '優先度')
        self.assertContains(response, 'href="{}"'.format(PROJECT['url']))


class IssueDetailTests(BoardTestCase):
    def get(self, number):
        return self.client.get(reverse('welfare_activity:issue_detail', args=[number]))

    def test_detail_is_shown(self):
        self.data['issues'][0]['bodyHTML'] = '<p>本文の<strong>説明</strong></p>'
        response = self.get(1)
        self.assertContains(response, 'ログイン画面を作る')
        self.assertContains(response, '<p>本文の<strong>説明</strong></p>', html=True)

    def test_links_to_github(self):
        self.assertContains(
            self.get(1), 'href="https://github.com/funITclub/wg-x/issues/1" '
                         'target="_blank" rel="noopener noreferrer"')

    def test_body_is_cleaned(self):
        self.data['issues'][0]['bodyHTML'] = '<p>本文</p><script>alert(1)</script>'
        self.assertNotContains(self.get(1), 'alert(1)')

    def test_images_point_to_github(self):
        self.data['issues'][0]['bodyHTML'] = '<p><img src="https://example.com/a.png"></p>'
        self.assertContains(self.get(1), '画像は GitHub で見られます')

    def test_unknown_number_is_404(self):
        self.assertEqual(self.get(999).status_code, 404)

    def test_says_it_cannot_read_instead_of_404(self):
        self.fetch.side_effect = github.GitHubError('落ちている')
        with self.assertLogs(tickets.logger, 'WARNING'):
            response = self.get(1)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'いま GitHub からチケットを読めません')


class ProjectViewTests(ProjectBoardTestCase):
    def test_project_list_counts_issues(self):
        response = self.client.get(reverse('welfare_activity:project_list'))
        self.assertContains(response, 'サンプルサイト')
        self.assertContains(response, '公開する Web サイト')

    def test_project_detail_lists_its_issues(self):
        response = self.client.get(reverse('welfare_activity:project_detail', args=['opt-site']))
        self.assertContains(response, 'ログイン画面を作る')
        self.assertNotContains(response, '古いリンクを直す')
        self.assertIn(('機能', 1), response.context['by_tracker'])

    def test_unknown_project_is_404(self):
        response = self.client.get(reverse('welfare_activity:project_detail', args=['nope']))
        self.assertEqual(response.status_code, 404)

    def test_nav_has_projects(self):
        home = self.client.get(reverse('welfare_activity:home'))
        self.assertContains(home, 'href="{}"'.format(reverse('welfare_activity:project_list')))


class NoProjectViewTests(BoardTestCase):
    def test_nav_hides_projects(self):
        home = self.client.get(reverse('welfare_activity:home'))
        self.assertNotContains(home, 'href="{}"'.format(reverse('welfare_activity:project_list')))

    def test_project_list_says_unused(self):
        response = self.client.get(reverse('welfare_activity:project_list'))
        self.assertContains(response, 'この WG はプロジェクトを使っていません')


class PublicAccessTests(SimpleTestCase):
    """本番は既定で全画面ログイン必須なので、すべてのビューを明示的に外してある。"""

    def test_every_view_is_public(self):
        found = [
            obj for obj in vars(views).values()
            if isinstance(obj, type) and issubclass(obj, View)
            and obj.__module__ == views.__name__
        ]
        self.assertGreaterEqual(len(found), 8)
        for view in found:
            self.assertIs(
                view.dispatch.login_required, False,
                '{} に login_not_required が付いていない'.format(view.__name__))

    def test_nothing_writes(self):
        """活動ページは読むだけ。チケットは GitHub で書く。"""
        for obj in vars(views).values():
            if isinstance(obj, type) and issubclass(obj, View):
                self.assertNotIn('post', vars(obj), obj.__name__)


class HomeTests(ProjectBoardTestCase):
    """活動ページのトップ。"""

    def get(self):
        return self.client.get(reverse('welfare_activity:home'))

    def test_home_is_the_root_of_the_app(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'welfare_activity/home.html')

    def test_shows_the_wg_from_content(self):
        response = self.get()
        self.assertContains(response, escape(content.NAME))
        self.assertContains(response, escape(content.CODE))
        self.assertContains(response, escape(content.LEAD))

    def test_intro_is_split_into_paragraphs(self):
        with mock.patch.object(content, 'INTRO', '一段落目。\n\n二段落目。'):
            response = self.get()
        self.assertContains(response, '<p>一段落目。</p>', html=True)
        self.assertContains(response, '<p>二段落目。</p>', html=True)

    def test_counts_the_tickets(self):
        response = self.get()
        self.assertEqual(response.context['open_count'], 2)
        self.assertEqual(response.context['closed_count'], 1)
        self.assertEqual(response.context['total_count'], 3)

    def test_links_to_the_tracker_and_github(self):
        response = self.get()
        self.assertContains(response, reverse('welfare_activity:issue_list'))
        self.assertContains(response, '/issues/new/choose')

    def test_due_soon_lists_open_issues_by_due_date(self):
        today = timezone.localdate()
        self.data['issues'] = [
            issue(1, '期日を過ぎたもの', fields={'期日': today - DAY}),
            issue(2, '来週が期日', fields={'期日': today + 7 * DAY}),
            issue(3, 'まだ先', fields={'期日': today + 60 * DAY}),
            issue(4, '終わったもの', state='CLOSED', fields={'期日': today}),
        ]
        response = self.get()
        self.assertEqual([t.number for t in response.context['due_soon']], [1, 2])
        self.assertContains(response, '（過ぎています）', count=1)

    def test_recent_issues_are_newest_first(self):
        recent = self.get().context['recent_issues']
        self.assertEqual(recent, sorted(recent, key=lambda t: t.updated_at, reverse=True))

    def test_project_progress(self):
        response = self.get()
        projects = {p.name: p for p in response.context['projects']}
        self.assertEqual(projects['サンプルサイト'].open_count, 1)
        self.assertEqual(projects['社内ツール'].closed_count, 1)
        self.assertContains(response, '100%')

    def test_empty_tracker_still_shows(self):
        self.data['issues'] = []
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'チケットがまだありません')

    def test_shows_even_when_github_fails(self):
        self.fetch.side_effect = github.GitHubError('落ちている')
        with self.assertLogs(tickets.logger, 'WARNING'):
            response = self.get()
        self.assertContains(response, escape(content.LEAD))
        self.assertContains(response, 'いま GitHub からチケットを読めません')

    def test_join_link(self):
        self.assertContains(self.get(), 'href="{}"'.format(content.JOIN_URL))

    def test_links_from_content(self):
        with mock.patch.object(content, 'LINKS', [('Webアプリ', 'https://example.com/app/')]):
            response = self.get()
        self.assertContains(response, '関連リンク')
        self.assertContains(response, 'href="https://example.com/app/"')

    def test_every_page_carries_the_wg_name(self):
        response = self.client.get(reverse('welfare_activity:issue_list'))
        self.assertContains(response, '<title>チケット | {}</title>'.format(content.NAME), html=True)
        self.assertContains(response, reverse('welfare_activity:home'))


class NoProjectHomeTests(BoardTestCase):
    def test_hides_due_dates_and_progress(self):
        response = self.client.get(reverse('welfare_activity:home'))
        self.assertNotContains(response, '期日が近いチケット')
        self.assertNotContains(response, 'プロジェクトの進み具合')
        self.assertEqual(response.context['open_count'], 2)


class SlidesTests(SimpleTestCase):
    """構想資料。content.SLIDES_TITLE が空のうちは出さない。"""

    def setUp(self):
        patcher = mock.patch.object(tickets, 'load', return_value=tickets.Board(state='ok'))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_hidden_until_titled(self):
        with mock.patch.object(content, 'SLIDES_TITLE', None):
            self.assertEqual(
                self.client.get(reverse('welfare_activity:slides')).status_code, 404)
            home = self.client.get(reverse('welfare_activity:home'))
        self.assertNotContains(home, reverse('welfare_activity:slides'))

    def test_shown_and_linked_once_titled(self):
        with mock.patch.object(content, 'SLIDES_TITLE', '構想の資料'):
            response = self.client.get(reverse('welfare_activity:slides'))
            home = self.client.get(reverse('welfare_activity:home'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'welfare_activity/slides.html')
        self.assertContains(home, reverse('welfare_activity:slides'))
        self.assertContains(home, '構想の資料')

    def test_slides_open_in_a_new_tab(self):
        """トップの「スライドを見る」とナビの「構想資料」の両方。スライドの「閉じる」で戻れるように。"""
        with mock.patch.object(content, 'SLIDES_TITLE', '構想の資料'):
            home = self.client.get(reverse('welfare_activity:home'))
        self.assertContains(
            home, 'href="{}" target="_blank" rel="noopener noreferrer"'.format(
                reverse('welfare_activity:slides')), count=2)

    def test_slides_have_navigation_and_a_close_button(self):
        """「閉じる」はタブを閉じる。閉じられないときの行き先として活動ページを指しておく。"""
        with mock.patch.object(content, 'SLIDES_TITLE', '構想の資料'):
            response = self.client.get(reverse('welfare_activity:slides'))
        for marker in ('id="prev"', 'id="next"', 'id="counter"', 'id="dots"', 'name="viewport"'):
            self.assertContains(response, marker)
        self.assertContains(
            response, 'id="close-tab" href="{}"'.format(reverse('welfare_activity:home')))
        self.assertContains(response, '× 閉じる')
        self.assertNotContains(response, '‹ 活動ページ')


class GitHubTokenTests(SimpleTestCase):
    """鍵は GITHUB_TOKEN だけ。無ければ NotConfigured。"""

    def test_without_any_key(self):
        with mock.patch.dict('os.environ', {}, clear=True):
            with self.assertRaises(github.NotConfigured):
                github.fetch('wg-x')

    def test_reads_with_the_token(self):
        page = {'repository': {'issues': {
            'nodes': [issue(1, 'a')], 'pageInfo': {'hasNextPage': False, 'endCursor': None}}}}
        with mock.patch.dict('os.environ', {'GITHUB_TOKEN': 'dev'}), \
                mock.patch.object(github, '_graphql', return_value=page) as graphql:
            data = github.fetch('wg-x')
        self.assertEqual(graphql.call_args.args[0], 'dev')
        self.assertFalse(graphql.call_args.args[2]['withProject'])
        self.assertEqual(len(data['issues']), 1)


class MountTests(ProjectBoardTestCase):
    """hirahira_room の中で /welfare/ に付き、ログインなしで開ける（ミドルウェアを通して確かめる）。"""

    def test_every_page_opens_without_login(self):
        for name, args in (('home', ()), ('slides', ()), ('issue_list', ()), ('issue_detail', (1,)),
                           ('wbs', ()), ('gantt', ()), ('project_list', ())):
            with self.subTest(name):
                url = reverse('welfare_activity:' + name, args=args)
                self.assertTrue(url.startswith('/welfare/'))
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_home_links_here(self):
        self.client.force_login(CustomUser.objects.create_user('me', 'me@example.com', 'pass12345'))
        self.assertContains(
            self.client.get(reverse('home:home')), 'href="{}"'.format(reverse('welfare_activity:home')))


class WbsTests(BoardTestCase):
    """WBS。sub-issue の親子で並べる。"""

    def make_issues(self):
        return [
            issue(1, '画面を作る'),
            issue(2, 'ログイン画面', parent=1),
            issue(3, '一覧画面', parent=1, state='CLOSED'),
            issue(4, '一覧の並べ替え', parent=3),
            issue(5, '調べもの'),
            issue(6, 'ほかのリポジトリの子', parent=999),
        ]

    def test_rows_follow_the_tree(self):
        rows = [(t.number, depth) for t, depth in tickets.load().wbs_rows()]
        self.assertEqual(rows, [(1, 0), (2, 1), (3, 1), (4, 2), (5, 0), (6, 0)])

    def test_parent_counts_its_children(self):
        parent = tickets.load().ticket(1)
        self.assertEqual((parent.child_total, parent.child_closed), (2, 1))

    def test_a_loop_does_not_hang(self):
        self.data['issues'] = [issue(1, 'a', parent=2), issue(2, 'b', parent=1)]
        rows = [(t.number, depth) for t, depth in tickets.load().wbs_rows()]
        self.assertEqual(rows, [(1, 0), (2, 1)])

    def test_page(self):
        response = self.client.get(reverse('welfare_activity:wbs'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '1 / 2')
        self.assertContains(response, 'padding-left:calc(2 * 1.4rem)')  # #4 は 2 段目

    def test_works_without_a_project(self):
        home = self.client.get(reverse('welfare_activity:home'))
        self.assertContains(home, 'href="{}"'.format(reverse('welfare_activity:wbs')))


class GanttTests(ProjectBoardTestCase):
    """ガントチャート。開始日〜期日を横棒にする。"""

    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        monday = self.today - datetime.timedelta(days=self.today.weekday())
        self.data['issues'] = [
            issue(1, '準備', fields={'開始日': monday, '期日': monday + 6 * DAY}),
            issue(2, '期日だけ', fields={'期日': monday + 13 * DAY, '進捗率': 50}),
            issue(3, '遅れ', fields={'開始日': monday - 7 * DAY, '期日': monday - DAY}),
            issue(4, '済み', state='CLOSED', fields={'開始日': monday, '期日': monday + DAY}),
            issue(5, '日付なし', fields={}),
        ]

    def get(self):
        return self.client.get(reverse('welfare_activity:gantt'))

    def test_only_dated_tickets_in_date_order(self):
        rows = self.get().context['rows']
        self.assertEqual([r['issue'].number for r in rows], [3, 4, 1, 2])  # 開始日 → 期日 → 番号の順

    def test_bars_are_placed_by_date(self):
        rows = {r['issue'].number: r for r in self.get().context['rows']}
        # 表は「遅れ」の始まりの週（月曜）から、「期日だけ」の週の日曜までの 3 週＝21 日
        self.assertEqual(rows[3]['left'], 0)
        self.assertAlmostEqual(rows[1]['left'], 7 / 21 * 100, places=2)
        self.assertAlmostEqual(rows[1]['width'], 7 / 21 * 100, places=2)
        self.assertAlmostEqual(rows[2]['width'], 1 / 21 * 100, places=2)  # 期日だけなら 1 日

    def test_states(self):
        rows = {r['issue'].number: r['state'] for r in self.get().context['rows']}
        self.assertEqual(rows, {1: 'open', 2: 'open', 3: 'late', 4: 'done'})

    def test_page_shows_today_and_progress(self):
        response = self.get()
        self.assertContains(response, 'class="g-today"')
        self.assertContains(response, 'class="g-progress" style="width:50%"')
        self.assertContains(response, '#2 期日だけ')
        self.assertNotContains(response, '日付なし')

    def test_empty(self):
        self.data['issues'] = [issue(1, 'a', fields={})]
        self.assertContains(self.get(), '開始日か期日の入ったチケットはまだありません')

    def test_nav_has_gantt(self):
        home = self.client.get(reverse('welfare_activity:home'))
        self.assertContains(home, 'href="{}"'.format(reverse('welfare_activity:gantt')))


class NoProjectGanttTests(BoardTestCase):
    def test_hidden_without_a_project(self):
        home = self.client.get(reverse('welfare_activity:home'))
        self.assertNotContains(home, 'href="{}"'.format(reverse('welfare_activity:gantt')))
        self.assertEqual(self.client.get(reverse('welfare_activity:gantt')).status_code, 404)
