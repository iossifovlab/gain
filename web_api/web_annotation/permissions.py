"""Custom permissions for job access in the web annotation system."""
from typing import Any
from rest_framework import permissions
from rest_framework.views import APIView, Request

from web_annotation.models import Job, User


def has_job_permission(job: Job, user: User) -> bool:
    """Check if the user has permission to access the job."""
    return job.owner == user.as_owner


class IsOwner(permissions.BasePermission):
    """Custom permission to only allow owners of a job to access it."""
    def has_object_permission(
        self, request: Request, view: APIView, obj: Any,
    ) -> bool:
        return has_job_permission(obj, request.user)
