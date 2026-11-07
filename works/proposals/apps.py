from django.apps import AppConfig


class ProposalsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'works.proposals'
    # テーブル名・マイグレーション・ContentType は label で決まる。
    # works/ の下へ移す前と同じにしておく。
    label = 'proposals'
    verbose_name = '社内活動 3つの構想'
