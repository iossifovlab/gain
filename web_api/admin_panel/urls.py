from django.urls import path

from admin_panel import views

urlpatterns = [
    path("admin-panel/reset-daily-quota", views.ResetDailyQuotaView.as_view()),
    path(
        "admin-panel/reset-monthly-quota",
        views.ResetMonthlyQuotaView.as_view(),
    ),
    path("admin-panel/set-extra-quota", views.SetExtraQuotaView.as_view()),
    path("admin-panel/set-current-quota", views.SetCurrentQuotaView.as_view()),
    path("admin-panel/set-session-quota", views.SetSessionQuotaView.as_view()),
    path("admin-panel/set-ip-quota", views.SetIpQuotaView.as_view()),
    path(
        "admin-panel/delete-anonymous-jobs",
        views.DeleteAnonymousJobsView.as_view(),
    ),
]
