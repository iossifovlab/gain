from __future__ import annotations

import copy
import json
import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from textwrap import dedent
from typing import TYPE_CHECKING, Any, TypedDict, overload

import yaml

from gain import logging
from gain.genomic_resources.aggregators import (
    Aggregator,
    AggregatorSource,
    aggregator_name,
)
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
)
from gain.genomic_resources.resource_query import (
    ResourceQuery,
    ResourceQueryParseError,
)
from gain.genomic_resources.resource_types import (
    FRAGMENT_SCORE_TYPES,
    RETIRED_ANNOTATOR_NAMES,
    retired_annotator_message,
)
from gain.utils.log_safety import escape_unsafe_characters

if TYPE_CHECKING:
    from gain.annotation.annotation_pipeline import AttributeSpec

logger = logging.getLogger(__name__)


# Framework-injected runtime parameters that must NOT affect annotator
# identity. ``work_dir`` is injected per-run by ``build_pipeline_annotator``
# (an index-encoded, output-relative path), so it differs between the
# previously-applied pipeline and the freshly-built one even when the
# annotators are otherwise identical. Excluding it from equality/hash lets
# reannotation recognise unchanged annotators and reuse their values
# instead of recomputing everything (#111).
NON_IDENTITY_PARAMS = frozenset({"work_dir"})


def _hash_params(params: Any) -> int:
    """Hash JSON-shaped parameters, insensitive to dict key order.

    ``sort_keys=True`` recursively sorts nested dict keys, so ``==``-equal
    parameters that differ only in key order hash equal; it also keeps
    unhashable values (list/dict parameters such as the chrom_mapping
    annotator's inline ``mapping``) hashable.  ``default=str`` lets
    otherwise non-JSON values (e.g. ``Path``) hash without raising, at the
    cost of acceptable collisions (#114).

    The identity hashes of ``ParamsUsageMonitor``, ``AttributeConfig`` and
    ``AnnotatorInfo`` chain through one another, so they must share one
    normalisation -- this one.
    """
    return hash(json.dumps(params, sort_keys=True, default=str))


class RawPreamble(TypedDict):
    summary: str
    description: str
    input_reference_genome: str
    metadata: dict[str, Any]


RawAnnotatorsConfig = list[dict[str, Any]]


class RawFullConfig(TypedDict):
    preamble: RawPreamble
    annotators: RawAnnotatorsConfig


RawPipelineConfig = RawAnnotatorsConfig | RawFullConfig


@dataclass
class ErrorMark:
    """Marks an error position in a file."""
    row: int
    column: int


