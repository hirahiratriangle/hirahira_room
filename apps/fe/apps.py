from django.apps import AppConfig


class FeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.fe'
    # テーブル名・マイグレーション・ContentType は label で決まる。
    # apps/ へ移す前と同じにしておく。
    label = 'fe'
    verbose_name = 'FE 基本情報技術者試験 対策'
