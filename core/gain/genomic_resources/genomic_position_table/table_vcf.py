from __future__ import annotations

from collections.abc import Callable, Generator
from functools import cache

import pysam

from gain.genomic_resources.repository import GenomicResource

from .record import Record
from .table_tabix import TabixGenomicPositionTable

# Slot positions inside a VCF record's PAYLOAD.  The payload of a VCF record is
# the variant record **paired with an allele index** -- and it has to be a pair,
# because a VCF record is not a row: one ``pysam.VariantRecord`` explodes into
# one record per ALT allele, and the variant record alone cannot say which of
# its alleles a given record stands for.  Everything else the INFO lookup needs
# is reachable from the variant record: the header metadata that types an INFO
# field is ``variant.header.info``, so the payload carries no third element.
#
# ``None`` in the ALLELE_INDEX position means the record's ALT is absent ('.'):
# the record stands for the *reference* allele, and a Number=R INFO field is
# read at its reference offset.  (Pinned in test_genomic_position_table.py.)
VARIANT = 0
ALLELE_INDEX = 1

# A VCF parser maps a raw variant record **and one allele index** to a record,
# or to ``None`` when the variant's contig is absent from a configured
# chromosome map.  The extra argument is why this is not a ``TabularParser``:
# the tabular backends parse one row into one record, VCF parses one variant
# record into one record *per allele*.  The two parsers are private to their
# own backends and never meet, so neither is contorted to fit the other.
VCFParser = Callable[[pysam.VariantRecord, "int | None"], "Record | None"]


def build_vcf_parser(rev_chrom_map: dict[str, str] | None) -> VCFParser:
    """Build a (variant, allele index) -> record parser for the VCF backend.

    The parser is a pure function of the reverse chromosome map (file contig ->
    reference contig), specialised on its presence **once**, here, rather than
    branched per record -- the same fusion ``build_tabular_parser`` does for the
    tabular backends, with the chromosome map as the only transform a VCF table
    can configure (a VCF is always one-based, so there is no zero-based
    variant, and REF/ALT are structural rather than configured columns).

    A variant whose contig is absent from the map yields ``None`` and the
    record is dropped by the callers, exactly as the tabular parser's rows are.

    **The map's presence selects the path, not its contents.**  An *empty* map
    -- which a well-formed ``chrom_mapping.filename`` with no body rows yields
    -- is a map that maps nothing, so every record is dropped.  It is not
    treated as "no mapping at all": a table configured with such a file has no
    chromosomes either (``get_chromosomes()`` comes from the mapping file), and
    passing the file contigs through would make it yield records on contigs it
    says it does not have -- ``get_records_in_region`` would raise for the very
    contig ``get_all_records`` had just handed back.  This is the same rule
    :func:`build_tabular_parser` follows, and it is a deliberate change from
    the pre-record VCF backend, which tested the map for *truthiness* and so
    identity-mapped an empty one.  (Pinned in test_genomic_position_table.py by
    test_an_empty_chrom_mapping_file_maps_nothing_and_so_drops_every_record.)
    """
    if rev_chrom_map is not None:
        def parse_mapped(
            raw: pysam.VariantRecord, allele_index: int | None,
        ) -> Record | None:
            rchrom = rev_chrom_map.get(raw.contig)
            if rchrom is None:
                return None
            alt = None
            if allele_index is not None:
                assert raw.alts is not None
                alt = raw.alts[allele_index]
            return (
                rchrom, raw.pos, raw.stop, raw.ref, alt,
                (raw, allele_index))
        return parse_mapped

    def parse_identity(
        raw: pysam.VariantRecord, allele_index: int | None,
    ) -> Record | None:
        alt = None
        if allele_index is not None:
            assert raw.alts is not None
            alt = raw.alts[allele_index]
        return (
            raw.contig, raw.pos, raw.stop, raw.ref, alt,
            (raw, allele_index))
    return parse_identity


