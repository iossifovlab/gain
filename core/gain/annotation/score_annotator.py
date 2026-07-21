"""This contains the implementation of the three score annotators.

Genomic score annotators defined are position_score_annotator,
np_score_annotator, and allele_score_annotator.
"""
import abc
import textwrap
from collections.abc import Callable
from typing import Any

from lark import Lark, Token, Tree

from gain import logging
from gain.annotation.annotatable import Annotatable, VCFAllele
from gain.annotation.annotate_utils import stringify
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
from gain.annotation.annotator_base import AnnotatorBase
from gain.genomic_resources.aggregators import (
    AggregatorSource,
    WeightedValues,
)
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    GenomicScore,
    GenomicScoreDef,
    PositionScore,
    ScoreLineBase,
)
from gain.genomic_resources.repository import GenomicResource
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
        self._region_length_cutoff = info.parameters.get(
            "region_length_cutoff", 500_000)

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
        """Collect score aggregator documentation."""
        default_aggregators = {
            "position_aggregator": {
                "float": "mean",
                "int": "mean",
                "str": "list",
            },
            "allele_aggregator": {
                "float": "max",
                "int": "max",
                "str": "list",
            },
        }
        aggregators_score_def_att: \
            dict[str, Callable[[GenomicScoreDef], str | None]] = {
                "position_aggregator":
                lambda sc: sc.pos_aggregator,
                "allele_aggregator":
                lambda sc: sc.allele_aggregator,
            }
        if attribute_conf_agg is None:
            score_def = self.score.get_score_definition(attr.source)
            assert score_def is not None
            value = aggregators_score_def_att[aggregator](score_def)
            if value is not None:
                value_str = f"`{value}` [default]"
            else:
                value = default_aggregators[aggregator][score_def.value_type]
                value_str = f"`{value}` [type default]"
        else:
            value_str = str(attribute_conf_agg)
        return f"**{aggregator}**: {value_str}"

    def add_score_aggregator_documentation(
            self, attr: Attribute,
            aggregator: str,
            attribute_conf_agg: AggregatorSource | None) -> None:
        """Collect score aggregator documentation."""
        aggregator_doc = self._build_score_aggregator_documentation(
            attr, aggregator, attribute_conf_agg)

        attr._documentation = (  # noqa: SLF001
            f"{attr.documentation}"
            f"\n\n{aggregator_doc}")

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

    The position_score_annotator recognizes one attribute level parameter
    called aggregator that controls how the position scores are
    aggregated for annotatables that refer to a region of the reference genome.
    The deprecated name position_aggregator is still accepted.
    """

    def __init__(self, pipeline: AnnotationPipeline, info: AnnotatorInfo):

        resource = get_genomic_resource(pipeline, info, {"position_score"})
        self.position_score = PositionScore(resource)
        super().__init__(pipeline, info, self.position_score)

        info.documentation += textwrap.dedent(f"""

Annotator to use with genomic scores depending on genomic position like
phastCons, phyloP, FitCons2, etc.

<a href="{self.BASE_DOC_URL}#position-score-annotator" target="_blank">More info</a>

