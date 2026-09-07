"""中分類マスタと問題テンプレートを投入する。

問題そのものは投入しない。問題はアカウントごとに持ち、利用者が
自分で用意した JSON を画面から取り込む方式のため、アプリは問題を
同梱していない。
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from fe.data.categories import CATEGORIES
from fe.exam import EXAM
from fe.generators import sync_templates
from fe.models import Category


class Command(BaseCommand):
    help = 'FE 対策アプリの中分類・問題テンプレートを投入する'

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

        self.stdout.write(
            '\n問題は同梱していません。{}の資料をもとに作った JSON を、\n'
            '画面の「自分の問題 → JSONを取り込む」から取り込んでください。'.format(EXAM['name'])
        )
