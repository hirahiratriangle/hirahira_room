"""廃止した応用情報（AP）対策アプリのテーブルと記録を、データベースから消す。

ap は応用情報技術者試験そのものが終わるため廃止し、コードもリポジトリから外した。
アプリを INSTALLED_APPS から外しても、テーブルは残り続ける（Django は消さない）。
ここで次の 3 つをまとめて片付ける。

1. ap_ で始まる 12 本のテーブル（学習者・問題・解答履歴など、AP だけが使っていたもの）
2. ap の権限（auth_permission）と、それを指す管理画面の操作履歴、ContentType
3. django_migrations に残る ap のマイグレーション記録

ap のコードはもうないので、ap のマイグレーションで消すことはできない。
そのため、残っているアプリのうち、モデルを持たない home にこのマイグレーションを置く。

テーブルがない DB（新しく作った開発環境やテスト用の DB）でも通るよう、
すべて「あれば消す」にしてある。消したデータは戻らないので、逆向きは何もしない。
"""
from django.db import migrations

# 外部キーで参照される側を後に消す。参照する側が残っていると、
# SQLite では DROP TABLE が外部キーの検査に引っかかることがある。
AP_TABLES = (
    'ap_learningitem',       # → learninground, question
    'ap_attempt',            # → learner, question, category
    'ap_categoryprogress',   # → learner, category
    'ap_dailyprogress',      # → learner
    'ap_studysession',       # → learner, category
    'ap_passreport',         # → learner
    'ap_learninground',      # → learner, learningnote
    'ap_question',           # → category, questiontemplate
    'ap_questiontemplate',   # → category
    'ap_learningnote',       # → category
    'ap_category',
    'ap_learner',
)


def drop_ap(apps, schema_editor):
    connection = schema_editor.connection
    quote = schema_editor.quote_name
    existing = set(connection.introspection.table_names())

    # 1. テーブル
    for table in AP_TABLES:
        if table in existing:
            schema_editor.execute(f'DROP TABLE {quote(table)}')

    # 2. 権限・管理画面の履歴・ContentType
    # ContentType を ORM で消すと、それを指す Permission と LogEntry も連鎖して消える
    # （dependencies に auth と admin を入れているので、履歴上のモデルにその関係がある）。
    ContentType = apps.get_model('contenttypes', 'ContentType')
    ContentType.objects.using(connection.alias).filter(app_label='ap').delete()

    # 3. マイグレーションの記録
    with connection.cursor() as cursor:
        cursor.execute('DELETE FROM django_migrations WHERE app = %s', ['ap'])


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('admin', '0003_logentry_add_action_flag_choices'),
    ]

    operations = [
        migrations.RunPython(drop_ap, migrations.RunPython.noop),
    ]
