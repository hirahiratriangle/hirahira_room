"""中分類マスタと問題テンプレートを投入する。

問題そのものはアカウントごとに持つので、このコマンドでは投入しない。
特定のアカウントに同梱の問題バンクを入れたいときは --user を付ける。
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from fe.data.categories import CATEGORIES
from fe.generators import sync_templates
from fe.importer import ImportError_, bundled_records, replace_all, validate
from fe.models import Category


class Command(BaseCommand):
    help = 'FE 対策アプリの中分類・問題テンプレートを投入する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--user', dest='username', default=None,
            help='同梱の問題バンクを、このユーザー名のアカウントに取り込む',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        for code, name, field, major_code, major_name, weight in CATEGORIES:
            Category.objects.update_or_create(
                code=code,
                defaults={
                    'name': name,
                    'field': field,
                    'major_code': major_code,
                    'major_name': major_name,
                    'exam_weight': weight,
                },
            )
        self.stdout.write(self.style.SUCCESS(
            '中分類 {} 件を登録しました。'.format(len(CATEGORIES))
        ))

        sync_templates()
        self.stdout.write(self.style.SUCCESS('問題テンプレートを同期しました。'))

        username = options['username']
        if not username:
            self.stdout.write(
                '問題はアカウントごとに持つため、ここでは投入していません。\n'
                '取り込むには --user <ユーザー名> を付けるか、画面からJSONをアップロードしてください。\n'
                '取り込みは常に一括差し替えです（既存の問題は削除されます）。'
            )
            return

        user_model = get_user_model()
        try:
            user = user_model.objects.get(username=username)
        except user_model.DoesNotExist:
            raise CommandError('ユーザー「{}」が見つかりません。'.format(username))

        records = bundled_records()
        try:
            validate(records)
        except ImportError_ as exc:
            raise CommandError('同梱の問題バンクに問題があります：{}'.format(exc))

        created, removed = replace_all(user, records)
        self.stdout.write(self.style.SUCCESS(
            '{} の問題集を {} 問に差し替えました{}。'.format(
                username, created,
                '（これまでの {} 問は削除。解答履歴は残ります）'.format(removed) if removed else '',
            )
        ))
