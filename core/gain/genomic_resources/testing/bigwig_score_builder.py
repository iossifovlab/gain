"""Fluent, immutable test-data builder for bigWig-backed position scores.

A sibling of :mod:`gain.genomic_resources.testing.builders`.  The
dependency runs one way: this module imports the shared single-realize
seam from ``builders``, and ``builders`` does not import back.  So
``a_bigwig_score`` is imported from here.
"""
from __future__ import annotations

import copy
import dataclasses
import pathlib
from typing import Any, Self

import yaml

from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.repository_ids import GR_CONF_FILE_NAME
from gain.genomic_resources.testing import (
    setup_bigwig,
    setup_directories,
)
from gain.genomic_resources.testing.builders import (
    _build_single_resource,
)
from gain.genomic_resources.testing.extra_files import ExtraFilesMixin
from gain.genomic_resources.testing.resource_meta import (
    MetaMixin,
)
from gain.genomic_resources.testing.score_specs import (
    ResourceValidationError,
)

_BIGWIG_FILENAME = "data.bw"


_BIGWIG_DEFAULT_DATA = """
    chr1  0   10  0.1
    chr1  10  20  0.2
    chr2  0   30  0.3
"""


_BIGWIG_DEFAULT_CHROM_LENS = {"chr1": 1000, "chr2": 1000}


def _normalize_bedgraph(data: str) -> str:
    """Strip blank and whitespace-only lines from a bedGraph block.

    ``setup_bigwig`` splits on newlines and asserts four columns on every
    line, so a whitespace-only line -- exactly what an indented closing
    triple-quote leaves behind -- trips a bare ``AssertionError`` naming
    neither the builder nor the row.  Authoring a block with the closing
    quote indented is the normal case for the other builders, so normalize
    here rather than make indentation significant for this one.
    """
    return "\n".join(
        stripped
        for line in data.split("\n")
        if (stripped := line.strip())
    )


