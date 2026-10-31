from django.contrib import admin
from django.contrib.staticfiles.urls import static
from django.urls import path, include, re_path
from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.http import Http404
from django.views.generic import RedirectView


@login_not_required  # ログイン画面へ回さず、誰が開いても「ない」と返す
def signup_removed(request):
    """新規アカウント作成の画面はない。アカウントは管理画面で作る。"""
    raise Http404

urlpatterns = [
    path('admin/', admin.site.urls),

    # 認証（django-allauth）
    # 新規アカウント作成はサイトから外した。allauth の signup より先に当てて 404 にする
    path('accounts/signup/', signup_removed),
    path('accounts/', include('allauth.urls')),

    # ホーム画面（ログイン必須）
    path('', include('home.urls', namespace='home')),

    # 日記アプリ
    path('diary/', include('diary.urls', namespace='diary')),

    # Iris Classifier アプリ
    path('iris/', include('iris_classifier.urls', namespace='iris_classifier')),

    # JP Core Issue 改善支援アプリ
    path('jpcore/', include('jpcore.urls', namespace='jpcore')),

    # カウントアップ＆ダウンアプリ
    path('countdown/', include('countdown.urls', namespace='countdown')),

    # 銀行業務検定 法務3級 対策アプリ
    path('bk_houmu3/', include('bk_houmu3.urls', namespace='bk_houmu3')),
    # PVSYS（genba・3つの構想・基本情報・福祉従事者の活動ページをまとめた入り口。ログインなしで公開）
    path('pvsys/', include('pvsys.urls', namespace='pvsys')),
    # 基本情報技術者試験（FE）対策アプリ
    path('pvsys/fe/', include('fe.urls', namespace='fe')),
    # サイト直下の /fe/ でも同じアプリを開く。Logic Apps と GitHub Actions の定期実行もここを叩く。
    # reverse の 'fe:' は /pvsys/fe/ を返す。テンプレートの {% url %} は開いている側の URL を保つ
    path('fe/', include('fe.urls', namespace='fe_root')),
    # genba（1現場1リポジトリの記録を4つの立場で使う）
    path('pvsys/genba/', include('genba.urls', namespace='genba')),
    # 社内活動の3つの構想（「社内活動 3つの構想.pptx」を順に読むページ）
    path('pvsys/proposals/', include('proposals.urls', namespace='proposals')),
    # 福祉従事者の活動ページ（fun IT club WG-03。ログインなしで公開）
    path('pvsys/welfare/', include('welfare_activity.urls', namespace='welfare_activity')),
    # 移す前の URL はプレゼンの資料や共有済みのリンクに残っているので、新しい場所へ送る
    re_path(r'^(?P<path>(?:genba|proposals|welfare)/.*)$', login_not_required(
        RedirectView.as_view(url='/pvsys/%(path)s', permanent=True, query_string=True))),
]

# メディアファイルの配信設定
# 本番環境でも明示的にメディアファイルを配信する
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
else:
    # Azure App Serviceでの本番環境でもメディアファイルを配信
    from django.views.static import serve

    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
    ]