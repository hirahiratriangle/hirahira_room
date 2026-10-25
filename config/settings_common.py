# config/settings_common.py

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY', 'your-default-secret-key')
ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    # Django 本体
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'django_bootstrap5',  
    
    # 自作アプリ
    'accounts',
    'home',
    'diary',
    'iris_classifier',
    'jpcore',
    'countdown',
    'fe',
    'ap',
    'bk_houmu3',
    'genba',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    # 既定で全ページをログイン必須にする。公開したいビューだけ
    # django.contrib.auth.decorators.login_not_required を付けて除外する
    # （allauth のログイン/サインアップ等と admin のログイン画面は除外済み）。
    'django.contrib.auth.middleware.LoginRequiredMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'allauth.account.middleware.AccountMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            BASE_DIR / 'templates',
            BASE_DIR / 'accounts' / 'templates',
        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# DATABASES = {
#     'default': {
#         'ENGINE': 'django.db.backends.postgresql',
#         'NAME': BASE_DIR / 'db.sqlite3',
#     }
# }

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME'),
        'USER': os.environ.get('DB_USER'),
        'PASSWORD': os.environ.get('DB_PASSWORD'),
        'HOST': os.environ.get('DB_HOST'),
    }   
}


AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LANGUAGE_CODE = 'ja'
TIME_ZONE = 'Asia/Tokyo'
USE_I18N = True
USE_L10N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [
    BASE_DIR / 'static',
]

# ==================================
# django-allauth セットアップ
# ==================================

INSTALLED_APPS += [
    'django.contrib.sites',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
]
SITE_ID = 1

AUTHENTICATION_BACKENDS = (
    'django.contrib.auth.backends.ModelBackend',              # デフォルト
    'allauth.account.auth_backends.AuthenticationBackend',    # allauth
)

# ログイン／ログアウト後のリダイレクト先
LOGIN_REDIRECT_URL = 'home:home'
LOGOUT_REDIRECT_URL = 'account_login'
LOGIN_URL = 'account_login'

# メディアファイル設定
MEDIA_URL = '/media/'

# FE の技術解説の読み上げ音声（manage.py make_note_audio で作る）
FE_AUDIO_ROOT = BASE_DIR / 'media' / 'fe_audio'

# 法務3級の問題と解説の読み上げ音声（manage.py make_question_audio で作る）
BK_HOUMU3_AUDIO_ROOT = BASE_DIR / 'media' / 'bk_houmu3_audio'

# カスタムユーザーモデルの指定
AUTH_USER_MODEL = 'accounts.CustomUser'

# ログアウト設定
ACCOUNT_LOGOUT_ON_GET = False  # GETでログアウトを無効化（セキュリティ向上）
ACCOUNT_LOGOUT_REDIRECT_URL = 'account_login'  # ログアウト後のリダイレクト先

# ==================================
# サイト名（ブラウザのタイトル・送信メールの差出人／件名で使用）
# ==================================
SITE_DISPLAY_NAME = 'HirahiraRoom'

# メールに書くリンクの頭。メールの送信はリクエストの外から起きるので、設定で持つ。
# 独自ドメイン（App Service ドメインで取得）。azurewebsites.net のアドレスも引き続き使える。
SITE_URL = os.environ.get('SITE_URL', 'https://hirahira-room.net')

# 差出人の既定値。本番では settings.py が Gmail アドレスを埋めて上書きする。
DEFAULT_FROM_EMAIL = f'{SITE_DISPLAY_NAME} <no-reply@hirahira-room.local>'
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# allauth のメール件名の接頭辞。未指定だと Site の名前（例: [example.com]）が
# 使われてしまうため明示する。
ACCOUNT_EMAIL_SUBJECT_PREFIX = f'[{SITE_DISPLAY_NAME}] '

