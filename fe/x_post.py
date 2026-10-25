"""基本情報の問題を X に投稿する（1日3回・1回1問）。

GitHub Actions が朝・昼・夜に /fe/tasks/post-x/ を叩き、post_slot がその時間帯の
1問を投稿する。投稿は「問題文＋解答ページの URL＋タグ」だけで、選択肢・正解・
解説は解答ページ（/fe/x/<code>/）にだけ出す。

- 出題は、投稿できる問題（eligible_questions）から重みなしで毎回1問を引く。
  前に出た問題も同じように引くので、近いうちに同じ問題が出ることもある。
- 投稿のときに問題の中身を XPost に写す。解答ページはこの写しから描く。
- 投稿から settings.FE_X_KEEP_DAYS 日たった XPost は、行ごと消す（purge_old）。
- 日付と時間帯の組は1つだけ。同じ時間帯に何度呼ばれても投稿は1回で、
  失敗した時間帯は同じ問題で投稿し直す（再試行で別の問題が出ないように）。

X API は URL 入りの投稿1件ごとに課金される。手元では settings.FE_X_DRY_RUN を
True にしておき、投稿せずに文面をログに出す。
"""

import base64
import datetime
import hashlib
import hmac
import json
import logging
import random
import re
import secrets
import string
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from .models import (IPA_SAMPLE_PREFIX, OpenProgress, Question, SUBJECT_A, XPost)

logger = logging.getLogger(__name__)

# X の上限。全角は1字で2、URL は長さによらず 23 と数える
LIMIT = 280
URL_WEIGHT = 23
HEADER = '【基本情報 今日の問題】'
LINK_LABEL = '▶選択肢と解答はこちら'
LINK_LABEL_FIGURE = '▶図・選択肢と解答はこちら'
TAGS = '#基本情報技術者試験 #FE'
EXAM_NAME = '基本情報技術者試験'
CODE_LENGTH = 10
CODE_ALPHABET = string.ascii_letters + string.digits
API_URL = 'https://api.x.com/2/tweets'


class PostError(Exception):
    """X への投稿に失敗した。"""


def weight(text):
    """X の数え方での長さ（半角1・全角2。URL は長さによらず 23）。"""
    urls = re.findall(r'https?://\S+', text)
    n = URL_WEIGHT * len(urls)
    for c in re.sub(r'https?://\S+', '', text):
        o = ord(c)
        light = (o <= 0x10FF or 0x2000 <= o <= 0x200D
                 or 0x2010 <= o <= 0x201F or 0x2032 <= o <= 0x2037)
        n += 1 if light else 2
    return n


def is_official(source):
    """IPA の公開問題・サンプル問題を転記したものか。"""
    return source.strip().startswith(('令和', '平成', IPA_SAMPLE_PREFIX))


def citation(source):
    """IPA の求める形の出典。「出典：令和7年度 基本情報技術者試験 科目A 問2」。

    転記の問題の source は、出典の後ろに「 ／ 」でシラバスの項目を続けているので、
    その前だけを使う。IPA の公開問題でなければ空文字を返す。
    """
    if not is_official(source):
        return ''
    head = source.split(' ／ ')[0].strip()
    if head.startswith(IPA_SAMPLE_PREFIX):
        return '出典：{} {}'.format(EXAM_NAME, head)
    match = re.match(r'(\S+年度(?:\s*[春秋]期)?)\s*(.*)', head)
    if not match:
        return '出典：{} {}'.format(EXAM_NAME, head)
    return '出典：{} {} {}'.format(match.group(1), EXAM_NAME, match.group(2)).strip()


def page_url(code):
    return settings.FE_SITE_URL.rstrip('/') + reverse('fe:x_answer', args=[code])


def build_text(stem, source, has_figures, url):
    lines = [HEADER, stem]
    cite = citation(source)
    if cite:
        lines.append('（{}）'.format(cite))
    lines += [LINK_LABEL_FIGURE if has_figures else LINK_LABEL, url, TAGS]
    return '\n'.join(lines)


def fits(question):
    """URL を入れて1件に収まるか。URL は長さによらず同じに数えるので、仮の URL で測る。"""
    text = build_text(question.stem, question.source, bool(question.figures),
                      page_url('x' * CODE_LENGTH))
    return weight(text) <= LIMIT


def eligible_questions():
    """投稿できる問題。科目Aの固定問題（計算テンプレートの問題は除く）で、1件に収まるもの。"""
    return [
        q for q in Question.objects.active()
        .filter(subject=SUBJECT_A, template__isnull=True)
        .select_related('category')
        if fits(q)
    ]


def new_code():
    while True:
        code = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if not XPost.objects.filter(code=code).exists():
            return code


