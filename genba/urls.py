from django.urls import path

from . import views

app_name = 'genba'

urlpatterns = [
    path('', views.PortalView.as_view(), name='portal'),
    # 現場テンプレートと言い回し集（全現場共通）
    path('kit/', views.KitView.as_view(), name='kit'),
    path('kit/phrases/', views.PhrasesView.as_view(), name='phrases'),
    # 立場ごとのホーム
    path('member/', views.MemberHomeView.as_view(), name='member_home'),
    path('leader/', views.LeaderHomeView.as_view(), name='leader_home'),
    path('management/', views.ManagementView.as_view(), name='management'),
    path('company/', views.CompanyView.as_view(), name='company'),
    # 現場（1現場＝1リポジトリ）
    path('sites/create/', views.SiteCreateView.as_view(), name='site_create'),
    path('sites/<int:site_pk>/', views.SiteDetailView.as_view(), name='site_detail'),
    path('sites/<int:site_pk>/archive/', views.SiteArchiveView.as_view(), name='site_archive'),
    path('sites/<int:site_pk>/summary/', views.SummaryView.as_view(), name='summary'),
    path('sites/<int:site_pk>/issues/create/<str:kind>/', views.IssueCreateView.as_view(), name='issue_create'),
    path('sites/<int:site_pk>/terms/create/', views.TermCreateView.as_view(), name='term_create'),
    path('sites/<int:site_pk>/promises/create/', views.PromiseCheckCreateView.as_view(), name='promise_create'),
    path('sites/<int:site_pk>/docs/<str:kind>/create/', views.DocumentCreateView.as_view(), name='document_create'),
    # Issue
    path('issues/<int:pk>/', views.IssueDetailView.as_view(), name='issue_detail'),
    path('issues/<int:pk>/toggle/', views.QuestionToggleView.as_view(), name='question_toggle'),
    path('checklist-items/<int:pk>/toggle/', views.ChecklistItemToggleView.as_view(), name='checklist_item_toggle'),
    # 文書
    path('docs/<int:pk>/', views.DocumentDetailView.as_view(), name='document_detail'),
    path('docs/<int:pk>/update/', views.DocumentUpdateView.as_view(), name='document_update'),
    # 公開レビュー（candidate / review / publish / unpublish / withdraw）
    path('knowledge/<str:model>/<int:pk>/<str:action>/', views.KnowledgeActionView.as_view(), name='knowledge_action'),
]
