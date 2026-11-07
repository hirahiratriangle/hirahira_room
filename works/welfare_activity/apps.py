from django.apps import AppConfig


class WelfareActivityConfig(AppConfig):
    name = 'works.welfare_activity'
    # テンプレートの置き場所や URL の名前空間と揃えるため、
    # works/ の下へ移す前と同じ label にしておく。
    label = 'welfare_activity'
    verbose_name = '福祉'
