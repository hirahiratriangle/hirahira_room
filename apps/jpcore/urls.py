from django.urls import path

from . import views

app_name = "jpcore"

urlpatterns = [
    path("", views.IndexView.as_view(), name="index"),
    path("materials/", views.MaterialsView.as_view(), name="materials"),
    # PPTX 公開配信（Office Online ビューアが取得するため認証なし・署名トークン必須）
    path("materials/public/<slug:slug>.pptx", views.PublicMaterialFileView.as_view(), name="material_public"),
    path("tool/", views.ToolView.as_view(), name="tool"),
]
