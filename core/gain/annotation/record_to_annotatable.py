from __future__ import annotations

import abc
import argparse
from collections.abc import Mapping
from typing import cast

from gain import logging
from gain.annotation.annotatable import (
    Annotatable,
    CNVAllele,
    Position,
    Region,
    VCFAllele,
)
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.utils.cnv_utils import cnv_variant_type, cshl2cnv_variant
from gain.utils.dae_utils import cshl2vcf_variant, dae2vcf_variant

logger = logging.getLogger(__name__)


class MalformedRecordError(ValueError):
    """A record the builders cannot turn into an annotatable."""


def _text(record: Mapping[str, object], column: str) -> str:
    value = record[column]
    if not isinstance(value, str):
        raise MalformedRecordError(
            f"{column} must be a string, got {value!r}")
    return value


def _position(record: Mapping[str, object], column: str) -> int:
    value = record[column]
    # An exact type test: a bool is an int subclass, and not a position.
    if type(value) not in (str, int):
        raise MalformedRecordError(
            f"{column} must be an integer, got {value!r}")
    try:
        position = int(cast("str | int", value))
    except ValueError:
        raise MalformedRecordError(
            f"{column} must be an integer, got {value!r}") from None
    if position < 1:
        raise MalformedRecordError(
            f"{column} is {position}; positions are 1-based")
    return position


class RecordToAnnotable(abc.ABC):
    """Base class for record to annotable transformation."""
    def __init__(self, columns: tuple, ref_genome: ReferenceGenome | None):
        self.columns = columns
        self.ref_genome = ref_genome

    def build(self, record: Mapping[str, object]) -> Annotatable:
        """Construct an annotatable from a record.

        Raises :class:`MalformedRecordError` when the record cannot be one:
        a field of the wrong type or format, a position below 1, or an end
        before the beginning.
        """
        try:
            annotatable = self._build(record)
        except MalformedRecordError:
            raise
        except ValueError as ex:
            raise MalformedRecordError(str(ex)) from ex
        if annotatable.pos < 1:
            raise MalformedRecordError(
                f"{annotatable} has position {annotatable.pos}; "
                f"positions are 1-based")
        if annotatable.pos_end < annotatable.pos:
            raise MalformedRecordError(
                f"{annotatable} ends at {annotatable.pos_end}, "
                f"before its beginning {annotatable.pos}")
        return annotatable

    @abc.abstractmethod
    def _build(self, record: Mapping[str, object]) -> Annotatable:
        """Construct an annotatable from a record of well-typed fields."""


class RecordToPosition(RecordToAnnotable):
    def __init__(self, columns: tuple, ref_genome: ReferenceGenome | None):
        super().__init__(columns, ref_genome)
        self.chrom_column, self.pos_column = columns

    def _build(self, record: Mapping[str, object]) -> Annotatable:
        return Position(_text(record, self.chrom_column),
                        _position(record, self.pos_column))


class RecordToRegion(RecordToAnnotable):
    def __init__(self, columns: tuple, ref_genome: ReferenceGenome | None):
        super().__init__(columns, ref_genome)
        self.chrom_col, self.pos_beg_col, self.pos_end_col = columns

    def _build(self, record: Mapping[str, object]) -> Annotatable:
        return Region(_text(record, self.chrom_col),
                      _position(record, self.pos_beg_col),
                      _position(record, self.pos_end_col))


class RecordToVcfAllele(RecordToAnnotable):
    def __init__(self, columns: tuple, ref_genome: ReferenceGenome | None):
        super().__init__(columns, ref_genome)
        self.chrom_col, self.pos_col, self.ref_col, self.alt_col = columns

    def _build(self, record: Mapping[str, object]) -> Annotatable:
        return VCFAllele(_text(record, self.chrom_col),
                         _position(record, self.pos_col),
                         _text(record, self.ref_col),
                         _text(record, self.alt_col))


class VcfLikeRecordToVcfAllele(RecordToAnnotable):
    """Transform a columns record into VCF allele annotatable."""

    def __init__(self, columns: tuple, ref_genome: ReferenceGenome | None):
        super().__init__(columns, ref_genome)
        self.vcf_like_col, = columns

    def _build(self, record: Mapping[str, object]) -> Annotatable:
        chrom, pos, ref, alt = _text(record, self.vcf_like_col).split(":")
        return VCFAllele(chrom, int(pos), ref, alt)


class RecordToCNVAllele(RecordToAnnotable):
    """Transform a columns record into a CNV allele annotatable."""

    def __init__(self, columns: tuple, ref_genome: ReferenceGenome | None):
        super().__init__(columns, ref_genome)
        self.chrom_col, self.pos_beg_col, self.pos_end_col, self.cnv_type_col \
            = columns

    def _build(self, record: Mapping[str, object]) -> Annotatable:
        variant = _text(record, self.cnv_type_col)
        cnv_type = cnv_variant_type(variant)
        if cnv_type is None:
            raise MalformedRecordError(
                f"unexpected CNV variant type: {variant}")
        return CNVAllele(
            _text(record, self.chrom_col),
            _position(record, self.pos_beg_col),
            _position(record, self.pos_end_col),
            CNVAllele.Type.from_string(cnv_type))


