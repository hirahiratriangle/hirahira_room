from django.test import TestCase

# Create your tests here.


from django.urls import reverse


class ClosedSignupTests(TestCase):
    """一般公開をやめたので、新規アカウント作成の画面はなく、お試しアカウントも見せない。"""

    def test_signup_is_gone(self):
        from accounts.models import CustomUser
        self.assertEqual(self.client.get('/accounts/signup/').status_code, 404)
        response = self.client.post('/accounts/signup/', {
            'username': 'newcomer', 'email': 'newcomer@example.com',
            'password1': 'a-Long-pass-2026', 'password2': 'a-Long-pass-2026',
        })
        self.assertEqual(response.status_code, 404)
        self.assertFalse(CustomUser.objects.filter(username='newcomer').exists())

    def test_login_page_hides_trial_account(self):
        html = self.client.get(reverse('account_login')).content.decode('utf-8')
        self.assertNotIn('お試しアカウント', html)
        self.assertNotIn('testuser', html)
        self.assertNotIn('/accounts/signup/', html)

    def test_ap_is_gone(self):
        self.assertEqual(self.client.get('/ap/').status_code, 404)

    def test_fe_stays_public(self):
        self.assertEqual(self.client.get(reverse('fe:index')).status_code, 200)