def _create(date, slot, question):
    code = new_code()
    return XPost.objects.create(
        date=date, slot=slot, code=code,
        subject=question.subject, category=question.category,
        stem=question.stem, choices=question.choices,
        figures=question.figures, choice_figures=question.choice_figures,
        answer_index=question.answer_index, explanation=question.explanation,
        source=question.source,
        text=build_text(question.stem, question.source, bool(question.figures), page_url(code)),
    )


def post_slot(slot, today=None, rng=None):
    """その日のその時間帯の1問を投稿する。結果を dict で返す。

    投稿済みなら何もしない。投稿前・失敗の記録があれば、同じ問題で投稿し直す。
    """
    today = today or timezone.localdate()
    post = XPost.objects.filter(date=today, slot=slot).first()
    if post and post.status == XPost.STATUS_POSTED:
        return {'status': 'already', 'code': post.code}
    if post is None:
        pool = eligible_questions()
        if not pool:
            return {'status': 'empty'}
        question = (rng or random).choice(pool)
        try:
            with transaction.atomic():
                post = _create(today, slot, question)
        except IntegrityError:
            # 同じ時間帯の呼び出しが重なった。先に作った方に任せる
            return {'status': 'already'}

    try:
        post.tweet_id = send(post.text)
    except PostError as e:
        post.status = XPost.STATUS_FAILED
        post.error = str(e)
        post.save(update_fields=['status', 'error'])
        raise
    post.status = XPost.STATUS_POSTED
    post.error = ''
    post.posted_at = timezone.now()
    post.save(update_fields=['tweet_id', 'status', 'error', 'posted_at'])
    return {'status': 'posted', 'code': post.code, 'tweet_id': post.tweet_id}


def purge_old(now=None):
    """投稿から FE_X_KEEP_DAYS 日たった投稿を、行ごと消す。消した件数を返す。"""
    today = timezone.localdate(now or timezone.now())
    limit = today - datetime.timedelta(days=settings.FE_X_KEEP_DAYS)
    deleted, _ = XPost.objects.filter(date__lte=limit).delete()
    return deleted


def is_open(post, now=None):
    """解答ページを見せてよいか。消す前の投稿でも、期限を過ぎたら案内ページにする。"""
    today = timezone.localdate(now or timezone.now())
    return (post.status == XPost.STATUS_POSTED
            and post.date > today - datetime.timedelta(days=settings.FE_X_KEEP_DAYS))


def record_answer(post, selected):
    """ID なしの解答を数える。全 ID を合わせた集計に足される。"""
    is_correct = selected == post.answer_index
    progress, _ = OpenProgress.objects.get_or_create(
        category=post.category, subject=post.subject
    )
    OpenProgress.objects.filter(pk=progress.pk).update(
        answered=F('answered') + 1,
        correct=F('correct') + (1 if is_correct else 0),
    )
    return is_correct


def send(text):
    """X API v2 で投稿し、投稿の id を返す。FE_X_DRY_RUN なら投稿せずにログへ出す。"""
    if settings.FE_X_DRY_RUN:
        logger.info('X への投稿（試し・投稿していません）:\n%s', text)
        return 'dry-run'
    key, key_secret = settings.X_API_KEY, settings.X_API_SECRET
    token, token_secret = settings.X_ACCESS_TOKEN, settings.X_ACCESS_SECRET
    if not all([key, key_secret, token, token_secret]):
        raise PostError('X API のキーが設定されていません。')
    oauth = {
        'oauth_consumer_key': key,
        'oauth_nonce': secrets.token_hex(16),
        'oauth_signature_method': 'HMAC-SHA1',
        'oauth_timestamp': str(int(time.time())),
        'oauth_token': token,
        'oauth_version': '1.0',
    }

    def enc(s):
        return urllib.parse.quote(s, safe='')

    params = '&'.join('{}={}'.format(enc(k), enc(v)) for k, v in sorted(oauth.items()))
    base = '&'.join(['POST', enc(API_URL), enc(params)])
    signing_key = '{}&{}'.format(enc(key_secret), enc(token_secret)).encode()
    oauth['oauth_signature'] = base64.b64encode(
        hmac.new(signing_key, base.encode(), hashlib.sha1).digest()
    ).decode()
    header = 'OAuth ' + ', '.join(
        '{}="{}"'.format(enc(k), enc(v)) for k, v in sorted(oauth.items())
    )
    request = urllib.request.Request(
        API_URL, data=json.dumps({'text': text}).encode(), method='POST',
        headers={'Authorization': header, 'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)['data']['id']
    except urllib.error.HTTPError as e:
        raise PostError('{} {}'.format(e.code, e.read().decode(errors='replace'))) from e
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as e:
        raise PostError(str(e)) from e
