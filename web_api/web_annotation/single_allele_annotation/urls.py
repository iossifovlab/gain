"""Module for single allele URLs."""
from django.urls.conf import path, re_path

from web_annotation.single_allele_annotation import views

urlpatterns = [
    path("api/single_allele/annotate", views.SingleAnnotation.as_view()),
    path("api/single_allele/history", views.AlleleHistory.as_view()),
    path("api/single_allele/note", views.UpdateAlleleNote.as_view()),
    re_path(
        r"api/single_allele/histograms/(?P<resource_id>.+)",
        views.HistogramView.as_view(),
    ),
]
