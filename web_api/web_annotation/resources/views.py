from itertools import islice
from rest_framework import status
from rest_framework.views import Request, Response
from gain.genomic_resources.repository import GenomicResource
from collections.abc import Iterable
from web_annotation.annotation_base_view import AnnotationBaseView


class ResourcesAPIView(AnnotationBaseView):
    SUPPORTED_RESOURCE_TYPES = {
        "gene_score", "position_score",
        "gene_set_collection", "genome",
        "gene_models", "allele_score",
        "liftover_chain",
        "cnv_collection",
    }


class Resources(ResourcesAPIView):
    """
    API endpoint that allows resources to be searched.
    """

    def get(self, request: Request) -> Response:
        """Search for resources based on query parameters."""
        query_params = request.query_params

        resources: Iterable[GenomicResource] = filter(
            lambda resource: resource.get_type()
            in self.SUPPORTED_RESOURCE_TYPES,
            self._grr.get_all_resources(),
        )

        # Filter by type if provided
        resource_type = query_params.get("type")

        if resource_type:
            assert isinstance(resource_type, str)
            resources = filter(
                lambda resource: resource.get_type() == resource_type,
                resources,
            )

        # Filter by name if provided
        search = query_params.get("search")

        if search:
            assert isinstance(search, str)
            resources = filter(
                lambda resource: (
                    search.lower() in resource.resource_id.lower()
                ),
                resources,
            )

        output = {resource.resource_id for resource in resources}

        return Response(output, status=status.HTTP_200_OK)


class ResourceTypes(ResourcesAPIView):
    """
    API endpoint that allows resource types to be listed.
    """

    def get(self, request: Request) -> Response:
        """List all available resource types."""
        return Response(
            list(self.SUPPORTED_RESOURCE_TYPES),
            status=status.HTTP_200_OK,
        )


class SearchResources(ResourcesAPIView):
    """Endpoint for resource FTS search."""

    def get(self, request: Request) -> Response:
        """Search for resources based on query parameters."""
        query_params = request.query_params

        # Filter by type if provided
        resource_type = query_params.get("type")

        # Filter by name if provided
        search = query_params.get("search")

        page = query_params.get("page", 0)

        try:
            page = int(query_params.get("page", 0))
        except ValueError:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        try:
            page_size = int(query_params.get("page_size", 50))
        except ValueError:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        resources = list(filter(
            lambda r: r.get_type() in self.SUPPORTED_RESOURCE_TYPES,
            self._grr.search_resources(
                search_term=search,
                resource_type=resource_type,
            ),
        ))

        resource_page = islice(
            resources,
            int(page) * int(page_size),
            (int(page) + 1) * int(page_size),
        )

        resource_details = [
            {
                "full_id": resource.get_full_id(),
                "resource_id": resource.resource_id,
                "type": resource.get_type(),
                "version": resource.version,
                "summary": resource.get_summary(),
                "url": resource.get_public_url(),
            }
            for resource in resource_page
        ]

        return Response({
            "page": int(page),
            "pages": (len(resources) + page_size - 1) // page_size,
            "total_resources": len(resources),
            "resources": resource_details,
        }, status=status.HTTP_200_OK)
