# 問題メールのアドレスを暗号化して持つ（3/3）。平文の列を消す。

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('fe', '0019_mail_subscription_encrypt_copy'),
    ]

    operations = [
        migrations.RemoveField(model_name='mailsubscription', name='email'),
        migrations.RemoveField(model_name='mailsubscription', name='pending_email'),
    ]
