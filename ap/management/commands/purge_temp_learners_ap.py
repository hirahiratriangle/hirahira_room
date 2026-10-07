"""履歴を残さずに使われた一時的な記録のうち、使われなくなったものを消す。

ID を登録していない記録はセッションからしかたどれないので、放っておくと
誰も参照しないまま増え続ける。最終利用から一定の日数が過ぎたものを消す。

ID を登録した記録には触らない。

コマンド名に _ap を付けてある。管理コマンドの名前はプロジェクト全体で1つなので、
fe の purge_temp_learners と同じ名前にすると、先に登録された fe のほうだけが動く。

    python manage.py purge_temp_learners_ap
    python manage.py purge_temp_learners_ap --days 7
    python manage.py purge_temp_learners_ap --dry-run
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from ap.models import Learner


class Command(BaseCommand):
    help = '使われなくなった、履歴を残さない利用の記録を消す。'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days', type=int, default=Learner.TEMP_RETENTION_DAYS,
            help='最終利用から何日たったものを消すか（既定は {}）。'.format(
                Learner.TEMP_RETENTION_DAYS
            ),
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='消さずに、消す件数だけを表示する。',
        )

    def handle(self, *args, **options):
        days = options['days']
        limit = timezone.now() - timezone.timedelta(days=days)
        targets = Learner.objects.filter(code__isnull=True, last_seen_at__lt=limit)
        count = targets.count()

        if options['dry_run']:
            self.stdout.write(
                '消す対象は {} 件です（最終利用が {} 日より前）。消していません。'.format(
                    count, days
                )
            )
            return

        # 解答履歴・理解度・学習ラウンドは学習者に紐づくので一緒に消える。
        targets.delete()
        self.stdout.write(self.style.SUCCESS(
            '履歴を残さない利用の記録を {} 件消しました（最終利用が {} 日より前）。'.format(
                count, days
            )
        ))
