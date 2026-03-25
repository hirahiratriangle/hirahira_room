from django.contrib import admin
from django.contrib.staticfiles.urls import static
from django.urls import path, include
from django.conf import settings

urlpatterns = [
    path('admin/', admin.site.urls),

    # 認証（django-allauth）
    path('accounts/', include('allauth.urls')),

    # ホーム画面（ログイン必須）
    path('', include('home.urls', namespace='home')),

    # 日記アプリ
    path('diary/', include('diary.urls', namespace='diary')),

    # Iris Classifier アプリ
    path('iris/', include('iris_classifier.urls', namespace='iris_classifier')),
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