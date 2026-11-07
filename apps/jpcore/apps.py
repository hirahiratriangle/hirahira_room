from django.apps import AppConfig


class JpcoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.jpcore'
    # テーブル名・マイグレーション・ContentType は label で決まる。
    # apps/ へ移す前と同じにしておく。
    label = 'jpcore'
    verbose_name = 'JP Core Issue 改善支援'
