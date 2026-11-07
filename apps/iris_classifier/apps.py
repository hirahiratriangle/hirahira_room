from django.apps import AppConfig


class IrisClassifierConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.iris_classifier'
    # テーブル名・マイグレーション・ContentType は label で決まる。
    # apps/ へ移す前と同じにしておく。
    label = 'iris_classifier'
