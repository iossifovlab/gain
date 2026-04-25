from django.urls import re_path

from web_annotation.quotas import views

urlpatterns = [
    re_path(r"api/quotas/?", views.QuotasView.as_view()),
]
