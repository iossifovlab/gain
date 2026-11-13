"""Fluent, immutable test-data builder for VCF INFO allele scores.

A sibling of :mod:`gain.genomic_resources.testing.builders`.  The
dependency runs one way: this module imports the shared single-realize
seam from ``builders``, and ``builders`` does not import back.  So
``a_vcf_info_score`` is imported from here.
"""
from __future__ import annotations

import dataclasses
import pathlib
from typing import Any, Self

from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.repository_ids import GR_CONF_FILE_NAME
from gain.genomic_resources.testing import (
    setup_directories,
    setup_vcf,
)
from gain.genomic_resources.testing.builders import (
    _build_single_resource,
)
from gain.genomic_resources.testing.extra_files import ExtraFilesMixin
from gain.genomic_resources.testing.resource_meta import (
    MetaMixin,
)
from gain.genomic_resources.testing.score_builder_helpers import (
    _validate_score_specs,
)
from gain.genomic_resources.testing.score_specs import (
    ResourceValidationError,
    ScoreSpec,
    append_score,
    render_score_specs_yaml,
    set_aggregator,
    set_histogram,
    set_na_values,
)

_VCF_FILENAME = "data.vcf.gz"


# ``setup_vcf`` derives the header sidecar from the data filename and indexes
# it alongside; ``without_header_index`` drops that index again.
_VCF_HEADER_INDEX_FILENAME = "data.header.vcf.gz.tbi"


_VCF_DEFAULT_DATA = """
##fileformat=VCFv4.1
##INFO=<ID=score,Number=1,Type=Float,Description="a float">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   10  .  A   T   .    .      score=0.1
chr1   11  .  A   T   .    .      score=0.2
"""