class AnnotationConfigurationError(Exception):
    """Exception raised for errors in the annotation configuration."""
    error_mark: ErrorMark | None
    message: str | None

    def __init__(
        self,
        message: str | None,
        other_error: Exception | None = None,
        error_mark: ErrorMark | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.other_error = other_error
        self.error_mark = error_mark

    def __str__(self) -> str:
        message = self.message
        if self.other_error is not None:
            message = f"{message} {self.other_error}"

        mark = None
        if self.error_mark is not None:
            mark = (
                f"At line {self.error_mark.row}, "
                f"column {self.error_mark.column}."
            )

        result = ""
        if message is not None and mark is not None:
            result = f"{message}: {mark}"
        elif message is not None:
            result = message
        elif mark is not None:
            result = mark
        return result


class ParamsUsageMonitor(Mapping):
    """Class to monitor usage of annotator parameters."""

    def __init__(self, data: dict[str, Any], owner: str | None = None):
        self._data = dict(data)
        self._used_keys: set[str] = set()
        #: Whose parameters these are -- the annotator type, set by the
        #: ``AnnotatorInfo`` that holds them.  It names the annotator in
        #: a refusal and does nothing else: parameters are compared and
        #: hashed by their data alone, so two monitors holding the same
        #: parameters stay equal whatever their owners are.
        self.owner = owner

    def __hash__(self) -> int:
        return _hash_params(self._data)

    def __getitem__(self, key: str) -> Any:
        self._used_keys.add(key)
        return self._data[key]

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Iterator:
        raise ValueError("Should not iterate a parameter dictionary.")

    def __repr__(self) -> str:
        return self._data.__repr__()

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, ParamsUsageMonitor):
            return False
        return self._data == other._data

    # A parameter WITH a default always answers a number; only one
    # without can answer `None`, and a caller of that kind (an optional
    # threshold) has to handle the absence anyway.
    @overload
    def get_number(
        self, key: str, *, default: float,
        minimum: float | None = None, maximum: float | None = None,
        not_a_number_explanation: str | None = None,
        out_of_range_explanation: str | None = None,
    ) -> float: ...

    @overload
    def get_number(
        self, key: str, *, default: None = None,
        minimum: float | None = None, maximum: float | None = None,
        not_a_number_explanation: str | None = None,
        out_of_range_explanation: str | None = None,
    ) -> float | None: ...

    def get_number(
        self, key: str, *,
        default: float | None = None,
        minimum: float | None = None,
        maximum: float | None = None,
        not_a_number_explanation: str | None = None,
        out_of_range_explanation: str | None = None,
    ) -> float | None:
        """Read a parameter that has to be a number.

        Absent -- unwritten, or written with no value -- means
        ``default``, and reading is what DECLARES the key: the lookup
        goes through item access, so a parameter read here is not an
        unused one.  Everything a number cannot be is refused with an
        ``AnnotationConfigurationError`` naming the key as the user
        spelled it, because a pipeline is wrong the moment it is written
        and whoever has to fix it is reading YAML (gain#477).

        The answer is a number, not necessarily a ``float``: a whole one
        stays an ``int``, so a caller needing that type can ask for it
        with :meth:`get_integer` and be sure of it.

        The two explanations say what THIS parameter is, in the caller's
        own words, and are appended to the refusal they are named for.
        A generic sentence stating the bounds stands in for a missing
        ``out_of_range_explanation``.
        """
        value = self._lookup(key)
        if value is None:
            return default
        # NOT widened to `float`: a whole number stays whole, and the
        # conversion would raise `OverflowError` on an integer too large
        # for one -- out of the accessor whose contract is that what it
        # refuses, it refuses by naming the key.
        return self._to_number(
            key, value, minimum=minimum, maximum=maximum,
            not_a_number_explanation=not_a_number_explanation,
            out_of_range_explanation=out_of_range_explanation)

    @overload
    def get_integer(
        self, key: str, *, default: int,
        minimum: float | None = None, maximum: float | None = None,
        not_a_number_explanation: str | None = None,
        out_of_range_explanation: str | None = None,
    ) -> int: ...

    @overload
    def get_integer(
        self, key: str, *, default: None = None,
        minimum: float | None = None, maximum: float | None = None,
        not_a_number_explanation: str | None = None,
        out_of_range_explanation: str | None = None,
    ) -> int | None: ...

    def get_integer(
        self, key: str, *,
        default: int | None = None,
        minimum: float | None = None,
        maximum: float | None = None,
        not_a_number_explanation: str | None = None,
        out_of_range_explanation: str | None = None,
    ) -> int | None:
        """Read a parameter that has to be a whole number.

        A length counted in bases is one: the effect annotator does index
        arithmetic with what it reads here, so a fractional value is a
        typo to refuse rather than something to truncate.  Otherwise as
        :meth:`get_number`.
        """
        value = self._lookup(key)
        if value is None:
            return default
        number = self._to_number(
            key, value, minimum=minimum, maximum=maximum,
            not_a_number_explanation=not_a_number_explanation,
            out_of_range_explanation=out_of_range_explanation)
        if isinstance(number, int):
            return number
        if not number.is_integer():
            raise self._refuse(
                key,
                f"{value!r}, which is not a whole number.",
                not_a_number_explanation)
        return int(number)

    def _lookup(self, key: str) -> Any:
        """Read one parameter, DECLARING it, absent or not.

        ``None`` comes back for a key nobody wrote and for one written
        with nothing after it -- ``promoter_len:`` is YAML for ``None``
        -- because a key with no value says as little as no key at all.
        """
        try:
            return self[key]
        except KeyError:
            return None

    def _to_number(
        self, key: str, value: Any, *,
        minimum: float | None, maximum: float | None,
        not_a_number_explanation: str | None,
        out_of_range_explanation: str | None,
    ) -> int | float:
        """Coerce and range-check one looked-up parameter value.

        An ``int`` stays an ``int``: past 2**53 a round trip through
        ``float`` answers a different number than the one configured.
        """
        # The three types a configuration spells a number as, and only
        # those: `bool` is named first because it subclasses `int`, so
        # without this `min_overlap: true` would parse as 1.0 -- a value
        # the user never asked for, applied in silence.  Anything else
        # `float()` happens to accept, `b"0.5"` among them, is refused
        # here rather than admitted for being convertible.
        if isinstance(value, bool) or not isinstance(value, int | float | str):
            raise self._not_a_number(key, value, not_a_number_explanation)
        number = (
            self._parse_number(key, value, not_a_number_explanation)
            if isinstance(value, str) else value
        )
        # `nan` and `inf` survive `float()` and are no length and no
        # share of one, so they are refused for what they are rather
        # than left to a bound -- the accessor is shared, and a caller
        # that asks for no range would otherwise admit them.  An `int`
        # is finite by construction, and asking `math.isfinite` would
        # itself overflow on a large one.
        if isinstance(number, float) and not math.isfinite(number):
            raise self._not_a_number(key, value, not_a_number_explanation)
        if ((minimum is not None and number < minimum)
                or (maximum is not None and number > maximum)):
            raise self._out_of_range(
                key, value, minimum, maximum, out_of_range_explanation)
        return number

    def _parse_number(
        self, key: str, text: str, explanation: str | None,
    ) -> int | float:
        """Parse a configured string, keeping a whole number whole.

        A string is what the annotation editor posts -- its form controls
        hold text, so a number typed there arrives as ``"100"``.  Quoting
        the value in hand-written YAML lands here too and means the same
        thing.  ``int`` is tried first so that a length spelled as a
        string is as exact as one spelled as a number.

        Whatever Python reads as a number is one, which is a slightly
        wider door than the editor posts through: ``"1_000"`` and
        ``" 5 "`` are numbers here.  Being liberal about a spelling
        nobody is likely to type is not the same as being liberal about
        the value, which is still bounded and still has to be finite.
        """
        try:
            return int(text)
        except ValueError:
            pass
        try:
            return float(text)
        except ValueError:
            raise self._not_a_number(key, text, explanation) from None

    def _not_a_number(
        self, key: str, value: object, explanation: str | None,
    ) -> AnnotationConfigurationError:
        """The refusal for a value no number can be spelled as."""
        return self._refuse(
            key, f"{value!r}, which is not a number.", explanation)

    def _out_of_range(
        self, key: str, value: object,
        minimum: float | None, maximum: float | None,
        explanation: str | None,
    ) -> AnnotationConfigurationError:
        """The refusal for a number outside the bounds asked for."""
        if minimum is not None and maximum is not None:
            bounds = f"between {minimum} and {maximum}"
        elif minimum is not None:
            bounds = f"no smaller than {minimum}"
        else:
            bounds = f"no larger than {maximum}"
        return self._refuse(
            key, f"{value}.",
            explanation or f"It has to be a number {bounds}.")

    def _refuse(
        self, key: str, problem: str, explanation: str | None,
    ) -> AnnotationConfigurationError:
        """One refusal: who configured what, what is wrong, what it means.

        The owner is named first when there is one, because a pipeline
        holds many annotators and the key alone does not say which of
        them the value was written under.

        Both it and the value are caller text reaching a logged message,
        so both are escaped to one line (gain#642, gain#655): ``float()``
        accepts the whitespace around a number, so a configured ``"2\\n"``
        parses, fails the range check and would otherwise emit a second,
        fully-formed-looking record.  The key is ours -- the annotator
        passes a literal -- and escaping it is a no-op that costs nothing
        to keep uniform.
        """
        prefix = f"{self.owner} configures " if self.owner else ""
        message = escape_unsafe_characters(f"{prefix}{key}: {problem}")
        if explanation is not None:
            message = f"{message} {explanation}"
        return AnnotationConfigurationError(message)

    def get_used_keys(self) -> set[str]:
        """Return the set of keys that have been accessed."""
        return self._used_keys

    def get_unused_keys(self) -> set[str]:
        """Return the set of keys that have not been accessed."""
        return set(self._data.keys()) - self._used_keys

    def as_dict(self) -> dict[str, Any]:
        """Return a plain copy of all parameters without tracking."""
        return dict(self._data)

    def inject(self, key: str, value: Any) -> None:
        """Add a parameter and mark it as used (for framework injection)."""
        self._data[key] = value
        self._used_keys.add(key)


