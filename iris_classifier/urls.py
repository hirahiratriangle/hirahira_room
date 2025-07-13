from django.urls import path
from . import views

app_name = 'iris_classifier'

urlpatterns = [
    path('', views.home, name='home'),
    path('predict/', views.predict, name='predict'),
    path('analysis/', views.analysis, name='analysis'),
]
