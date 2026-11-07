"""廃止した日記（diary）・Iris Classifier・JP Core のテーブルと記録を、データベースから消す。

3 つとも使わなくなったため、コードはリポジトリから外した。
アプリを INSTALLED_APPS から外しても、テーブルは残り続ける（Django は消さない）。
考え方は 0001（ap を消したもの）と同じで、次の 3 つを片付ける。

1. テーブル：diary_diary（日記）と iris_classifier_predictionhistory（予測の履歴）。
   jpcore はモデルを持たないので、テーブルはない。
2. 3 つの権限（auth_permission）と、それを指す管理画面の操作履歴、ContentType
3. django_migrations に残る 3 つのマイグレーション記録

日記の写真（media/ に置いたファイル）は DB の外にあるので、ここでは消さない。

テーブルがない DB でも通るよう、すべて「あれば消す」にしてある。
消したデータは戻らないので、逆向きは何もしない。
"""
from django.db import migrations

RETIRED_APPS = ('diary', 'iris_classifier', 'jpcore')

# どちらも auth_user を指すだけで、互いには参照し合わない。順番は問わない。
RETIRED_TABLES = (
    'diary_diary',
    'iris_classifier_predictionhistory',
)


def drop_retired(apps, schema_editor):
    connection = schema_editor.connection
    existing = set(connection.introspection.table_names())

    # 1. テーブル
    for table in RETIRED_TABLES:
        if table in existing:
            schema_editor.execute(f'DROP TABLE {schema_editor.quote_name(table)}')

    # 2. 権限・管理画面の履歴・ContentType
    # ContentType を ORM で消すと、それを指す Permission と LogEntry も連鎖して消える。
    ContentType = apps.get_model('contenttypes', 'ContentType')
    ContentType.objects.using(connection.alias).filter(app_label__in=RETIRED_APPS).delete()

    # 3. マイグレーションの記録
    with connection.cursor() as cursor:
        cursor.execute(
            'DELETE FROM django_migrations WHERE app IN (%s, %s, %s)', list(RETIRED_APPS))


class Migration(migrations.Migration):

    dependencies = [
        ('home', '0001_drop_retired_ap_tables'),
    ]

    operations = [
        migrations.RunPython(drop_retired, migrations.RunPython.noop),
    ]
