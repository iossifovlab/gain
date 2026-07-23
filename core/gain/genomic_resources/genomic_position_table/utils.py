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

    if table_fmt in ("mem", "csv", "tsv"):
        return InmemoryGenomicPositionTable(resource, table_definition,
                                            table_fmt)
    if table_fmt == "tabix":
        return TabixGenomicPositionTable(resource, table_definition)
    if table_fmt == "vcf_info":
        if "zero_based" in table_definition:
            logger.warning(
                "zero_based is not honored for vcf_info tables (a VCF is "
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
                "zero_based is not honored for bigwig tables (the "
                "0-based-half-open to closed-1-based conversion is "
                "intrinsic), ignoring it in %s",
                resource.get_full_id(),
            )
        return BigWigTable(resource, table_definition)

    raise ValueError(f"unknown table format {table_fmt}")
