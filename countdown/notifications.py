"""これからの予定のリマインドメールを組み立てて送る。

毎日0時に1回実行し、通知をオンにした予定を1通にまとめて送る。
送信済みの日は ReminderLog に記録し、同じ日に二重送信しない。
"""

import logging
from collections import defaultdict

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from .models import CountEvent, ReminderLog

logger = logging.getLogger(__name__)


def collect_events(today=None):
    """通知対象の予定を、ユーザーごとにまとめて返す。

    対象は「通知をオンにした、今日以降の予定」。過ぎた出来事は含めない。
    その日すでに送信済みのユーザーと、宛先が無いユーザーは除く。
    戻り値は {user: [event, ...]}（予定日の近い順）。
    """
    today = today or timezone.localdate()

    sent_today = set(
        ReminderLog.objects.filter(sent_on=today).values_list('user_id', flat=True)
    )
    events = (
        CountEvent.objects
        .filter(notify=True, date__gte=today)
        .select_related('user')
        .order_by('date')
    )

    due = defaultdict(list)
    for event in events:
        if event.user_id in sent_today:
            continue
        if not event.user.email:
            continue
        due[event.user].append(event)
    return due


def build_message(user, events, today):
    """1ユーザー分のメールを組み立てる。events は予定日の近い順。"""
    rows = [
        {
            'name': event.name,
            'date': event.date,
            'memo': event.memo,
            'days_left': (event.date - today).days,
        }
        for event in events
    ]
    context = {
        'user': user,
        'rows': rows,
        'today': today,
        'site_name': getattr(settings, 'SITE_DISPLAY_NAME', 'HirahiraRoom'),
    }
    subject = render_to_string(
        'countdown/email/reminder_subject.txt', context).strip()
    body = render_to_string('countdown/email/reminder_body.txt', context)
    return EmailMessage(
        subject=subject,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )


def send_reminders(today=None, dry_run=False):
    """通知対象を集めて送信する。送った件数などの集計を返す。"""
    today = today or timezone.localdate()
    due = collect_events(today)

    result = {'users': 0, 'events': 0, 'failed': 0, 'skipped_no_email': 0, 'details': []}

    # 宛先が無いために送れないユーザー数（予定の件数ではなく人数で数える）
    result['skipped_no_email'] = (
        CountEvent.objects
        .filter(notify=True, date__gte=today, user__email='')
        .values('user_id').distinct().count()
    )

    for user, events in due.items():
        names = '、'.join(event.name for event in events)
        if dry_run:
            result['users'] += 1
            result['events'] += len(events)
            result['details'].append(f'{user.username} <{user.email}>: {names}')
            continue

        message = build_message(user, events, today)
        try:
            message.send(fail_silently=False)
        except Exception:
            result['failed'] += 1
            logger.exception('リマインド送信に失敗しました（%s）', user.username)
            continue

        try:
            with transaction.atomic():
                ReminderLog.objects.create(
                    user=user, sent_on=today, event_count=len(events),
                )
        except IntegrityError:
            # 同時実行などで既に記録済みの場合は何もしない
            pass

        result['users'] += 1
        result['events'] += len(events)
        result['details'].append(f'{user.username} <{user.email}>: {names}')
        logger.info('リマインドを送信しました（%s / %s件）', user.username, len(events))

    return result
