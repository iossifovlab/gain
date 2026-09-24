from typing import ClassVar

from rest_framework.views import Request, Response, status

from web_annotation.annotation_base_view import AnnotationBaseView
from web_annotation.authentication import WebAnnotationAuthentication
from web_annotation.models import Quota, QuotaSnapshot


def _period(snapshot: QuotaSnapshot, counter: str) -> dict[str, int]:
    """Report one period counter: units remaining and the limit it is under."""
    return {
        "current": getattr(snapshot, counter),
        "max": snapshot.limit_for(counter),
    }


class QuotasView(AnnotationBaseView):
    """View to get the quotas for the current user."""

    authentication_classes: ClassVar = [WebAnnotationAuthentication]

    def get(self, request: Request) -> Response:
        """Get the quotas for the current user.

        One entry per resource in ``Quota.RESOURCE_FIELDS``, read off the
        user's snapshot through the columns that resource declares. A
        declared resource the snapshot does not carry fails the request
        rather than being left out of it.
        """
        snapshot = request.user.get_quota()
        quotas = {
            resource: {
                "daily": _period(snapshot, daily),
                "monthly": _period(snapshot, monthly),
                "extra": getattr(snapshot, extra),
            }
            for resource, (daily, monthly, extra)
            in Quota.RESOURCE_FIELDS.items()
        }
        return Response(quotas, status=status.HTTP_200_OK)
