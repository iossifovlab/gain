"""This contains the implementation of the three score annotators.

Genomic score annotators defined are positions_score, np_score,
and allele_score.
"""
import abc
import logging
import textwrap
from collections.abc import Callable
from typing import Any, cast

from jinja2 import Template
from lark import Lark, Token, Tree

from gain.annotation.annotatable import Annotatable, VCFAllele
from gain.annotation.annotate_utils import stringify
from gain.annotation.annotation_config import (
    AnnotationConfigParser,
    AnnotationConfigurationError,
    AnnotatorInfo,
    AttributeInfo,
)
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    Annotator,
)
from gain.annotation.annotator_base import AttributeDesc
from gain.genomic_resources.aggregators import (
    build_aggregator,
    validate_aggregator,
)
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    AlleleScoreQuery,
    GenomicScore,
    PositionScore,
    PositionScoreQuery,
    ScoreDef,
    ScoreLine,
)
from gain.genomic_resources.repository import GenomicResource

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


class GenomicScoreAnnotatorBase(Annotator):
    """Genomic score base annotator."""

    SCORE_HISTOGRAM = textwrap.dedent("""
    <div class="modal-histogram">

    <div class="histogram-image">

    ![HISTOGRAM]({{ hist_url }})

    </div>

    </div>
    """)

    GENOMIC_SCORE_HELP = textwrap.dedent("""

    <div class="score-description">

    ## {{ data.name }}

    {{ data.description}}

    {{ data.resource_summary }}

    {{ data.histogram }}

    Genomic resource:
    <a href={{data.resource_url}} target="_blank">{{ data.resource_id }}</a>

    <details>

    <summary class="details">

    #### Details

    </summary>

    <div class="details-body">

    ##### Attribute properties:

    * **source**: {{ data.source }}
    {% for aggregator in data.aggregators %}

    * {{ aggregator }}

    {% endfor %}


    ##### Resource properties:

    * **resource_type**: `{{ data.resource_type }}`


    ##### Annotator documentation:

    * **annotator_type**: `{{ data.annotator_type }}`

    {{ data.annotator_doc }}

    </div>

    </details>

    </div>

    """)

    def __init__(self, pipeline: AnnotationPipeline, info: AnnotatorInfo,
                 score: GenomicScore):
        self.score = score
        super().__init__(pipeline, info)
        self._region_length_cutoff = info.parameters.get(
            "region_length_cutoff", 500_000)

        info.resources.append(score.resource)

        if info.attributes:
            for attribute_info in info.attributes:
                if attribute_info.source not in self.score.score_definitions:
                    continue
                score_def = score.get_score_definition(attribute_info.source)
                if score_def is None:
                    message = (
                        f"The score '{attribute_info.source}' is "
                        f"unknown in '{score.resource.get_id()}' "
                        "resource!")
                    raise ValueError(message)
                attribute_info.value_type = score_def.value_type
                attribute_info.description = score_def.desc
        else:
            info.attributes = []
            for attr_desc in self.get_all_attribute_descriptions().values():
                if attr_desc.default:
                    attr = AttributeInfo(
                        name=cast(str, attr_desc.name),
                        source=attr_desc.source,
                        internal=attr_desc.internal,
                        parameters={},
                        _type=attr_desc.type,
                        description=attr_desc.description,
                        attribute_type=attr_desc.attribute_type,
                        default=attr_desc.default,
                    )
                    info.attributes.append(attr)

        self.simple_score_queries: list[str] = [
            attr.source for attr in info.attributes
            if attr.source in self.score.score_definitions]

    def open(self) -> Annotator:
        self.score.open()
        return self

    def is_open(self) -> bool:
        return self.score.is_open()

    def _collect_score_queries(self) -> list:
        return []

    def close(self) -> None:
        self.score.close()
        super().close()

    def get_all_attribute_descriptions(self) -> dict[str, AttributeDesc]:
        attribute_defs = self.score.score_definitions
        result = {}
        for attr_source, attr_def in attribute_defs.items():
            result[attr_source] = AttributeDesc(
                source=attr_def.score_id,
                name=attr_def.score_id,
                type=attr_def.value_type,
                description=attr_def.desc,
                default=True,
                internal=False,
            )

        default_annotation = self.score.get_config().get("default_annotation")
        if default_annotation is not None:
            for desc in result.values():
                desc.default = False
            for attr in default_annotation:
                default_attr = \
                    AnnotationConfigParser.parse_raw_attribute_config(attr)
                if default_attr.source not in result:
                    raise ValueError(
                        f"Default annotation attribute '{attr}' is not "
                        "defined in the score resource!")

                result[default_attr.source].source = default_attr.source
                if default_attr.name:
                    result[default_attr.source].name = default_attr.name
                if default_attr.description:
                    result[default_attr.source].description = \
                        default_attr.description
                if len(default_attr.parameters) > 0:
                    result[default_attr.source].params = cast(
                        dict, default_attr.parameters)
                result[default_attr.source].default = True
                if default_attr.internal is not None:
                    result[default_attr.source].internal = \
                        default_attr.internal
        return result

    def _build_score_aggregator_documentation(
        self, attribute_info: AttributeInfo,
        aggregator: str,
        attribute_conf_agg: str | None,
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
            dict[str, Callable[[ScoreDef], str | None]] = {
                "position_aggregator":
                lambda sc: sc.pos_aggregator,
                "allele_aggregator":
                lambda sc: sc.allele_aggregator,
            }
        if attribute_conf_agg is None:
            score_def = self.score.get_score_definition(attribute_info.source)
            assert score_def is not None
            value = aggregators_score_def_att[aggregator](
                cast(ScoreDef, score_def))
            if value is not None:
                value_str = f"`{value}` [default]"
            else:
                value = default_aggregators[aggregator][score_def.value_type]
                value_str = f"`{value}` [type default]"
        else:
            value_str = attribute_conf_agg
        return f"**{aggregator}**: {value_str}"

    def add_score_aggregator_documentation(
            self, attribute_info: AttributeInfo,
            aggregator: str,
            attribute_conf_agg: str | None) -> None:
        """Collect score aggregator documentation."""
        # pylint: disable=protected-access
        aggregator_doc = self._build_score_aggregator_documentation(
            attribute_info, aggregator, attribute_conf_agg)

        attribute_info._documentation = (  # noqa: SLF001
            f"{attribute_info.documentation}"
            f"\n\n{aggregator_doc}")

    @abc.abstractmethod
    def build_score_aggregator_documentation(
        self, attr_info: AttributeInfo,
    ) -> list[str]:
        """Construct score aggregator documentation."""

    def build_attribute_help(self, attr_info: AttributeInfo) -> str:
        """Build attribute help."""
        hist_url = self.score.get_histogram_image_url(attr_info.source)
        score_def = self.score.get_score_definition(attr_info.source)
        assert score_def is not None

        histogram = Template(self.SCORE_HISTOGRAM).render(
            hist_url=hist_url,
            score_def=score_def,
        )

        data = {
            "name": attr_info.name,
            "description": attr_info.description,
            "resource_id": self.score.resource_id,
            "resource_summary": self.score.resource.get_summary(),
            "resource_url":
            f"{self.score.resource.get_public_url()}/index.html",
            "resource_type": self.score.resource.get_type(),
            "histogram": histogram,
            "source": attr_info.source,
            "aggregators": self.build_score_aggregator_documentation(
                attr_info,
            ),
            "annotator_type": self.get_info().type,
            "annotator_doc": self.get_info().documentation,
        }
        template = Template(self.GENOMIC_SCORE_HELP)
        return template.render(data=data)


def build_position_score_annotator(pipeline: AnnotationPipeline,
                                   info: AnnotatorInfo) -> Annotator:
    return PositionScoreAnnotator(pipeline, info)


class PositionScoreAnnotator(GenomicScoreAnnotatorBase):
    """This class implements the position_score annotator.

    The position_score
    annotator requires the resrouce_id parameter, whose value must be an id
    of a genomic resource of type position_score.

    The position_score resource provides a set of scores (see …) that the
    position_score annotator uses as attributes to assign to the annotatable.

    The position_score annotator recognized one attribute level parameter
    called position_aggregator that controls how the position scores are
    aggregator for annotates that ref to a region of the reference genome.
    """

    def __init__(self, pipeline: AnnotationPipeline, info: AnnotatorInfo):

        resource = get_genomic_resource(pipeline, info, {"position_score"})
        self.position_score = PositionScore(resource)
        super().__init__(pipeline, info, self.position_score)

        self.position_score_queries = []
        info.documentation += textwrap.dedent(f"""

Annotator to use with genomic scores depending on genomic position like
phastCons, phyloP, FitCons2, etc.

<a href="{self.BASE_DOC_URL}#position-score-annotator" target="_blank">More info</a>

""")  # noqa

        for att_info in info.attributes:
            pos_aggregator = att_info.parameters.get("position_aggregator")
            if pos_aggregator:
                validate_aggregator(pos_aggregator)
            self.position_score_queries.append(
                PositionScoreQuery(att_info.source, pos_aggregator))

            self.add_score_aggregator_documentation(
                att_info, "position_aggregator", pos_aggregator)

    def build_score_aggregator_documentation(
        self, attr_info: AttributeInfo,
    ) -> list[str]:
        """Collect score aggregator documentation."""
        # pylint: disable=protected-access
        pos_aggregator = attr_info.parameters.get("position_aggregator")

        doc = self._build_score_aggregator_documentation(
            attr_info, "position_aggregator", pos_aggregator)
        return [doc]

    def _fetch_substitution_scores(self, allele: VCFAllele) \
            -> list[Any] | None:
        return self.position_score.fetch_scores(
            allele.chromosome, allele.position, self.simple_score_queries)

    def _fetch_aggregated_scores(
            self, chrom: str, pos_begin: int, pos_end: int) -> list[Any]:
        scores_agg = self.position_score.fetch_scores_agg(
            chrom, pos_begin, pos_end, self.position_score_queries,
        )
        return [sagg.get_final() for sagg in scores_agg]

    def annotate(
        self, annotatable: Annotatable | None,
        context: dict[str, Any],  # noqa: ARG002
    ) -> dict[str, Any]:

        if annotatable is None:
            return self._empty_result()

        if annotatable.chromosome not in self.score.get_all_chromosomes():
            return self._empty_result()

        if annotatable.type == Annotatable.Type.SUBSTITUTION:
            assert isinstance(annotatable, VCFAllele)
            scores = self._fetch_substitution_scores(annotatable)
        else:
            if len(annotatable) > self._region_length_cutoff:
                scores = None
            else:
                scores = self._fetch_aggregated_scores(
                    annotatable.chrom, annotatable.pos, annotatable.pos_end)
        if not scores:
            return self._empty_result()

        return dict(zip(
                [att.name for att in self.attributes],
                scores, strict=True))


def build_np_score_annotator(pipeline: AnnotationPipeline,
                             info: AnnotatorInfo) -> Annotator:
    logger.warning(
        "usage of 'np_score' annotator is deprecated, "
        "use 'allele_score' annotator instead")
    return AlleleScoreAnnotator(pipeline, info)


def build_allele_score_annotator(pipeline: AnnotationPipeline,
                                 info: AnnotatorInfo) -> Annotator:
    return AlleleScoreAnnotator(pipeline, info)


class AlleleScoreAnnotator(GenomicScoreAnnotatorBase):
    """This class implements allele_score annotator."""

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

        word: /[a-zA-Z0-9!@#$%^&*()_+]+/

        number: /[0-9\\.]+/

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
                f"Invalid mode '{mode}' for allele_score annotator; "
                "valid values are 'allele' and 'region'")
        self.mode = mode

        super().__init__(pipeline, info, self.allele_score)
        self.allele_score_queries = []
        info.documentation += textwrap.dedent(f"""

Annotator to use with scores that depend on allele like
variant frequencies, etc.

<a href="{self.BASE_DOC_URL}#allele-score-annotator" target="_blank">More info</a>

""")  # noqa

        self.allele_attribute = None
        self.attrs_to_include = []

        for att_info in info.attributes:
            if att_info.source == "allele":
                self.attrs_to_include = att_info.parameters.get(
                    "include_attributes", [])
                if isinstance(self.attrs_to_include, str):
                    self.attrs_to_include = [self.attrs_to_include]
                self.allele_attribute = att_info
                continue
            pos_agg = att_info.parameters.get("position_aggregator")
            if pos_agg is not None:
                logger.warning(
                    "attribute `position_aggregator` is no longer used "
                    "in allele_score annotator and will be ignored")
            nuc_agg = att_info.parameters.get("nucleotide_aggregator")
            allele_agg = att_info.parameters.get("allele_aggregator")
            if nuc_agg is not None:
                logger.warning(
                    "attibute `nucleotide_aggregator` is deprecated, "
                    "use `allele_aggregator` instead")
                assert allele_agg is None
                allele_agg = nuc_agg

            if allele_agg:
                validate_aggregator(allele_agg)
            self.allele_score_queries.append(
                AlleleScoreQuery(att_info.source, allele_aggregator=allele_agg))
            self.add_score_aggregator_documentation(
                att_info, "allele_aggregator", allele_agg)

    @classmethod
    def _build_allele_filter_func(
        cls, tree: Tree,
    ) -> Callable[[ScoreLine], bool]:
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

            def left_accessor(_score: ScoreLine) -> Any:
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
                _score: ScoreLine,
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

            def right_accessor(_score: ScoreLine) -> Any:
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
                _score: ScoreLine,
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

    def get_all_attribute_descriptions(self) -> dict[str, AttributeDesc]:
        result = super().get_all_attribute_descriptions()
        result["allele"] = AttributeDesc(
            source="allele",
            name="allele",
            type="list",
            description="The allele in the format 'chr:pos:ref:alt'",
            default=False,
            internal=False,
        )
        return result

    def build_score_aggregator_documentation(
        self, attr_info: AttributeInfo,
    ) -> list[str]:
        """Collect score aggregator documentation."""
        nuc_agg = attr_info.parameters.get("nucleotide_aggregator")
        allele_agg = attr_info.parameters.get("allele_aggregator")
        if nuc_agg is not None:
            logger.warning(
                "attibute `nucleotide_aggregator` is deprecated, "
                "use `allele_aggregator` instead")
            allele_agg = nuc_agg
        allele_doc = self._build_score_aggregator_documentation(
            attr_info, "allele_aggregator", allele_agg,
        )
        return [allele_doc]

    def _annotate_exact_match(
        self, annotatable: VCFAllele,
    ) -> dict[str, Any]:
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
            scores[self.allele_attribute.name] = [allele_str]

        return {attr.name: scores.get(attr.source) for attr in self.attributes}

    def _annotate_aggregated(
        self, annotatable: Annotatable,
    ) -> dict[str, Any]:
        score_aggs = {}
        for q in self.allele_score_queries:
            scr_def = self.allele_score.score_definitions[q.score]
            agg_type = q.allele_aggregator or scr_def.allele_aggregator
            assert agg_type is not None
            score_aggs[q.score] = build_aggregator(agg_type)
        alleles: set[str] = set()
        has_lines = False

        for line in self.allele_score.fetch_lines(
            annotatable.chrom, annotatable.position, annotatable.pos_end,
        ):
            has_lines = True
            if self.allele_filter is not None and not self.allele_filter(line):
                continue

            for q in self.allele_score_queries:
                score_aggs[q.score].add(line.get_score(q.score))

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

        scores = {
            q.score: score_aggs[q.score].get_final()
            for q in self.allele_score_queries
        }
        if self.allele_attribute is not None:
            scores[self.allele_attribute.source] = list(alleles)

        return {attr.name: scores.get(attr.source) for attr in self.attributes}

    def annotate(
        self, annotatable: Annotatable | None,
        context: dict[str, Any],  # noqa: ARG002
    ) -> dict[str, Any]:

        if annotatable is None:
            return self._empty_result()

        if annotatable.chromosome not in self.score.get_all_chromosomes():
            return self._empty_result()

        if self.mode == "allele":
            if not isinstance(annotatable, VCFAllele):
                return self._empty_result()
            return self._annotate_exact_match(annotatable)

        # region mode
        if len(annotatable) > self._region_length_cutoff:
            return self._empty_result()
        return self._annotate_aggregated(annotatable)
