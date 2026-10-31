"""リポジトリを現場ごとからメンバーごとに改める（憲章 版 0.7）。

現場は GitHub の Team に当たるので、repo_name を team_slug に名前だけ変え、値は残す。
既に現場に入っている人には、ここでリポジトリを作る。
"""
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

import genba.kit


def create_repositories(apps, schema_editor):
    Membership = apps.get_model('genba', 'Membership')
    MemberRepository = apps.get_model('genba', 'MemberRepository')
    for user_id, username in Membership.objects.values_list('user_id', 'user__username').distinct():
        MemberRepository.objects.get_or_create(user_id=user_id, defaults={'name': f'genba-{username}'})


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('genba', '0005_grade_review'),
    ]

    operations = [
        migrations.RenameField(model_name='site', old_name='repo_name', new_name='team_slug'),
        migrations.AlterField(
            model_name='site', name='team_slug',
            field=models.CharField(help_text='例: genba-acme-order。GitHub の Team に当たる。プロトタイプでは名前だけ持つ。',
                                   max_length=100, unique=True, verbose_name='現場チーム名'),
        ),
        migrations.RemoveField(model_name='site', name='template_version'),
        migrations.CreateModel(
            name='MemberRepository',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, unique=True, verbose_name='リポジトリ名')),
                ('template_version', models.CharField(default=genba.kit.TEMPLATE_VERSION, max_length=20, verbose_name='テンプレートの版')),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='作成日時')),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='genba_repository',
                                              to=settings.AUTH_USER_MODEL, verbose_name='本人')),
            ],
            options={'verbose_name': 'メンバーのリポジトリ', 'verbose_name_plural': 'メンバーのリポジトリ'},
        ),
        migrations.CreateModel(
            name='FocusSkill',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('skill_item', models.CharField(max_length=40, unique=True, verbose_name='スキル項目')),
            ],
            options={'verbose_name': '重点スキル項目', 'verbose_name_plural': '重点スキル項目'},
        ),
        migrations.CreateModel(
            name='Assignment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('matched', models.BooleanField(default=False, verbose_name='次の目標に合う現場')),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='作成日時')),
                ('assigned_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+',
                                                  to=settings.AUTH_USER_MODEL, verbose_name='決めた人')),
                ('goal', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='assignments',
                                           to='genba.goal', verbose_name='元になった成長')),
                ('site', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='assignments',
                                           to='genba.site', verbose_name='アサインした現場')),
            ],
            options={'verbose_name': 'アサイン', 'verbose_name_plural': 'アサイン', 'ordering': ['-created_at']},
        ),
        migrations.RunPython(create_repositories, migrations.RunPython.noop),
    ]