@dataclass(eq=True)
class AttributeConfig:
    """Configuration for an annotator attribute (from pipeline YAML)."""

    name: str
    source: str
    internal: bool | None = None
    aggregator: AggregatorSource | None = None
    parameters: dict[str, Any] = field(default_factory=dict)

    def __hash__(self) -> int:
        # Parameters are identity (#1155): a ``value_transform`` or
        # ``none_value_replacement`` changes what the annotator emits.
        return hash((
            self.name, self.source, self.internal, str(self.aggregator),
            _hash_params(self.parameters),
        ))

    def as_dict(self) -> dict[str, Any]:
        """Serialize to a config dict, omitting fields that are unset."""
        d: dict[str, Any] = {"name": self.name, "source": self.source}
        if self.internal is not None:
            d["internal"] = self.internal
        if self.aggregator is not None:
            d["aggregator"] = aggregator_name(self.aggregator)
        return {**self.parameters, **d}


@dataclass(eq=True)
class Attribute:
    """Runtime attribute instance produced by an annotator."""

    name: str
    source: str
    internal: bool | None = None
    aggregator: AggregatorSource | None = None
    parameters: ParamsUsageMonitor = field(
        default_factory=lambda: ParamsUsageMonitor({}))
    spec: AttributeSpec | None = field(
        default=None, compare=False, hash=False)
    _documentation: str | None = field(
        default=None, compare=False, hash=False)

    def __hash__(self) -> int:
        return hash((
            self.name, self.source, self.internal, str(self.aggregator),
            self.parameters,
        ))

    def fold(self, values: list[Any]) -> Any:
        """Reduce ``values`` with the aggregator this attribute NAMES.

        The one statement of HOW an attribute reduces, kept beside the
        name it reduces by.  The caller decides WHETHER there is anything
        to reduce, because the container differs by annotator -- a list of
        a score's values, a mapping of per-gene values -- and must hold an
        aggregator before asking.

        A fresh accumulator per call, never a held one: an aggregator is
        mutable state, and one built here cannot outlive the fold it was
        built for.  Building costs ~0.26 us against the ~0.06 us of
        clearing a held instance (measured, gain#1133) -- a fifth of a
        microsecond per folded attribute per variant, paid only where a
        fold actually happens.  The name resolution itself is memoised
        (gain#1157), so what is left is the object, not the parsing.
        """
        assert self.aggregator is not None
        return Aggregator.build(self.aggregator).aggregate(values)

    def get_value_type(self, *, aggregated: bool = True) -> str:
        """Value type produced by this attribute.

        Pass ``aggregated=True`` (default) when the aggregator is known to have
        run; the aggregator's ``output_value_type`` then takes precedence over
        the spec's declared type.  Pass ``aggregated=False`` when aggregation
        was skipped (e.g. a scalar value that bypassed a list aggregator) so
        that the spec type is returned instead.  The raw spec type is always
        accessible via ``self.spec.value_type``.

        The type is read off the aggregator's NAME -- the only thing the
        attribute holds since gain#1133 -- through
        :meth:`Aggregator.resolve_class`, which is class-level and builds
        no accumulator.
        """
        if aggregated and self.aggregator is not None:
            agg_output_type = \
                Aggregator.resolve_class(self.aggregator).output_value_type
            if agg_output_type is not None:
                return agg_output_type
        return self.spec.value_type if self.spec else ""

    @property
    def description(self) -> str:
        return self.spec.description if self.spec else ""

    @property
    def documentation(self) -> str:
        if self._documentation is None:
            return self.spec.description if self.spec else ""
        return self._documentation


