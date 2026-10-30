from allauth.account.adapter import DefaultAccountAdapter


class ClosedSignupAdapter(DefaultAccountAdapter):
    """アカウントの新規作成を止める allauth のアダプタ。

    サイトの一般公開をやめたため、誰でもアカウントを作れる状態をなくす。
    画面（/accounts/signup/）は config/urls.py で外してあり、これは二重の止め。
    アカウントが要るときは、管理画面（/admin/）か createsuperuser で作る。
    ログインやパスワード再設定など、既存のアカウントの操作はそのまま使える。
    """

    def is_open_for_signup(self, request):
        return False
