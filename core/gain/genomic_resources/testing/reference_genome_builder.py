"""Fluent, immutable test-data builder for reference genome resources.

A sibling of :mod:`gain.genomic_resources.testing.builders`.  The
dependency runs one way: this module imports the shared single-realize
seam from ``builders``, and ``builders`` does not import back.  So
``a_reference_genome`` is imported from here.
"""
from __future__ import annotations

import dataclasses
import pathlib

import yaml

from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing import (
    setup_genome,
    setup_genome_bgz,
)
from gain.genomic_resources.testing.builders import (
    _build_single_resource,
)
from gain.genomic_resources.testing.resource_meta import (
    MetaMixin,
    append_config_block,
)
from gain.genomic_resources.testing.score_specs import (
    ResourceValidationError,
)

_GENOME_BASENAME = "chr"


# A deterministic, valid minimal sequence for a bare genome (24 bases).
_MINIMAL_GENOME_SEQUENCE = "ACGTACGTACGTACGTACGTACGT"


@dataclasses.dataclass(frozen=True)
class ReferenceGenomeBuilder(MetaMixin):
    """Immutable builder for a single ``genome`` resource.

    Two authoring modes:

    * ``with_fasta(raw)`` -- author the FASTA text.  The content is
      normalized via ``convert_to_tab_separated`` (leading indentation and
      blank lines are stripped, internal whitespace within a line becomes a
      TAB), so write single-token headers and put each chromosome's
      sequence on its own line(s) with no internal spaces.
    * ``with_chromosome(id, seq)`` -- accumulate chromosomes; the FASTA is
      synthesized (``>id`` header + the sequence wrapped at
      ``with_line_width``).

    The two modes are mutually exclusive: setting both raises when the
    genome is realized.  A bare builder (neither set) realizes a valid
    minimal genome -- one chromosome ``"1"`` with a short deterministic
    sequence.

    Realization is bgzipped by default (``.fa.gz`` + ``.fai`` + ``.gzi``);
    ``as_plain()`` switches to a plain ``.fa`` + ``.fai``.
    """

    fasta: str | None = None
    chromosomes: tuple[tuple[str, str], ...] = ()
    line_width: int = 60
    bgzip: bool = True
    index_file: str | None = None

    def with_fasta(self, raw: str) -> ReferenceGenomeBuilder:
        """Author the genome as FASTA text (primary mode).

        The content is not byte-exact: it is normalized via
        ``convert_to_tab_separated`` (leading indentation and blank lines
        are stripped; internal whitespace within a line becomes a TAB).
        Write single-token headers (``>1``, not ``>1 description``) and put
        each chromosome's sequence on its own line(s) with no internal
        spaces.

        Rejects empty or whitespace-only content fast at the call site (a
        pysam ``SamtoolsError`` otherwise surfaces with no resource context
        deep inside faidx), mirroring the ``with_chromosome`` guard.
        """
        if not raw.strip():
            raise ResourceValidationError(
                "reference genome: FASTA content must be non-empty")
        return dataclasses.replace(self, fasta=raw)

    def with_chromosome(
        self, chrom_id: str, sequence: str,
    ) -> ReferenceGenomeBuilder:
        """Accumulate one chromosome; FASTA is synthesized on realize.

        Rejects an empty or whitespace-only sequence fast at the call site
        (a pysam ``SamtoolsError`` otherwise surfaces with no resource
        context deep inside faidx).
        """
        if not sequence.strip():
            raise ResourceValidationError(
                f"chromosome {chrom_id!r}: sequence must be non-empty")
        return dataclasses.replace(
            self, chromosomes=(*self.chromosomes, (chrom_id, sequence)))

    def with_line_width(self, n: int) -> ReferenceGenomeBuilder:
        """Set the FASTA wrapping width for the synthesized-FASTA path."""
        if n <= 0:
            raise ResourceValidationError(
                f"line width must be positive, got {n}")
        return dataclasses.replace(self, line_width=n)

    def as_plain(self) -> ReferenceGenomeBuilder:
        """Realize a plain (uncompressed) ``.fa`` genome instead of bgz."""
        return dataclasses.replace(self, bgzip=False)

    def with_index_file(self, name: str) -> ReferenceGenomeBuilder:
        """Publish the ``.fai`` index under ``name`` via ``index_file``.

        The default ``<filename>.fai`` is **renamed**, not copied, so the
        realized resource carries exactly one FASTA index.  That is what
        makes a test of the ``index_file`` override meaningful: htslib and
        the protocol layer both fall back to the adjacent default name, so
        a resource that still has a ``<filename>.fai`` sitting next to the
        genome is read successfully even when the override is ignored
        entirely.
        """
        if not name.strip():
            raise ResourceValidationError(
                "reference genome: index_file name must be non-empty")
        return dataclasses.replace(self, index_file=name)

    def realize_into(self, resource_dir: pathlib.Path) -> None:
        """Write this genome resource into ``resource_dir``.

        Delegates compression/indexing and the ``genomic_resource.yaml``
        to the existing ``setup_genome``/``setup_genome_bgz`` helpers.
        """
        content = self._effective_fasta()
        genome_path = resource_dir / self._genome_filename()
        if self.bgzip:
            setup_genome_bgz(genome_path, content)
        else:
            setup_genome(genome_path, content)
        self._rename_index_into(resource_dir)
        self.append_meta_into(resource_dir)

    def _genome_filename(self) -> str:
        """Return the FASTA name this builder realizes."""
        return f"{_GENOME_BASENAME}{'.fa.gz' if self.bgzip else '.fa'}"

    def _rename_index_into(self, resource_dir: pathlib.Path) -> None:
        """Apply the ``index_file`` override to an already-written resource.

        Runs after the ``setup_*`` helper, which writes (and rewrites) the
        ``genomic_resource.yaml`` itself -- declaring ``index_file`` before
        the helper runs would silently lose the key.
        """
        if self.index_file is None:
            return
        default_index = resource_dir / f"{self._genome_filename()}.fai"
        target = resource_dir / self.index_file
        # Everything the resource realized is already on disk, so asking
        # whether the target is taken covers the FASTA, its .gzi and the
        # config without enumerating names that would drift from what
        # realize_into actually writes.
        if target != default_index and target.exists():
            raise ResourceValidationError(
                f"reference genome: index_file {self.index_file!r} would "
                f"overwrite another file of the realized resource")
        default_index.rename(target)
        # safe_dump quotes a name carrying YAML syntax, so an exotic index
        # name lands as a value instead of corrupting the config.
        append_config_block(resource_dir, yaml.safe_dump(
            {"index_file": self.index_file}, default_flow_style=False))

    def build_resource(
        self, tmp_path: pathlib.Path,
    ) -> GenomicResource:
        """Realize this single resource (repo id ``""``) into ``tmp_path``."""
        return _build_single_resource(self, tmp_path)

    def _effective_fasta(self) -> str:
        if self.fasta is not None and self.chromosomes:
            raise ResourceValidationError(
                "reference genome: with_fasta and with_chromosome are "
                "mutually exclusive; set only one authoring mode")
        if self.fasta is not None:
            return self.fasta
        chromosomes = self.chromosomes or (("1", _MINIMAL_GENOME_SEQUENCE),)
        return _synthesize_fasta(chromosomes, self.line_width)


def _synthesize_fasta(
    chromosomes: tuple[tuple[str, str], ...], line_width: int,
) -> str:
    """Render chromosomes as FASTA, wrapping each sequence at line_width."""
    lines: list[str] = []
    for chrom_id, sequence in chromosomes:
        lines.append(f">{chrom_id}")
        lines.extend(
            sequence[i:i + line_width]
            for i in range(0, len(sequence), line_width))
    return "\n".join(lines)


def a_reference_genome() -> ReferenceGenomeBuilder:
    """Return an immutable reference-genome builder."""
    return ReferenceGenomeBuilder()
