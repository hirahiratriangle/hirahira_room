from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = 'fe'

urlpatterns = [
    # ID なしの入口。そのままダッシュボードになる（ID で入っていれば /fe/mypage/ へ送る）
    path('', views.IndexView.as_view(), name='index'),
    # ID ありの入口。ID で入る・作る・切り替える。入っていればここがダッシュボード
    path('mypage/', views.MyPageView.as_view(), name='mypage'),
    # このアプリについて（試験の概要・使い方・対応していない問題・ID と記録）
    path('guide/', views.GuideView.as_view(), name='guide'),
    path('quiz/', views.QuizView.as_view(), name='quiz'),
    path('quiz/settings/', views.QuizSettingsView.as_view(), name='quiz_settings'),
    # 学習モード（先に解説を読み、そのあと同じ範囲を解く）
    path('learn/', views.LearnStartView.as_view(), name='learn_start'),
    path('learn/<int:pk>/read/', views.LearnReadView.as_view(), name='learn_read'),
    # 読み上げを再生している間は、読む時間を止める
    path('learn/<int:pk>/timer/', views.LearnTimerView.as_view(), name='learn_timer'),
    path('learn/audio/<str:name>', views.NoteAudioView.as_view(), name='note_audio'),
    path('learn/<int:pk>/quiz/', views.LearnQuizView.as_view(), name='learn_quiz'),
    path('learn/<int:pk>/next/', views.LearnNextView.as_view(), name='learn_next'),
    path('learn/<int:pk>/abandon/', views.LearnAbandonView.as_view(), name='learn_abandon'),
    path('learn/<int:pk>/result/', views.LearnResultView.as_view(), name='learn_result'),

    path('stats/', views.StatsView.as_view(), name='stats'),
    path('history/', views.HistoryView.as_view(), name='history'),
    # 本番合格の申告（集計はダッシュボードに出す）
    path('pass/', views.PassReportView.as_view(), name='pass_report'),

    # 問題メール（毎日0時に届く。登録はダッシュボードから、確定と解除はメールのリンクから）
    path('mail/', views.MailSettingsView.as_view(), name='mail_settings'),
    path('mail/confirm/<str:token>/', views.MailConfirmView.as_view(), name='mail_confirm'),
    path('mail/stop/<str:token>/', views.MailStopView.as_view(), name='mail_stop'),
    path('tasks/send-mails/', views.SendMailsView.as_view(), name='send_mails'),

    # X への投稿（朝・昼・夜に1問）と、投稿ごとの解答ページ（ID なしで誰でも開ける）
    path('tasks/post-x/', views.PostXView.as_view(), name='post_x'),
    path('x/<str:code>/', views.XAnswerView.as_view(), name='x_answer'),

    # 問題の管理（全 ID で共有する問題集。学習の画面とは別ページで、スタッフのログインが要る。
    # 登録は JSON の一括差し替えで行う）
    path('console/', RedirectView.as_view(pattern_name='fe:manage_list'), name='console'),
    path('console/questions/', views.ManageListView.as_view(), name='manage_list'),
    path('console/upload/', views.QuestionUploadView.as_view(), name='manage_upload'),
    # 学習の画面のページビュー（直近30日）
    path('console/pageviews/', views.PageViewListView.as_view(), name='manage_pageviews'),
]
