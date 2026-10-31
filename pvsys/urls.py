from django.contrib.auth.decorators import login_not_required
from django.urls import path

from . import views

app_name = 'pvsys'

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
]

# リンク先の genba・3つの構想・基本情報・福祉従事者の活動ページはどれもログインなしで開けるので、入り口も同じにする
for pattern in urlpatterns:
    login_not_required(pattern.callback)
