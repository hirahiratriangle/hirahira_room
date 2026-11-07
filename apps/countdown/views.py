"""countdown の画面とタスクの入り口。

一覧・登録・編集・削除の画面は React で作っている（frontend/countdown）。
Django が返すのは、React を読み込むための入れ物のページ（AppView）だけで、
中身は React が API（api.py）からデータをもらって描く。
URL は React にする前と同じにしてあり、どの URL を開いても同じ入れ物を返す。
どの画面を描くかは、React がアドレスを見て決める。

紹介ページ（IndexView）は読むだけのページなので、テンプレートのまま残している。
"""

import hmac
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View, generic
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie

from .notifications import send_reminders

logger = logging.getLogger(__name__)


class IndexView(generic.TemplateView):
    """アプリの紹介ページ"""
    template_name = 'countdown/index.html'


@method_decorator(ensure_csrf_cookie, name='dispatch')
class AppView(generic.TemplateView):
    """React の画面の入れ物。ログインが要る（サイト全体の LoginRequiredMiddleware）。

    ensure_csrf_cookie で csrftoken の Cookie を必ず配る。React は書き込みのとき、
    この Cookie の値を X-CSRFToken ヘッダーに載せて送る。
    """
    template_name = 'countdown/app.html'


@method_decorator(login_not_required, name='dispatch')
@method_decorator(csrf_exempt, name='dispatch')
class SendRemindersView(View):
    """リマインド送信を外部スケジューラから起動するためのエンドポイント。

    Azure App Service には cron が無いため、GitHub Actions の定期実行から
    POST で叩く。ログインの代わりに共有トークンで認証する。
    """

    def post(self, request):
        expected = getattr(settings, 'COUNTDOWN_REMINDER_TOKEN', '')
        if not expected:
            logger.error('COUNTDOWN_REMINDER_TOKEN が未設定のため実行できません。')
            return JsonResponse(
                {'detail': 'リマインド用トークンが未設定です。'}, status=503)

        provided = request.headers.get('X-Reminder-Token', '')
        if not hmac.compare_digest(provided, expected):
            logger.warning('リマインド起動が不正なトークンで試みられました。')
            return JsonResponse({'detail': '認証できません。'}, status=403)

        result = send_reminders()
        logger.info(
            'リマインドを実行しました（%s人 / %s件）', result['users'], result['events'])
        return JsonResponse({
            'users': result['users'],
            'events': result['events'],
            'failed': result['failed'],
            'skipped_no_email': result['skipped_no_email'],
        })
