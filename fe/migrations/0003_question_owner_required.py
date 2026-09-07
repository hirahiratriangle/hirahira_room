"""問題を所有者必須にする。

問題はアカウントごとに持ち、JSON の一括取り込みで入れ替える方式にしたため、
所有者のいない問題は出題先が無い。移行時に取り除いてから必須にする。
取り除かれた問題に紐づく解答履歴も併せて消えるが、この時点では
移行前の検証データしか存在しない。

問題は取り込み直せる（画面のアップロード、または
`manage.py seed_fe --user <ユーザー名>`）。
"""

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def drop_ownerless_questions(apps, schema_editor):
    Question = apps.get_model('fe', 'Question')
    Question.objects.filter(owner__isnull=True).delete()


def flush_pending_triggers(apps, schema_editor):
    """保留中のトリガーイベントを先に処理させる。

    PostgreSQL は外部キー制約を DEFERRABLE INITIALLY DEFERRED で作るため、
    行を削除するとカスケードのトリガーがトランザクション終了まで保留される。
    保留が残ったままでは同じテーブルを ALTER TABLE できないので、
    ここで確定させてから型を変える。SQLite には該当する仕組みが無い。
    """
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE')


def noop(apps, schema_editor):
    """所有者を復元する手立ては無いので、巻き戻しでは何もしない。"""


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('fe', '0002_question_per_account'),
    ]

    operations = [
        migrations.RunPython(drop_ownerless_questions, noop),
        migrations.RunPython(flush_pending_triggers, noop),
        migrations.AlterField(
            model_name='question',
            name='owner',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='fe_questions',
                to=settings.AUTH_USER_MODEL,
                verbose_name='所有者',
            ),
        ),
    ]
