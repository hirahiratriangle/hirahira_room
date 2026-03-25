from django.urls import path
from . import views

app_name = 'iris_classifier'

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
    path('predict/', views.PredictView.as_view(), name='predict'),
    path('history/', views.HistoryView.as_view(), name='history'),
    path('analysis/', views.AnalysisView.as_view(), name='analysis'),
    path('retrain/', views.RetrainModelView.as_view(), name='retrain'),
    path('download-dataset/', views.DownloadDatasetView.as_view(), name='download_dataset'),
]
