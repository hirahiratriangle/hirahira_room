"""countdown の API。React の画面（frontend/countdown）が呼ぶ。

    GET    /countdown/api/events/        自分の予定・出来事の一覧（これからの予定／過ぎた出来事に分けて）
    POST   /countdown/api/events/        登録
    GET    /countdown/api/events/<id>/   1件
    PATCH  /countdown/api/events/<id>/   更新
    DELETE /countdown/api/events/<id>/   削除

ログインは画面と同じセッションを使う（settings の REST_FRAMEWORK）。

サイト全体には LoginRequiredMiddleware がかかっていて、未ログインだとログイン画面へ
転送（302 と HTML）を返してしまう。API がそれを返すと、React は JSON を読めない。
そこで API には login_not_required を付けて中間処理を外し、ログインの確認は DRF に任せる。

DRF の SessionAuthentication は、未ログインを 403 で返す。ところが CSRF トークンの
食い違いも 403 なので、React からは見分けがつかない。そこで下の SessionAuth で
未ログインを 401 にする。React は 401 を見たらログイン画面へ移る。

他人の予定は、一覧に出さないだけでなく、id を指定しても「無い」（404）と答える。
あるかどうかも漏らさないため。テンプレートの画面のころは 403 だった。
"""

import logging

from django.contrib.auth.decorators import login_not_required
from django.utils import timezone
from django.utils.decorators import method_decorator
from rest_framework import generics
from rest_framework.authentication import SessionAuthentication
from rest_framework.response import Response

from .models import CountEvent
from .serializers import CountEventSerializer

logger = logging.getLogger(__name__)


class SessionAuth(SessionAuthentication):
    """セッションでのログイン。未ログインのとき 403 ではなく 401 を返させる。

    DRF は、authenticate_header が値を返す認証方式なら、未ログインを 401 にする
    （HTTP の決まりで、401 には WWW-Authenticate ヘッダーが要るため）。
    ここで返す 'Session' はブラウザが何かするための値ではなく、その印にすぎない。
    """

    def authenticate_header(self, request):
        return 'Session'


class OwnEventsMixin:
    """ログインしている本人の予定だけを扱う。"""

    authentication_classes = [SessionAuth]
    serializer_class = CountEventSerializer

    def get_queryset(self):
        return CountEvent.objects.filter(user=self.request.user)


@method_decorator(login_not_required, name='dispatch')
class EventListCreateAPI(OwnEventsMixin, generics.ListCreateAPIView):

    def list(self, request, *args, **kwargs):
        """一覧。並べ方もここで決めて、React はそのまま描くだけにする。

        これからの予定（当日を含む）は近い順、過ぎた出来事は新しい順。
        today は見出しの「本日 ○年○月○日」に使う。
        """
        today = timezone.localdate()
        events = list(self.get_queryset())
        upcoming = sorted((e for e in events if e.date >= today), key=lambda e: e.date)
        past = sorted((e for e in events if e.date < today), key=lambda e: e.date, reverse=True)
        return Response({
            'today': today.isoformat(),
            'upcoming': self.get_serializer(upcoming, many=True).data,
            'past': self.get_serializer(past, many=True).data,
        })

    def perform_create(self, serializer):
        event = serializer.save(user=self.request.user)
        logger.info('CountEvent created by %s', self.request.user)
        return event


@method_decorator(login_not_required, name='dispatch')
class EventDetailAPI(OwnEventsMixin, generics.RetrieveUpdateDestroyAPIView):
    # 書きかえは欄の一部だけを送る PATCH にそろえる。PUT は受けない
    http_method_names = ['get', 'patch', 'delete', 'head', 'options']