@dataclasses.dataclass(frozen=True)
class BigWigScoreBuilder(ExtraFilesMixin, MetaMixin):
    """Immutable builder for a bigWig-backed ``position_score``.

    Authored as bedGraph rows (``chrom start end value``), whose intervals
    are 0-based half-open -- 1-based position ``p`` reads the interval
    containing ``p - 1``.  Unlike the tabular builders this one declares
    exactly one score, because a bigWig carries a single value -- and a
    resource declaring more than one is refused at open
    (``bigwig_scores.validate_bigwig_scoredefs``).

    **The emitted score block addresses no column**, which is the canonical
    bigWig config: a bigWig record's payload IS its value, so there is nothing
    to address.  An ``index:`` key is a deprecated no-op, accepted with a
    warning for the deployed resources that carry it; a test that wants that
    path authors it as yaml -- see ``test_bigwig_scores.py``.
    """

    score_id: str = "score"
    value_type: str = "float"
    filename: str | None = None
    table_format: str | None = None
    data: str | None = None
    chrom_lens: dict[str, int] | None = None
    histogram: dict[str, Any] | None = None
    na_values: str | list[str] | None = None
    fetch_budgets: dict[str, int] | None = None
    zero_based: bool = False
    resource_type: str | None = None

    def with_resource_type(self, resource_type: str) -> Self:
        """Render a ``type:`` other than ``position_score``.

        The backend a resource gets is chosen by its table FORMAT, not by
        its type (``build_genomic_position_table``), so a bigWig can sit
        under an ``allele_score`` just as well as under a
        ``position_score``.  Nobody publishes one -- a bigWig has no
        reference or alternative to carry -- and that is precisely what
        makes it worth building: it is how a test reaches an allele score
        whose backend serves column arrays and whose table has no key
        columns at all.

        Restricted to the types that read back through a score class over
        this backend; ``fragment_score`` is not among them, since a bigWig
        record is a point value rather than an attributed interval.
        """
        allowed = ("position_score", "allele_score")
        if resource_type not in allowed:
            raise ResourceValidationError(
                f"with_resource_type: {resource_type!r} is not a score type "
                f"a bigWig backs; expected one of {list(allowed)}")
        return dataclasses.replace(self, resource_type=resource_type)

    def with_fetch_size(self, fetch_size: int) -> Self:
        """Emit ``fetch_size:`` in the ``table:`` config.

        A budget in *records per range query*, not base pairs.  The only
        fetch knob offered: this builder is ours rather than config surface,
        so it offers no key that does nothing (see
        ``docs/adr/0002-remove-bigwig-fetch-buffering.md``).
        """
        return dataclasses.replace(
            self, fetch_budgets={"fetch_size": fetch_size})

    def with_score(self, score_id: str, value_type: str = "float") -> Self:
        """Name the single score this bigWig exposes."""
        return dataclasses.replace(
            self, score_id=score_id, value_type=value_type)

    def with_format(self, table_format: str) -> Self:
        """Emit an explicit ``format:`` in the ``table:`` config.

        The key the suffix only supplies a default for.  Authoring it makes
        a test able to state the precedence between the two inputs --
        pairing it with a :meth:`with_filename` whose suffix maps elsewhere
        is the only way to observe that the explicit key wins (gain#348).
        Emitted verbatim, so a test can also author the case variants the
        dispatch accepts.
        """
        return dataclasses.replace(self, table_format=table_format)

    def with_filename(self, filename: str) -> Self:
        """Name the bigWig file, overriding the default ``data.bw``.

        The suffix is not decoration: with no ``format:`` key it is the
        whole input to the backend decision
        (``build_genomic_position_table``), so a test about that decision
        has to author it.  Any name is accepted, including one whose suffix
        maps elsewhere -- proving that an explicit ``format:`` overrides the
        suffix needs exactly such a resource.  The bigWig payload itself is
        written by ``pyBigWig``, which reads the handle rather than the name
        (gain#348).
        """
        return dataclasses.replace(self, filename=filename)

    def with_na_values(self, na_values: str | list[str]) -> Self:
        """Declare the NA sentinel(s) for the single bigWig score.

        A bigWig exposes exactly one score, so no ``score_id`` is needed.
        Accepts either a scalar (``"-1"``) or a list, emitted verbatim under
        ``na_values:`` in the resource config -- the schema permits both.
        """
        if isinstance(na_values, list):
            na_values = list(na_values)
        return dataclasses.replace(self, na_values=na_values)

    def with_histogram(self, histogram: dict[str, Any]) -> Self:
        """Attach a histogram block to the single bigWig score.

        A bigWig exposes exactly one score, so no ``score_id`` is needed.  The
        block is emitted verbatim under ``histogram:`` in the resource config;
        without it a numeric bigWig score relies on the resource's auto-built
        default histogram.
        """
        # Defensive copy so a caller mutating their dict afterward cannot
        # leak into this immutable builder.
        return dataclasses.replace(self, histogram=copy.deepcopy(histogram))

    def with_data(self, data: str) -> Self:
        """Author the bedGraph rows as a whitespace-separated block."""
        return dataclasses.replace(self, data=data)

    def with_chrom_lens(self, chrom_lens: dict[str, int]) -> Self:
        """Declare the chromosome lengths written into the bigWig header."""
        return dataclasses.replace(self, chrom_lens=dict(chrom_lens))

    def with_zero_based(self) -> Self:
        """Emit ``zero_based: true`` in the ``table:`` config.

        A bigWig hard-codes its 0-based-half-open to closed-1-based
        conversion and never consults the key -- authoring it here
        realizes, config-first, the misconfiguration the table-build
        warning is meant to surface, and lets a test carry the key
        through schema validation instead of injecting it into a table
        dict by hand.
        """
        return dataclasses.replace(self, zero_based=True)

    def realize_into(self, resource_dir: pathlib.Path) -> None:
        """Write the resource config and the bigWig into ``resource_dir``."""
        data = self.data if self.data is not None else _BIGWIG_DEFAULT_DATA
        chrom_lens = (
            self.chrom_lens if self.chrom_lens is not None
            else _BIGWIG_DEFAULT_CHROM_LENS
        )
        data = _normalize_bedgraph(data)
        self._validate(data, chrom_lens)
        setup_directories(
            resource_dir, {GR_CONF_FILE_NAME: self._render_config()})
        setup_bigwig(
            resource_dir / (self.filename or _BIGWIG_FILENAME),
            data, chrom_lens)
        self.realize_files_into(resource_dir)

    def build_resource(self, tmp_path: pathlib.Path) -> GenomicResource:
        """Realize this single resource (repo id ``""``) into ``tmp_path``."""
        return _build_single_resource(self, tmp_path)

    @staticmethod
    def _validate(data: str, chrom_lens: dict[str, int]) -> None:
        """Reject a bedGraph block the bigWig writer would reject opaquely.

        ``setup_bigwig`` asserts on arity and ``pyBigWig`` raises on an
        unheadered contig; both surface without naming the builder or the
        offending row, so check here where the row is still in hand.
        """
        for line in data.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            tokens = stripped.split()
            if len(tokens) != 4:
                raise ResourceValidationError(
                    f"bedGraph row must have 4 columns "
                    f"(chrom start end value), got {len(tokens)}: "
                    f"{stripped!r}")
            if tokens[0] not in chrom_lens:
                raise ResourceValidationError(
                    f"bedGraph row on contig {tokens[0]!r} which has no "
                    f"declared length; call with_chrom_lens for it")

    def _render_config(self) -> str:
        budget_lines = "".join(
            f"    {key}: {value}\n"
            for key, value in (self.fetch_budgets or {}).items()
        )
        zero_based_line = (
            "    zero_based: true\n" if self.zero_based else "")
        format_line = (
            f"    format: {self.table_format}\n"
            if self.table_format is not None else "")
        config = (
            f"type: {self.resource_type or 'position_score'}\n"
            "table:\n"
            f"    filename: {self.filename or _BIGWIG_FILENAME}\n"
            f"{format_line}"
            f"{zero_based_line}"
            f"{budget_lines}"
            "scores:\n"
            f"- id: {self.score_id}\n"
            f"  type: {self.value_type}\n"
        )
        if self.na_values is not None:
            na_yaml = yaml.safe_dump(
                {"na_values": self.na_values}, default_flow_style=False,
                sort_keys=False)
            config += "".join(
                f"  {na_line}\n"
                for na_line in na_yaml.rstrip("\n").split("\n")
            )
        if self.histogram is not None:
            config += "  histogram:\n"
            hist_yaml = yaml.safe_dump(
                self.histogram, default_flow_style=False, sort_keys=False)
            config += "".join(
                f"    {hist_line}\n"
                for hist_line in hist_yaml.rstrip("\n").split("\n")
            )
        return config + self.render_meta()


def a_bigwig_score() -> BigWigScoreBuilder:
    """Return an immutable bigWig-backed position-score builder."""
    return BigWigScoreBuilder()