# ==================================
# カウントダウンのリマインド通知
# ==================================
# 定期実行（GitHub Actions）から送信を起動するときの共有トークン。
# 未設定ならエンドポイントは 503 を返し、何も送らない。
COUNTDOWN_REMINDER_TOKEN = os.environ.get('COUNTDOWN_REMINDER_TOKEN', '')

# ---- 基本情報の問題メール（fe/mail.py） ----
# 毎日0時の送信を起こす呼び出し（/fe/tasks/send-mails/）の共有トークン。
# 未設定なら入口は 503 を返し、何も送らない。GitHub の Secrets の FE_MAIL_TOKEN と同じ値。
FE_MAIL_TOKEN = os.environ.get('FE_MAIL_TOKEN', '')
# メールに書くリンクの頭（既定は SITE_URL）
FE_SITE_URL = os.environ.get('FE_SITE_URL', SITE_URL)
# 確認のメールのリンクの有効期限（秒）
FE_MAIL_CONFIRM_MAX_AGE = 60 * 60
# 確認・解除のメールを、同じ ID から続けて送らせない間隔（秒）
FE_MAIL_LINK_INTERVAL = 5 * 60

# ---- 基本情報の X 投稿（fe/x_post.py） ----
# 朝・昼・夜の投稿を起こす呼び出し（/fe/tasks/post-x/）の共有トークン。
# 未設定なら入口は 503 を返し、何も投稿しない。GitHub の Secrets の FE_X_TOKEN と同じ値。
FE_X_TOKEN = os.environ.get('FE_X_TOKEN', '')
# X API のキー（OAuth 1.0a のユーザーコンテキスト）
X_API_KEY = os.environ.get('X_API_KEY', '')
X_API_SECRET = os.environ.get('X_API_SECRET', '')
X_ACCESS_TOKEN = os.environ.get('X_ACCESS_TOKEN', '')
X_ACCESS_SECRET = os.environ.get('X_ACCESS_SECRET', '')
# True なら X に投稿せず、文面をログに出すだけにする。本番だけ False にする。
FE_X_DRY_RUN = os.environ.get('FE_X_DRY_RUN', 'True') == 'True'
# 解答ページを出しておく日数。過ぎたら投稿の記録を行ごと消し、案内ページにする。
FE_X_KEEP_DAYS = 30


# ==================================
# アップロードの上限
# ==================================
# FE 対策アプリの問題 JSON は、技術解説を含めると 1MB を超える。
# 小分類 96 本ぶんの解説（各 3000 字）と問題 200 問で 1.1MB になる実測がある。
# これを受け取れる大きさにし、取り違えを疑う線として上限も置く。
# 実際に弾くのはこの値で、fe.forms がここを見る。
FE_MAX_UPLOAD_BYTES = 8 * 1024 * 1024

# 応用情報（AP）対策アプリの上限。ap.forms がここを見る。考え方は FE と同じ。
AP_MAX_UPLOAD_BYTES = 8 * 1024 * 1024

# 銀行業務検定 法務3級 対策アプリの上限。bk_houmu3.forms がここを見る。考え方は FE と同じ。
BK_HOUMU3_MAX_UPLOAD_BYTES = 8 * 1024 * 1024

# ファイル本体は multipart で送られ、この値の対象外になる（Django の仕様）。
# ここで効くのは、本文に JSON をそのまま載せて POST したときだけ。
# 画面からのアップロードには関係しないが、上の上限と食い違わないよう合わせておく。
DATA_UPLOAD_MAX_MEMORY_SIZE = max(FE_MAX_UPLOAD_BYTES, AP_MAX_UPLOAD_BYTES, BK_HOUMU3_MAX_UPLOAD_BYTES) + 1024 * 1024

# FILE_UPLOAD_MAX_MEMORY_SIZE は拒否の上限ではなく、メモリに載せるか
# 一時ファイルに流すかの分かれ目。既定（2.5MB）のままにしておく。
# 大きいファイルをメモリに抱えないほうが、B1 の本番では都合がよい。
