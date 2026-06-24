"""Views for annotator editor API."""
from itertools import islice
from pathlib import Path
from typing import Any, ClassVar

import yaml
from asgiref.sync import sync_to_async
from gain.annotation.annotation_config import (
    AnnotationConfigParser,
    AnnotationConfigurationError,
    AnnotatorInfo,
)
from gain.annotation.annotation_factory import (
    build_pipeline_annotator,
    check_for_repeated_attributes_in_pipeline,
    get_annotator_factory,
    get_available_annotator_types,
)
from gain.genomic_resources.aggregators import (
    AGGREGATOR_CLASS_DICT,
    NUMERIC_ONLY_AGGREGATORS,
)
from rest_framework.views import Request, Response, status

from web_annotation.annotation_base_view import (
    AnnotationBaseView,
    AsyncAnnotationBaseView,
)
from web_annotation.authentication import WebAnnotationAuthentication
from web_annotation.pipeline_cache import ThreadSafePipeline


class _InvalidSearchTermError(Exception):
    """Raised off-loop when a search term is not a string.

    Lets ``_collect_attributes`` defer the search-term type check until after
    the factory build (master's order) and have the async caller map it to the
    same 400 ("Search term must be a string").
    """


class EditorMixin:  # pylint: disable=too-few-public-methods
    """Editor-specific helpers shared by the sync and async editor bases.

    These helpers are pure config/template builders -- no ORM, no GRR build --
    so they are mixed into BOTH ``EditorView`` (sync) and ``AsyncEditorView``
    (async). The cache/executors and the (a)``get_pipeline`` machinery come
    from ``AnnotationMixin`` via the concrete annotation base each editor base
    inherits, so the single-shared-cache invariant (iossifovlab/gain#163) is
    preserved across both editor paths.
    """

    def _get_annotator_types(self) -> list[str]:
        """Get all available annotator types from the DAE registry."""

        return [
            "position_score_annotator",
            "allele_score_annotator",
            "gene_score_annotator",
            "gene_set_annotator",
            "cnv_collection_annotator",
            "effect_annotator",
            "simple_effect_annotator",
            "liftover_annotator",
            "normalize_allele_annotator",
        ]

    BASE_DOC_URL = "https://iossifovlab.com/gaindocs/annotation_infrastructure.html#"

    def _get_annotator_config_template(
        self, annotator_type: str,
    ) -> dict[str, Any]:
        """
        Temporary method to get annotator config template
        until it is implemented internally in DAE.
        """

        if annotator_type not in get_available_annotator_types():
            raise ValueError(f"Unknown annotator_type: {annotator_type}")

        if annotator_type == "position_score_annotator":
            return {
                "annotator_type": "position_score_annotator",
                "documentation_url": (
                    f"{self.BASE_DOC_URL}#position-score-annotator"
                ),
                "resource_id": {
                    "field_type": "resource",
                    "resource_type": "position_score",
                    "optional": False,
                },
                "input_annotatable": {
                    "field_type": "attribute",
                    "attribute_type": "annotatable",
                    "optional": True,
                },
            }
        if annotator_type == "allele_score_annotator":
            return {
                "annotator_type": "allele_score",
                "documentation_url": (
                    f"{self.BASE_DOC_URL}#allele-score-annotator"
                ),
                "resource_id": {
                    "field_type": "resource",
                    "resource_type": "allele_score",
                    "optional": False,
                },
                "input_annotatable": {
                    "field_type": "attribute",
                    "attribute_type": "annotatable",
                    "optional": True,
                },
            }
        if annotator_type == "gene_score_annotator":
            return {
                "annotator_type": "gene_score_annotator",
                "documentation_url": (
                    f"{self.BASE_DOC_URL}#gene-score-annotator"
                ),
                "resource_id": {
                    "field_type": "resource",
                    "resource_type": "gene_score",
                    "optional": False,
                },
                "input_gene_list": {
                    "field_type": "attribute",
                    "attribute_type": "gene_list",
                    "optional": False,
                },
                "input_annotatable": {
                    "field_type": "attribute",
                    "attribute_type": "annotatable",
                    "optional": True,
                },
            }
        if annotator_type == "gene_set_annotator":
            return {
                "annotator_type": "gene_set_annotator",
                "documentation_url": (
                    f"{self.BASE_DOC_URL}#gene-set-annotator"
                ),
                "resource_id": {
                    "field_type": "resource",
                    "resource_type": "gene_set_collection",
                    "optional": False,
                },
                "input_gene_list": {
                    "field_type": "attribute",
                    "attribute_type": "gene_list",
                    "optional": False,
                },
            }
        if annotator_type == "cnv_collection_annotator":
            return {
                "annotator_type": "cnv_collection",
                "documentation_url": (
                    f"{self.BASE_DOC_URL}#cnv-collection-annotator"
                ),
                "resource_id": {
                    "field_type": "resource",
                    "resource_type": "cnv_collection",
                    "optional": False,
                },
                "cnv_filter": {
                    "field_type": "string",
                    "optional": True,
                },
                "input_annotatable": {
                    "field_type": "attribute",
                    "attribute_type": "annotatable",
                    "optional": True,
                },
            }
        if annotator_type == "effect_annotator":
            return {
                "annotator_type": "effect_annotator",
                "documentation_url": (
                    f"{self.BASE_DOC_URL}#effect-annotator"
                ),
                "gene_models": {
                    "field_type": "resource",
                    "resource_type": "gene_models",
                    "optional": False,
                },
                "genome": {
                    "field_type": "resource",
                    "resource_type": "genome",
                    "optional": True,
                },
                "input_annotatable": {
                    "field_type": "attribute",
                    "attribute_type": "annotatable",
                    "optional": True,
                },
            }
        if annotator_type == "simple_effect_annotator":
            return {
                "annotator_type": "effect_annotator",
                "documentation_url": (
                    f"{self.BASE_DOC_URL}#effect-annotator"
                ),
                "gene_models": {
                    "field_type": "resource",
                    "resource_type": "gene_models",
                    "optional": False,
                },
                "input_annotatable": {
                    "field_type": "attribute",
                    "attribute_type": "annotatable",
                    "optional": True,
                },
            }
        if annotator_type == "liftover_annotator":
            return {
                "annotator_type": "liftover_annotator",
                "documentation_url": (
                    f"{self.BASE_DOC_URL}#liftover-annotator"
                ),
                "chain": {
                    "field_type": "resource",
                    "resource_type": "liftover_chain",
                    "optional": False,
                },
                "source_genome": {
                    "field_type": "resource",
                    "resource_type": "genome",
                    "optional": False,
                },
                "target_genome": {
                    "field_type": "resource",
                    "resource_type": "genome",
                    "optional": False,
                },
                "input_annotatable": {
                    "field_type": "attribute",
                    "attribute_type": "annotatable",
                    "optional": True,
                },
            }
        if annotator_type == "normalize_allele_annotator":
            return {
                "annotator_type": "normalize_allele_annotator",
                "documentation_url": (
                    f"{self.BASE_DOC_URL}#normalize-allele-annotator"
                ),
                "genome": {
                    "field_type": "resource",
                    "resource_type": "genome",
                    "optional": False,
                },
                "input_annotatable": {
                    "field_type": "attribute",
                    "attribute_type": "annotatable",
                    "optional": True,
                },
            }

        raise KeyError(f"Unknown annotator_type: {annotator_type}")


