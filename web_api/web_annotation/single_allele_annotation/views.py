"""Module for single allele annotation views."""
from datetime import datetime
from typing import Any, cast

from gain.annotation.record_to_annotatable import build_annotatable_from_dict
from gain.annotation.annotation_config import AttributeInfo
from gain.annotation.annotation_pipeline import Annotator
from gain.annotation.gene_score_annotator import GeneScoreAnnotator
from gain.annotation.score_annotator import GenomicScoreAnnotatorBase
from gain.gene_scores.gene_scores import (
    _build_gene_score_help,
    build_gene_score_from_resource,
)
from gain.genomic_resources.genomic_scores import build_score_from_resource
from gain.genomic_resources.histogram import (
    Histogram,
    NullHistogram,
    NullHistogramConfig,
)
from gain.genomic_resources.repository import GenomicResource
from django.conf import settings
from django.db.models import QuerySet
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.http import last_modified
from rest_framework import generics, permissions, views
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import Request, Response

from web_annotation.annotation_base_view import AnnotationBaseView
from web_annotation.authentication import WebAnnotationAuthentication
from web_annotation.models import AlleleQuery, BaseUser, User
from web_annotation.serializers import AlleleSerializer


def get_histogram_genomic_score(
    resource: GenomicResource, score_id: str,
) -> tuple[Histogram, dict[str, Any]]:
    """Get histogram and extra data for a genomic score."""
    if resource.get_type() not in [
        "allele_score", "position_score",
    ]:
        raise ValueError(f"{resource.resource_id} is not a genomic score!")
    score = build_score_from_resource(resource)
    score_def = score.score_definitions[score_id]
    return (
        score.get_score_histogram(score_id),
        {
            "small_values_desc": score_def.small_values_desc,
            "large_values_desc": score_def.large_values_desc,
        },
    )


def get_histogram_gene_score(
    resource: GenomicResource, score_id: str,
) -> tuple[Histogram, dict[str, Any]]:
    """Get histogram and extra data for a gene score."""
    if resource.get_type() != "gene_score":
        raise ValueError(f"{resource.resource_id} is not a genomic score!")
    score = build_gene_score_from_resource(resource)
    score_def = score.score_definitions[score_id]
    return (
        score.get_score_histogram(score_id),
        {
            "small_values_desc": score_def.small_values_desc,
            "large_values_desc": score_def.large_values_desc,
        },
    )


def get_histogram_not_supported(
    _resource: GenomicResource, _score: str,  # pylint: disable=unused-argument
) -> tuple[Histogram, dict[str, Any]]:
    """Return an empty histogram for unsupported resources."""
    return (NullHistogram(NullHistogramConfig("not supported")), {})


HISTOGRAM_GETTERS = {
    "allele_score": get_histogram_genomic_score,
    "position_score": get_histogram_genomic_score,
    "gene_score": get_histogram_gene_score,
}


def has_histogram(resource: GenomicResource, score: str) -> bool:
    """Check if a resource has a histogram for a score."""
    histogram_getter = HISTOGRAM_GETTERS.get(
        resource.get_type(), get_histogram_not_supported,
    )
    histogram, _details = histogram_getter(resource, score)
    return not isinstance(histogram, NullHistogram)


STARTUP_TIME = timezone.now()


def always_cache(
    *_args: list[Any], **_kwargs: dict[str, Any],
) -> datetime:
    """Function to enable a view to always be cached, due to static data."""
    return STARTUP_TIME


