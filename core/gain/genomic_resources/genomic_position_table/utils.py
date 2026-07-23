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
        if table_definition.get("header_mode") is not None:
            logger.warning(
                "header_mode is not supported for bigwig tables, "
                "ignoring it in %s",
                resource.get_full_id(),
            )
        if "zero_based" in table_definition:
            logger.warning(
                "zero_based is not supported for bigWig tables (the "
                "0-based-half-open to closed-1-based conversion is "
                "intrinsic), ignoring it in %s",
                resource.get_full_id(),
            )
        return BigWigTable(resource, table_definition)

    raise ValueError(f"unknown table format {table_fmt}")
