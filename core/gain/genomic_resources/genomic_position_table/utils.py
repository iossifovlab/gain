from gain import logging
from gain.genomic_resources.repository import GenomicResource

from .table import GenomicPositionTable
from .table_bigwig import BigWigTable
from .table_inmemory import InmemoryGenomicPositionTable
from .table_tabix import TabixGenomicPositionTable
from .table_vcf import VCFGenomicPositionTable

logger = logging.getLogger(__name__)


def build_genomic_position_table(
    resource: GenomicResource, table_definition: dict,
) -> GenomicPositionTable:
    """Instantiate a genome position table from a genomic resource."""
    filename = table_definition["filename"]

    if filename.endswith(".bgz"):
        default_format = "tabix"
    elif filename.endswith(".vcf.gz"):
        default_format = "vcf_info"
    elif filename.endswith((".txt", ".txt.gz", ".tsv", ".tsv.gz")):
        default_format = "tsv"
    elif filename.endswith((".csv", ".csv.gz")):
        default_format = "csv"
    elif filename.endswith(".bw"):
        default_format = "bw"
    else:
        default_format = "mem"

    table_fmt = table_definition.get("format", default_format)

    # Only the in-memory (mem/csv/tsv) and tabix backends honour zero_based;
    # both default a missing key to False (1-based).  That default is a silent
    # off-by-one for a 0-based/BED-derived table whose config omits the flag --
    # every position reads one base over, with no crash.  Warn, naming the
    # resource, when the key is absent, so the omission is no longer silent;
    # stating the flag (either value) silences it.  The default is deliberately
    # NOT flipped -- that would shift every currently-correct 1-based table
    # (gain#379).  VCF/bigwig ignore zero_based entirely and are not warned.
    if (table_fmt in ("mem", "csv", "tsv", "tabix")
            and "zero_based" not in table_definition):
        logger.warning(
            "the table of resource <%s> omits 'zero_based'; assuming 1-based "
            "(False). If this table is 0-based/BED-derived set "
            "'zero_based: true'; otherwise set 'zero_based: false' to confirm "
            "1-based and silence this warning",
            resource.get_full_id(),
        )

    if table_fmt in ("mem", "csv", "tsv"):
        return InmemoryGenomicPositionTable(resource, table_definition,
                                            table_fmt)
    if table_fmt == "tabix":
        return TabixGenomicPositionTable(resource, table_definition)
    if table_fmt == "vcf_info":
        if "zero_based" in table_definition:
            logger.warning(
                "zero_based is not supported for VCF tables (a VCF is "
                "always 1-based), ignoring it in %s",
                resource.get_full_id(),
            )
        return VCFGenomicPositionTable(resource, table_definition)
    if table_fmt.lower() in ("bw", "bigwig"):
        _warn_inert_bigwig_keys(resource, table_definition)
        return BigWigTable(resource, table_definition)

    raise ValueError(f"unknown table format {table_fmt}")


def _warn_inert_bigwig_keys(
    resource: GenomicResource, table_definition: dict,
) -> None:
    """Report table keys a bigWig reads nothing from, and ignore them.

    **Two levels, and the split is by how often the key actually appears.**
    A message that fires for nearly every resource is not a warning, it is
    noise with a severity label on it, and it trains a reader to ignore the
    level.  Measured across the 150 deployed bigWig resources:

    * ``chrom``/``pos_begin``/``pos_end`` -- 142 of 150.  Endemic
      boilerplate, copied from resource to resource.  INFO.
    * the retired ``buffer_fetch_size``/``use_buffered_threshold`` -- 0 of
      150.  INFO as well: they name a feature that no longer exists, so
      their presence is a historical artifact rather than a misreading of
      the format, and at zero deployments the level is moot anyway.
    * ``header_mode`` and ``zero_based`` -- 0 of 150, and both are
      meaningful keys on OTHER backends.  WARNING.  Setting either on a
      bigWig says the author thinks this file has a header, or that its
      coordinate convention is theirs to choose; neither is true, and
      because nobody sets them today such a message would fire only for a
      genuinely unusual config.  Rare is what makes it signal.

    Nothing here refuses: every one of these keys is inert, and refusing
    would take a working resource offline to report yaml that changes no
    value.  Contrast the SCORE config, where a column address really would
    name a column that is not the value --
    ``bigwig_scores.validate_bigwig_scoredefs`` refuses that one.
    """
    if table_definition.get("header_mode") is not None:
        logger.warning(
            "header_mode is not supported for bigwig tables, "
            "ignoring it in %s",
            resource.get_full_id(),
        )
    # The tabular table keys a bigWig has no use for.  A bigWig is a
    # binary format with a fixed layout: it has no header to name columns
    # in and no columns to address, and a record's positional fields are
    # decoded by the backend itself.  ``BigWigTable.open`` does call
    # ``_set_core_column_keys()``, so these keys DO set
    # ``chrom_key``/``pos_begin_key``/``pos_end_key`` -- and nothing in
    # ``table_bigwig`` reads any of the three.  So they are inert, not
    # wrong: they corrupt no value, and this is the MAJORITY shape in the
    # deployed GRRs -- 142 of the 150 bigWig resources carry it (every
    # FitCons2 per-cell-type track, plus Linsight).  That majority is why
    # this reports at INFO rather than WARNING; see the docstring.
    for inert_key in ("chrom", "pos_begin", "pos_end", "header"):
        if inert_key in table_definition:
            logger.info(
                "'%s' is not supported for bigWig tables (a bigWig has "
                "no columns and no header; its positions are decoded by "
                "the backend), ignoring it in %s",
                inert_key, resource.get_full_id(),
            )
    # The retired buffered-fetch strategy's two knobs.  They configured a
    # second fetch path that no longer exists (gain#449 tracks bringing it
    # back for high-latency repositories), so there is nothing to rename
    # them to -- ignore them rather than refuse the resource.  The surviving
    # knob was renamed ``direct_fetch_size`` -> ``fetch_size`` and is NOT
    # aliased: see the schema in ``genomic_scores`` for why those two cases
    # are handled differently.
    for retired_key in ("buffer_fetch_size", "use_buffered_threshold"):
        if retired_key in table_definition:
            logger.info(
                "'%s' no longer does anything: bigWig fetch buffering was "
                "removed, leaving a single chunked fetch strategy sized by "
                "'fetch_size'. Delete the key from %s",
                retired_key, resource.get_full_id(),
            )
    if "zero_based" in table_definition:
        logger.warning(
            "zero_based is not supported for bigWig tables (the "
            "0-based-half-open to closed-1-based conversion is "
            "intrinsic), ignoring it in %s",
            resource.get_full_id(),
        )
