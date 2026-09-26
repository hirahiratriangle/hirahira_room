"""中分類マスタと問題テンプレートを投入する。

問題そのものは投入しない。問題は全 ID で共有し、管理用の ID が
用意した JSON を画面から取り込む方式のため、アプリは問題を
同梱していない。
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from fe.data.categories import CATEGORIES, SUBJECT_B_CATEGORIES
from fe.exam import EXAM
from fe.generators import sync_templates
from fe.models import Category


class Command(BaseCommand):
    help = 'FE 対策アプリの中分類・問題テンプレートを投入する'

    @transaction.atomic
    def handle(self, *args, **options):
        # 科目ごとに出題範囲の立て方が違うので、分類も科目ごとに入れる
        for subject, rows in (
            (Category.SUBJECT_A, CATEGORIES),
            (Category.SUBJECT_B, SUBJECT_B_CATEGORIES),
        ):
            for code, name, field, major_code, major_name, weight in rows:
                Category.objects.update_or_create(
                    code=code,
                    defaults={
                        'name': name,
                        'subject': subject,
                        'field': field,
                        'major_code': major_code,
                        'major_name': major_name,
                        'exam_weight': weight,
                    },
                )
        self.stdout.write(self.style.SUCCESS(
            '分類を登録しました（科目A {} 件・科目B {} 件）。'.format(
                len(CATEGORIES), len(SUBJECT_B_CATEGORIES)
            )
        ))

        sync_templates()
        self.stdout.write(self.style.SUCCESS('問題テンプレートを同期しました。'))

        self.stdout.write(
            '\n問題は同梱していません。{}の資料をもとに作った JSON を、\n'
            '管理用の ID で入り、画面の「問題の管理 → JSONを取り込む」から取り込んでください。'.format(EXAM['name'])
        )
