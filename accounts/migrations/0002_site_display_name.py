from django.conf import settings
from django.db import migrations

# django.contrib.sites の Site レコードは初期値が 'example.com' のままで、
# allauth のメール本文などにその名前が出てしまう。表示名をサイト名に揃える。
NEW_NAME = getattr(settings, 'SITE_DISPLAY_NAME', 'HirahiraRoom')
OLD_NAME = 'example.com'


def set_site_name(apps, schema_editor):
    Site = apps.get_model('sites', 'Site')
    site_id = getattr(settings, 'SITE_ID', 1)
    Site.objects.filter(pk=site_id).update(name=NEW_NAME)


def unset_site_name(apps, schema_editor):
    Site = apps.get_model('sites', 'Site')
    site_id = getattr(settings, 'SITE_ID', 1)
    Site.objects.filter(pk=site_id).update(name=OLD_NAME)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
        ('sites', '0002_alter_domain_unique'),
    ]

    operations = [
        migrations.RunPython(set_site_name, unset_site_name),
    ]
