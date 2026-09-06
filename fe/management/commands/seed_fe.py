"""中分類マスタ・問題テンプレート・問題バンクを投入する。

何度実行しても同じ結果になる（キーで突き合わせて更新する）ので，
問題を追加したら再実行すればよい。
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from fe.data.categories import CATEGORIES
from fe.generators import sync_templates
from fe.models import Category, Question

DATA_DIR = Path(__file__).resolve().parent.parent.parent / 'data'

QUESTION_FILES = [
    'questions_tech_a.json',
    'questions_tech_b.json',
    'questions_management.json',
    'questions_strategy.json',
    'questions_subject_b.json',
]


class Command(BaseCommand):
    help = 'FE 対策アプリの中分類・テンプレート・問題バンクを投入する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--prune', action='store_true',
            help='問題バンクに存在しない固定問題を無効化する（生成問題は対象外）',
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

        categories = {c.code: c for c in Category.objects.all()}
        created = updated = 0
        seen_keys = []

        for filename in QUESTION_FILES:
            path = DATA_DIR / filename
            if not path.exists():
                self.stdout.write(self.style.WARNING(
                    '{} が見つかりません。スキップします。'.format(filename)
                ))
                continue

            with path.open(encoding='utf-8') as fp:
                records = json.load(fp)

            for record in records:
                category = categories.get(record['category'])
                if category is None:
                    raise ValueError(
                        '中分類 {} が未登録です（{}）'.format(
                            record['category'], record['key']
                        )
                    )
                self._validate(record)
                seen_keys.append(record['key'])
                _, made = Question.objects.update_or_create(
                    key=record['key'],
                    defaults={
                        'subject': record.get('subject', Question.SUBJECT_A),
                        'category': category,
                        'topic': record.get('topic', ''),
                        'stem': record['stem'],
                        'choices': record['choices'],
                        'answer_index': record['answer'],
                        'explanation': record.get('explanation', ''),
                        'difficulty': record.get('difficulty', 2),
                        'source': record.get('source', ''),
                        'template': None,
                        'params': None,
                        'is_active': True,
                    },
                )
                created += 1 if made else 0
                updated += 0 if made else 1

            self.stdout.write('  {} : {} 件'.format(filename, len(records)))

        if options['prune']:
            stale = Question.objects.filter(template__isnull=True).exclude(key__in=seen_keys)
            count = stale.update(is_active=False)
            if count:
                self.stdout.write(self.style.WARNING(
                    '問題バンクから消えた {} 件を無効化しました。'.format(count)
                ))

        self.stdout.write(self.style.SUCCESS(
            '問題を投入しました（新規 {} 件 / 更新 {} 件）。'.format(created, updated)
        ))

    @staticmethod
    def _validate(record):
        key = record['key']
        choices = record['choices']
        if not 2 <= len(choices) <= 10:
            raise ValueError('{}: 選択肢の数が不正です'.format(key))
        if len(set(choices)) != len(choices):
            raise ValueError('{}: 選択肢が重複しています'.format(key))
        if not 0 <= record['answer'] < len(choices):
            raise ValueError('{}: 正解の番号が範囲外です'.format(key))
