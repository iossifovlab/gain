from django.urls import path

from web_annotation.jobs import views


urlpatterns = [
    path('api/jobs', views.JobList.as_view()),
    path('api/jobs/all', views.JobAll.as_view()),
    path('api/jobs/annotate_columns', views.AnnotateColumns.as_view()),
    path('api/jobs/annotate_vcf', views.AnnotateVCF.as_view()),
    path('api/jobs/<int:pk>/file/<str:file>', views.JobGetFile.as_view()),
    path('api/jobs/<int:pk>', views.JobDetail.as_view()),
    path('api/jobs/validate_columns', views.ColumnValidation.as_view()),
    path(
        'api/jobs/preview',
        views.PreviewFileUpload.as_view(),
    ),
    path(
        'api/jobs/genomes',
        views.ListGenomePipelines.as_view(),
    ),
]
