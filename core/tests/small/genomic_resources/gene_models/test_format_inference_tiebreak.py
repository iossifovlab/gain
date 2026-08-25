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
import pathlib
from io import StringIO

from gain.genomic_resources.gene_models.gene_models_factory import (
    build_gene_models_from_file,
)
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


def test_headerless_refseq_file_loads_without_an_explicit_format(
    tmp_path: pathlib.Path,
) -> None:
    """The winning parser is the refseq one, not just the refseq label.

    The parsers disagree about the gene-symbol column, so the proof that
    the right one won is the loaded gene symbol.
    """
    path = tmp_path / "refGene.txt"
    path.write_text(REFSEQ_CONTENT)
    gene_models = build_gene_models_from_file(str(path))

    gene_models.load()

    assert set(gene_models.gene_names()) == {"TP53", "DDX11L1"}


def test_all_ccds_names_settle_the_collision_for_ccds() -> None:
    content = (
        headerless_record("CCDS11118.1", "")
        + headerless_record("CCDS30547.1", "")
    )

    inference = infer_gene_models_format(StringIO(content))

    assert inference.file_format == "ccds"


def test_versioned_refseq_accessions_count_as_refseq_names() -> None:
    content = (
        headerless_record("NM_000546.6", "TP53")
        + headerless_record("XR_007065454.1", "LOC124903914")
    )

    inference = infer_gene_models_format(StringIO(content))

    assert inference.file_format == "refseq"


def test_mixed_transcript_names_leave_the_collision_standing() -> None:
    content = (
        headerless_record("NM_000546", "TP53")
        + headerless_record("CCDS11118.1", "")
    )

    inference = infer_gene_models_format(StringIO(content))

    assert inference.file_format is None
    assert set(inference.matched) == {"ccds", "refseq"}


def test_tie_break_report_names_the_winner_and_the_evidence() -> None:
    """The reader sees both the collision and why it was resolved."""
    inference = infer_gene_models_format(StringIO(REFSEQ_CONTENT))

    report = inference.report()

    assert "the format is refseq" in report
    assert "every sampled transcript name is a RefSeq accession" in report


def test_unrecognized_transcript_names_leave_the_collision_standing() -> None:
    content = (
        headerless_record("ENST00000269305", "TP53")
        + headerless_record("ENST00000456328", "DDX11L1")
    )

    inference = infer_gene_models_format(StringIO(content))

    assert inference.file_format is None
    assert set(inference.matched) == {"ccds", "refseq"}
