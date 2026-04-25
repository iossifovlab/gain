from django.urls import re_path

from web_annotation.resources import views

urlpatterns = [
    re_path(r"api/resources/types/?", views.ResourceTypes.as_view()),
    re_path(r"api/resources/search?", views.SearchResources.as_view()),
    re_path(r"api/resources/?", views.Resources.as_view()),
]
