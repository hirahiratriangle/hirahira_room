# 問題メールのアドレスを暗号化して持つ（1/3）。暗号文の列を足す。
#
# 行の更新と列の削除を1本にまとめると、PostgreSQL で「pending trigger events」で
# 失敗することがあるので（2026-09-25 の障害）、足す・移す・消すを3本に分けてある。

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('fe', '0017_mail_subscription'),
    ]

    operations = [
        migrations.AddField(
            model_name='mailsubscription',
            name='email_enc',
            field=models.TextField(blank=True, editable=False, verbose_name='配信先（暗号化）'),
        ),
        migrations.AddField(
            model_name='mailsubscription',
            name='pending_email_enc',
            field=models.TextField(blank=True, editable=False, verbose_name='確認待ちのアドレス（暗号化）'),
        ),
    ]
