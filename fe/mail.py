"""毎日0時に届く問題メール。

登録 … ダッシュボードから、ID ごとに1件。ID には合言葉がなく、ID を知っている人なら
       誰でも操作できるので、入力されたアドレスに確認のメールを送り、そのリンクを
       開いて「受け取る」を押したアドレスだけを配信先にする。確認待ちのアドレスは、
       確認の期限を過ぎたら消す（purge_pending）。登録したあとは、画面から
       アドレスを変えられない（変えたいときは解除して登録し直す）。
       送り方（マラソン・特訓・特訓の小分類）は画面から変えられる。
解除 … 登録されたアドレスに届くメールのリンクからだけできる。リンクは毎回のメールに
       付けるほか、画面から「解除用のメール」として配信先へ送れる。
送信 … 1日1回。学習モードと同じく小分類を単位にし、1通に同じ小分類の問題を最大10問、
       解答と解説、その ID の分野別正答率を付けて送る。
       マラソン … 全問を、はじめから順番に。最後まで行ったら最初に戻る。
       特訓     … 選んだ小分類を順に。小分類を送り切ったら次の小分類へ。
       同じ日に2通送らないよう、送り方ごとに送った日を控える。

公開問題の図は data URI で持っているが、多くのメールソフトは data URI の画像を
出さないので、HTML メールの添付（インライン画像）にして本文から参照する。
アドレスは DB に暗号化して持ち（fe.crypto）、ログに出さない。
"""

import base64
import binascii
import datetime
import hashlib
import logging
import re
import smtplib
from email.mime.image import MIMEImage

from django.conf import settings
from django.core import signing
from django.core.mail import BadHeaderError, EmailMessage, EmailMultiAlternatives
from django.db.models import Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .data.categories import topic_rank
from .models import Category, MailSubscription, Question
from .stats import category_stats

logger = logging.getLogger(__name__)

CONFIRM_SALT = 'fe.mail.confirm'
STOP_SALT = 'fe.mail.stop'
# 1通に載せる問題の上限。学習モードの1ラウンドと同じ
QUESTIONS_PER_MAIL = 10

_DATA_URI = re.compile(r'^data:(image/(png|jpeg|gif));base64,(.+)$', re.S)


class LinkInvalid(Exception):
    """リンクが壊れている・期限切れ・いまのアドレスあてのものではない。"""


def _address_key(email):
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]


def absolute(path):
    return settings.FE_SITE_URL.rstrip('/') + path


def can_send_link(subscription, now=None):
    """確認・解除のメールを送ってよいか。続けて送らせない（settings.FE_MAIL_LINK_INTERVAL）。"""
    if subscription.link_sent_at is None:
        return True
    now = now or timezone.now()
    return (now - subscription.link_sent_at).total_seconds() >= settings.FE_MAIL_LINK_INTERVAL


# ---- 確認と解除のリンク ----

def make_confirm_token(subscription):
    """確認のリンクに入れる値。アドレスは入れず、確認待ちのアドレスの印だけを入れる。"""
    return signing.dumps(
        {'s': subscription.pk, 'a': _address_key(subscription.pending_email)}, salt=CONFIRM_SALT,
    )


def read_confirm_token(token):
    """MailSubscription。確認待ちのアドレスが替わっていたり、登録済みなら LinkInvalid。"""
    try:
        data = signing.loads(token, salt=CONFIRM_SALT, max_age=settings.FE_MAIL_CONFIRM_MAX_AGE)
    except signing.BadSignature as e:  # SignatureExpired も含む
        raise LinkInvalid from e
    subscription = MailSubscription.objects.filter(pk=data['s']).first()
    if subscription is None or subscription.email or not subscription.pending_email:
        raise LinkInvalid
    if _address_key(subscription.pending_email) != data['a']:
        raise LinkInvalid
    return subscription


def confirm(subscription):
    """確認待ちのアドレスを配信先にする。"""
    subscription.email = subscription.pending_email
    subscription.pending_email = ''
    subscription.save(update_fields=['email_enc', 'pending_email_enc'])


