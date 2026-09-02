from typing import Any

from gain import logging
from gain.annotation.annotatable import Annotatable
from gain.annotation.annotation_config import (
    AnnotationConfigurationError,
    AnnotatorInfo,
)
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    Annotator,
    AttributeSpec,
)
from gain.annotation.annotator_base import AggregatedValues, AnnotatorBase
from gain.genomic_resources.aggregators import (
    ScoreAggregationQuery,
    aggregator_name,
)
from gain.genomic_resources.genomic_scores import FragmentScore
from gain.genomic_resources.resource_types import (
    warn_deprecated_spelling,
)
from gain.genomic_resources.score_filter import ScoreFilterError

logger = logging.getLogger(__name__)

#: Preferred spelling of the fragment-filter parameter.
FRAGMENT_FILTER_PARAMETER = "fragment_filter"
#: Deprecated spelling, still honoured -- pipelines we do not control write
#: it.  Stops being accepted in ``LEGACY_VOCABULARY_REMOVAL_RELEASE``.
LEGACY_FILTER_PARAMETER = "cnv_filter"

#: The annotator names that mean this annotator, deprecated spelling to the
#: preferred one it should be rewritten as.  Both are registered entry-point
#: keys, so a pipeline naming either builds; only the value is worth typing
#: in a config written today.
LEGACY_ANNOTATOR_NAMES = {
    "cnv_collection": "fragment_score",
    "cnv_collection_annotator": "fragment_score_annotator",
}


def build_fragment_score_annotator(pipeline: AnnotationPipeline,
                                   info: AnnotatorInfo) -> Annotator:
    return FragmentScoreAnnotator(pipeline, info)


