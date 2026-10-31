from django.contrib.auth.decorators import login_not_required
from django.urls import path

from . import views

app_name = 'proposals'

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
]

# 社内の人が各自で読むページで、アカウントを持たない人も開けるようログインを外す（リンク先の genba と fe も同じ）
for pattern in urlpatterns:
    login_not_required(pattern.callback)
