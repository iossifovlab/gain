import textwrap
from collections.abc import Callable
from typing import Any

from lark import Lark, Token, Tree

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
from gain.annotation.annotator_base import AnnotatorBase
from gain.genomic_resources.genomic_scores import Fragment, FragmentScore


def build_fragment_score_annotator(pipeline: AnnotationPipeline,
                                   info: AnnotatorInfo) -> Annotator:
    return FragmentScoreAnnotator(pipeline, info)


class FragmentScoreAnnotator(AnnotatorBase):
    """Annotator over a fragment score.

    The annotator name accepted in pipeline configuration is still
    ``cnv_collection`` / ``cnv_collection_annotator``, and the filter
    parameter is still ``cnv_filter``; only the Python names moved
    (gain#470).  gain#471 widens the accepted configuration vocabulary.
    """

    FRAGMENT_FILTER_GRAMMAR = textwrap.dedent("""
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

        word: /[a-zA-Z!@#$%^&*()_+]+/

        number: /[0-9\\.]+/

        %ignore " "
    """)

    def __init__(self, pipeline: AnnotationPipeline, info: AnnotatorInfo):
        resource_id = info.parameters.get("resource_id")
        if resource_id is None:
            raise ValueError(f"Can't create {info.type}: "
                             "no resrouce_id parameter.")
        resource = pipeline.repository.get_resource(resource_id)
        # Deliberately constructed directly rather than through
        # `build_fragment_score_from_resource`: that factory returns a
        # process-wide shared instance, and `self.close()` below closes the
        # score -- which would tear it down for every other holder.
        # `FragmentScore.__init__` validates the resource type, so nothing is
        # lost by bypassing the factory here.
        self.fragment_score = FragmentScore(resource)
        info.resources.append(resource)

        self.filter_parser = Lark(self.FRAGMENT_FILTER_GRAMMAR)

        # The configuration key stays `cnv_filter` (gain#470 renamed the
        # Python surface only); gain#471 adds `fragment_filter` beside it.
        self.fragment_filter = None
        filter_str = info.parameters.get("cnv_filter")
        if filter_str is not None:
            assert isinstance(filter_str, str)

            filter_str = filter_str.replace(
                "\n", " ").replace("\t", " ").strip()
            try:
                self.fragment_filter = self._build_fragment_filter_func(
                    self.filter_parser.parse(filter_str))
            except Exception as e:
                raise AnnotationConfigurationError(
                    f"Error parsing cnv_filter: {e}") from e

        super().__init__(pipeline, info)

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
                """  # noqa: SLF001

    def get_attribute_specs(self) -> dict[str, AttributeSpec]:
        attributes: dict[str, AttributeSpec] = {
            "count": AttributeSpec(
                source="count",
                value_type="int",
                # Deliberately NOT renamed with the Python surface: this
                # string is annotation output a user reads, so editing it
                # is a behaviour change (gain#470 changes none).  It moves
                # when the config vocabulary does, in gain#471.
                description="The number of CNVs overlapping with the "
                "annotatable.",
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

    @classmethod
    def _build_fragment_filter_func(
        cls, tree: Tree,
    ) -> Callable[[Fragment], bool]:
        if tree.data == "and_":
            assert isinstance(tree.children[0], Tree)
            assert isinstance(tree.children[1], Tree)
            left_func = cls._build_fragment_filter_func(tree.children[0])
            right_func = cls._build_fragment_filter_func(tree.children[1])
            return lambda frag: left_func(frag) and right_func(frag)
        if tree.data == "or":
            left_func = cls._build_fragment_filter_func(tree.children[0])
            right_func = cls._build_fragment_filter_func(tree.children[1])
            return lambda frag: left_func(frag) or right_func(frag)

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

            def left_accessor(_fragment: Fragment) -> Any:
                return _fragment.attributes.get(left_value)
        else:
            assert isinstance(left.children[0], Tree)
            assert isinstance(left.children[0].data, Token)
            is_number = left.children[0].data.value == "number"
            assert isinstance(left.children[0].children[0], Token)
            left_value = left.children[0].children[0].value
            if is_number:
                left_value = float(left_value)

            def left_accessor(
                    _fragment: Fragment,
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

            def right_accessor(_fragment: Fragment) -> Any:
                return _fragment.attributes.get(right_value)
        else:
            assert isinstance(right.children[0], Tree)
            assert isinstance(right.children[0].data, Token)
            is_number = right.children[0].data.value == "number"
            assert isinstance(right.children[0].children[0], Token)
            right_value = right.children[0].children[0].value
            if is_number:
                right_value = float(right_value)

            def right_accessor(
                    _fragment: Fragment,
            ) -> Any:  # pylint: disable=unused-argument
                return right_value

        if operator == "equals":
            return lambda frag: left_accessor(frag) == right_accessor(frag)
        if operator == "greater_than":
            return lambda frag: left_accessor(frag) > right_accessor(frag)
        if operator == "less_than":
            return lambda frag: left_accessor(frag) < right_accessor(frag)
        if operator == "in":
            return lambda frag: left_accessor(frag) in right_accessor(frag)

        raise ValueError(f"Unsupported operator {operator.data}")

    def open(self) -> Annotator:
        self.fragment_score.open()
        super().open()
        return self

    def close(self) -> None:
        self.fragment_score.close()
        super().close()

    def _do_annotate(
        self, annotatable: Annotatable,
        context: dict[str, Any],  # noqa: ARG002
    ) -> dict[str, Any]:
        fragments = self.fragment_score.fetch_fragments(
            annotatable.chrom, annotatable.pos, annotatable.pos_end)

        if self.fragment_filter:
            fragments = [
                fragment for fragment in fragments
                if self.fragment_filter(fragment)
            ]

        raw: dict[str, list] = {
            attr.source: []
            for attr in self._attributes
            if attr.aggregator is not None
        }

        for fragment in fragments:
            for source in raw:
                raw[source].append(fragment.attributes[source])

        result: dict[str, Any] = {}
        for attr in self._attributes:
            if attr.source in raw:
                result[attr.source] = raw[attr.source]
            else:
                result[attr.source] = len(fragments)

        return result
