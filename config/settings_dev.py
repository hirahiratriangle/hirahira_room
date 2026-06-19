import os

from .settings_common import *


# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'django-insecure-uxe5-)2zw#&-_vo04i5v20*q6s37%fjgbz&5c$@9mklu^v-)%5'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = 'True'

# 既定はローカルのみ許可。トンネル(ngrok/cloudflared)経由で Office ビューアの
# PPTX 表示を検証する場合は、環境変数 EXTRA_ALLOWED_HOSTS にトンネルのホスト名を
# カンマ区切りで指定する（例: EXTRA_ALLOWED_HOSTS=xxxx.ngrok-free.app）。
ALLOWED_HOSTS = ['localhost', '127.0.0.1', '[::1]']
_extra_hosts = os.environ.get('EXTRA_ALLOWED_HOSTS', '').strip()
if _extra_hosts:
    ALLOWED_HOSTS += [h.strip() for h in _extra_hosts.split(',') if h.strip()]

# トンネル経由(HTTPS)でツールの模擬実行フォーム(POST)を使う場合に必要。
# 例: CSRF_TRUSTED_ORIGINS=https://xxxx.ngrok-free.app
CSRF_TRUSTED_ORIGINS = []
_csrf = os.environ.get('CSRF_TRUSTED_ORIGINS', '').strip()
if _csrf:
    CSRF_TRUSTED_ORIGINS += [o.strip() for o in _csrf.split(',') if o.strip()]

# トンネル(ngrok/cloudflared)経由のとき、転送元の https を Django に正しく
# 認識させ、Office ビューアへ渡す PPTX のURLを https で生成させる。
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# データベース設定（開発環境ではSQLiteを使用）
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# ロギング設定
LOGGING = {
    'version': 1,  # 1固定
    'disable_existing_loggers': False,

    # ロガーの設定
    'loggers': {
        # Djangoが利用するロガー
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
        },
        # diaryアプリケーションが利用するロガー
        'diary': {
            'handlers': ['console'],
            'level': 'DEBUG',
        },
        #　iris_classifierアプリケーションが利用するロガー 
        'iris_classifier': {
            'handlers': ['console'],
            'level': 'DEBUG',
        },
    },

    # ハンドラの設定
    'handlers': {
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'dev'
        },
    },

    # フォーマッタの設定
    'formatters': {
        'dev': {
            'format': '\t'.join([
                '%(asctime)s',
                '[%(levelname)s]',
                '%(pathname)s(Line:%(lineno)d)',
                '%(message)s'
            ])
        },
    }
}

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

MEDIA_ROOT = BASE_DIR / 'media'
