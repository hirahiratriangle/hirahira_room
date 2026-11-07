from django.apps import AppConfig


class GenbaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.genba'
    # テーブル名・マイグレーション・ContentType は label で決まる。
    # apps/ へ移す前と同じにしておく。
    label = 'genba'
    verbose_name = 'genba 現場の記録'