def purge_pending(now=None):
    """確認の期限（FE_MAIL_CONFIRM_MAX_AGE）を過ぎた確認待ちのアドレスを消す。消した件数を返す。

    期限を過ぎたリンクはもう使えないので、アドレスを残しておく理由がない。
    毎日の送信のたびと、ダッシュボードを開いたときに呼ぶ。
    """
    now = now or timezone.now()
    limit = now - datetime.timedelta(seconds=settings.FE_MAIL_CONFIRM_MAX_AGE)
    return (
        MailSubscription.objects.exclude(pending_email_enc='')
        .filter(Q(link_sent_at__lt=limit) | Q(link_sent_at__isnull=True))
        .update(pending_email_enc='')
    )


def make_stop_token(subscription):
    """解除のリンクに入れる値。配信先が替わったら、前のアドレスあてのリンクは効かなくなる。"""
    return signing.dumps(
        {'s': subscription.pk, 'a': _address_key(subscription.email)}, salt=STOP_SALT,
    )


def read_stop_token(token):
    """MailSubscription。いまの配信先あてのリンクでなければ LinkInvalid。"""
    try:
        data = signing.loads(token, salt=STOP_SALT)
    except signing.BadSignature as e:
        raise LinkInvalid from e
    subscription = MailSubscription.objects.filter(pk=data['s']).first()
    if subscription is None or not subscription.email:
        raise LinkInvalid
    if _address_key(subscription.email) != data['a']:
        raise LinkInvalid
    return subscription


def stop(subscription):
    """解除する。アドレスを消し、送り方の選択と進み具合は残す（登録し直したら続きから）。"""
    subscription.email = ''
    subscription.save(update_fields=['email_enc'])


def _footer():
    return (
        '--\n'
        '{} 基本情報技術者試験 対策\n'
        'このメールは自動送信です。\n'
    ).format(settings.SITE_DISPLAY_NAME)


def send_confirmation(subscription):
    """確認待ちのアドレスあて。リンクを開いて「受け取る」を押すと配信先になる。"""
    url = absolute(reverse('fe:mail_confirm', args=[make_confirm_token(subscription)]))
    minutes = settings.FE_MAIL_CONFIRM_MAX_AGE // 60
    EmailMessage(
        subject='[{}] 基本情報の問題メールの確認'.format(settings.SITE_DISPLAY_NAME),
        body=(
            '基本情報技術者試験 対策の ID「{code}」で、このアドレスに問題メールを送る手続きがありました。\n\n'
            '次のリンクを開いて「受け取る」を押すと、登録されます（{minutes}分以内）。\n'
            '{url}\n\n'
            '登録すると、毎日0時ごろに問題（解答・解説付き）が届きます。\n\n'
            '■ 解除について\n'
            '登録したあとは、このアドレスに届くメールのリンクからしか解除できません\n'
            '（画面からはアドレスの変更も解除もできません）。解除のリンクは毎回のメールに付いています。\n\n'
            '■ このメールに心当たりがない場合\n'
            '第三者があなたのメールアドレスを入力した可能性があります。\n'
            'このメールを破棄していただければ、登録されず、今後メールは届きません。\n\n'
        ).format(code=subscription.learner.code, minutes=minutes, url=url) + _footer(),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[subscription.pending_email],
    ).send()


def send_stop_link(subscription):
    """配信先あて。リンクを開いて「配信を止める」を押すと解除される。"""
    url = absolute(reverse('fe:mail_stop', args=[make_stop_token(subscription)]))
    EmailMessage(
        subject='[{}] 基本情報の問題メールの解除'.format(settings.SITE_DISPLAY_NAME),
        body=(
            '基本情報技術者試験 対策の ID「{code}」で、問題メールを解除する手続きがありました。\n\n'
            '次のリンクを開いて「配信を止める」を押すと、配信が止まり、登録されているアドレスが消えます。\n'
            '{url}\n\n'
            '■ このメールに心当たりがない場合\n'
            'この ID を知っている人が手続きをした可能性があります。\n'
            'このメールを破棄していただければ、配信はそのまま続きます。\n\n'
        ).format(code=subscription.learner.code, url=url) + _footer(),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[subscription.email],
    ).send()


# ---- 送る問題を選ぶ ----

def _base_questions():
    """メールで送る問題。数値を振り直す計算問題は、同じ問題が決まらないので除く。"""
    return Question.objects.active().filter(template__isnull=True).select_related('category')


def _order_key(question):
    return (question.subject, question.category.code,
            topic_rank(question.category.code, question.topic), question.id)


