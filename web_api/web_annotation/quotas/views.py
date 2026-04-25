

from typing import ClassVar

from rest_framework.views import Request, Response, status

from web_annotation.annotation_base_view import AnnotationBaseView
from web_annotation.authentication import WebAnnotationAuthentication


class QuotasView(AnnotationBaseView):
    """View to get the quotas for the current user."""

    authentication_classes: ClassVar = [WebAnnotationAuthentication]

    def get(self, request: Request) -> Response:
        """Get the quotas for the current user."""
        quota = request.user.get_quota()
        quotas = {
            "variants": {
                "daily": {
                    "current": quota.daily_variants,
                    "max": quota.get_daily_variant_max(),
                },
                "monthly": {
                    "current": quota.monthly_variants,
                    "max": quota.get_monthly_variant_max(),
                },
                "extra": quota.extra_variants,
            },
            "attributes": {
                "daily": {
                    "current": quota.daily_attributes,
                    "max": quota.get_daily_attribute_max(),
                },
                "monthly": {
                    "current": quota.monthly_attributes,
                    "max": quota.get_monthly_attribute_max(),
                },
                "extra": quota.extra_attributes,
            },
            "jobs": {
                "daily": {
                    "current": quota.daily_jobs,
                    "max": quota.get_daily_job_max(),
                },
                "monthly": {
                    "current": quota.monthly_jobs,
                    "max": quota.get_monthly_job_max(),
                },
                "extra": quota.extra_jobs,
            },
            "single_variant_queries": {
                "daily": {
                    "current": quota.daily_allele_queries,
                    "max": quota.get_daily_allele_query_max(),
                },
                "monthly": {
                    "current": quota.monthly_allele_queries,
                    "max": quota.get_monthly_allele_query_max(),
                },
                "extra": quota.extra_allele_queries,
            },
        }
        return Response(quotas, status=status.HTTP_200_OK)
