"""基本情報の問題メールを、今日の分だけ送る。

通常は GitHub Actions（.github/workflows/fe-mails.yml）が毎日0時に
/fe/tasks/send-mails/ を呼んで送る。手で送りたいとき・確かめたいときに使う。
同じ日に何度流しても、同じ登録の同じ送り方には1通しか送らない。

    python manage.py send_fe_mails --settings=config.settings_dev
"""

from django.core.management.base import BaseCommand

from fe.mail import send_daily


class Command(BaseCommand):
    help = '基本情報の問題メールを、今日の分だけ送る。'

    def handle(self, *args, **options):
        result = send_daily()
        self.stdout.write(self.style.SUCCESS(
            'マラソン {marathon} 通、特訓 {training} 通を送りました（失敗 {failed} 通）。'.format(**result)
        ))
