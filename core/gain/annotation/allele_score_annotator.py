"""The ``allele_score_annotator``.

Annotates with scores keyed by allele -- frequencies, pathogenicity
predictions and the like -- read from an ``allele_score`` resource, by
exact allele match or reduced over a region.
"""
import textwrap
from typing import Any

from gain.annotation.annotatable import Annotatable, VCFAllele
from gain.annotation.annotation_config import (
    AnnotationConfigurationError,
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
    ScoreAggregationQuery,
)
from gain.genomic_resources.genomic_scores import (
    allele_key,
    build_allele_score_from_resource,
)
from gain.genomic_resources.resource_types import (
    PREFERRED_ALLELE_SCORE_TYPE,
)
from gain.genomic_resources.score_filter import ScoreFilterError


def build_allele_score_annotator(pipeline: AnnotationPipeline,
                                 info: AnnotatorInfo) -> Annotator:
    return AlleleScoreAnnotator(pipeline, info)


class AlleleScoreAnnotator(GenomicScoreAnnotatorBase):
    """Annotator for allele-level genomic scores (frequencies, pathogenicity…).

    Operates in one of two modes, selected by the ``mode`` parameter:

    - ``allele`` (**default**): performs an exact chrom/pos/ref/alt lookup and
      returns the single matching line's scores.  The annotatable must be a
      ``VCFAllele``; other types receive an empty result.

    - ``region``: the score reduces all allele lines that overlap the
      annotatable's span, in one streaming walk
      (``AlleleScore.get_allele_scores_in_region_agg``).  Works with any
      ``Annotatable`` (``VCFAllele``, ``Region``, CNV, …).  An aggregator
      must be defined for every score attribute, either in the attribute
      config or as the score's ``aggregator`` default in the resource YAML;
      an attribute with neither -- only a ``bool`` score can be in that
      position -- is refused when the pipeline loads, in either mode,
      because a CNV or a region takes the region path whatever the mode.

    Virtual ``allele`` attribute
    ----------------------------
    All annotators expose a virtual attribute ``"allele"``
    (``is_default=False``)
    that is synthesised rather than read from the data file.

    - In ``allele`` mode: returns ``["chrom:pos:ref:alt"]`` for the matched
      line.
    - In ``region`` mode: returns the distinct ``"chrom:pos:ref:alt"``
      strings of the lines that pass the optional ``allele_filter``, in the
      order the lines were first met -- the resource's own genomic order.

    Optionally append score values to each allele string with
    ``include_attributes``.  The string's format is the score's,
    :func:`~gain.genomic_resources.genomic_scores.allele.allele_key`, so
    the two modes cannot drift.

    An aggregator named on this attribute reduces nothing, in either
    mode.  It is not a score: its value is the keys the annotator
    synthesised, and ``region`` mode has always answered them beside the
    reductions rather than as one of them.  Exact-match mode used to
    differ -- the base folded its one-element list -- and stopped in
    gain#1133, so the two modes now agree.

    ``allele_filter``
    -----------------
    An optional annotator-level boolean expression evaluated against each
    record before it is included in the result.  The annotator only resolves
    the parameter; the expression language belongs to the score, so see
    :meth:`GenomicScore.compile_filter` for the operators it admits and what
    a name may contain.
    """

    def __init__(self, pipeline: AnnotationPipeline, info: AnnotatorInfo):
        resource = get_genomic_resource(
            pipeline, info, {PREFERRED_ALLELE_SCORE_TYPE})
        self.allele_score = build_allele_score_from_resource(resource)
        self.allele_filter = None
        allele_filter_str = info.parameters.get("allele_filter")
        if allele_filter_str is not None:
            assert isinstance(allele_filter_str, str)

            try:
                self.allele_filter = self.allele_score.compile_filter(
                    allele_filter_str)
            except ScoreFilterError as e:
                # Named after the parameter the user wrote: the score knows
                # nothing about how the expression reached it (cf. gain#477).
                raise AnnotationConfigurationError(
                    f"Error parsing allele_filter: {e}") from e

        mode = info.parameters.get("mode", "allele")
        if mode not in {"allele", "region"}:
            raise AnnotationConfigurationError(
                f"Invalid mode '{mode}' for allele_score_annotator; "
                "valid values are 'allele' and 'region'")
        self.mode = mode

        super().__init__(pipeline, info, self.allele_score)
        info.documentation += textwrap.dedent(f"""

Annotator to use with scores that depend on allele like
variant frequencies, etc.

**Mode** (``mode`` parameter, applies to ``VCFAllele`` inputs only):

- ``allele`` (default): exact chrom/pos/ref/alt match.
- ``region``: aggregates scores for all allele lines overlapping the
  annotatable's span.

Non-``VCFAllele`` annotatables always use region aggregation.

<a href="{self.BASE_DOC_URL}#allele-score-annotator" target="_blank">More info</a>

""")  # ruff: ignore[line-too-long]

        self.allele_attribute = None
        self.attrs_to_include: list[str] = []

        for attr in self._attributes:
            if attr.source == "allele":
                attrs_to_include = attr.parameters.get(
                    "include_attributes", [])
                if isinstance(attrs_to_include, str):
                    attrs_to_include = [attrs_to_include]
                self.attrs_to_include = list(attrs_to_include)
                self.allele_attribute = attr
                continue
            self.add_score_aggregator_documentation(
                attr, "aggregator", attr.aggregator)

        # One query per SCORE attribute, as `PositionScoreAnnotator` builds
        # its list and for the reasons its query block gives.  The virtual
        # `allele` attribute is not a score and asks the read for the keys
        # instead.
        self._region_queries = [
            ScoreAggregationQuery(attr.source, self._query_aggregator(attr))
            for attr in self._attributes
            if attr is not self.allele_attribute
        ]
        # Resolved now for the refusal alone -- at load, in BOTH modes; see
        # the `region` bullet of the class docstring -- and the same for the
        # `include_attributes` ids.
        self.allele_score.resolve_aggregation_queries(self._region_queries)
        self.allele_score.resolve_allele_key_scores(self.attrs_to_include)

    def get_attribute_defaults(
        self, spec: AttributeSpec,
    ) -> dict[str, Any]:
        defaults = super().get_attribute_defaults(spec)
        if "aggregator" not in defaults:
            score_def = self.allele_score.get_score_definition(spec.source)
            if score_def is not None \
                    and score_def.aggregator is not None:
                defaults["aggregator"] = score_def.aggregator
        return defaults

    def get_attribute_specs(self) -> dict[str, AttributeSpec]:
        """Return score attribute specs plus the virtual ``allele``."""
        result = super().get_attribute_specs()
        result["allele"] = AttributeSpec(
            source="allele",
            value_type="list",
            description="The allele in the format 'chr:pos:ref:alt'",
            is_default=False,
            internal_default=False,
        )
        return result

    def build_score_aggregator_documentation(
        self, attr: Attribute,
    ) -> list[str]:
        """Collect score aggregator documentation."""
        allele_doc = self._build_score_aggregator_documentation(
            attr, "aggregator", attr.aggregator,
        )
        return [allele_doc]

    def _annotate_allele(
        self, annotatable: VCFAllele,
    ) -> AnnotatedValues:
        """Return scores for an exact chrom/pos/ref/alt match."""
        values = self.allele_score.fetch_allele_scores(
            annotatable.chrom,
            annotatable.position,
            annotatable.reference,
            annotatable.alternative,
            self.simple_score_queries or None,
            score_filter=self.allele_filter,
        )
        if values is None:
            return self._empty_result()
        # Widened, because the virtual `allele` attribute below is a LIST of
        # strings and the score's own values are scalars.
        scores: dict[str, Any] = dict(values)

        if self.allele_attribute is not None:
            # The same helper the region read builds its keys with, so
            # the two paths spell an allele identically.
            scores[self.allele_attribute.source] = [allele_key(
                annotatable.chromosome, annotatable.position,
                annotatable.reference, annotatable.alternative,
                [scores.get(a) for a in self.attrs_to_include])]

        # Not ``fold_own_values``: the virtual ``allele`` attribute's
        # key list is the answer, not something to reduce.
        return self._from_sources(scores)

    def _annotate_region(
        self, annotatable: Annotatable,
    ) -> AnnotatedValues:
        """Answer the region already reduced, keyed by attribute name.

        The SCORE reduces (gain#1163): one value per query and, when the
        virtual ``allele`` attribute is configured, the distinct allele
        keys, off a single walk that never materialises the records.
        That removes the per-record list this path used to hold beside
        the aggregators (gain#834); what an aggregator itself keeps --
        ``list``, the ``str`` default, keeps every value -- is the
        aggregator's property and stays.
        """
        aggregate = self.allele_score.get_allele_scores_in_region_agg(
            annotatable.chrom, annotatable.position, annotatable.pos_end,
            queries=self._region_queries,
            allele_keys=(
                self.attrs_to_include
                if self.allele_attribute is not None else None),
            score_filter=self.allele_filter,
        )
        # `None` is absent data -- no record overlaps the region -- and
        # answers `None` for every attribute, as it always has.  An
        # aggregate whose fold saw nothing is different: records were
        # there and the filter rejected them all, so each aggregator has
        # answered for an empty selection and the keys are empty.
        if aggregate is None:
            return self._empty_result()

        # Paired back over the same attributes that built the queries, the
        # `allele` attribute taking the keys.
        return self._pair_aggregated(
            aggregate.values, len(self._region_queries),
            resource_id=self.allele_score.resource_id,
            reduced=lambda attr: attr is not self.allele_attribute,
            otherwise=lambda _attr: list(aggregate.allele_keys or ()))

    def _do_annotate(
        self, annotatable: Annotatable,
        context: dict[str, Any],  # ruff: ignore[unused-method-argument]
    ) -> AnnotatedValues:
        """Dispatch annotation based on annotatable type and mode.

        For VCFAllele: mode selects between exact-match and region aggregation.
        For all other annotatables: always use region aggregation.
        """
        all_chroms = self.allele_score.get_all_chromosomes()
        if annotatable.chromosome not in all_chroms:
            return self._empty_result()

        if isinstance(annotatable, VCFAllele):
            if self.mode == "allele":
                return self._annotate_allele(annotatable)
            return self._annotate_region(annotatable)

        if len(annotatable) > self._region_length_cutoff:
            return self._empty_result()
        return self._annotate_region(annotatable)