class VCFGenomicPositionTable(TabixGenomicPositionTable):
    """Represents a VCF file genome position table.

    Yields **records** -- the same six-slot plain tuples every other record
    backend yields -- so it inherits its tabix parent's read cascade and
    record-indexed :class:`LineBuffer` as they are: the buffer windows a VCF
    record by the very slots (``CHROM``, ``POS_BEGIN``, ``POS_END``) it windows
    a tabix record by, and neither knows nor asks which backend built the one it
    is holding.

    **Its PAYLOAD is not a raw row.**  A VCF record carries ``(variant record,
    allele index)`` in the slot where a tabix record carries the raw tabular
    row (see ``VARIANT``/``ALLELE_INDEX`` above), because a VCF score is not a
    column: it is an INFO field, looked up by name against the variant's header
    metadata and selected by allele.  That lookup lives in one place --
    ``VCFScoreLine`` in ``genomic_scores.py``, chosen once per table when the
    score is opened.  Only the five decoded slots (``CHROM`` ... ``ALT``) mean
    the same thing across every backend.
    """

    CHROM = "CHROM"
    POS_BEGIN = "POS"
    POS_END = "POS"

    def __init__(
            self, genomic_resource: GenomicResource, table_definition: dict):
        super().__init__(genomic_resource, table_definition)
        self.header = self._load_vcf_header()
        # Built in open(), once the file's contigs -- and so the reverse
        # chromosome map -- are known.  Not the parent's ``self.parser``: a VCF
        # parser takes an allele index as well, so it has a signature of its
        # own (see VCFParser).
        self.vcf_parser: VCFParser | None = None

    def _load_vcf_header(self) -> pysam.VariantHeaderMetadata:
        """Load the table-level INFO metadata from the *.header.vcf.gz file.

        This is the metadata the *score definitions* are built from, before any
        record is read (``GenomicScore._build_scoredefs`` autogenerates one
        score def per INFO field from it).  The per-record INFO **lookup** needs
        no such thing: it derives the header from the variant record it is
        reading (``variant.header.info``), so nothing is carried alongside a
        record.

        **The returned metadata outlives the file it is read from.**  A
        ``pysam.VariantHeaderMetadata`` is a *view*, not a copy, so closing the
        file under it would be a use-after-free if the view borrowed the
        ``bcf_hdr_t`` from the ``htsFile``.  It does not: the view holds a
        strong reference to its ``pysam.VariantHeader``, which owns the header
        struct and frees it only when *it* is collected -- so the header
        survives the file, and closing the file is safe.  That is what lets this
        method hand the metadata out and shut the file behind it, rather than
        leaving the descriptor to refcount finalisation.

        Both halves are pinned, in test_genomic_position_table.py:
        test_vcf_header_metadata_outlives_the_closed_header_file pins that the
        metadata survives the close (that this is not a use-after-free), and
        test_vcf_header_load_closes_the_header_file pins the close itself.  The
        latter has to spy on it: closing is invisible to every functional test
        -- *because* the metadata outlives it, a version that retained the file
        forever would return byte-identical results.

        The close is deliberate, and it is not free: ``VariantFile.close()``
        raises ``OSError`` when ``hts_close`` fails, where the implicit
        refcount-driven ``__dealloc__`` it replaces would have swallowed that
        error.  The sidecar is a small read-only bgzf file, so a failing close
        is effectively unreachable here -- but on a closing file this *is* a
        newly reachable exception, and it is preferred to relying on
        finalisation: an exception raised between the open and the return keeps
        the frame -- and so the file -- alive.
        """
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
        with vcf_file:
            return vcf_file.header.info

    def open(self) -> VCFGenomicPositionTable:
        self.pysam_file = self.genomic_resource.open_vcf_file(
            self.definition.filename)
        self._set_core_column_keys()
        self._build_chrom_mapping()
        # Like the tabix parser, this cannot be built any earlier: the reverse
        # chromosome map needs the file's contigs.
        self.vcf_parser = build_vcf_parser(self.rev_chrom_map)
        return self

    def close(self) -> None:
        super().close()
        # The parser closes over the reverse chromosome map, which open()
        # resolves from the file; a closed table must not keep it.
        self.vcf_parser = None

    @cache  # pylint: disable=method-cache-max-size-none
    def get_file_chromosomes(self) -> list[str]:
        with self.genomic_resource.open_tabix_file(
                self.definition.filename) as pysam_file_tabix:
            contigs = pysam_file_tabix.contigs
        return list(map(str, contigs))

    def get_line_iterator(
        self, chrom: str | None = None, pos_begin: int | None = None,
    ) -> Generator[Record | None, None, None]:
        """Fetch the variant records and parse them into records, per allele.

        One variant record becomes **one record per ALT allele** -- that is the
        VCF backend's whole shape, and it is why its parser takes an allele
        index.  A variant whose ALT is absent ('.') has no alternative allele at
        all, and yields a single record with a ``None`` allele index; the score
        layer reads its reference-allele INFO values accordingly.
        """
        assert isinstance(self.pysam_file, pysam.VariantFile)
        assert self.vcf_parser is not None
        parser = self.vcf_parser

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
        for raw in self.pysam_file.fetch(fchrom, pos_begin):
            assert raw.ref is not None
            if raw.alts is None:
                yield parser(raw, None)
                continue
            for allele_index in range(len(raw.alts)):
                yield parser(raw, allele_index)
