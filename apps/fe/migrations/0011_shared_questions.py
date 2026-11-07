"""問題と技術解説を全 ID の共有にし、管理用の ID を持てるようにする。

問題集の取り込みは管理用の ID（Learner.is_admin）だけができる。
取り込み済みの問題と解説はそのまま残し、共有の問題集として扱う。
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('fe', '0010_dedupe_generated_questions'),
    ]

    operations = [
        migrations.AddField(
            model_name='learner',
            name='is_admin',
            field=models.BooleanField(default=False, help_text='問題集の取り込み・書き出しと、問題の一覧ができる。', verbose_name='管理用'),
        ),
        migrations.RemoveIndex(model_name='question', name='fe_question_learner_subj_idx'),
        migrations.RemoveField(model_name='question', name='learner'),
        migrations.RemoveField(model_name='learningnote', name='learner'),
        migrations.AddIndex(
            model_name='question',
            index=models.Index(fields=['subject', 'category'], name='fe_question_subj_cat_idx'),
        ),
    ]