def marathon_questions():
    """マラソンで送る全問の並び。科目A→科目B、中分類の番号順、小分類はシラバスの順。"""
    return sorted(_base_questions(), key=_order_key)


def topic_choices():
    """特訓で選べる小分類。中分類ごとにまとめ、小分類ごとの問数を添える。

    [{'category': Category, 'topics': [{'topic', 'count', 'value'}, ...]}, ...]
    value はフォームで送る値（「中分類番号|小分類」）。
    """
    counts = {}
    for question in _base_questions():
        key = (question.category_id, question.topic)
        counts[key] = counts.get(key, 0) + 1
    groups = []
    for category in Category.objects.order_by('subject', 'code'):
        topics = sorted(
            (topic for (category_id, topic) in counts if category_id == category.id),
            key=lambda t: topic_rank(category.code, t),
        )
        if topics:
            groups.append({'category': category, 'topics': [
                {'topic': t, 'count': counts[(category.id, t)], 'value': topic_value(category.code, t)}
                for t in topics
            ]})
    return groups


def topic_value(code, topic):
    return '{}|{}'.format(code, topic)


def parse_topic_values(values):
    """フォームの値を [[中分類番号, 小分類], ...] にする。いま選べないものは捨てる。"""
    valid = {
        t['value']: [group['category'].code, t['topic']]
        for group in topic_choices() for t in group['topics']
    }
    picked = []
    for value in values:
        if value in valid and valid[value] not in picked:
            picked.append(valid[value])
    return picked


def next_marathon(subscription):
    """マラソンで次に送る問題、送ったあとの (位置, 周回数)、進み具合の文言。

    いまの位置から、同じ小分類が続くあいだを最大10問取る。最後まで行ったら最初に戻る。
    問題が無ければ ([], None, '')。
    """
    questions = marathon_questions()
    if not questions:
        return [], None, ''
    position, laps = subscription.marathon_position, subscription.marathon_laps
    if position >= len(questions):
        position, laps = 0, laps + 1
    first = questions[position]
    picked = []
    for question in questions[position:position + QUESTIONS_PER_MAIL]:
        if (question.category_id, question.topic) != (first.category_id, first.topic):
            break
        picked.append(question)
    progress = '{}〜{}問目／全{}問'.format(position + 1, position + len(picked), len(questions))
    if laps:
        progress += '・{}周目'.format(laps + 1)
    return picked, (position + len(picked), laps), progress


def next_training(subscription):
    """特訓で次に送る問題、送ったあとの (小分類の番号, 位置)、進み具合の文言。

    選んだ小分類のうち、いま問題があるものを順に回る。1通に1つの小分類から最大10問。
    小分類を送り切ったら次の小分類へ進み、最後まで行ったら最初に戻る。
    送れなければ ([], None, '')。
    """
    by_topic = {}
    for question in _base_questions():
        by_topic.setdefault((question.category.code, question.topic), []).append(question)
    topics = [
        (code, topic) for code, topic in subscription.training_topics if (code, topic) in by_topic
    ]
    if not topics:
        return [], None, ''
    index = subscription.training_index % len(topics)
    offset = subscription.training_offset
    if offset >= len(by_topic[topics[index]]):
        index, offset = (index + 1) % len(topics), 0
    pool = sorted(by_topic[topics[index]], key=lambda q: q.id)
    picked = pool[offset:offset + QUESTIONS_PER_MAIL]
    progress = '{}〜{}問目／{}問'.format(offset + 1, offset + len(picked), len(pool))
    after = offset + len(picked)
    if after >= len(pool):
        return picked, ((index + 1) % len(topics), 0), progress
    return picked, (index, after), progress


# ---- 組み立てと送信 ----

def _inline_image(src, cid):
    """data URI をインライン画像の添付にする。読めなければ None。"""
    match = _DATA_URI.match(src or '')
    if not match:
        return None
    try:
        data = base64.b64decode(match.group(3), validate=False)
    except (binascii.Error, ValueError):
        return None
    image = MIMEImage(data, _subtype=match.group(2))
    image.add_header('Content-ID', '<{}>'.format(cid))
    image.add_header('Content-Disposition', 'inline', filename='{}.{}'.format(
        cid.split('@')[0], 'jpg' if match.group(2) == 'jpeg' else match.group(2)))
    return image