class EditorView(EditorMixin, AnnotationBaseView):
    """Synchronous base view for editor API endpoints.

    Dispatch is unchanged from ``AnnotationBaseView``; every existing sync
    editor view keeps working untouched. Editor helpers come from
    ``EditorMixin``; cache/executors from ``AnnotationMixin``.
    """


class AsyncEditorView(EditorMixin, AsyncAnnotationBaseView):
    """Async base view (``adrf``) for editor read GETs and POSTs that await the
    build.

    Shares the same ``EditorMixin`` helpers as ``EditorView`` and the same
    cache/executors as every other annotation view (via ``AnnotationMixin``).
    ``adrf`` dispatches a view async iff *all* its handlers are coroutines, so
    a subclass must expose ONLY async handlers (iossifovlab/gain#165).
    """


class AnnotatorConfig(EditorView):
    """View for annotator configuration templates."""
    def post(self, request: Request) -> Response:
        """POST method to get annotator config template."""
        assert isinstance(request.data, dict)
        data = {**request.data}
        if "annotator_type" not in data:
            return Response(
                {"error": "annotator_type is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        annotator_type = data.pop("annotator_type", None)

        if not isinstance(annotator_type, str):
            return Response(
                {"error": "annotator_type must be a string"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = self._get_annotator_config_template(annotator_type)

        for key, value in data.items():
            if key in result:
                result[key]["value"] = value

        return Response(result, status=status.HTTP_200_OK)


class AnnotatorTypes(EditorView):
    """View for available annotator types."""
    def get(self, _request: Request) -> Response:
        """GET method to retrieve available annotator types."""
        annotator_types = self._get_annotator_types()
        return Response(annotator_types, status=status.HTTP_200_OK)


class AnnotatorAttributes(AsyncEditorView):
    """View for annotator attributes.

    Async (#166): the long pole -- the GRR pipeline build wait -- leaves the
    event loop via ``aget_pipeline``. The annotator factory build and the
    attribute-spec computation that follow touch GRR metadata, so they run off
    the loop via ``sync_to_async`` (asgiref default thread_sensitive). Build
    failure -> 400, missing -> 404 mapping is inherited from ``aget_pipeline``.
    ``request.data`` is parsed by adrf without blocking the loop. There is no
    ORM and no ``annotate()`` here, so no dedicated executor is needed.
    """

    ATTRIBUTE_PAGE_SIZE = 50

    authentication_classes: ClassVar = [WebAnnotationAuthentication]

    async def post(self, request: Request) -> Response:
        """POST method to get annotator attributes."""
        assert isinstance(request.data, dict)
        data = dict(request.data)
        if "annotator_type" not in data:
            return Response(
                {"error": "annotator_type is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        annotator_type = data.pop("annotator_type")

        if not isinstance(annotator_type, str):
            return Response(
                {"error": "annotator_type must be a string"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        pipeline_id = data.pop("pipeline_id", None)
        if pipeline_id is None or not isinstance(pipeline_id, str):
            return Response(
                {"error": "A pipeline_id string is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        page = data.pop("page", 0)

        assert isinstance(page, int), "Page must be an integer"
        assert page >= 0, "Page must be non-negative"

        search_term = data.pop("search", None)

        # Long pole: await the GRR pipeline build OFF the event loop. Build
        # failure -> 400, missing -> 404 mapping comes from aget_pipeline.
        # Resolved before the unknown-type check to preserve the exact prior
        # validation order of the sync handler. The search-term string-type
        # check stays inside _collect_attributes (after the factory build) so a
        # dual-invalid request -- non-string search AND an unbuildable annotator
        # config -- still yields the build's 400, exactly as master did.
        pipeline = await self.aget_pipeline(pipeline_id, request.user)

        data["work_dir"] = "/tmp"  # noqa: S108

        if annotator_type not in get_available_annotator_types():
            return Response(
                {"error": f"Unknown annotator_type: {annotator_type}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Factory build + spec computation touch GRR metadata; run off the loop.
        try:
            attributes_result, total_attribute_count = await sync_to_async(
                self._collect_attributes)(
                pipeline, annotator_type, data, page, search_term,
            )
        except AnnotationConfigurationError as e:
            return Response(
                {"error": f"Invalid annotator configuration: {e!s}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except _InvalidSearchTermError:
            return Response(
                {"error": "Search term must be a string"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "page": page,
                "total_pages": (
                    total_attribute_count // self.ATTRIBUTE_PAGE_SIZE) + 1,
                "total_attributes": total_attribute_count,
                "attributes": attributes_result,
            },
            status=status.HTTP_200_OK,
        )

    def _collect_attributes(
        self,
        pipeline: ThreadSafePipeline,
        annotator_type: str,
        data: dict[str, Any],
        page: int,
        search_term: Any,
    ) -> tuple[list[dict[str, Any]], int]:
        """Build the annotator and page its attribute specs off the loop.

        Touches GRR metadata (factory build, ``get_attribute_specs``); raises
        ``AnnotationConfigurationError`` for an invalid annotator config and
        ``_InvalidSearchTermError`` for a non-string ``search_term`` (checked
        after the build, matching master), both mapped to 400 by the caller.
        """
        annotator_config = AnnotatorInfo(annotator_type, [], data)
        factory = get_annotator_factory(annotator_type)
        annotator = factory(pipeline, annotator_config)
        all_specs = annotator.get_attribute_specs()
        attributes_by_source = {
            attr.source: attr for attr in annotator.attributes
        }
        if search_term is None:
            attribute_items: Any = list(all_specs.items())
        else:
            # Master validated the search-term type HERE, after the factory
            # build -- so a non-string search on an unbuildable config surfaces
            # the build's 400 first. Preserve that by checking after the build
            # and mapping to the same 400 in the caller (#166 review).
            if not isinstance(search_term, str):
                raise _InvalidSearchTermError
            attribute_items = [
                (name, spec)
                for name, spec in all_specs.items()
                if search_term.lower() in spec.source.lower()
                or search_term.lower() in spec.description.lower()
            ]
        total_attribute_count = len(attribute_items)
        page_attributes = islice(
            attribute_items,
            page * self.ATTRIBUTE_PAGE_SIZE,
            (page + 1) * self.ATTRIBUTE_PAGE_SIZE,
        )
        attributes_result = []
        for source, spec in page_attributes:
            attr = attributes_by_source.get(source)
            attributes_result.append({
                "name": attr.name if attr else source,
                **spec.as_dict(),
            })
        return attributes_result, total_attribute_count


class PipelineAttributes(AsyncEditorView):
    """View for annotator attributes.

    Async (#165): the only long pole -- the GRR pipeline build wait -- leaves
    the event loop via ``aget_pipeline``. The pipeline-metadata reads
    (``get_attributes`` / ``get_attributes_by_type``) touch GRR, so they run
    off the loop via ``sync_to_async`` (asgiref default thread_sensitive). There
    is no ``annotate()`` and no ORM here, so no dedicated executor is needed.
    """

    authentication_classes: ClassVar = [WebAnnotationAuthentication]

    async def get(self, request: Request) -> Response:
        """GET method to get pipeline attributes."""
        pipeline_id = request.query_params.get("pipeline_id")
        if pipeline_id is None:
            return Response(
                {"error": "pipeline_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        attribute_type = request.query_params.get("attribute_type")
        if attribute_type is not None and not isinstance(attribute_type, str):
            return Response(
                {"error": "attribute_type must be a string"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Long pole: await the GRR pipeline build OFF the event loop. Build
        # failure -> 400, missing -> 404 mapping comes from aget_pipeline.
        pipeline = await self.aget_pipeline(pipeline_id, request.user)

        result = await sync_to_async(self._collect_attribute_names)(
            pipeline, attribute_type,
        )

        return Response(result, status=status.HTTP_200_OK)

    @staticmethod
    def _collect_attribute_names(
        pipeline: ThreadSafePipeline, attribute_type: str | None,
    ) -> list[str]:
        """Read attribute names off the loop (touches GRR metadata)."""
        if attribute_type is not None:
            attributes = pipeline.get_attributes_by_type(attribute_type)
        else:
            attributes = pipeline.get_attributes()
        return [attr.name for attr in attributes]


class AnnotatorYAML(AsyncEditorView):
    """View for annotator configuration in YAML format.

    Async (#166): the GRR pipeline build wait leaves the event loop via
    ``aget_pipeline``. The subsequent ``build_pipeline_annotator`` /
    repeated-attribute check and config serialization touch GRR metadata, so
    they run off the loop via ``sync_to_async``. Build failure -> 400, missing
    -> 404 mapping is inherited from ``aget_pipeline``; ``request.data`` is
    parsed by adrf without blocking the loop. No ORM, no ``annotate()``.
    """

    authentication_classes: ClassVar = [WebAnnotationAuthentication]

    async def post(self, request: Request) -> Response:
        """POST method to get annotator config in YAML format."""
        assert isinstance(request.data, dict)
        data = dict(request.data)
        if "annotator_type" not in data:
            return Response(
                {"error": "annotator_type is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if "pipeline_id" not in data:
            return Response(
                {"error": "pipeline_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        pipeline_id = data.pop("pipeline_id")
        if not isinstance(pipeline_id, str):
            return Response(
                {"error": "pipeline_id must be a string"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Long pole: await the GRR pipeline build OFF the event loop. Build
        # failure -> 400, missing -> 404 mapping comes from aget_pipeline.
        pipeline = await self.aget_pipeline(pipeline_id, request.user)

        annotator_type = data.pop("annotator_type")

        if not isinstance(annotator_type, str):
            return Response(
                {"error": "annotator_type must be a string"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if annotator_type not in get_available_annotator_types():
            return Response(
                {"error": f"Unknown annotator_type: {annotator_type}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Annotator build + repeated-attribute check + serialization touch GRR
        # metadata; run off the loop.
        try:
            config_yaml = await sync_to_async(self._build_annotator_yaml)(
                pipeline, annotator_type, data,
            )
        except AnnotationConfigurationError as e:
            return Response(
                {"error": f"Invalid annotator configuration: {e!s}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(config_yaml, status=status.HTTP_200_OK)

    @staticmethod
    def _build_annotator_yaml(
        pipeline: ThreadSafePipeline,
        annotator_type: str,
        data: dict[str, Any],
    ) -> str:
        """Build the annotator against the pipeline and dump its config to YAML.

        Touches GRR metadata; raises ``AnnotationConfigurationError`` for an
        invalid config or a repeated-attribute clash, which the caller maps to
        400.
        """
        _, annotator_configs = AnnotationConfigParser.parse_raw(
            [{annotator_type: data}])

        assert len(annotator_configs) == 1
        annotator_config = annotator_configs[0]

        build_pipeline_annotator(
            pipeline, annotator_config, Path("./work"),
        )
        check_for_repeated_attributes_in_pipeline(
            pipeline, annotator_config=annotator_config,
        )

        config_dict = annotator_config.to_dict()

        if "work_dir" in config_dict[annotator_type]:
            del config_dict[annotator_type]["work_dir"]

        return yaml.safe_dump(
            [config_dict],
            sort_keys=False,
            default_flow_style=False,
        )


class ResourceAnnotators(EditorView):
    """View for annotators associated with a resource."""

    def get(self, request: Request) -> Response:
        """GET method to retrieve annotators associated with a resource."""
        resource_id = request.query_params.get("resource_id")
        if resource_id is None:
            return Response(
                {"error": "resource_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            resource = self.grr.get_resource(resource_id)
        except ValueError:
            return Response(
                {"error": f"Resource '{resource_id}' not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        configs = {}

        for annotator_type in self._get_annotator_types():
            config = {
                "annotator_type": annotator_type,
            }
            matched = False
            try:
                template = self._get_annotator_config_template(annotator_type)
            except KeyError:
                continue
            for field_name, field in template.items():
                if isinstance(field, dict):
                    field_type = field.get("field_type")
                    if field_type is not None and field_type == "resource":
                        resource_type = field.get("resource_type")
                        if resource_type == resource.get_type():
                            matched = True
                            config[field_name] = resource_id
                            break

            if (
                resource.get_type() == "liftover_chain" and
                annotator_type == "liftover_annotator"
            ):
                labels = resource.get_labels()
                if "source_genome" in labels:
                    config["source_genome"] = labels["source_genome"]
                if "target_genome" in labels:
                    config["target_genome"] = labels["target_genome"]

            if not matched:
                continue
            configs[annotator_type] = config

        resource_default_annotators_mapping = {
            "allele_score": "allele_score_annotator",
            "cnv_collection": "cnv_collection_annotator",
            "gene_models": "effect_annotator",
            "gene_score": "gene_score_annotator",
            "gene_set_collection": "gene_set_annotator",
            "liftover_chain": "liftover_annotator",
            "position_score": "position_score_annotator",
        }

        return Response(
            {
                "default": resource_default_annotators_mapping.get(
                    resource.get_type()),
                "configs": configs,
            }, status=status.HTTP_200_OK)


class PipelineStatus(AsyncEditorView):
    """View for pipeline status and statistics.

    Async (#165): the GRR build wait leaves the event loop via
    ``aget_pipeline``; the pipeline-metadata reads (attribute/annotator counts)
    touch GRR and run off the loop via ``sync_to_async``. No ``annotate()`` and
    no ORM here.
    """

    authentication_classes: ClassVar = [WebAnnotationAuthentication]

    async def get(self, request: Request) -> Response:
        """GET method to retrieve pipeline status."""
        pipeline_id = request.query_params.get("pipeline_id")
        if pipeline_id is None:
            return Response(
                {"error": "pipeline_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Long pole: await the GRR pipeline build OFF the event loop. Build
        # failure -> 400, missing -> 404 mapping comes from aget_pipeline.
        pipeline = await self.aget_pipeline(pipeline_id, request.user)

        status_info = await sync_to_async(self._build_status_info)(pipeline)

        return Response(status_info, status=status.HTTP_200_OK)

    @staticmethod
    def _build_status_info(pipeline: ThreadSafePipeline) -> dict[str, Any]:
        """Read pipeline metadata off the loop (touches GRR)."""
        return {
            "attributes_count": len(pipeline.get_attributes()),
            "annotators_count": len(pipeline.annotators),
            "annotatables": [
                attr.name for attr in
                pipeline.get_attributes_by_type("annotatable")
            ],
            "gene_lists": [
                attr.name for attr in
                pipeline.get_attributes_by_type("gene_list")
            ],
        }


class Aggregators(EditorView):
    """View listing all available aggregator types and their metadata."""

    def get(self, _request: Request) -> Response:
        """GET method to retrieve all aggregator types."""
        result = []
        for aggregator_type, aggregator_class in AGGREGATOR_CLASS_DICT.items():
            entry: dict[str, Any] = {
                "aggregator_type": aggregator_type,
                "parametrized": aggregator_class.parametrized,
            }
            if aggregator_class.default_parameter is not None:
                entry["default_parameter_value"] = (
                    aggregator_class.default_parameter)
            result.append(entry)
        return Response(result, status=status.HTTP_200_OK)


class AnnotatorAggregators(AsyncEditorView):
    """View for computing valid aggregators per attribute source.

    Async (#166): the GRR pipeline build wait leaves the event loop via
    ``aget_pipeline``. The annotator factory build and per-source spec lookup
    touch GRR metadata, so they run off the loop via ``sync_to_async``. Build
    failure -> 400, missing -> 404 mapping is inherited from ``aget_pipeline``;
    ``request.data`` is parsed by adrf without blocking the loop. No ORM, no
    ``annotate()``.
    """

    authentication_classes: ClassVar = [WebAnnotationAuthentication]

    async def post(self, request: Request) -> Response:
        """POST method to get valid aggregators per attribute source."""
        assert isinstance(request.data, dict)
        data = dict(request.data)

        if "annotator_type" not in data:
            return Response(
                {"error": "annotator_type is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if "pipeline_id" not in data:
            return Response(
                {"error": "pipeline_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        annotator_type = data.pop("annotator_type")
        pipeline_id = data.pop("pipeline_id")
        attribute_sources = data.pop("attribute_sources", [])

        if not isinstance(annotator_type, str):
            return Response(
                {"error": "annotator_type must be a string"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not isinstance(pipeline_id, str):
            return Response(
                {"error": "pipeline_id must be a string"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not isinstance(attribute_sources, list):
            return Response(
                {"error": "attribute_sources must be a list"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Long pole: await the GRR pipeline build OFF the event loop. Build
        # failure -> 400, missing -> 404 mapping comes from aget_pipeline.
        pipeline = await self.aget_pipeline(pipeline_id, request.user)
        data["work_dir"] = "/tmp"  # noqa: S108

        if annotator_type not in get_available_annotator_types():
            return Response(
                {"error": f"Unknown annotator_type: {annotator_type}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Factory build + spec lookup touch GRR metadata; run off the loop.
        try:
            result = await sync_to_async(self._compute_aggregators)(
                pipeline, annotator_type, data, attribute_sources,
            )
        except AnnotationConfigurationError as e:
            return Response(
                {"error": f"Invalid annotator configuration: {e!s}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(result, status=status.HTTP_200_OK)

    @staticmethod
    def _compute_aggregators(
        pipeline: ThreadSafePipeline,
        annotator_type: str,
        data: dict[str, Any],
        attribute_sources: list[Any],
    ) -> dict[str, Any]:
        """Build the annotator and compute valid aggregators off the loop.

        Touches GRR metadata; raises ``AnnotationConfigurationError`` for an
        invalid annotator config, which the caller maps to 400.
        """
        annotator_config = AnnotatorInfo(annotator_type, [], data)
        factory = get_annotator_factory(annotator_type)
        annotator = factory(pipeline, annotator_config)

        all_specs = annotator.get_attribute_specs()
        attributes_by_source = {
            attr.source: attr for attr in annotator.attributes
        }

        result: dict[str, Any] = {}
        for source in attribute_sources:
            if not isinstance(source, str):
                continue
            spec = all_specs.get(source)
            if spec is None or not spec.supports_aggregation:
                result[source] = {
                    "aggregators": None, "default_aggregator": None}
                continue

            valid_aggregators = [
                agg_type for agg_type in AGGREGATOR_CLASS_DICT
                if agg_type not in NUMERIC_ONLY_AGGREGATORS
                or spec.value_type in {"int", "float"}
            ]

            attr = attributes_by_source.get(source)
            default_aggregator = attr.aggregator if attr else None

            result[source] = {
                "aggregators": valid_aggregators,
                "default_aggregator": default_aggregator,
            }

        return result