""")  # noqa

        for attr, attr_config in zip(
            self._attributes, self.get_info().attributes, strict=True,
        ):
            self.add_score_aggregator_documentation(
                attr, "position_aggregator", attr_config.aggregator)

    def get_attribute_defaults(
        self, spec: AttributeSpec,
    ) -> dict[str, Any]:
        defaults = super().get_attribute_defaults(spec)
        if "aggregator" not in defaults:
            score_def = self.position_score.get_score_definition(spec.source)
            if score_def is not None and score_def.pos_aggregator is not None:
                defaults["aggregator"] = score_def.pos_aggregator
        return defaults

    def build_score_aggregator_documentation(
        self, attr: Attribute,
    ) -> list[str]:
        """Collect score aggregator documentation."""
        doc = self._build_score_aggregator_documentation(
            attr, "position_aggregator", attr.aggregator)
        return [doc]

    def _fetch_raw_region_scores(
        self, chrom: str, pos_begin: int, pos_end: int,
        sources: list[str],
    ) -> dict[str, WeightedValues]:
        """Collect the region's records as weighted values, one per record.

        The weight of a record -- how many base pairs of the region it
        covers -- comes from the score layer, which has already clipped
        the record to the query window.  Nothing here is materialised per
        base pair.
        """
        raw: dict[str, WeightedValues] = {
            source: WeightedValues() for source in sources}
        fetch = self.position_score.fetch_region_weighted_values(
            chrom, pos_begin, pos_end, sources,
        )
        for values, weight in fetch:
            if values is None:
                continue
            for i, source in enumerate(sources):
                raw[source].add(values[i], weight)
        return raw

    def _do_annotate(
        self, annotatable: Annotatable,
        context: dict[str, Any],  # noqa: ARG002
    ) -> dict[str, Any]:

        if annotatable.chromosome not in self.score.get_all_chromosomes():
            return self._empty_result()

        sources = list(dict.fromkeys(
            attr.source for attr in self._attributes))

        if annotatable.type == Annotatable.Type.SUBSTITUTION:
            assert isinstance(annotatable, VCFAllele)
            point_scores = self.position_score.fetch_scores(
                annotatable.chromosome, annotatable.position, sources)
            if not point_scores:
                return self._empty_result()
            return dict(zip(sources, point_scores, strict=True))

        if len(annotatable) > self._region_length_cutoff:
            return self._empty_result()
        raw = self._fetch_raw_region_scores(
            annotatable.chrom, annotatable.pos, annotatable.pos_end, sources)
        if not any(raw.values()):
            return self._empty_result()
        return raw


def build_np_score_annotator(pipeline: AnnotationPipeline,
                             info: AnnotatorInfo) -> Annotator:
    logger.warning(
        "usage of 'np_score_annotator' is deprecated, "
        "use 'allele_score_annotator' instead")
    return AlleleScoreAnnotator(pipeline, info)


def build_allele_score_annotator(pipeline: AnnotationPipeline,
                                 info: AnnotatorInfo) -> Annotator:
    return AlleleScoreAnnotator(pipeline, info)


class AlleleScoreAnnotator(GenomicScoreAnnotatorBase):
    """Annotator for allele-level genomic scores (frequencies, pathogenicity…).

    Operates in one of two modes, selected by the ``mode`` parameter:

    - ``allele`` (**default**): performs an exact chrom/pos/ref/alt lookup and
      returns the single matching line's scores.  The annotatable must be a
      ``VCFAllele``; other types receive an empty result.

    - ``region``: iterates all allele lines that overlap the annotatable's span
      and aggregates their scores.  Works with any ``Annotatable``
      (``VCFAllele``, ``Region``, CNV, …).  An aggregator must be defined for
      every score attribute, either in the attribute config or as the score's
      ``allele_aggregator`` default in the resource YAML.

    Virtual ``allele`` attribute
    ----------------------------
    All annotators expose a virtual attribute ``"allele"``
    (``is_default=False``)
    that is synthesised rather than read from the data file.

    - In ``allele`` mode: returns ``["chrom:pos:ref:alt"]`` for the matched
      line.
    - In ``region`` mode: returns the set of ``"chrom:pos:ref:alt"`` strings
      for all lines that pass the optional ``allele_filter``.

    Optionally append score values to each allele string with
    ``include_attributes``.

    ``allele_filter``
    -----------------
    An optional annotator-level boolean expression evaluated against each
    ``ScoreLineBase`` before it is included in the result.  Supported
    operators: ``>``, ``<``, ``==``, ``in``, ``and``, ``or``.  Variables
    resolve via ``ScoreLineBase.get_score``.
    """

    ALLELE_FILTER_GRAMMAR = textwrap.dedent("""
        ?start: filter | and_ | or

        and_: filter "and" filter

        or: filter "or" filter

        ?filter: subject operator subject | or | and_

        ?subject: variable | value

        value: "\\"" word "\\"" | number

        variable: word

        operator: equals | greater_than | less_than | in

        equals: "=="

        greater_than: ">"

        less_than: "<"

        in: "in"

        word: /[0-9]*[a-zA-Z_!@#$%^&*()_+][a-zA-Z0-9!@#$%^&*()_+]*/

        number: /-?[0-9]+\\.?[0-9]*/

        %ignore " "
    """)

    def __init__(self, pipeline: AnnotationPipeline, info: AnnotatorInfo):
        resource = get_genomic_resource(
            pipeline, info, {"np_score", "allele_score"})
        self.allele_score = AlleleScore(resource)
        self.filter_parser = Lark(self.ALLELE_FILTER_GRAMMAR)
        self.allele_filter = None
        allele_filter_str = info.parameters.get("allele_filter")
        if allele_filter_str is not None:
            assert isinstance(allele_filter_str, str)

            cnv_filter_str = allele_filter_str.replace(
                "\n", " ").replace("\t", " ").strip()
            try:
                self.allele_filter = self._build_allele_filter_func(
                    self.filter_parser.parse(cnv_filter_str))
            except Exception as e:
                raise AnnotationConfigurationError(
                    f"Error parsing cnv_filter: {e}") from e

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

""")  # noqa

        self.allele_attribute = None
        self.attrs_to_include = []
        self.allele_score_sources: list[str] = []

        for attr in self._attributes:
            if attr.source == "allele":
                self.attrs_to_include = attr.parameters.get(
                    "include_attributes", [])
                if isinstance(self.attrs_to_include, str):
                    self.attrs_to_include = [self.attrs_to_include]
                self.allele_attribute = attr
                continue
            self.allele_score_sources.append(attr.source)
            self.add_score_aggregator_documentation(
                attr, "allele_aggregator", attr.aggregator)

    @classmethod
    def _build_allele_filter_func(
        cls, tree: Tree,
    ) -> Callable[[ScoreLineBase], bool]:
        """Compile a Lark parse tree into a ScoreLineBase predicate."""
        if tree.data == "and_":
            assert isinstance(tree.children[0], Tree)
            assert isinstance(tree.children[1], Tree)
            left_func = cls._build_allele_filter_func(tree.children[0])
            right_func = cls._build_allele_filter_func(tree.children[1])
            return lambda cnv: left_func(cnv) and right_func(cnv)
        if tree.data == "or":
            left_func = cls._build_allele_filter_func(tree.children[0])
            right_func = cls._build_allele_filter_func(tree.children[1])
            return lambda cnv: left_func(cnv) or right_func(cnv)

        left = tree.children[0]
        assert isinstance(left, Tree)
        assert isinstance(left.data, Token)
        left_type = left.data.value
        if left_type == "variable":
            assert isinstance(left.children[0], Tree)
            assert isinstance(left.children[0].data, Token)
            assert left.children[0].data.value == "word"
            assert isinstance(left.children[0].children[0], Token)
            left_value = left.children[0].children[0].value

            def left_accessor(_score: ScoreLineBase) -> Any:
                return _score.get_score(left_value)
        else:
            assert isinstance(left.children[0], Tree)
            assert isinstance(left.children[0].data, Token)
            is_number = left.children[0].data.value == "number"
            assert isinstance(left.children[0].children[0], Token)
            left_value = left.children[0].children[0].value
            if is_number:
                left_value = float(left_value)

            def left_accessor(
                _score: ScoreLineBase,
            ) -> Any:  # pylint: disable=unused-argument
                return left_value
        assert isinstance(tree.children[1], Tree)
        assert isinstance(tree.children[1].children[0], Tree)
        assert isinstance(tree.children[1].children[0].data, Token)
        operator = tree.children[1].children[0].data.value
        right = tree.children[2]
        assert isinstance(right, Tree)
        assert isinstance(right.data, Token)
        right_type = right.data.value
        if right_type == "variable":
            assert isinstance(right.children[0], Tree)
            assert isinstance(right.children[0].data, Token)
            assert right.children[0].data.value == "word"
            assert isinstance(right.children[0].children[0], Token)
            right_value = right.children[0].children[0].value

            def right_accessor(_score: ScoreLineBase) -> Any:
                return _score.get_score(right_value)
        else:
            assert isinstance(right.children[0], Tree)
            assert isinstance(right.children[0].data, Token)
            is_number = right.children[0].data.value == "number"
            assert isinstance(right.children[0].children[0], Token)
            right_value = right.children[0].children[0].value
            if is_number:
                right_value = float(right_value)

            def right_accessor(
                _score: ScoreLineBase,
            ) -> Any:  # pylint: disable=unused-argument
                return right_value

        if operator == "equals":
            return lambda cnv: left_accessor(cnv) == right_accessor(cnv)
        if operator == "greater_than":
            return lambda cnv: left_accessor(cnv) > right_accessor(cnv)
        if operator == "less_than":
            return lambda cnv: left_accessor(cnv) < right_accessor(cnv)
        if operator == "in":
            return lambda cnv: left_accessor(cnv) in right_accessor(cnv)

        raise ValueError(f"Unsupported operator {operator.data}")

    def get_attribute_defaults(
        self, spec: AttributeSpec,
    ) -> dict[str, Any]:
        defaults = super().get_attribute_defaults(spec)
        if "aggregator" not in defaults:
            score_def = self.allele_score.get_score_definition(spec.source)
            if score_def is not None \
                    and score_def.allele_aggregator is not None:
                defaults["aggregator"] = score_def.allele_aggregator
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
            attr, "allele_aggregator", attr.aggregator,
        )
        return [allele_doc]

    def _annotate_allele(
        self, annotatable: VCFAllele,
    ) -> dict[str, Any]:
        """Return scores for an exact chrom/pos/ref/alt match."""
        line = self.allele_score.fetch_allele_line(
            annotatable.chrom,
            annotatable.position,
            annotatable.reference,
            annotatable.alternative,
        )
        if line is None:
            return self._empty_result()

        if self.allele_filter is not None and not self.allele_filter(line):
            return self._empty_result()

        scores: dict[str, Any] = {
            sc: line.get_score(sc)
            for sc in (
                self.simple_score_queries or self.allele_score.get_all_scores()
            )
        }

        if self.allele_attribute is not None:
            allele_str = (
                f"{annotatable.chromosome}:{annotatable.position}"
                f":{annotatable.reference}:{annotatable.alternative}"
            )
            if self.attrs_to_include:
                attrs_str = ",".join(
                    stringify(scores.get(a)) for a in self.attrs_to_include)
                allele_str += f":{attrs_str}"
            scores[self.allele_attribute.source] = [allele_str]

        return {
            attr.source: scores.get(attr.source) for attr in self.attributes
        }

    def _annotate_region(
        self, annotatable: Annotatable,
    ) -> dict[str, Any]:
        """Collect raw score lists for all allele lines overlapping the region.

        Aggregation is handled by AnnotatorBase._apply_aggregators.
        """
        raw: dict[str, list] = {
            source: [] for source in self.allele_score_sources}
        alleles: set[str] = set()
        has_lines = False

        for line in self.allele_score.fetch_lines(
            annotatable.chrom, annotatable.position, annotatable.pos_end,
        ):
            has_lines = True
            if self.allele_filter is not None and not self.allele_filter(line):
                continue

            for source in self.allele_score_sources:
                raw[source].append(line.get_score(source))

            if self.allele_attribute is not None:
                allele_str = f"{line.chrom}:{line.pos_begin}"
                if line.ref is not None and line.alt is not None:
                    allele_str += f":{line.ref}:{line.alt}"
                if self.attrs_to_include:
                    attrs_str = ",".join(
                        stringify(line.get_score(a))
                        for a in self.attrs_to_include)
                    allele_str += f":{attrs_str}"
                alleles.add(allele_str)

        if not has_lines:
            return self._empty_result()

        result = {
            attr.source: raw.get(attr.source) for attr in self.attributes
        }
        if self.allele_attribute is not None:
            result[self.allele_attribute.source] = list(alleles)
        return result

    def _do_annotate(
        self, annotatable: Annotatable,
        context: dict[str, Any],  # noqa: ARG002
    ) -> dict[str, Any]:
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
