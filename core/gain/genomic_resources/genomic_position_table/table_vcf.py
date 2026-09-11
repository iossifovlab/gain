from __future__ import annotations

from collections.abc import Callable, Generator
from typing import ClassVar

import pysam

from gain.genomic_resources.repository import GenomicResource
from gain.utils.fs_utils import find_ci

from .record import Record
from .table_tabix import TabixGenomicPositionTable

# Slot positions inside a VCF record's PAYLOAD.  The payload of a VCF record
# carries the variant record, an allele index, and the two pysam proxies an
# INFO lookup needs.
#
# The allele index has to be there because a VCF record is not a row: one
# ``pysam.VariantRecord`` explodes into one record per ALT allele, and the
# variant record alone cannot say which of its alleles a given record stands
# for.  ``None`` in that position means the record's ALT is absent ('.'): the
# record stands for the *reference* allele, and a Number=R INFO field is read
# at its reference offset.  (Pinned in test_genomic_position_table.py.)
#
# **INFO and INFO_META are here because pysam allocates a fresh proxy on every
# access.**  ``variant.info`` is not a cached attribute -- ``v.info is v.info``
# is False, and each access costs ~85ns -- so a reader that re-derived them per
# score would pay ~170ns per score per record.  Measured, before they lived
# here: a 20-score read of a 3000-row VCF went from 8.50 to 10.76us/line.
#
# They used to be memoised on the score line, resolved on its first score read.
# With the score lines gone the value read is a pure function of the record
# (``vcf_scores.extract_vcf_value``), so the memo has to live in
# the record itself -- which is what a backend-defined payload is for.  The
# trade is that resolution is now EAGER: a record whose scores are never read
# pays the ~170ns anyway.  That case is real but narrow -- ``AlleleScore``
# fetches every record at a position and reads only the ref/alt match, so a
# 4-allele position wastes ~0.5us -- and it buys a value read that needs no
# state of its own, and so no per-line object to hold it.
#
# Resolving them here does NOT make the per-key metadata lookup eager: the
# ``INFO_META.get(key)`` that types a field is still made only in the branch
# that needs it (see ``extract_vcf_value``), because that call builds a fresh
# ``VariantMetadata`` per key and a Number=1 field must not pay for one.
VARIANT = 0
ALLELE_INDEX = 1
INFO = 2
INFO_META = 3

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
                (raw, allele_index, raw.info, raw.header.info))
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
            (raw, allele_index, raw.info, raw.header.info))
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
    allele index, info, info_meta)`` in the slot where a tabix record carries
    the raw tabular row (see ``VARIANT``/``ALLELE_INDEX``/``INFO``/
    ``INFO_META`` above), because a VCF score is not a column: it is an INFO
    field, looked up by name against the variant's header metadata and
    selected by allele.  That lookup lives in one place --
    ``vcf_scores.extract_vcf_value``, bound once per score when it is opened.
    Only the five decoded slots (``CHROM`` ... ``ALT``) mean the same thing
    across every backend.
    """

    # **Set back to False on purpose.**  This backend inherits its tabix
    # parent's ``get_region_value_arrays`` implementation, but cannot honour
    # its contract: that method reads a raw tabular row and serves columns by
    # integer payload index, and neither holds here -- the PAYLOAD is
    # ``(variant, allele index, info, info_meta)`` and a VCF score is an INFO
    # field
    # addressed by *name*.  Inheriting True would hand a caller rows that are
    # not rows.  This one line is what the callers' old
    # ``isinstance(Tabix) and not isinstance(VCF)`` said, said once and in the
    # place that knows why.
    supports_value_arrays: ClassVar[bool] = False

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

        **The sidecar is read through a handle, never opened by name.**  It
        used to be handed to ``pysam.VariantFile`` as a filename, and because
        htslib probes for an index on every by-name open -- and a header-only
        sidecar ships none -- that logged a spurious ``[E::idx_find_and_load]``
        which had to be silenced with a ``set_verbosity(0)`` bracket: on every
        url, credentialed or not, from this constructor, serialised on a
        process-global lock (gain#1360).  Reading the ``##`` lines off
        ``open_raw_file`` -- the route the tabix base class already takes for
        its own header -- and feeding them to ``pysam.VariantHeader.add_line``
        hands htslib no filename at all, so there is no probe, nothing to
        silence and no bracket (gain#1406).  Under ADR 0023 that makes the
        sidecar a first escape, redacted by the handle, rather than a third.

        The metadata outlives the handle it is read from: a
        ``pysam.VariantHeaderMetadata`` is a *view*, and it holds a strong
        reference to the ``pysam.VariantHeader`` built here, which owns the
        header struct and frees it only when *it* is collected.  Pinned by
        test_vcf_header_metadata_outlives_the_header_read.
        """
        assert self.definition.get("header_mode", "file") == "file"
        filename = self.definition.filename
        # Case-insensitively, matching the suffix rule that routes a file to
        # this backend (gain#348).  A plain ``.index(".vcf")`` raised a bare
        # "substring not found" from this constructor for a ``.VCF.GZ``
        # resource, naming neither the resource nor the file.  The name is
        # spliced out of the ORIGINAL filename at the match, so the sidecar
        # keeps the resource's own spelling.
        idx = find_ci(filename, ".vcf")
        if idx < 0:
            raise ValueError(
                f"the table of resource "
                f"<{self.genomic_resource.get_full_id()}> is a VCF table, "
                f"but its filename {filename} has no '.vcf' in it, so the "
                f"accompanying '.header.vcf.gz' file cannot be named")
        header_filename = filename[:idx] + ".header" + filename[idx:]
        assert self.genomic_resource.file_exists(header_filename), \
            "VCF tables must have an accompanying *.header.vcf.gz file!"
        header = pysam.VariantHeader()
        with self.genomic_resource.open_raw_file(
                header_filename, compression="gzip") as infile:
            for line in infile:
                if not line.startswith("##"):
                    break
                header.add_line(line.rstrip("\n"))
        return header.info

    def open(self) -> VCFGenomicPositionTable:
        self.pysam_file = self.genomic_resource.open_vcf_file(
            self.definition.filename, self.index_filename)
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

    def _load_file_chromosomes(self) -> list[str]:
        """Read the file's contigs off its TABIX INDEX, not its VCF header.

        The index lists the contigs the file actually carries records on, which
        is what every caller of this means by "the file's chromosomes"; the VCF
        header's ``##contig`` lines are a declaration and may name contigs with
        no records (or none at all).  So this opens the file a second time, as a
        tabix file -- ``self.pysam_file`` is a ``VariantFile`` and cannot answer
        it.  It is the SAME file, so it is opened with the same index the
        definition configures: this open, not the one in :meth:`open`, is where
        a dropped ``index_filename`` surfaced, as an ``OSError`` naming an
        index path the config never mentions (gain#596).

        Which is why the open table is a **precondition** here, rather than
        something this can quietly do without.  Unlike its three siblings, an
        implementation that opens the file itself can still answer after
        ``close()``: a closed table would hand back the contigs of a file it
        reopened behind the caller's back, and leave that file -- or, on the
        http and s3 protocols, that connection -- open on a table the caller
        believes is closed.  Refusing keeps a closed VCF table saying exactly
        what a closed tabix one says (gain#350).

        It used to be able to make a closed table answer *wrongly* as well:
        ``get_chromosomes()`` reached this method, and with the chromosome map
        released it mapped these contigs through nothing and returned the
        FILE's names where an open table returns reference-space ones, with no
        error to notice it by.  That route is gone since gain#1303 --
        ``get_chromosomes()`` reads ``chrom_order`` and refuses off that, never
        reaching this -- so what is left to protect is the reopen and the
        leaked handle, which is enough on its own.
        """
        if self.pysam_file is None:
            raise ValueError(
                f"vcf table not open: "
                f"{self.genomic_resource.resource_id}: "
                f"{self.definition}")
        with self.genomic_resource.open_tabix_file(
                self.definition.filename,
                self.index_filename) as pysam_file_tabix:
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