class FragmentScoreAnnotator(AnnotatorBase):
    """Annotator over a fragment score.

    Configured as ``fragment_score`` / ``fragment_score_annotator``, with
    ``fragment_filter:`` selecting which fragments count.  The older
    ``cnv_collection`` / ``cnv_collection_annotator`` / ``cnv_filter``
    spellings resolve here too, deprecated: each one logs a warning naming
    the pipeline's annotator and the release it stops being accepted in.
    See ``docs/adr/0011-deprecate-cnv-collection-vocabulary.md``.
    """

    def __init__(self, pipeline: AnnotationPipeline, info: AnnotatorInfo):
        resource_id = info.parameters.get("resource_id")
        if resource_id is None:
            raise ValueError(f"Can't create {info.type}: "
                             "no resrouce_id parameter.")
        resource = pipeline.repository.get_resource(resource_id)

        # The stack at this point runs through GAIn's config parsing, never
        # through the YAML the reader has to edit, so the messages below
        # carry the location themselves.  This is the whole pipeline's worth
        # of them: the constructor runs once per pipeline build, not once
        # per annotated record.  A run that rebuilds the same pipeline --
        # once per partition, say -- collapses to one line per offending
        # annotator through `warn_deprecated_spelling`.
        found_in = (
            f"Annotator {info.annotator_id} on resource '{resource_id}'")
        preferred_annotator_name = LEGACY_ANNOTATOR_NAMES.get(info.type)
        if preferred_annotator_name is not None:
            warn_deprecated_spelling(
                logger, "annotator name", info.type,
                preferred_annotator_name, found_in=found_in)

        # Deliberately constructed directly rather than through
        # `build_fragment_score_from_resource`: that factory returns a
        # process-wide shared instance, and `self.close()` below closes the
        # score -- which would tear it down for every other holder.
        # `FragmentScore.__init__` validates the resource type, so nothing is
        # lost by bypassing the factory here.
        self.fragment_score = FragmentScore(resource)
        info.resources.append(resource)

        # Two spellings -- `fragment_filter` is the one to write, `cnv_filter`
        # is what pipelines we do not control say.
        # Read BOTH unconditionally: `info.parameters` refuses a parameter
        # nobody read, so a `get` skipped after an early match would turn
        # the unmatched spelling into an "unused parameter" error instead
        # of the duplicate-configuration error below.
        fragment_filter_str = info.parameters.get(FRAGMENT_FILTER_PARAMETER)
        cnv_filter_str = info.parameters.get(LEGACY_FILTER_PARAMETER)
        if cnv_filter_str is not None:
            warn_deprecated_spelling(
                logger, "parameter", LEGACY_FILTER_PARAMETER,
                FRAGMENT_FILTER_PARAMETER, found_in=found_in)
        if fragment_filter_str is not None and cnv_filter_str is not None:
            raise AnnotationConfigurationError(
                f"{info.type} configures both "
                f"'{FRAGMENT_FILTER_PARAMETER}' and "
                f"'{LEGACY_FILTER_PARAMETER}'. They are two spellings of one "
                f"parameter, so choosing between them would apply a filter "
                f"the configuration did not ask for; keep "
                f"'{FRAGMENT_FILTER_PARAMETER}' and delete the other")

        self.fragment_filter = None
        used_parameter = (
            LEGACY_FILTER_PARAMETER if fragment_filter_str is None
            else FRAGMENT_FILTER_PARAMETER)
        filter_str = (
            cnv_filter_str if fragment_filter_str is None
            else fragment_filter_str)
        if filter_str is not None:
            assert isinstance(filter_str, str)

            try:
                self.fragment_filter = self.fragment_score.compile_filter(
                    filter_str)
            except ScoreFilterError as e:
                # Names the spelling the user actually wrote -- reporting a
                # key absent from their config sends them looking in the
                # wrong place (cf. gain#477).  The score cannot do this: it
                # is handed an expression, not the parameter it came from.
                raise AnnotationConfigurationError(
                    f"Error parsing {used_parameter}: {e}") from e

        super().__init__(pipeline, info)

        #: One query per attribute that HAS an aggregator, in attribute
        #: order, so the tuple the plane answers indexes straight back to
        #: the attributes it was built from.
        #:
        #: Deliberately NOT deduped, unlike the source list this replaces:
        #: a source named twice under two aggregators is two queries and
        #: two answers, which is what lets each attribute keep its own
        #: reduction.  One fetch still serves both -- the fold shares a
        #: column between requests naming one score.
        #:
        #: An attribute with no aggregator is left out and answers the
        #: fragment COUNT instead.  That is not only the `count`
        #: attribute: a `bool` score has no default aggregator either, so
        #: an attribute over one lands here too.
        self._region_queries: list[ScoreAggregationQuery] = [
            ScoreAggregationQuery(
                attr.source, aggregator_name(attr.aggregator))
            for attr in self._attributes
            if attr.aggregator is not None
        ]

        for attr in self._attributes:
            spec = self.attribute_specs[attr.source]
            score_def = self.fragment_score\
                .get_score_definition(attr.source)
            if score_def is not None:
                attr._documentation = f"""
                    {spec.description}

                    small values: {score_def.small_values_desc},
                    large_values: {score_def.large_values_desc}
                    aggregator: {attr.aggregator}
                """  # ruff: ignore[private-member-access]

    def get_attribute_specs(self) -> dict[str, AttributeSpec]:
        attributes: dict[str, AttributeSpec] = {
            "count": AttributeSpec(
                source="count",
                value_type="int",
                # Held back from gain#470 because it is annotation output a
                # user reads, so editing it is a behaviour change; it moves
                # here, with the vocabulary.  This is a DESCRIPTION, not an
                # attribute name -- the attribute is still `count`, so no
                # pipeline that requests it breaks.
                #
                # "kept" rather than "overlapping": with a `fragment_filter`
                # configured the rejected fragments are not counted, which
                # has always been true and was never said here.
                description="The number of fragments overlapping with the "
                "annotatable that the fragment filter kept.",
            ),
        }
        for score_id, score_def in \
                self.fragment_score.score_definitions.items():
            attributes[score_id] = AttributeSpec(
                source=score_id,
                value_type=score_def.value_type,
                description=score_def.desc,
                is_default=False,
            )
        return attributes

    def get_attribute_defaults(
        self, spec: AttributeSpec,
    ) -> dict[str, Any]:
        score_def = self.fragment_score.get_score_definition(spec.source)
        if score_def is not None:
            return {"aggregator": score_def.aggregator}
        return {}

    def open(self) -> Annotator:
        self.fragment_score.open()
        super().open()
        return self

    def close(self) -> None:
        self.fragment_score.close()
        super().close()

    def _do_annotate(
        self, annotatable: Annotatable,
        context: dict[str, Any],  # ruff: ignore[unused-method-argument]
    ) -> dict[str, Any]:
        # The SCORE reduces (gain#1124).  What comes back is one value per
        # query and the number of fragments the walk saw, off a single pass
        # that never materialises the fragments -- which is what keeps peak
        # memory flat however many of them overlap the annotatable.
        aggregate = self.fragment_score \
            .get_fragment_scores_overlapping_region_agg(
                annotatable.chrom, annotatable.pos, annotatable.pos_end,
                queries=self._region_queries,
                score_filter=self.fragment_filter)

        # Paired back by ORDER, against the same filter that built the
        # queries.  The names are read HERE rather than cached beside the
        # queries: a pipeline naming one attribute twice renames the later
        # ones (`resolve_repeated_attributes`) after every annotator has
        # been constructed, so names captured in `__init__` would key the
        # answers by names the pipeline has since moved away from.
        #
        # An attribute with no aggregator has no reduction of its own and
        # answers the fragment count instead -- see `_region_queries`.
        values = iter(aggregate.values)
        result = AggregatedValues()
        for attr in self._attributes:
            result[attr.name] = (
                next(values) if attr.aggregator is not None
                else aggregate.count)
        return result
