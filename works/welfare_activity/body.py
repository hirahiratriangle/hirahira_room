# チケットの本文（GitHub が Markdown から描いた HTML）を、公開のページに出せる形にする。
#
# GitHub の HTML は GitHub の中で見るためのもので、そのまま出すと次の 3 つが困る。
#   画像       … 非公開リポジトリの画像の URL は期限つきの鍵入り。出すと画像を公開してしまう
#   @メンション … GitHub のユーザー名。担当者と同じく、公開のページには出さない
#   ほかのタグ  … 念のため許可リストで削る（スクリプト・style・on* 属性・iframe など）

import re
from urllib.parse import urlsplit

import nh3

TAGS = {
    'p', 'br', 'hr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'ul', 'ol', 'li', 'dl', 'dt', 'dd', 'blockquote', 'details', 'summary',
    'strong', 'b', 'em', 'i', 'del', 's', 'ins', 'sub', 'sup', 'kbd', 'mark',
    'code', 'pre', 'a', 'table', 'thead', 'tbody', 'tr', 'th', 'td',
}
ATTRIBUTES = {
    'a': {'href', 'title'},
    'ol': {'start'},
    'th': {'align'},
    'td': {'align'},
}

MENTION = re.compile(r'<a\b[^>]*\bclass="[^"]*\b(?:user|team)-mention\b[^"]*"[^>]*>.*?</a>', re.S)
CHECKED = re.compile(r'<input\b[^>]*\btype="checkbox"[^>]*\bchecked\b[^>]*>')
CHECKBOX = re.compile(r'<input\b[^>]*\btype="checkbox"[^>]*>')
EMPTY_LINK = re.compile(r'<a\b[^>]*>\s*</a>')


def _is_attachment(url):
    """GitHub に上げた画像・ファイルの URL か。非公開リポジトリのものは外から開けない（鍵入りのものもある）。"""
    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    return host.endswith('githubusercontent.com') or (
        host == 'github.com' and parts.path.startswith('/user-attachments/'))


def _drop_attachment_links(tag, attribute, value):
    if tag == 'a' and attribute == 'href' and _is_attachment(value):
        return None
    return value


def clean(html):
    """(公開してよい HTML, 画像を外したか) を返す。"""
    html = html or ''
    had_images = '<img' in html or '<video' in html
    html = MENTION.sub('@メンバー', html)
    html = CHECKED.sub('☑', html)
    html = CHECKBOX.sub('☐', html)
    html = nh3.clean(
        html,
        tags=TAGS,
        attributes=ATTRIBUTES,
        attribute_filter=_drop_attachment_links,
        url_schemes={'http', 'https', 'mailto'},
        url_relative='deny',
        link_rel='noopener noreferrer nofollow',
        set_tag_attribute_values={'a': {'target': '_blank'}},
    )
    return EMPTY_LINK.sub('', html).strip(), had_images
