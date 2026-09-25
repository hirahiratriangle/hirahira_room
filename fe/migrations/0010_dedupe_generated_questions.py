"""問題を全 ID の共有にする前に、計算問題の重複をまとめる。

テンプレートから生成した計算問題は、これまで ID ごとに同じ数値のものを
別々に持っていた。共有にすると (テンプレート, 数値) で1問に定まるので、
重複は最も古い1問に寄せ、解答履歴と学習ラウンドの問題もそちらへ付け替える。

PostgreSQL では行の更新と ALTER TABLE を同じトランザクションで行えないため、
データの整理だけをこのマイグレーションに分けてある。
"""

import json

from django.db import migrations


def merge_generated(apps, schema_editor):
    Question = apps.get_model('fe', 'Question')
    Attempt = apps.get_model('fe', 'Attempt')
    LearningItem = apps.get_model('fe', 'LearningItem')

    keep = {}
    for question in Question.objects.filter(template__isnull=False).order_by('id'):
        key = (question.template_id, json.dumps(question.params, sort_keys=True))
        kept = keep.setdefault(key, question.id)
        if kept == question.id:
            continue
        Attempt.objects.filter(question_id=question.id).update(question_id=kept)
        LearningItem.objects.filter(question_id=question.id).update(question_id=kept)
        question.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('fe', '0009_learner_id'),
    ]

    operations = [
        migrations.RunPython(merge_generated, migrations.RunPython.noop),
    ]
