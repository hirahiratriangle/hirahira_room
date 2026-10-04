"""予定のリマインドメールを送る。1日1回の定期実行を想定している。

    python manage.py send_countdown_reminders
    python manage.py send_countdown_reminders --dry-run
    python manage.py send_countdown_reminders --date 2026-12-25
"""

from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from countdown.notifications import send_reminders


class Command(BaseCommand):
    help = 'カウントアップ＆ダウンの予定リマインドをメールで送る'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='送信せずに対象だけを表示する',
        )
        parser.add_argument(
            '--date',
            help='基準日を YYYY-MM-DD で指定する（既定は今日）',
        )

    def handle(self, *args, **options):
        if options['date']:
            try:
                today = datetime.strptime(options['date'], '%Y-%m-%d').date()
            except ValueError:
                raise CommandError('--date は YYYY-MM-DD の形式で指定してください。')
        else:
            today = timezone.localdate()

        dry_run = options['dry_run']
        result = send_reminders(today=today, dry_run=dry_run)

        header = f'基準日 {today}'
        if dry_run:
            header += '（--dry-run のため送信しません）'
        self.stdout.write(header)

        for line in result['details']:
            self.stdout.write(f'  {line}')

        if result['skipped_no_email']:
            self.stdout.write(self.style.WARNING(
                'メールアドレス未登録のため対象外: {} 件'.format(result['skipped_no_email'])
            ))
        if result['failed']:
            self.stdout.write(self.style.ERROR(
                '送信に失敗: {} 件（詳細はログを確認してください）'.format(result['failed'])
            ))

        summary = '{}人に {} 件の予定を{}'.format(
            result['users'], result['events'],
            '送信対象として検出しました。' if dry_run else '通知しました。',
        )
        self.stdout.write(self.style.SUCCESS(summary))