def _rows(questions):
    """テンプレートに渡す問題。図はインライン画像の参照（cid:）に置き換える。"""
    rows, images = [], []
    for number, question in enumerate(questions, start=1):
        figures = []
        for i, figure in enumerate(question.figures or []):
            cid = 'q{}-f{}@fe.mail'.format(question.id, i)
            image = _inline_image(figure.get('src'), cid)
            if image is not None:
                images.append(image)
                figures.append({'cid': cid, 'alt': figure.get('alt', '')})
        choices = []
        for i, label, text in question.labeled_choices():
            cid = None
            src = question.choice_figure(i)
            if src:
                cid = 'q{}-c{}@fe.mail'.format(question.id, i)
                image = _inline_image(src, cid)
                if image is None:
                    cid = None
                else:
                    images.append(image)
            choices.append({'label': label, 'text': text, 'cid': cid})
        rows.append({'number': number, 'question': question, 'figures': figures, 'choices': choices})
    return rows, images


def build_message(subscription, mode, questions, progress, today):
    """1通ぶんのメール。mode は 'marathon' か 'training'。progress は件名と本文に出す進み具合。"""
    learner = subscription.learner
    first = questions[0]
    rows, images = _rows(questions)
    stats = [
        row for row in category_stats(learner, subject=first.subject) if row['question_count']
    ]
    context = {
        'mode_label': 'マラソン' if mode == 'marathon' else '特訓',
        'today': today,
        'category': first.category,
        'topic': first.topic,
        'progress': progress,
        'rows': rows,
        'stats': stats,
        'learner': learner,
        'dashboard_url': absolute(reverse('fe:dashboard', args=[learner.code])),
        'stop_url': absolute(reverse('fe:mail_stop', args=[make_stop_token(subscription)])),
        'site_name': settings.SITE_DISPLAY_NAME,
    }
    subject = '[{}] 基本情報 {} {:%-m/%-d} {}（{}）'.format(
        settings.SITE_DISPLAY_NAME, context['mode_label'], today,
        first.topic or first.category.name, progress,
    )
    message = EmailMultiAlternatives(
        subject=subject,
        body=render_to_string('fe/email/daily.txt', context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[subscription.email],
    )
    message.attach_alternative(render_to_string('fe/email/daily.html', context), 'text/html')
    if images:
        # 本文と画像をひとまとまりにして、本文から cid: で画像を参照できるようにする
        message.mixed_subtype = 'related'
        for image in images:
            message.attach(image)
    return message


def _send(message, subscription, mode):
    try:
        message.send()
    except (smtplib.SMTPException, OSError, BadHeaderError) as e:
        # 例外の文言には宛先のアドレスが入ることがあるので、種類だけ残す
        logger.error('問題メール（%s）を送れませんでした（ID %s、%s）',
                     mode, subscription.learner.code, type(e).__name__)
        return False
    return True


def send_daily(today=None):
    """今日の分を送る。送り方ごとの通数を返す。何度呼んでも、同じ日に同じ送り方は1通だけ。

    1件の失敗でほかを止めない。失敗したものは位置も送った日も進めないので、
    次に呼ばれたときに送り直す。
    """
    today = today or timezone.localdate()
    purge_pending()
    result = {'marathon': 0, 'training': 0, 'failed': 0}
    subscriptions = (
        MailSubscription.objects.exclude(email_enc='').select_related('learner')
        .filter(learner__code__isnull=False)
    )
    for subscription in subscriptions:
        if not subscription.email:
            # 鍵が替わって読めなくなったもの。送れないので飛ばす
            continue
        for mode, pick, sent_on, fields in (
            ('marathon', next_marathon, subscription.marathon_sent_on,
             ('marathon_position', 'marathon_laps', 'marathon_sent_on')),
            ('training', next_training, subscription.training_sent_on,
             ('training_index', 'training_offset', 'training_sent_on')),
        ):
            if not getattr(subscription, mode) or sent_on == today:
                continue
            questions, after, progress = pick(subscription)
            if not questions:
                continue
            message = build_message(subscription, mode, questions, progress, today)
            if not _send(message, subscription, mode):
                result['failed'] += 1
                continue
            MailSubscription.objects.filter(pk=subscription.pk).update(
                **dict(zip(fields, (*after, today)))
            )
            result[mode] += 1
    return result
