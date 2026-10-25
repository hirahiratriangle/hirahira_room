from django.urls import path

from . import views

app_name = 'genba'

urlpatterns = [
    path('', views.PortalView.as_view(), name='portal'),
    # 立場ごとのホーム
    path('member/', views.MemberHomeView.as_view(), name='member_home'),
    path('leader/', views.LeaderHomeView.as_view(), name='leader_home'),
    path('management/', views.ManagementView.as_view(), name='management'),
    path('company/', views.CompanyView.as_view(), name='company'),
    # 現場（1現場＝1リポジトリ）
    path('sites/<int:site_pk>/', views.SiteDetailView.as_view(), name='site_detail'),
    path('sites/<int:site_pk>/questions/create/', views.QuestionCreateView.as_view(), name='question_create'),
    path('sites/<int:site_pk>/terms/create/', views.TermCreateView.as_view(), name='term_create'),
    path('sites/<int:site_pk>/promises/create/', views.PromiseCheckCreateView.as_view(), name='promise_create'),
    path('sites/<int:site_pk>/docs/<str:kind>/create/', views.DocumentCreateView.as_view(), name='document_create'),
    path('questions/<int:pk>/toggle/', views.QuestionToggleView.as_view(), name='question_toggle'),
    path('docs/<int:pk>/', views.DocumentDetailView.as_view(), name='document_detail'),
    path('docs/<int:pk>/update/', views.DocumentUpdateView.as_view(), name='document_update'),
    # 公開レビュー（candidate / review / publish / unpublish / withdraw）
    path('knowledge/<str:model>/<int:pk>/<str:action>/', views.KnowledgeActionView.as_view(), name='knowledge_action'),
]
