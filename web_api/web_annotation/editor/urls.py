from django.urls import re_path

from web_annotation.editor import views


urlpatterns = [
    re_path(r"api/editor/annotator_types/?", views.AnnotatorTypes.as_view()),
    re_path(r"api/editor/annotator_config/?", views.AnnotatorConfig.as_view()),
    re_path(r"api/editor/pipeline_attributes/?", views.PipelineAttributes.as_view()),
    re_path(r"api/editor/annotator_attributes/?", views.AnnotatorAttributes.as_view()),
    re_path(r"api/editor/annotator_yaml/?", views.AnnotatorYAML.as_view()),
    re_path(r"api/editor/resource_annotators/?", views.ResourceAnnotators.as_view()),
    re_path(r"api/editor/pipeline_status/?", views.PipelineStatus.as_view()),
]
