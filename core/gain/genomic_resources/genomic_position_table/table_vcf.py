from __future__ import annotations

from collections.abc import Generator
from functools import cache
from typing import ClassVar

import pysam

from gain.genomic_resources.repository import GenomicResource

from .line import VCFLine
from .table_tabix import TabixGenomicPositionTable


class VCFGenomicPositionTable(TabixGenomicPositionTable):
    """Represents a VCF file genome position table.

    Still on the *adapter* path: it yields :class:`VCFLine` objects, and the
    score layer wraps them in a ``ScoreLine`` that reads INFO fields by name.
    So it resets ``yields_records``, which its tabix parent sets.  It can
    nevertheless reuse the parent's record read cascade and record-indexed
    line buffer unchanged, because a ``VCFLine`` *is* a record-shaped tuple.
    #237 migrates this backend proper.
    """

    yields_records: ClassVar[bool] = False

    CHROM = "CHROM"
    POS_BEGIN = "POS"
    POS_END = "POS"

    def __init__(
            self, genomic_resource: GenomicResource, table_definition: dict):
        super().__init__(genomic_resource, table_definition)
        self.header = self._load_vcf_header()

    def _load_vcf_header(self) -> pysam.VariantHeaderMetadata:
        assert self.definition.get("header_mode", "file") == "file"
        filename = self.definition.filename
        idx = filename.index(".vcf")
        header_filename = filename[:idx] + ".header" + filename[idx:]
        assert self.genomic_resource.file_exists(header_filename), \
            "VCF tables must have an accompanying *.header.vcf.gz file!"
        # The header file is opened only to read `header.info`; it is never
        # fetched. Header-only resources (e.g. dbSNP) ship no index, so htslib
        # would log a spurious `[E::idx_find_and_load]` while auto-probing for
        # one on open. Silence htslib for the duration of the open.
        saved_verbosity = pysam.set_verbosity(0)
        try:
            vcf_file = self.genomic_resource.open_vcf_file(header_filename)
        finally:
            pysam.set_verbosity(saved_verbosity)
        return vcf_file.header.info

    def _make_vcf_line(
        self, raw_line: pysam.VariantRecord, allele_index: int | None,
    ) -> VCFLine | None:
        """Build a VCF line, resolving its contig before construction.

        The mapped contig is passed in rather than written back onto a
        finished line: a VCF line is a record-shaped tuple, so its CHROM slot
        is fixed once and for all when it is built.  A file contig that is
        absent from the chromosome map yields ``None`` and the row is dropped.
        """
        if not self.rev_chrom_map:
            return VCFLine(raw_line, allele_index)
        rchrom = self.rev_chrom_map.get(raw_line.contig)
        if rchrom is None:
            return None
        return VCFLine(raw_line, allele_index, rchrom)

    def open(self) -> VCFGenomicPositionTable:
        self.pysam_file = self.genomic_resource.open_vcf_file(
            self.definition.filename)
        self._set_core_column_keys()
        self._build_chrom_mapping()
        return self

    @cache  # pylint: disable=method-cache-max-size-none
    def get_file_chromosomes(self) -> list[str]:
        with self.genomic_resource.open_tabix_file(
                self.definition.filename) as pysam_file_tabix:
            contigs = pysam_file_tabix.contigs
        return list(map(str, contigs))

    def get_line_iterator(
        self, chrom: str | None = None, pos_begin: int | None = None,
    ) -> Generator[VCFLine | None, None, None]:
        assert isinstance(self.pysam_file, pysam.VariantFile)

        if chrom is not None:
            fchrom = self.unmap_chromosome(chrom)
            if fchrom is None:
                raise ValueError(
                    f"error in mapping chromosome {chrom} to file contigs: "
                    f"{self.get_file_chromosomes()}")
        else:
            fchrom = None

        self.stats["tabix fetch"] += 1
        self.buffer.clear()
        for raw_line in self.pysam_file.fetch(fchrom, pos_begin):
            allele_index: int | None
            for allele_index, alt in enumerate(raw_line.alts or [None]):
                assert raw_line.ref is not None
                allele_index = allele_index if alt is not None else None
                line = self._make_vcf_line(raw_line, allele_index)
                yield line
