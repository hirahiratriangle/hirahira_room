# GitHub から WG のチケット（Issue と Projects の項目）を読む。書き込みはしない。
#
# 鍵は環境変数 GITHUB_TOKEN。本番は App Service のアプリ設定、手元は GITHUB_TOKEN=$(gh auth token) で渡す。
# funITclub の GitHub App は使わない。あの App は org のメンバーも読み書きできるので、
# クラブの外にある hirahira-room に秘密鍵を置かず、読み取りだけのトークンで足りるようにしてある。
# 無ければ NotConfigured。活動ページは「GitHub に繋がっていません」と出して動く。

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings

API = 'https://api.github.com'
HEADERS = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
TIMEOUT = 15
MAX_PAGES = 5  # 500 件まで読む

ISSUES_QUERY = '''
query($owner: String!, $name: String!, $cursor: String, $withProject: Boolean!) {
  repository(owner: $owner, name: $name) {
    issues(first: 100, after: $cursor, states: [OPEN, CLOSED],
           orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title url bodyHTML state stateReason createdAt updatedAt closedAt
        comments { totalCount }
        parent { number }
        labels(first: 20) { nodes { name } }
        projectItems(first: 10) @include(if: $withProject) {
          nodes {
            project { number }
            fieldValues(first: 20) {
              nodes {
                ... on ProjectV2ItemFieldSingleSelectValue {
                  optionId name field { ... on ProjectV2FieldCommon { name } }
                }
                ... on ProjectV2ItemFieldDateValue {
                  date field { ... on ProjectV2FieldCommon { name } }
                }
                ... on ProjectV2ItemFieldNumberValue {
                  number field { ... on ProjectV2FieldCommon { name } }
                }
              }
            }
          }
        }
      }
    }
  }
}
'''

PROJECT_QUERY = '''
query($owner: String!, $number: Int!) {
  organization(login: $owner) {
    projectV2(number: $number) {
      title url
      fields(first: 30) {
        nodes { ... on ProjectV2SingleSelectField { name options { id name description } } }
      }
    }
  }
}
'''


class GitHubError(Exception):
    """GitHub から読めなかった（通信の失敗・権限が無い・想定外の応答）。"""


class NotConfigured(GitHubError):
    """鍵が無い（GITHUB_TOKEN を渡していない）。"""


def owner():
    return getattr(settings, 'GITHUB_ORG', '') or 'funITclub'


def _call(method, url, token_header, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = Request(url, data=data, method=method, headers={
        **HEADERS, 'Authorization': token_header, 'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            status, raw = response.status, response.read()
    except HTTPError as e:
        status, raw = e.code, e.read()
    except (URLError, TimeoutError, OSError) as e:
        raise GitHubError(f'{method} api.github.com に接続できませんでした') from e
    try:
        return status, json.loads(raw or b'{}')
    except ValueError as e:
        raise GitHubError(f'{method} api.github.com の応答を読めませんでした（{status}）') from e


def _token():
    if os.environ.get('GITHUB_TOKEN'):
        return os.environ['GITHUB_TOKEN']
    raise NotConfigured('GitHub の鍵がありません')


def _graphql(token, query, variables):
    status, body = _call('POST', f'{API}/graphql', f'Bearer {token}',
                         {'query': query, 'variables': variables})
    if status != 200 or body.get('errors'):
        errors = '; '.join(e.get('message', '') for e in body.get('errors', []))[:300]
        raise GitHubError(f'GitHub から読めませんでした（{status} {errors}）')
    return body['data']


def fetch(repo, project_number=None):
    """リポジトリの Issue（PR は含まない）と、Project の項目の定義を読む。

    返すのは GitHub の応答をほぼそのまま並べたもの（tickets.build が読む）。
    キャッシュに入れるので、JSON にできる形だけにする。
    """
    with_project = project_number is not None
    token = _token()
    issues, cursor = [], None
    for _ in range(MAX_PAGES):
        data = _graphql(token, ISSUES_QUERY, {
            'owner': owner(), 'name': repo, 'cursor': cursor, 'withProject': with_project})
        if not data.get('repository'):
            raise GitHubError(f'リポジトリ {owner()}/{repo} が見えません')
        page = data['repository']['issues']
        issues.extend(page['nodes'])
        if not page['pageInfo']['hasNextPage']:
            break
        cursor = page['pageInfo']['endCursor']

    project = None
    if with_project:
        data = _graphql(token, PROJECT_QUERY, {'owner': owner(), 'number': project_number})
        project = (data.get('organization') or {}).get('projectV2')
        if not project:
            raise GitHubError(f'Project {project_number} が見えません')
    return {'issues': issues, 'project': project}
