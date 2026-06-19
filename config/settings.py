# settings.py
from .settings_common import *
import os

# 本番環境設定
DEBUG = os.getenv("DEBUG", "False") == "True"

# WhiteNoise を有効化
# runserver_nostatic を先頭に
INSTALLED_APPS.insert(0, "whitenoise.runserver_nostatic")
# SecurityMiddleware の直後に WhiteNoiseMiddleware を挿入
idx = MIDDLEWARE.index("django.middleware.security.SecurityMiddleware")
MIDDLEWARE.insert(idx + 1, "whitenoise.middleware.WhiteNoiseMiddleware")
# 圧縮＋ハッシュ付きで配信
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

ALLOWED_HOSTS = ['*']
CSRF_TRUSTED_ORIGINS = ['https://hirahira-room.azurewebsites.net']

# Azure App Service 用の設定
if 'WEBSITE_HOSTNAME' in os.environ:
    ALLOWED_HOSTS.append(os.environ['WEBSITE_HOSTNAME'])
    _origin = f"https://{os.environ['WEBSITE_HOSTNAME']}"
    if _origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(_origin)

# Azure はフロントで HTTPS を終端し X-Forwarded-Proto で転送する。
# これを認識させることで request.build_absolute_uri() / is_secure() が
# 正しく https になり、Office Online ビューアへ渡す PPTX の公開URLも https で生成される。
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# 静的ファイルの設定
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# メディアファイル設定
MEDIA_ROOT = '/home/site/wwwroot/media'

# ロギング設定（Azure用に修正）
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'level': 'INFO',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'diary': {
            'handlers': ['console'],
            'level': 'INFO',
        },
    },
}

# 送信メール設定(Gmail)
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True

EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER')  # Gmail アドレス
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD')  # アプリパスワード
DEFAULT_FROM_EMAIL = 'Hirahira Room<EMAIL_HOST_USER>'  # 送信元メールアドレス
