"""成績と問題を、hirahira_room のアカウントごとから本アプリの ID（Learner）ごとへ移す。

既存のデータは移さずに捨てる（ID とアカウントを紐づけないため、移し先が決まらない）。
"""

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models

# アカウントに紐づいていたデータ。子から順に消す。
ACCOUNT_SCOPED = [
    'LearningRound', 'Attempt', 'Question', 'LearningNote',
    'CategoryProgress', 'DailyProgress', 'StudySession', 'PassReport',
]


def discard_account_data(apps, schema_editor):
    for name in ACCOUNT_SCOPED:
        apps.get_model('fe', name).objects.all().delete()


def learner_fk(related_name, one=False):
    kind = models.OneToOneField if one else models.ForeignKey
    return kind(
        on_delete=django.db.models.deletion.CASCADE, related_name=related_name,
        to='fe.learner', verbose_name='ID',
    )


class Migration(migrations.Migration):

    dependencies = [
        ('fe', '0008_pass_report'),
    ]

    operations = [
        migrations.RunPython(discard_account_data, migrations.RunPython.noop),

        migrations.CreateModel(
            name='Learner',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=30, unique=True, validators=[django.core.validators.RegexValidator('^[a-z0-9_-]{3,30}$', 'ID は半角の英小文字・数字・「_」「-」で、3〜30文字にしてください。')], verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='作成日時')),
            ],
            options={
                'verbose_name': 'FE 学習者ID',
                'verbose_name_plural': 'FE 学習者ID',
                'ordering': ['code'],
            },
        ),

        # アカウントを指していた索引と一意制約を外してから、列を付け替える
        migrations.RemoveIndex(model_name='question', name='fe_question_owner_i_e82c7f_idx'),
        migrations.RemoveIndex(model_name='attempt', name='fe_attempt_user_id_38082c_idx'),
        migrations.RemoveIndex(model_name='attempt', name='fe_attempt_user_id_526886_idx'),
        migrations.RemoveConstraint(model_name='categoryprogress', name='fe_unique_progress'),
        migrations.RemoveConstraint(model_name='dailyprogress', name='fe_unique_daily_progress'),

        migrations.RemoveField(model_name='question', name='owner'),
        migrations.RemoveField(model_name='learningnote', name='owner'),
        migrations.RemoveField(model_name='attempt', name='user'),
        migrations.RemoveField(model_name='categoryprogress', name='user'),
        migrations.RemoveField(model_name='dailyprogress', name='user'),
        migrations.RemoveField(model_name='studysession', name='user'),
        migrations.RemoveField(model_name='learninground', name='user'),
        migrations.RemoveField(model_name='passreport', name='user'),

        # 表は空にしてあるので、既定値なしで必須の列を足せる
        migrations.AddField(model_name='question', name='learner', field=learner_fk('questions')),
        migrations.AddField(model_name='learningnote', name='learner', field=learner_fk('notes')),
        migrations.AddField(model_name='attempt', name='learner', field=learner_fk('attempts')),
        migrations.AddField(model_name='categoryprogress', name='learner', field=learner_fk('progress')),
        migrations.AddField(model_name='dailyprogress', name='learner', field=learner_fk('daily_progress')),
        migrations.AddField(model_name='studysession', name='learner', field=learner_fk('study_session', one=True)),
        migrations.AddField(model_name='learninground', name='learner', field=learner_fk('rounds')),
        migrations.AddField(model_name='passreport', name='learner', field=learner_fk('pass_report', one=True)),

        migrations.AddIndex(
            model_name='question',
            index=models.Index(fields=['learner', 'subject', 'category'], name='fe_question_learner_subj_idx'),
        ),
        migrations.AddIndex(
            model_name='attempt',
            index=models.Index(fields=['learner', '-answered_at'], name='fe_attempt_learner_at_idx'),
        ),
        migrations.AddIndex(
            model_name='attempt',
            index=models.Index(fields=['learner', 'category'], name='fe_attempt_learner_cat_idx'),
        ),
        migrations.AddConstraint(
            model_name='categoryprogress',
            constraint=models.UniqueConstraint(fields=('learner', 'category', 'subject'), name='fe_unique_progress'),
        ),
        migrations.AddConstraint(
            model_name='dailyprogress',
            constraint=models.UniqueConstraint(fields=('learner', 'date'), name='fe_unique_daily_progress'),
        ),
    ]
