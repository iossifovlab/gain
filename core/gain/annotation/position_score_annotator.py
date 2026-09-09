"""The ``position_score_annotator``.

Annotates with scores keyed by genomic position -- phastCons, phyloP,
FitCons2 and the like -- read from a ``position_score`` resource.
"""
import textwrap
from typing import Any

from gain.annotation.annotatable import Annotatable, VCFAllele
from gain.annotation.annotation_config import (
    AnnotatorInfo,
    Attribute,
)
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    Annotator,
    AttributeSpec,
)
from gain.annotation.annotator_base import AnnotatedValues
from gain.annotation.genomic_score_annotator_base import (
    GenomicScoreAnnotatorBase,
    get_genomic_resource,
)
from gain.genomic_resources.aggregators import (
    PositionScoreAggregationQuery,
)
from gain.genomic_resources.genomic_scores import (
    build_position_score_from_resource,
)


def build_position_score_annotator(pipeline: AnnotationPipeline,
                                   info: AnnotatorInfo) -> Annotator:
    return PositionScoreAnnotator(pipeline, info)


class PositionScoreAnnotator(GenomicScoreAnnotatorBase):
    """This class implements the position_score_annotator.

    The position_score_annotator requires the resource_id parameter, whose
    value must be an id of a genomic resource of type position_score.

    The position_score resource provides a set of scores (see …) that the
    position_score_annotator uses as attributes to assign to the annotatable.

    The position_score_annotator recognizes two attribute level parameters,
    both of which apply to annotatables that refer to a region of the
    reference genome:

    - aggregator controls how the position scores are aggregated. The
      deprecated name position_aggregator is still accepted.
    - none_value_replacement stands in for every null of the region's
      per-position expansion -- a position no record covers, and a covered
      position whose value is NA -- before the aggregator sees it. Unset,
      nulls stay inert and every aggregator skips them, so a region's mean
      is the mean over its covered positions alone.

    Neither applies to an annotatable that never reaches the region fold: a
    substitution, which reads a single position; one on a chromosome the
    resource does not carry; or one longer than region_length_cutoff, which
    is declined before it is read.
    """

    def __init__(self, pipeline: AnnotationPipeline, info: AnnotatorInfo):

        resource = get_genomic_resource(pipeline, info, {"position_score"})
        self.position_score = build_position_score_from_resource(resource)
        super().__init__(pipeline, info, self.position_score)

        info.documentation += textwrap.dedent(f"""

Annotator to use with genomic scores depending on genomic position like
phastCons, phyloP, FitCons2, etc.

<a href="{self.BASE_DOC_URL}#position-score-annotator" target="_blank">More info</a>

""")  # ruff: ignore[line-too-long]

        for attr, attr_config in zip(
            self._attributes, self.get_info().attributes, strict=True,
        ):
            self.add_score_aggregator_documentation(
                attr, "aggregator", attr_config.aggregator)
            replacement_doc = self._none_value_replacement_documentation(attr)
            if replacement_doc is not None:
                self._append_attribute_documentation(attr, replacement_doc)

        # One query per attribute, in attribute order, so the tuple the
        # plane answers with indexes straight back to the names.  A source
        # named twice is two queries and two answers, which is the whole
        # reason the result is keyed by name.
        self._region_queries = [
            PositionScoreAggregationQuery(
                attr.source,
                self._query_aggregator(attr),
                # Reading the key is also what MARKS it used, which is what
                # keeps ``check_for_unused_attribute_parameters`` from
                # refusing the pipeline that sets it (#1135).  Absent, it
                # reads as ``None`` -- the plane's "leave nulls inert" --
                # which is what every attribute got before it was exposed.
                attr.parameters.get("none_value_replacement"))
            for attr in self._attributes
        ]
        # Ask the two questions a query asks now, and throw the answers
        # away: what is wanted is the refusal.  An attribute whose score
        # has no default aggregator and names none -- only ``bool`` can be
        # in that position -- fails HERE, as the pipeline loads, with the
        # plane's own remedy, rather than on the first region that reaches
        # it.  Resolution only; the read builds its aggregators per call.
        self.position_score.resolve_aggregation_queries(self._region_queries)

    def _none_value_replacement_documentation(
        self, attr: Attribute,
    ) -> str | None:
        """The line naming a configured replacement, or ``None`` if unset.

        An attribute is documented by two routes that do not share a
        string -- the one it carries, which the pipeline doc renders, and
        the properties list the web help builds -- so the line is built
        once here and handed to both.  An unset attribute documents
        nothing rather than a ``None``, which a reader would take for a
        configured value.
        """
        replacement = attr.parameters.get("none_value_replacement")
        if replacement is None:
            return None
        return f"**none_value_replacement**: {replacement}"

    def get_attribute_defaults(
        self, spec: AttributeSpec,
    ) -> dict[str, Any]:
        defaults = super().get_attribute_defaults(spec)
        if "aggregator" not in defaults:
            score_def = self.position_score.get_score_definition(spec.source)
            if score_def is not None and score_def.aggregator is not None:
                defaults["aggregator"] = score_def.aggregator
        return defaults

    def build_score_aggregator_documentation(
        self, attr: Attribute,
    ) -> list[str]:
        """Collect score aggregator documentation."""
        docs = [self._build_score_aggregator_documentation(
            attr, "aggregator", attr.aggregator)]
        replacement = self._none_value_replacement_documentation(attr)
        if replacement is not None:
            docs.append(replacement)
        return docs

    def _do_annotate(
        self, annotatable: Annotatable,
        context: dict[str, Any],  # ruff: ignore[unused-method-argument]
    ) -> AnnotatedValues:

        if not self.score.has_chromosome(annotatable.chromosome):
            return self._empty_result()

        if annotatable.type == Annotatable.Type.SUBSTITUTION:
            assert isinstance(annotatable, VCFAllele)
            # One source per attribute, in attribute order: the read
            # answers one value per id asked, a source named twice
            # included, so the answers pair back by position.  The base's
            # list is every attribute's source here -- a position score's
            # attribute specs ARE its score definitions, so the filter
            # that builds it drops nothing.  An uncovered position needs no
            # guard of its own for the same reason: it answers a tuple of
            # ``None``, which pairs to what ``_empty_result`` built.
            point_scores = self.position_score.get_scores_at_position(
                annotatable.chromosome, annotatable.position,
                self.simple_score_queries)
            return self._pair_all(
                point_scores, resource_id=self.position_score.resource_id)

        if len(annotatable) > self._region_length_cutoff:
            return self._empty_result()

        values = self.position_score.get_scores_in_region_agg(
            annotatable.chrom, annotatable.pos, annotatable.pos_end,
            self._region_queries)
        return self._pair_all(
            values, resource_id=self.position_score.resource_id)
