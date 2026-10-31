from django.contrib import admin
from django.contrib.staticfiles.urls import static
from django.urls import path, include
from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.http import Http404


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

    # 基本情報技術者試験（FE）対策アプリ
    path('fe/', include('fe.urls', namespace='fe')),
    # 銀行業務検定 法務3級 対策アプリ
    path('bk_houmu3/', include('bk_houmu3.urls', namespace='bk_houmu3')),
    # genba（1現場1リポジトリの記録を4つの立場で使う）
    path('genba/', include('genba.urls', namespace='genba')),
    # 社内活動の3つの構想（「社内活動 3つの構想.pptx」を順に読むページ）
    path('proposals/', include('proposals.urls', namespace='proposals')),
    # 福祉従事者の活動ページ（fun IT club WG-03。ログインなしで公開）
    path('welfare/', include('welfare_activity.urls', namespace='welfare_activity')),
]

# メディアファイルの配信設定
# 本番環境でも明示的にメディアファイルを配信する
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
else:
    # Azure App Serviceでの本番環境でもメディアファイルを配信
    from django.views.static import serve
    from django.urls import re_path
    
    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
    ]