"""Unit tests for the pure tabular record parser factory.

These exercise :func:`build_tabular_parser` directly against plain lists of
strings -- no pysam, no file handles, no genomic resource -- covering all
four fused specialisations, the absent-ref/alt columns, the unmapped-contig
drop and the end-position bump.
"""
from __future__ import annotations

from gain.genomic_resources.genomic_position_table.record import (
    ALT,
    CHROM,
    PAYLOAD,
    POS_BEGIN,
    POS_END,
    REF,
    build_tabular_parser,
)


def _parser(**kwargs):
    defaults = {
        "chrom_key": 0,
        "pos_begin_key": 1,
        "pos_end_key": 2,
        "ref_key": None,
        "alt_key": None,
        "rev_chrom_map": None,
        "zero_based": False,
    }
    defaults.update(kwargs)
    return build_tabular_parser(**defaults)


# --- specialisation 1: identity ------------------------------------------

def test_identity_decodes_core_fields_eagerly() -> None:
    parse = _parser()
    row = ["1", "10", "12", "extra"]
    record = parse(row)
    assert record is not None
    assert record[CHROM] == "1"
    assert record[POS_BEGIN] == 10
    assert record[POS_END] == 12
    assert record[REF] is None
    assert record[ALT] is None


def test_identity_payload_is_the_raw_row() -> None:
    parse = _parser()
    row = ["1", "10", "12", "0.5"]
    record = parse(row)
    assert record is not None
    # payload is the raw row object, kept lazy (not copied/decoded)
    assert record[PAYLOAD] is row


def test_record_is_an_immutable_tuple() -> None:
    parse = _parser()
    record = parse(["1", "10", "12"])
    assert isinstance(record, tuple)


# --- ref/alt columns ------------------------------------------------------

def test_ref_alt_read_when_configured() -> None:
    parse = _parser(ref_key=3, alt_key=4)
    record = parse(["1", "10", "10", "A", "T"])
    assert record is not None
    assert record[REF] == "A"
    assert record[ALT] == "T"


def test_ref_alt_absent_yield_none() -> None:
    parse = _parser(ref_key=None, alt_key=None)
    record = parse(["1", "10", "10", "A", "T"])
    assert record is not None
    assert record[REF] is None
    assert record[ALT] is None


# --- specialisation 2: zero-based ----------------------------------------

def test_zero_based_shifts_begin_and_keeps_end() -> None:
    parse = _parser(zero_based=True)
    # [10, 20) half-open zero-based -> begin+1, end unchanged
    record = parse(["1", "10", "20"])
    assert record is not None
    assert record[POS_BEGIN] == 11
    assert record[POS_END] == 20


def test_zero_based_end_bump_when_it_would_collapse_onto_begin() -> None:
    parse = _parser(zero_based=True)
    # a single-base zero-based row where begin == end: end is bumped so it
    # does not collapse onto the shifted begin
    record = parse(["1", "10", "10"])
    assert record is not None
    assert record[POS_BEGIN] == 11
    assert record[POS_END] == 11


# --- specialisation 3: chromosome-mapping --------------------------------

def test_chrom_mapping_remaps_contig() -> None:
    parse = _parser(rev_chrom_map={"1": "chr1"})
    record = parse(["1", "10", "12"])
    assert record is not None
    assert record[CHROM] == "chr1"
    assert record[POS_BEGIN] == 10
    assert record[POS_END] == 12


def test_chrom_mapping_unmapped_contig_returns_none() -> None:
    parse = _parser(rev_chrom_map={"1": "chr1"})
    assert parse(["2", "10", "12"]) is None


def test_chrom_mapping_payload_keeps_file_contig() -> None:
    parse = _parser(rev_chrom_map={"1": "chr1"})
    row = ["1", "10", "12"]
    record = parse(row)
    assert record is not None
    # the record's contig is remapped, but the raw payload is untouched
    assert record[CHROM] == "chr1"
    assert record[PAYLOAD][0] == "1"


# --- specialisation 4: zero-based AND chromosome-mapping -----------------

def test_zero_based_and_chrom_mapping_applies_both() -> None:
    parse = _parser(rev_chrom_map={"1": "chr1"}, zero_based=True)
    record = parse(["1", "10", "10"])
    assert record is not None
    assert record[CHROM] == "chr1"
    assert record[POS_BEGIN] == 11
    assert record[POS_END] == 11


def test_zero_based_and_chrom_mapping_unmapped_contig_returns_none() -> None:
    parse = _parser(rev_chrom_map={"1": "chr1"}, zero_based=True)
    assert parse(["2", "10", "10"]) is None


def test_factory_selects_one_specialisation_reused_across_rows() -> None:
    # the same parser callable handles many rows -- the specialisation is
    # selected once by the factory, not re-decided per row
    parse = _parser(zero_based=True)
    first = parse(["1", "0", "0"])
    second = parse(["1", "5", "9"])
    assert first is not None
    assert second is not None
    assert (first[POS_BEGIN], first[POS_END]) == (1, 1)
    assert (second[POS_BEGIN], second[POS_END]) == (6, 9)