@dataclasses.dataclass(frozen=True)
class VcfInfoScoreBuilder(ExtraFilesMixin, MetaMixin):
    """Immutable builder for a VCF-backed ``allele_score`` resource.

    The score definitions are derived by the resource from the VCF's
    ``##INFO`` header, so a bare builder declares none: author the INFO
    metadata in the VCF text and the scores follow.  :meth:`with_score`
    AMENDS one of them through a ``scores:`` entry -- the ``desc``,
    ``na_values``, ``aggregator`` and ``histogram`` a config may give a
    header-derived score, and a ``type:`` where the header declares a
    scalar (dbSNP's ``Flag`` typed ``bool`` is the canonical case).  Its
    ``value_type`` defaults to ``None``, an entry that states no ``type:``
    at all, because that is a first-class shape here (gain#1221) and the
    header already carries one.  The block is a FILTER by default -- the
    resource defines only the fields it names; :meth:`with_merge_vcf_scores`
    turns it into an override, merging every unnamed header field back in.

    Nothing declared is checked against the VCF text: an entry naming no
    INFO field, or stating a type the header's ``Number`` denies, is what a
    test authors to watch the RESOURCE refuse it (gain#1336), so the builder
    renders it as given.  The block itself is checked as the table builders
    check theirs -- a field declared twice is refused at realize.  Reads
    back through ``AlleleScore`` on the ``vcf_info`` table backend, which
    the ``.vcf.gz`` filename selects.
    """

    data: str | None = None
    scores: tuple[ScoreSpec, ...] = ()
    merge_vcf_scores: bool = False
    zero_based: bool = False
    csi: bool = False
    index_filename: str | None = None
    realize_index_filename: bool = True
    header_index: bool = True

    def with_data(self, data: str) -> Self:
        """Author the whole VCF, ``##`` header lines included."""
        return dataclasses.replace(self, data=data)

    def with_score(
        self, score_id: str, value_type: str | None = None, *,
        desc: str | None = None,
    ) -> Self:
        """Amend the INFO field ``score_id`` through a ``scores:`` entry.

        With no ``value_type`` the entry states no ``type:`` and reads what
        the header-only resource reads (gain#1221).
        """
        return dataclasses.replace(
            self,
            scores=append_score(self.scores, score_id, value_type, desc=desc),
        )

    def with_histogram(
        self, histogram: dict[str, Any], *, score_id: str | None = None,
    ) -> Self:
        """Attach a histogram block to a score declared with_score."""
        return dataclasses.replace(
            self,
            scores=set_histogram(self.scores, histogram, score_id=score_id),
        )

    def with_na_values(
        self, na_values: str | list[str], *, score_id: str | None = None,
    ) -> Self:
        """Declare the NA sentinel(s) of a score declared with_score."""
        return dataclasses.replace(
            self,
            scores=set_na_values(self.scores, na_values, score_id=score_id),
        )

    def with_aggregator(
        self, aggregator: str, *, score_id: str | None = None,
    ) -> Self:
        """Declare the default aggregator of a score declared with_score."""
        return dataclasses.replace(
            self,
            scores=set_aggregator(self.scores, aggregator, score_id=score_id),
        )

    def with_csi_index(self) -> Self:
        """Index the bgzipped VCF as ``.csi``, not the default ``.tbi``."""
        return dataclasses.replace(self, csi=True)

    def with_index_filename(self, index_filename: str) -> Self:
        """Realize the index at ``index_filename`` and declare it in config.

        The index htslib writes next to the data file is MOVED to
        ``index_filename`` -- so no adjacent ``data.vcf.gz.tbi`` /
        ``data.vcf.gz.csi`` is left behind -- and the table config declares
        ``index_filename:`` pointing at it.

        Moving it is the whole point: htslib auto-probes for an adjacent
        index and opens happily without being told about it, so a resource
        whose index sits at its default name proves nothing about whether
        the configured ``index_filename`` was honoured (gain#596).
        """
        return dataclasses.replace(
            self, index_filename=index_filename, realize_index_filename=True)

    def with_missing_index_filename(self, index_filename: str) -> Self:
        """Declare ``index_filename:`` in the config with no such file.

        The realized index stays at its default adjacent name, so htslib
        could still auto-probe its way to a working file -- which is
        exactly what an open must NOT do here: an explicitly configured
        index that does not exist has to fail, naming the configured path
        rather than quietly reading through some other one (gain#596).
        """
        return dataclasses.replace(
            self, index_filename=index_filename, realize_index_filename=False)

    def without_header_index(self) -> Self:
        """Ship the ``*.header.vcf.gz`` sidecar with no index of its own.

        The realize path indexes the sidecar because it bgzips it the same
        way as the data file; real score resources (dbSNP is the canonical
        one) ship it unindexed.  Use this to realize that shape: the table
        reads its INFO metadata without an index, and opened by name it is a
        file whose index resolution resolves to NOTHING and must still open
        (gain#596).
        """
        return dataclasses.replace(self, header_index=False)

    def with_zero_based(self) -> Self:
        """Emit ``zero_based: true`` in the ``table:`` config.

        A VCF is always 1-based, so the ``vcf_info`` backend ignores the
        key -- authoring it here realizes, config-first, the
        misconfiguration the table-build warning is meant to surface, and
        lets a test carry the key through schema validation instead of
        injecting it into a table dict by hand.
        """
        return dataclasses.replace(self, zero_based=True)

    def with_merge_vcf_scores(self) -> Self:
        """Emit a top-level ``merge_vcf_scores: true``.

        With the block an override rather than a filter, a refusal keyed on
        a header field (gain#1258) is reachable through a block that never
        names that field.
        """
        return dataclasses.replace(self, merge_vcf_scores=True)

    def realize_into(self, resource_dir: pathlib.Path) -> None:
        """Write the resource config and the bgzipped VCF + index."""
        data = self.data if self.data is not None else _VCF_DEFAULT_DATA
        self._validate(data)
        _validate_score_specs(self.scores)
        setup_directories(
            resource_dir, {GR_CONF_FILE_NAME: self._render_config()})
        setup_vcf(resource_dir / _VCF_FILENAME, data, csi=self.csi)
        if not self.header_index:
            (resource_dir / _VCF_HEADER_INDEX_FILENAME).unlink()
        if self.index_filename is not None and self.realize_index_filename:
            suffix = ".csi" if self.csi else ".tbi"
            (resource_dir / f"{_VCF_FILENAME}{suffix}").rename(
                resource_dir / self.index_filename)
        self.realize_files_into(resource_dir)

    def build_resource(self, tmp_path: pathlib.Path) -> GenomicResource:
        """Realize this single resource (repo id ``""``) into ``tmp_path``."""
        return _build_single_resource(self, tmp_path)

    @staticmethod
    def _validate(data: str) -> None:
        """Require the header lines the scores are derived from.

        Without an ``##INFO`` line the resource realizes with zero scores
        and every annotated value comes back empty -- a silent, confusing
        failure well downstream of the builder.
        """
        if "##fileformat" not in data:
            raise ResourceValidationError(
                "VCF data must carry a '##fileformat' header line")
        if "##INFO=" not in data:
            raise ResourceValidationError(
                "VCF data must declare at least one '##INFO=' field; the "
                "score definitions are derived from them")
        if "#CHROM" not in data:
            raise ResourceValidationError(
                "VCF data must carry a '#CHROM' column header line")

    def _render_config(self) -> str:
        zero_based_line = (
            "    zero_based: true\n" if self.zero_based else "")
        index_line = (
            f"    index_filename: {self.index_filename}\n"
            if self.index_filename is not None else "")
        merge_line = (
            "merge_vcf_scores: true\n" if self.merge_vcf_scores else "")
        scores_block = (
            "scores:\n" + render_score_specs_yaml(self.scores)
            if self.scores else "")
        return (
            "type: allele_score\n"
            "table:\n"
            f"    filename: {_VCF_FILENAME}\n"
            f"{index_line}"
            f"{zero_based_line}"
            f"{merge_line}"
            f"{scores_block}"
            f"{self.render_meta()}"
        )


def a_vcf_info_score() -> VcfInfoScoreBuilder:
    """Return an immutable VCF-backed allele-score builder."""
    return VcfInfoScoreBuilder()
