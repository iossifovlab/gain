from django.urls import path
from web_annotation.pipelines import views

urlpatterns = [
    path('api/pipelines/validate', views.PipelineValidation.as_view()),
    path("api/pipelines/load", views.LoadPipeline.as_view()),
    path("api/pipelines/user", views.UserPipeline.as_view()),
    path("api/pipelines", views.ListPipelines.as_view()),
]
