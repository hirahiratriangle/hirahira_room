from django.apps import AppConfig


class CountdownConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.countdown'
    # テーブル名・マイグレーション・ContentType は label で決まる。
    # apps/ へ移す前と同じにしておく。
    label = 'countdown'
    verbose_name = 'Countdown'
