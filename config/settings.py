# settings.py
from .settings_common import *
import os

# 本番環境設定
DEBUG = False
ALLOWED_HOSTS = ['*']
CSRF_TRUSTED_ORIGINS = ['https://hirahira-room.azurewebsites.net']

# Azure App Service 用の設定
if 'WEBSITE_HOSTNAME' in os.environ:
    ALLOWED_HOSTS.append(os.environ['WEBSITE_HOSTNAME'])

# 静的ファイルの設定
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# WhiteNoise の設定（オプション）
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

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
DEFAULT_FROM_EMAIL = 'Diary<EMAIL_HOST_USER>'