class SingleAnnotation(AnnotationBaseView):
    """Single annotation view."""

    throttle_classes = [UserRateThrottle]
    authentication_classes = [WebAnnotationAuthentication]

    def generate_annotator_help(
        self,
        annotator: Annotator,
        attribute_info: AttributeInfo,
    ) -> str | None:
        """Generate annotator help for gene scores and genomic scores"""
        if not isinstance(
            annotator, (GeneScoreAnnotator, GenomicScoreAnnotatorBase),
        ):
            return None

        if isinstance(annotator, GenomicScoreAnnotatorBase):
            assert isinstance(annotator, GenomicScoreAnnotatorBase)
            return annotator.build_attribute_help(attribute_info)

        assert isinstance(annotator, GeneScoreAnnotator)
        for score_def in annotator.score.score_definitions.values():
            if score_def.score_id == attribute_info.source:
                return _build_gene_score_help(
                    score_def,
                    annotator.score,
                )
        return None

    def post(self, request: Request) -> Response:
        """View for single annotation"""

        assert isinstance(request.data, dict)
        if "annotatable" not in request.data:
            return Response(
                {"reason": "Annotatable not provided!"},
                status=views.status.HTTP_400_BAD_REQUEST,
            )
        annotatable = request.data["annotatable"]
        assert isinstance(annotatable, dict)

        if "pipeline_id" not in request.data:
            return Response(
                {"reason": "Pipeline not provided!"},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        pipeline_id = request.data["pipeline_id"]
        if not isinstance(pipeline_id, str):
            return Response(
                {"reason": "Invalid pipeline provided!"},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        pipeline = self.get_pipeline(pipeline_id, request.user)

        attributes_count = sum(
            1 for annotator in pipeline.annotators
            for attr in annotator.attributes
            if not attr.internal
        )

        quota = request.user.get_quota()
        if not quota.single_allele_allowed(attributes_count):
            return Response(
                {"reason": "Single allele query quota exceeded!"},
                status=views.status.HTTP_403_FORBIDDEN,
            )

        annotatable = build_annotatable_from_dict(annotatable)

        annotation = pipeline.annotate(annotatable, {})

        annotators_data = []
        if (
            getattr(settings, "RESOURCES_BASE_URL") is None
            or settings.RESOURCES_BASE_URL is None
        ):
            base_url = ""
        else:
            base_url = settings.RESOURCES_BASE_URL

        for annotator in pipeline.annotators:
            details = {}
            attributes = []
            annotator_info = annotator.get_info()
            annotator_resources = []
            for resource in annotator_info.resources:
                url = f"{base_url}{resource.resource_id}/index.html"
                annotator_resources.append({
                    "resource_id": resource.resource_id,
                    "resource_url": url,
                })
            details = {
                "name": annotator_info.type,
                "description": annotator_info.documentation,
                "resources": annotator_resources,
            }
            for attribute_info in annotator.attributes:
                if attribute_info.internal:
                    continue
                attributes.append(
                    self._build_attribute_description(
                        annotation, annotator,
                        attribute_info)
                )
            if len(attributes) == 0:
                continue
            annotators_data.append(
                {"details": details, "attributes": attributes},
            )

        if (
            request.user.is_authenticated
            and isinstance(request.user, BaseUser)
        ):
            allele = str(annotatable)
            allele_query = AlleleQuery.objects.filter(
                allele=allele,
                owner=request.user.as_owner,
            ).first()
            if allele_query is None:
                allele_query = AlleleQuery(
                    allele=allele,
                    owner=request.user.as_owner,
                )
            else:
                allele_query.last_used = timezone.now()
            allele_query.save()

        quota.single_allele_query_complete(attributes_count)

        response_data = {
            "annotatable": annotatable.to_dict(),
            "annotators": annotators_data,
        }

        return Response(response_data)

    def _build_attribute_description(
            self, result: dict[str, Any], annotator: Annotator,
            attribute_info: AttributeInfo,
    ) -> dict[str, Any]:
        resource = self.grr.get_resource(
                    list(annotator.resource_ids)[0])
        if has_histogram(resource, attribute_info.source):
            histogram_path = (
                        f"histograms/{resource.resource_id}"
                        f"?score_id={attribute_info.source}"
                    )
        else:
            histogram_path = None
        value = result[attribute_info.name]

        annotator_help = self.generate_annotator_help(
                    annotator,
                    attribute_info,
                )

        if attribute_info.value_type in ["object", "annotatable"]:
            if not isinstance(value, (dict, list)):
                value = str(value)
        return {
            "name": attribute_info.name,
            "description": attribute_info.description,
            "help": annotator_help,
            "source": attribute_info.source,
            "type": attribute_info.value_type,
            "result": {
                "value": value,
                "histogram": histogram_path,
            },
        }


class HistogramView(AnnotationBaseView):
    """View for returning histogram data."""

    @method_decorator(last_modified(always_cache))
    def get(self, request: Request, resource_id: str) -> Response:
        """Return histogram data for a resource and score ID."""
        try:
            resource = self.grr.get_resource(resource_id)
        except (FileNotFoundError, ValueError):
            return Response(status=views.status.HTTP_404_NOT_FOUND)

        score_id = request.query_params.get("score_id")
        if score_id is None:
            return Response(
                {"reason": "Score id not provided!"},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        histogram_getter = HISTOGRAM_GETTERS.get(
            resource.get_type(), get_histogram_not_supported,
        )

        histogram, extra_data = histogram_getter(
            resource, score_id,
        )
        if isinstance(histogram, NullHistogram):
            return Response(status=views.status.HTTP_404_NOT_FOUND)

        output = {
            **histogram.to_dict(),
            **extra_data,
        }

        return Response(output)


class AlleleHistory(generics.ListAPIView):
    """View for managing a user's allele annotation history."""

    authentication_classes = [WebAnnotationAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = AlleleSerializer

    def get_queryset(self) -> QuerySet:
        assert isinstance(self.request.user, BaseUser)
        return AlleleQuery.objects.filter(
            owner=cast(User, self.request.user.as_owner),
        ).order_by("-last_used")

    def delete(self, request: Request) -> Response:
        """Delete user allele annotation query from history"""
        query_id = request.query_params.get("id")
        if not query_id:
            return Response(
                {"reason": "Allele query ID must be provided!"},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        allele_query = AlleleQuery.objects.filter(
            id=query_id,
            owner=request.user.as_owner,
        )

        if allele_query.count() == 0:
            return Response(
                {"reason": "Allele query id not recognized!"},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        allele_query.delete()

        return Response(status=views.status.HTTP_204_NO_CONTENT)


class UpdateAlleleNote(views.APIView):
    """View for updating a user's note on an allele query."""

    authentication_classes = [WebAnnotationAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request) -> Response:
        """Update the note for an allele query."""
        allele = request.data.get("allele")
        note = request.data.get("note")

        if not allele:
            return Response(
                {"reason": "Allele must be provided!"},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        allele_query = AlleleQuery.objects.filter(
            allele=allele,
            owner=request.user.as_owner,
        ).first()

        if allele_query is None:
            return Response(
                {"reason": "Allele not found!"},
                status=views.status.HTTP_404_NOT_FOUND,
            )

        allele_query.note = note
        allele_query.save()

        return Response(status=views.status.HTTP_200_OK)
