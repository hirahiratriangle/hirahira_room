from django.apps import AppConfig


class WagesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'works.wages'
    # テーブル名・マイグレーション・ContentType は label で決まる。
    # works/ の下へ移す前と同じにしておく。
    label = 'wages'
    verbose_name = 'SE／PGの賃金と仕事の実質'
