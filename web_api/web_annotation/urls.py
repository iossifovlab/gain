"""
URL configuration for gpf_web_annotation project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from collections.abc import Sequence
from typing import cast

from django.urls import URLResolver, include, path, re_path

from web_annotation import views
from web_annotation.consumers import AnnotationStateConsumer
from web_annotation.editor.urls import (
    urlpatterns as editor_urls,
)
from web_annotation.jobs.urls import urlpatterns as job_urls
from web_annotation.pipelines.urls import urlpatterns as pipeline_urls
from web_annotation.quotas.urls import (
    urlpatterns as quotas_urls,
)
from web_annotation.resources.urls import (
    urlpatterns as resources_urls,
)
from web_annotation.single_allele_annotation.urls import (
    urlpatterns as single_allele_urls,
)

urlpatterns = [
    path("api-auth", include("rest_framework.urls")),

    *job_urls,
    *single_allele_urls,
    *pipeline_urls,
    *resources_urls,
    *editor_urls,
    *quotas_urls,

    path("api/about", views.AboutPage.as_view()),

    path("api/users", views.UserList.as_view()),
    path("api/users/<int:pk>", views.UserDetail.as_view()),

    path("api/login", views.Login.as_view()),
    path("api/logout", views.Logout.as_view()),
    path("api/register", views.Registration.as_view()),
    path("api/user_info", views.UserInfo.as_view()),
    path("api/confirm_account", views.ConfirmAccount.as_view()),
    path(
        "api/forgotten_password",
        views.ForgotPassword.as_view(),
        name="forgotten_password",
    ),
    path(
        "api/reset_password",
        views.PasswordReset.as_view(),
        name="reset_password",
    ),
]

websocket_urlpatterns = [
    re_path(
        r"ws/notifications/?$",
        cast(Sequence[URLResolver], AnnotationStateConsumer.as_asgi()),
    ),
]
