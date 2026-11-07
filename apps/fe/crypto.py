"""問題メールの宛先を、暗号化して DB に持つための部品。funitclub の countdown/crypto.py と同じ作り。

メールを送るにはアドレスそのものが要るので、ハッシュではなく、元に戻せる暗号（Fernet。
AES と HMAC の組み合わせ）を使う。鍵は SECRET_KEY から作り、DB には置かない。
DB の中身だけが漏れても（バックアップ、SQL の抜き取りなど）アドレスは読めない。
アプリの設定（SECRET_KEY）まで一緒に取られた場合は防げない。

SECRET_KEY を替えるときは、前の値を SECRET_KEY_FALLBACKS に残しておけば読める。
残さずに替えると読めなくなり、その宛先は「未登録」として扱う（登録し直してもらう）。
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings

_SALT = b'fe.mail_email'


def _fernet():
    keys = [settings.SECRET_KEY, *settings.SECRET_KEY_FALLBACKS]
    return MultiFernet([
        Fernet(base64.urlsafe_b64encode(hashlib.sha256(_SALT + key.encode()).digest()))
        for key in keys
    ])


def encrypt(text):
    """暗号文。同じ文字列でも毎回違う値になる。空は空のまま。"""
    if not text:
        return ''
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token):
    """元の文字列。空や、読めない暗号文（鍵が替わった・壊れている）は空。"""
    if not token:
        return ''
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return ''
