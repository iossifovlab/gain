# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Content tie-break for the headerless refseq/ccds collision (gain#869).

refseq and ccds declare identical column layouts, so a headerless file with
that layout matches both and column-count probing cannot choose. The two
parsers disagree about which column carries the gene symbol, so the choice
matters. When the collision is exactly this pair, inference inspects the
transcript-name column of the sampled records: names shaped like RefSeq
accessions settle it for refseq, names shaped like CCDS ids settle it for
ccds, and anything else leaves the ambiguity standing.
"""
from io import StringIO

from gain.genomic_resources.gene_models.parsers import (
    infer_gene_models_format,
)


def headerless_record(name: str, gene: str) -> str:
    """One headerless 16-column refseq/ccds-layout record."""
    return (
        f"0\t{name}\t17\t-\t7571719\t7590868\t7572826\t7590856\t2\t"
        f"7571719,7572926\t7573008,7573009\t0\t{gene}\tcmpl\tcmpl\t0,0\n"
    )


REFSEQ_CONTENT = (
    headerless_record("NM_000546", "TP53")
    + headerless_record("NR_046018", "DDX11L1")
)


def test_all_refseq_names_settle_the_collision_for_refseq() -> None:
    inference = infer_gene_models_format(StringIO(REFSEQ_CONTENT))

    assert inference.file_format == "refseq"