@dataclass(init=False, eq=False)
class AnnotatorInfo:
    """Defines annotator configuration."""

    def __init__(
        self,
        _type: str,
        attributes: list[AttributeConfig],
        parameters: ParamsUsageMonitor | dict[str, Any],
        documentation: str = "",
        resources: list[GenomicResource] | None = None,
        annotator_id: str = "N/A",
    ):
        self.type = _type
        self.annotator_id = f"{annotator_id}"
        self.attributes = attributes
        self.documentation = documentation
        if isinstance(parameters, ParamsUsageMonitor):
            self.parameters = parameters
        else:
            self.parameters = ParamsUsageMonitor(parameters)
        # These parameters belong to THIS annotator, whichever way they
        # arrived, and a refusal out of them says so.  One monitor to one
        # info: they already share `used_keys`, so a monitor handed to a
        # second info would mark keys used across both -- an owner that
        # names the later one is the smaller half of that problem.
        self.parameters.owner = _type
        if resources is None:
            self.resources = []
        else:
            self.resources = resources

    annotator_id: str
    type: str
    attributes: list[AttributeConfig]
    parameters: ParamsUsageMonitor
    documentation: str = ""
    resources: list[GenomicResource] = field(default_factory=list)

    def _identity_params(self) -> tuple[tuple[str, Any], ...]:
        """Parameters that participate in identity, sorted, work_dir excluded.

        ``NON_IDENTITY_PARAMS`` (e.g. the framework-injected ``work_dir``) are
        dropped so old/new annotator infos compare equal for reannotation
        reuse (#111).
        """
        return tuple(sorted(
            (k, v)
            for k, v in self.parameters.as_dict().items()
            if k not in NON_IDENTITY_PARAMS
        ))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AnnotatorInfo):
            return NotImplemented
        return (
            self.type == other.type
            and self.attributes == other.attributes
            and self.documentation == other.documentation
            and self.resources == other.resources
            and self._identity_params() == other._identity_params()
        )

    def __hash__(self) -> int:
        attrs_hash = "".join(str(hash(attr)) for attr in self.attributes)
        resources_hash = "".join(str(hash(res)) for res in self.resources)
        # Hash the same identity parameters ``__eq__`` compares.
        params_hash = _hash_params(self._identity_params())
        return hash(f"{self.type}{attrs_hash}{resources_hash}{params_hash}")

    def to_dict(self) -> dict[str, Any]:
        """Convert annotator info to a configuration dictionary."""
        result = {
            **self.parameters.as_dict(),
            "attributes": [attr.as_dict() for attr in self.attributes],
        }
        return {
            self.type: result,
        }


