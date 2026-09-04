"""This contains the implementation of the two score annotators.

Genomic score annotators defined are position_score_annotator and
allele_score_annotator.
"""
import abc
import textwrap
from typing import Any

from gain import logging
from gain.annotation.annotatable import Annotatable, VCFAllele
from gain.annotation.annotation_config import (
    AnnotationConfigParser,
    AnnotationConfigurationError,
    AnnotatorInfo,
    Attribute,
    AttributeConfig,
)
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    Annotator,
    AttributeSpec,
)
from gain.annotation.annotator_base import AnnotatedValues, AnnotatorBase
from gain.genomic_resources.aggregators import (
    AggregatorSource,
    PositionScoreAggregationQuery,
    ScoreAggregationQuery,
    aggregator_name,
)
from gain.genomic_resources.genomic_scores import (
    GenomicScore,
    allele_key,
    build_allele_score_from_resource,
    build_position_score_from_resource,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.resource_types import (
    PREFERRED_ALLELE_SCORE_TYPE,
    reject_retired_resource,
)
from gain.genomic_resources.score_filter import ScoreFilterError
from gain.templates import get_template

logger = logging.getLogger(__name__)


def get_genomic_resource(
        pipeline: AnnotationPipeline, info: AnnotatorInfo,
        resource_types: set[str]) -> GenomicResource:
    """Return genomic score resource used for given genomic score annotator."""
    if "resource_id" not in info.parameters:
        raise ValueError(f"The {info} has not 'resource_id' parameters")
    resource_id = info.parameters["resource_id"]
    resource = pipeline.repository.get_resource(resource_id)
    # Before the membership test: a retired spelling is a type GAIn used to
    # accept, and the generic message below would only say the annotator
    # wants something else -- true, and no help to someone holding a
    # resource that worked last release (gain#920).
    reject_retired_resource(resource)
    if resource.get_type() not in resource_types:
        raise ValueError(
            f"The {info} requires 'resource_id' to point to a "
            f"resource of type {resource_types}; "
            f"resource of type <{resource.get_type()}> found.")
    return resource


class GenomicScoreAnnotatorBase(AnnotatorBase):
    """Genomic score base annotator."""

    def __init__(self, pipeline: AnnotationPipeline, info: AnnotatorInfo,
                 score: GenomicScore):
        self.score = score
        self._resource_attr_params: dict[str, dict[str, Any]] = {}
        info.resources.append(score.resource)

        default_annotation = self.score.get_config().get("default_annotation")
        if default_annotation is not None:
            score_defs = self.score.score_definitions
            parsed_defaults = [
                AnnotationConfigParser.parse_raw_attribute_config(attr)
                for attr in default_annotation
            ]
            for parsed in parsed_defaults:
                if parsed.source not in score_defs:
                    raise ValueError(
                        f"Default annotation attribute '{parsed.source}' is "
                        "not defined in the score resource!")
                params = {
                    k: v for k, v in parsed.parameters.items()
                    if k != "description"
                }
                if parsed.aggregator is not None:
                    params["aggregator"] = parsed.aggregator
                if params:
                    self._resource_attr_params[parsed.source] = params
            if not info.attributes:
                defaults_by_source = {p.source: p for p in parsed_defaults}
                for source in score_defs:
                    if source not in defaults_by_source:
                        continue
                    parsed = defaults_by_source[source]
                    info.attributes.append(AttributeConfig(
                        name=parsed.name or parsed.source,
                        source=parsed.source,
                        internal=parsed.internal,
                        aggregator=parsed.aggregator,
                    ))

        super().__init__(pipeline, info)
        # A count of bases, read through the accessor that says so: it is
        # compared against an annotatable's length, and anything that is
        # not a whole non-negative number is refused here, as the
        # pipeline loads.  Read with a bare `.get()` it reached the
        # comparison instead and raised per annotated variant
        # (gain#1166).
        self._region_length_cutoff = info.parameters.get_integer(
            "region_length_cutoff", default=500_000, minimum=0)

        self.simple_score_queries: list[str] = [
            attr.source for attr in self._attributes
            if attr.source in self.score.score_definitions]

    def open(self) -> Annotator:
        self.score.open()
        super().open()
        return self

    def is_open(self) -> bool:
        return self.score.is_open()

    def _collect_score_queries(self) -> list:
        return []

    @staticmethod
    def _query_aggregator(attr: Attribute) -> str | None:
        """The aggregator NAME an attribute puts on its region query.

        ``None`` when the attribute names none, passed through to be
        refused by the score's resolver as the pipeline loads -- only a
        ``bool`` score, which has no default, can be in that position.
        """
        return (
            aggregator_name(attr.aggregator)
            if attr.aggregator is not None else None)

    def close(self) -> None:
        self.score.close()
        super().close()

    def get_attribute_specs(self) -> dict[str, AttributeSpec]:
        default_annotation = self.score.get_config().get("default_annotation")
        specs = {
            attr_source: AttributeSpec(
                source=attr_def.score_id,
                value_type=attr_def.value_type,
                description=attr_def.desc,
                is_default=default_annotation is None,
                internal_default=False,
            )
            for attr_source, attr_def in self.score.score_definitions.items()
        }
        if default_annotation is not None:
            for attr in default_annotation:
                parsed = \
                    AnnotationConfigParser.parse_raw_attribute_config(attr)
                if parsed.source in specs:
                    specs[parsed.source].is_default = True
        return specs

    def get_attribute_defaults(
        self, spec: AttributeSpec,
    ) -> dict[str, Any]:
        return dict(self._resource_attr_params.get(spec.source, {}))

    def _build_score_aggregator_documentation(
        self, attr: Attribute,
        aggregator: str,
        attribute_conf_agg: AggregatorSource | None,
    ) -> str:
        """Collect score aggregator documentation.

        No fallback for an unset aggregator, and no local copy of the
        per-value-type defaults: the score class owns that table
        (``GenomicScore.DEFAULT_AGGREGATORS``) and ``_build_scoredefs``
        applies it, so a definition's ``aggregator`` is already resolved by
        the time anything reads it.
        """
        if attribute_conf_agg is None:
            score_def = self.score.get_score_definition(attr.source)
            assert score_def is not None
            value_str = f"`{score_def.aggregator}` [default]"
        else:
            value_str = str(attribute_conf_agg)
        return f"**{aggregator}**: {value_str}"

    @staticmethod
    def _append_attribute_documentation(attr: Attribute, line: str) -> None:
        """Add one markdown line to what an attribute documents about itself.

        The separator and the write to the attribute's own string live here
        so that everything documenting an attribute agrees on them without
        each caller spelling the append out again.
        """
        attr._documentation = (  # ruff: ignore[private-member-access]
            f"{attr.documentation}\n\n{line}")

    def add_score_aggregator_documentation(
            self, attr: Attribute,
            aggregator: str,
            attribute_conf_agg: AggregatorSource | None) -> None:
        """Collect score aggregator documentation."""
        self._append_attribute_documentation(
            attr,
            self._build_score_aggregator_documentation(
                attr, aggregator, attribute_conf_agg))

    @abc.abstractmethod
    def build_score_aggregator_documentation(
        self, attr: Attribute,
    ) -> list[str]:
        """Construct score aggregator documentation."""

    def build_attribute_help(self, attr: Attribute) -> str:
        """Build attribute help."""
        hist_url = self.score.get_histogram_image_public_url(attr.source)
        score_def = self.score.get_score_definition(attr.source)
        assert score_def is not None

        histogram = get_template("score_histogram.jinja").render(
            hist_url=hist_url,
            score_def=score_def,
        )

        assert attr.spec is not None
        data = {
            "name": attr.name,
            "description": attr.spec.description,
            "resource_id": self.score.resource_id,
            "resource_summary": self.score.resource.get_summary(),
            "resource_url":
            f"{self.score.resource.get_public_url()}/index.html",
            "resource_type": self.score.resource.get_type(),
            "histogram": histogram,
            "source": attr.source,
            "aggregators": self.build_score_aggregator_documentation(
                attr,
            ),
            "annotator_type": self.get_info().type,
            "annotator_doc": self.get_info().documentation,
        }
        return get_template("genomic_score_help.jinja").render(data=data)


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

        if annotatable.chromosome not in self.score.get_all_chromosomes():
            return self._empty_result()

        if annotatable.type == Annotatable.Type.SUBSTITUTION:
            assert isinstance(annotatable, VCFAllele)
            # One source per attribute, in attribute order: the read
            # answers one value per id asked, a source named twice
            # included, so the answers pair back by position.  The base's
            # list is every attribute's source here -- a position score's
            # attribute specs ARE its score definitions, so the filter
            # that builds it drops nothing.
            point_scores = self.position_score.fetch_position_scores(
                annotatable.chromosome, annotatable.position,
                self.simple_score_queries)
            if not point_scores:
                return self._empty_result()
            return self._pair_all(
                point_scores, resource_id=self.position_score.resource_id)

        if len(annotatable) > self._region_length_cutoff:
            return self._empty_result()

        values = self.position_score.get_scores_in_region_agg(
            annotatable.chrom, annotatable.pos, annotatable.pos_end,
            self._region_queries)
        return self._pair_all(
            values, resource_id=self.position_score.resource_id)


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
