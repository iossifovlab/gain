from collections.abc import Iterable
from itertools import islice
from typing import ClassVar

from gain.genomic_resources.repository import (
    GenomicResource,
    SearchIndexUnavailableError,
    SearchTermError,
    drain_search,
)
from gain.genomic_resources.resource_query import (
    MAX_RESOURCE_QUERY_LENGTH as MAX_RESOURCE_QUERY_LENGTH_CORE,
)
from gain.genomic_resources.resource_query import ResourceQueryParseError
from gain.genomic_resources.resource_types import equivalent_resource_types
from rest_framework import status
from rest_framework.views import Request, Response

from web_annotation.annotation_base_view import AnnotationBaseView


class ResourcesAPIView(AnnotationBaseView):
    SUPPORTED_RESOURCE_TYPES: ClassVar = {
        "gene_score", "position_score",
        "gene_set_collection", "genome",
        "gene_models", "allele_score",
        "liftover_chain",
        # Both accepted spellings of a fragment score (gain#471).  The
        # legacy one is deprecated (gain#538) and still reported: this
        # advertises what the API can READ, and unmigrated repositories
        # really do contain it.
        "fragment_score",
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
            accepted = equivalent_resource_types(resource_type)
            resources = filter(
                lambda resource: resource.get_type() in accepted,
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

    def get(self, _request: Request) -> Response:
        """List all available resource types."""
        return Response(
            list(self.SUPPORTED_RESOURCE_TYPES),
            status=status.HTTP_200_OK,
        )


class SearchResources(ResourcesAPIView):
    """Endpoint for resource FTS search."""

    # The longest `query` this endpoint will hand to the parser. The
    # grammar is ambiguous -- `and_` recurses through `?operation` -- so
    # Earley costs roughly O(n^3) in the length of the query: 200 clauses
    # (2000 characters) parse in ~18s and 500 (5000 characters, still
    # inside Apache's 8190-byte request line) in ~95s, all of it CPU in a
    # worker. The endpoint is anonymous and unthrottled, so that is a
    # denial of service a single GET can buy. Length is the cheap bound:
    # it caps the clause count that drives the exponent, and it caps the
    # id globs that accumulate in `fnmatch`'s module-level pattern cache.
    # 256 characters is an order of magnitude more than a real query --
    # `hg38/scores/*[phenotype="autism" and "UCSC" in provenance]` is 57 --
    # and parses in tens of milliseconds at the bound, depending on shape.
    #
    # This check is no longer the only thing standing between an untrusted
    # query and the parser: `ResourceQuery.parse` enforces the same bound
    # itself as of iossifovlab/gain#635, because bounding one endpoint left
    # the parser reachable through every other caller. It is kept because
    # the two refusals are not interchangeable -- this one answers a 400
    # naming the parameter, which is the useful answer for a caller who got
    # a query wrong, rather than surfacing a parse error from underneath.
    # The value is deliberately the parser's own bound, not a stricter one.
    MAX_RESOURCE_QUERY_LENGTH: ClassVar[int] = MAX_RESOURCE_QUERY_LENGTH_CORE

    def get(self, request: Request) -> Response:
        """Search for resources based on query parameters."""
        query_params = request.query_params

        # Filter by type if provided
        resource_type = query_params.get("type")

        # Filter by name if provided
        search = query_params.get("search")

        # Filter by the annotator wildcard query if provided
        resource_query = query_params.get("query")

        if resource_query is not None and \
                len(resource_query) > self.MAX_RESOURCE_QUERY_LENGTH:
            return Response(
                {"error": (
                    f"resource query is too long: "
                    f"{len(resource_query)} characters, at most "
                    f"{self.MAX_RESOURCE_QUERY_LENGTH} are accepted"
                )},
                status=status.HTTP_400_BAD_REQUEST,
            )

        page = query_params.get("page", 0)

        try:
            page = int(query_params.get("page", 0))
        except ValueError:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        try:
            page_size = int(query_params.get("page_size", 50))
        except ValueError:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        # `resource_type` is passed straight down: `search_resources`
        # expands equivalent spellings itself, in SQL.  An earlier revision
        # dropped the predicate here and post-filtered instead, which
        # quietly changed the data source -- with no search term left, the
        # query short-circuits to `get_all_resources()` and never opens the
        # FTS index, so paging and index-skip warnings differed between
        # fragment and non-fragment filters.
        try:
            # `search_resources` parses `resource_query` eagerly, when
            # called rather than on the first row, so a malformed query is
            # a bad request here -- not an exception escaping halfway
            # through a response that has already begun.
            found = self._grr.search_resources(
                search_term=search,
                resource_type=resource_type,
                resource_query=resource_query,
            )
            # Drained inside the guard, not after it: `search_term` is the
            # other half of the story above. FTS5 reads it when the
            # statement is first stepped, which is here rather than at the
            # call, so a malformed term raises out of this drain -- and
            # used to leave the endpoint with an unhandled 500 (gain#632).
            #
            # `drain_search` rather than `list`: the generator's return
            # value carries the children a group skipped while still
            # answering (gain#686), and this endpoint presents totals, so
            # it is the caller that must not discard them.
            rows, skips = drain_search(found)
            resources = [
                res for res in rows
                if res.get_type() in self.SUPPORTED_RESOURCE_TYPES
            ]
        except SearchIndexUnavailableError as err:
            # Not a bad request: the caller supplied nothing wrong and has
            # no repair to make. Ahead of the arm below, which it would
            # otherwise fall into -- both are `ValueError`s (ADR 0012).
            return Response(
                {"error": str(err)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        except (ResourceQueryParseError, SearchTermError) as err:
            return Response(
                {"error": str(err)},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
            # Always present, so a client branches on the content rather
            # than the existence: the repositories a group skipped while
            # answering, i.e. how the totals above fall short (gain#686).
            "incomplete": [
                {"repo": repo_id, "reason": reason}
                for repo_id, reason in skips
            ],
        }, status=status.HTTP_200_OK)