class CSHLAlleleRecordToAnnotatable(RecordToAnnotable):
    """Transform a CSHL variant record into a VCF allele annotatable."""

    def __init__(self, columns: tuple, ref_genome: ReferenceGenome | None):
        super().__init__(columns, ref_genome)
        self.location_col, self.variant_col = columns

    def _build(self, record: Mapping[str, object]) -> Annotatable:
        location = _text(record, self.location_col)
        variant = _text(record, self.variant_col)
        cnv_type = cnv_variant_type(variant)
        if cnv_type is not None:
            chrom, pos_begin, pos_end, cnv_type = cshl2cnv_variant(
                location, variant)

            assert cnv_type is not None
            return CNVAllele(
                chrom, pos_begin, pos_end,
                CNVAllele.Type.from_string(cnv_type))

        return VCFAllele(*cshl2vcf_variant(
            location, variant, self.ref_genome))


class DaeAlleleRecordToAnnotatable(RecordToAnnotable):
    """Transform a CSHL variant record into a VCF allele annotatable."""

    def __init__(self, columns: tuple, ref_genome: ReferenceGenome | None):
        super().__init__(columns, ref_genome)
        self.chrom_column, self.pos_column, self.variant_column = columns

    def _build(self, record: Mapping[str, object]) -> Annotatable:
        variant = _text(record, self.variant_column)
        chrom = _text(record, self.chrom_column)
        return VCFAllele(chrom, *dae2vcf_variant(
            chrom,
            _position(record, self.pos_column),
            variant,
            self.ref_genome))


RECORD_TO_ANNOTATABLE_CONFIGURATION: dict[tuple, type[RecordToAnnotable]] = {
    ("chrom", "pos_beg", "pos_end", "cnv_type"): RecordToCNVAllele,
    ("chrom", "pos_beg", "pos_end"): RecordToRegion,
    ("chrom", "pos", "ref", "alt"): RecordToVcfAllele,
    ("vcf_like",): VcfLikeRecordToVcfAllele,
    ("chrom", "pos", "variant"): DaeAlleleRecordToAnnotatable,
    ("location", "variant"): CSHLAlleleRecordToAnnotatable,
    ("chrom", "pos"): RecordToPosition,
}


def add_record_to_annotable_arguments(parser: argparse.ArgumentParser) -> None:
    all_columns = {
        col for cols in RECORD_TO_ANNOTATABLE_CONFIGURATION
        for col in cols}
    for col in all_columns:
        parser.add_argument(
            f"--col-{col.replace('_', '-')}",
            default=col,
            help=(
                f"The column name that stores {col}. "
                f'Use "-" to exclude this column requirement, causing '
                f'annotatable patterns that require {col} to be skipped.'
            ),
        )


def build_record_to_annotatable(
        renamed_columns: dict[str, str],
        available_columns: set[str],
        ref_genome: ReferenceGenome | None = None) -> RecordToAnnotable:
    """
    Transform a variant record into an annotatable.

    Parameters
    ----------
    renamed_columns : dict[str, str]
        Mapping from expected internal column identifiers (e.g. "col_<field>")
        to the actual column names present in the input source.
        A column can be excluded from usage if an identifier is mapped to "-".
        Example rename::

            "col_<field>": "<input source column name for the field>"

        Example exclude::

            "col_<field>": "-"
    available_columns : set[str]
        The set of column names available in the input records.
    ref_genome : ReferenceGenome | None, optional
        Optional reference genome context used for creating annotatables.
        Not all annotatables require it.
    """
    for annotatable_columns, record_to_annotatable_class in \
            RECORD_TO_ANNOTATABLE_CONFIGURATION.items():
        columns = [
            renamed_columns.get(f"col_{annot_col}", annot_col)
            for annot_col in annotatable_columns
        ]
        if set(columns).issubset(available_columns):
            logger.info(
                "record to annotatable using %s(%s, ref_genome=%s)",
                record_to_annotatable_class.__name__,
                tuple(columns),
                ref_genome.resource_id if ref_genome else None,
            )
            return record_to_annotatable_class(
                tuple(columns), ref_genome,
            )
    raise MalformedRecordError("no record to annotatable could be found.")


def build_annotatable_from_dict(
    obj: Mapping[str, object],
    ref_genome: ReferenceGenome | None = None,
) -> Annotatable:
    """Build an annotatable from a record held in a dictionary.

    Raises :class:`MalformedRecordError` when no converter matches the
    record's keys or the matching converter refuses the record.
    """
    record_to_annotatable = build_record_to_annotatable(
        {}, set(obj.keys()), ref_genome)

    return record_to_annotatable.build(obj)