@dataclass
class AnnotationPreamble:
    summary: str
    description: str
    input_reference_genome: str
    input_reference_genome_res: GenomicResource | None
    metadata: dict[str, Any]


class AnnotationConfigParser:
    """Parser for annotation configuration."""

    WILDCARD_LIMIT = 500

    @staticmethod
    def query_resources(
        annotator_type: str, resource_query: str, grr: GenomicResourceRepo,
    ) -> list[str]:
        """Collect the ids of the resources matching ``resource_query``.

        ``resource_query`` is an id glob plus an optional label filter, not
        a resource id -- the config key it is read from is spelled
        ``resource_id``, but by the time it reaches here it has been
        recognised as a wildcard. It is spelled the same as the
        ``search_resources`` parameter that takes the same language.

        The query language itself lives in ``genomic_resources``; what this
        adds is the annotation layer's policy about the result -- an
        annotator name selects the resource types it can consume, a wildcard
        that selects nothing is a configuration error, and one that selects
        more than ``WILDCARD_LIMIT`` resources is refused rather than
        silently expanded into a pipeline of that size.
        """
        # Maps an annotator name a user may type to the resource types it
        # consumes.  A SET, not one type: a fragment score has two accepted
        # spellings and either annotator name must find either of them --
        # a pipeline on the new name will point at repositories that never
        # migrated, and third-party GRRs answer to no migration of ours.
        #
        # The legacy keys are deprecated (gain#538) but warn nowhere near
        # here: this resolves a wildcard against every resource in the
        # repository, so a warning would fire per candidate rather than per
        # pipeline.  `FragmentScoreAnnotator.__init__` owns that.
        annotator_resources_map = {
            "position_score": {"position_score"},
            "position_score_annotator": {"position_score"},
            "allele_score": {"allele_score"},
            "allele_score_annotator": {"allele_score"},
            "fragment_score": FRAGMENT_SCORE_TYPES,
            "fragment_score_annotator": FRAGMENT_SCORE_TYPES,
            "cnv_collection": FRAGMENT_SCORE_TYPES,
            "cnv_collection_annotator": FRAGMENT_SCORE_TYPES,
            "gene_score_annotator": {"gene_score"},
        }

        # Before the query runs, because a retired annotator name is absent
        # from the map above and so matches nothing -- the reader would be
        # told their wildcard selected no resources, which is true and
        # useless, instead of that the annotator name is the problem
        # (gain#919). `get_annotator_factory` cannot cover this: parsing
        # gets here first, and raises.
        if annotator_type in RETIRED_ANNOTATOR_NAMES:
            raise AnnotationConfigurationError(
                retired_annotator_message(annotator_type))

        try:
            parsed_query = ResourceQuery.parse(resource_query)
        except ResourceQueryParseError as err:
            raise AnnotationConfigurationError(str(err)) from err

        accepted_types = annotator_resources_map.get(
            annotator_type, frozenset())

        # Both reach the logged messages below as caller text -- the
        # annotator type is a YAML mapping key and can carry anything -- so
        # both are escaped to one line (iossifovlab/gain#655).
        safe_pattern = escape_unsafe_characters(
            parsed_query.resource_id_pattern)
        safe_type = escape_unsafe_characters(annotator_type)

        selected_resources: set[str] = set()
        result: list[str] = []
        for resource in grr.get_all_resources():
            if resource.get_id() in selected_resources:
                continue
            if resource.get_type() in accepted_types \
                    and parsed_query.match(resource):
                selected_resources.add(resource.resource_id)
                result.append(resource.resource_id)
                if len(result) > AnnotationConfigParser.WILDCARD_LIMIT:
                    raise AnnotationConfigurationError(
                        f"Too many resources ({len(result)}/"
                        f"{AnnotationConfigParser.WILDCARD_LIMIT}) "
                        f"match the wildcard '{safe_pattern}' "
                        f"for annotator '{safe_type}'.",
                    )

        if len(result) == 0:
            raise AnnotationConfigurationError(
                f"No resources match the wildcard '{safe_pattern}' "
                f"for annotator type '{safe_type}'.",
            )
        return result

    @staticmethod
    def has_wildcard(string: str) -> bool:
        """Ascertain whether a string contains a valid wildcard."""
        # Check if at least one wildcard symbol is present
        # in the resource id itself, since '*' can also be used
        # in the label query as well (within square bracket)
        return "*" in string \
            and ("[" not in string or string.index("*") < string.index("["))

    @staticmethod
    def parse_minimal(raw: str, idx: int) -> AnnotatorInfo:
        """Parse a minimal-form annotation config."""
        return AnnotatorInfo(raw, [], {}, annotator_id=f"A{idx}")

    @staticmethod
    def parse_short(
        raw: dict[str, Any], idx: int,
        grr: GenomicResourceRepo | None = None,
    ) -> list[AnnotatorInfo]:
        """Parse a short-form annotation config."""
        ann_type, ann_details = next(iter(raw.items()))
        if AnnotationConfigParser.has_wildcard(ann_details):
            assert grr is not None
            matching_resources = AnnotationConfigParser.query_resources(
                ann_type, ann_details, grr,
            )
            return [
                AnnotatorInfo(
                    ann_type, [], {"resource_id": resource},
                    annotator_id=f"A{idx}_{resource}",
                )
                for resource in matching_resources
            ]
        return [
            AnnotatorInfo(
                ann_type, [], {"resource_id": ann_details},
                annotator_id=f"A{idx}",
            ),
        ]

    @staticmethod
    def parse_complete(
        raw: dict[str, Any], idx: int,
        grr: GenomicResourceRepo | None = None,
    ) -> list[AnnotatorInfo]:
        """Parse a full-form annotation config."""
        ann_type, ann_details = next(iter(raw.items()))
        attributes = []
        if "attributes" in ann_details:
            attributes = AnnotationConfigParser.parse_raw_attributes(
                ann_details["attributes"],
            )
        parameters = {
            k: v for k, v in ann_details.items() if k != "attributes"}

        if "resource_id" in parameters \
           and AnnotationConfigParser.has_wildcard(parameters["resource_id"]):
            assert grr is not None
            matching_resources = AnnotationConfigParser.query_resources(
                ann_type, parameters.pop("resource_id"), grr,
            )
            return [
                AnnotatorInfo(ann_type, attributes,
                              {"resource_id": resource, **parameters},
                              annotator_id=f"A{idx}_{resource}")
                for resource in matching_resources
            ]
        return [AnnotatorInfo(
                ann_type, attributes, parameters, annotator_id=f"A{idx}")]

    @staticmethod
    def _parse_preamble(
        raw: RawPreamble,
        grr: GenomicResourceRepo | None = None,
    ) -> AnnotationPreamble | None:
        """Parse the preamble section of a pipeline config, if present."""
        if not set(raw.keys()) <= {
            "summary", "description", "input_reference_genome", "metadata",
        }:
            raise AnnotationConfigurationError("Invalid preamble keys")

        if not isinstance(raw.get("summary", ""), str):
            raise TypeError("preamble summary must be a string!")
        if not isinstance(raw.get("description", ""), str):
            raise TypeError("preamble description must be a string!")
        if not isinstance(raw.get("input_reference_genome", ""), str):
            raise TypeError("preamble reference genome id must be a string!")
        if not isinstance(raw.get("metadata", {}), dict):
            raise TypeError("preamble metadata must be a dictionary!")

        genome_id = raw.get("input_reference_genome", "")
        genome = None
        if genome_id != "" and grr is not None:
            genome = grr.get_resource(genome_id)

        return AnnotationPreamble(
            raw.get("summary", ""),
            raw.get("description", ""),
            genome_id,
            genome,
            raw.get("metadata", {}),
        )

    @staticmethod
    def parse_raw(
        pipeline_raw_config: RawPipelineConfig | None,
        grr: GenomicResourceRepo | None = None,
    ) -> tuple[AnnotationPreamble | None, list[AnnotatorInfo]]:
        """Parse raw dictionary annotation pipeline configuration."""
        if pipeline_raw_config is None:
            logger.warning("empty annotation pipeline configuration")
            return None, []

        if isinstance(pipeline_raw_config, dict):
            annotators = pipeline_raw_config["annotators"]
            # Insist on a list. Every other iterable shape YAML can put here
            # is iterated into nonsense rather than refused -- a string most
            # of all, which yields one attempted annotator per character, so
            # a few kilobytes of quoted text become tens of thousands of
            # them. That also walks straight past a caller counting the
            # declared annotators to bound the work (iossifovlab/gain#635).
            if not isinstance(annotators, list):
                raise AnnotationConfigurationError(
                    "The 'annotators' section of a pipeline configuration "
                    f"must be a list, not {type(annotators).__name__}.",
                )
            preamble = AnnotationConfigParser._parse_preamble(
                pipeline_raw_config["preamble"], grr,
            )
        elif isinstance(pipeline_raw_config, list):
            annotators = pipeline_raw_config
            preamble = None
        else:
            raise AnnotationConfigurationError(
                "Raw pipeline configuration is not a list or dict.",
            )

        result = []
        for idx, raw_cfg in enumerate(annotators):
            if isinstance(raw_cfg, str):
                # the minimal annotator configuration form
                result.append(
                    AnnotationConfigParser.parse_minimal(raw_cfg, idx),
                )
                continue
            if isinstance(raw_cfg, dict):
                ann_details = next(iter(raw_cfg.values()))
                if isinstance(ann_details, str):
                    # the short annotator configuation form
                    result.extend(AnnotationConfigParser.parse_short(
                        raw_cfg, idx, grr,
                    ))
                    continue
                if isinstance(ann_details, dict):
                    # the complete annotator configuration form
                    result.extend(AnnotationConfigParser.parse_complete(
                        raw_cfg, idx, grr,
                    ))
                    continue
            # ``raw_cfg`` is caller data, but line-safe without escaping:
            # a str reaches parse_minimal above, so what lands here is a
            # dict/list/scalar whose f-string rendering goes through
            # ``repr`` (control characters escaped) or is a number
            # (none). Switching this to interpolate a str field would
            # reopen the log-forging hole (iossifovlab/gain#655).
            raise AnnotationConfigurationError(dedent(f"""
                Incorrect annotator configuation form: {raw_cfg}.
                The allowed forms are:
                    * minimal
                        - <annotator type>
                    * short
                        - <annotator type>: <resource_id_pattern>
                    * complete without attributes
                        - <annotator type>:
                            <param1>: <value1>
                            ...
                    * complete with attributes
                        - <annotator type>:
                            <param1>: <value1>
                            ...
                            attributes:
                            - <att1 config>
                            ....
            """))
        return preamble, result

    @staticmethod
    def parse_str(
        content: str, source_file_name: str | None = None,
        grr: GenomicResourceRepo | None = None,
    ) -> tuple[AnnotationPreamble | None, list[AnnotatorInfo]]:
        """Parse annotation pipeline configuration string."""
        try:
            pipeline_raw_config = yaml.safe_load(content)
        except yaml.MarkedYAMLError as error:
            error_mark = None
            if error.problem_mark is not None:
                error_mark = ErrorMark(
                    error.problem_mark.line + 1,
                    error.problem_mark.column + 1,
                )
            if source_file_name is None:
                # The caller's text stays out of the message: this branch
                # is reachable with anonymous request bodies whose
                # exception is logged, so echoing the content -- newlines
                # intact -- lets the caller forge log records
                # (iossifovlab/gain#655). The error mark carries the
                # position instead.
                raise AnnotationConfigurationError(
                    "The pipeline configuration is an invalid yaml string.",
                    error_mark=error_mark,
                ) from error
            raise AnnotationConfigurationError(
                f"The pipeline configuration file {source_file_name} "
                f"is an invalid yaml file.",
                error_mark=error_mark,
            ) from error

        return AnnotationConfigParser.parse_raw(pipeline_raw_config, grr=grr)

    @staticmethod
    def parse_raw_attribute_config(
            raw_attribute_config: dict[str, Any]) -> AttributeConfig:
        """Parse annotation attribute raw configuration."""
        attribute_config = copy.deepcopy(raw_attribute_config)
        if "destination" in attribute_config:
            logger.warning(
                "usage of 'destination' in annotators attribute configuration "
                "is deprecated; use 'name' instead")
            name = attribute_config.get("destination")
            attribute_config.pop("destination")
            attribute_config["name"] = name

        name = attribute_config.get("name")
        source = attribute_config.get("source")

        if name is None and source is None:
            # A dict renders through ``repr``, so caller newlines are
            # line-safe without an explicit escape (see the ``raw_cfg``
            # note above), unlike the string ``source`` fields below.
            raise ValueError(f"The raw attribute configuraion "
                             f"{attribute_config} has neigther "
                             "name nor source.")

        name = name or source
        source = source or name

        internal = attribute_config.get("internal")
        if internal is not None and not isinstance(internal, bool):
            # The source is caller text and the exception is logged, so
            # it is escaped to one line (iossifovlab/gain#655) -- as in
            # the aggregator-conflict message below.
            raise TypeError(
                "The 'internal' field in attribute "
                f"{escape_unsafe_characters(str(source))} "
                "is not a boolean!",
            )
        assert source is not None
        if not isinstance(name, str):
            message = ("The name for in an attribute "
                       f"config {attribute_config} should be a string")
            raise TypeError(message)

        deprecated_aggregator_params = {
            "position_aggregator",
            "allele_aggregator",
            "nucleotide_aggregator",
            "gene_list_aggregator",
            "gene_aggregator",
        }
        aggregator = attribute_config.get("aggregator")
        for old_name in deprecated_aggregator_params:
            if old_name not in attribute_config:
                continue
            if aggregator is not None:
                raise ValueError(
                    f"Cannot specify both 'aggregator' and '{old_name}' "
                    f"for attribute "
                    f"'{escape_unsafe_characters(str(source))}'")
            logger.warning(
                "'%s' is deprecated in attribute config; use 'aggregator'",
                old_name)
            aggregator = attribute_config[old_name]

        excluded = (
            {"name", "source", "internal", "type", "aggregator"}
            | deprecated_aggregator_params
        )
        parameters = {
            k: v for k, v in attribute_config.items() if k not in excluded
        }

        return AttributeConfig(
            name=name,
            source=source,
            internal=internal,
            aggregator=aggregator,
            parameters=parameters,
        )

    @staticmethod
    def parse_raw_attributes(
            raw_attributes_config: Any) -> list[AttributeConfig]:
        """Parse annotator pipeline attribute configuration."""
        if not isinstance(raw_attributes_config, list):
            message = "The attributes parameters should be a list."
            raise TypeError(message)

        attribute_config = []
        for raw_attribute_config in raw_attributes_config:
            if isinstance(raw_attribute_config, str):
                raw_attribute_config = {"name": raw_attribute_config}
            try:
                attribute_config.append(
                    AnnotationConfigParser.parse_raw_attribute_config(
                        raw_attribute_config))
            except ValueError as e:
                raise AnnotationConfigurationError(str(e)) from e
        return attribute_config
