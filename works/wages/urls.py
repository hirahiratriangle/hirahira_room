from django.contrib.auth.decorators import login_not_required
from django.urls import path

from . import views

app_name = 'wages'

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
]

# 各自で読んでもらう資料なので、3つの構想と同じくアカウントを持たない人も開けるようにする
for pattern in urlpatterns:
    login_not_required(pattern.callback)
