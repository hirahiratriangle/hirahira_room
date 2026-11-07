from django.apps import AppConfig


class BkHoumu3Config(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.bk_houmu3'
    # テーブル名・マイグレーション・ContentType は label で決まる。
    # apps/ へ移す前と同じにしておく。
    label = 'bk_houmu3'
    verbose_name = '銀行業務検定 法務3級 対策'
