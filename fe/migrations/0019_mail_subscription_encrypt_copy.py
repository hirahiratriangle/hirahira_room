# 問題メールのアドレスを暗号化して持つ（2/3）。平文の列から暗号文の列へ移す。

from django.db import migrations

from fe.crypto import encrypt


def forwards(apps, schema_editor):
    MailSubscription = apps.get_model('fe', 'MailSubscription')
    for row in MailSubscription.objects.all():
        row.email_enc = encrypt(row.email)
        row.pending_email_enc = encrypt(row.pending_email)
        row.save(update_fields=['email_enc', 'pending_email_enc'])


class Migration(migrations.Migration):

    dependencies = [
        ('fe', '0018_mail_subscription_encrypt_add'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
