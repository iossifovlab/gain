from django.urls import path

from admin_panel import views

urlpatterns = [
    path("admin-panel/reset-daily-quota", views.reset_daily_quota),
    path("admin-panel/reset-monthly-quota", views.reset_monthly_quota),
    path("admin-panel/set-extra-quota", views.set_extra_quota),
    path("admin-panel/set-current-quota", views.set_current_quota),
]
