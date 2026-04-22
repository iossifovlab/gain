import gzip
from pathlib import Path

from gain.annotation.annotate_columns import cli
from gain.genomic_resources.genomic_context import (
    clear_registered_contexts,
)


def test_normal_run(tmp_path: Path) -> None:
    test_dir = Path(__file__).parent.resolve()
    expected_file = test_dir / "expected.tsv"
    expected = expected_file.read_text()
    out_path = tmp_path / "vep_full_output"
    output_file = out_path / "out.tsv.gz"
    clear_registered_contexts()

    cli([
        str(test_dir / "variants.tsv.gz"),
        str(test_dir / "annotation.yaml"),
        "-w", str(out_path),
        "-o", str(output_file),
        "-v",
        "-j", "1",
        "--batch-size", "50",
        "--col-chrom", "CHROM",
        "--col-pos", "POS",
        "--col-ref", "REF",
        "--col-alt", "ALT",
        "--allow-repeated-attributes",
    ])

    with gzip.open(output_file) as output:
        content = output.read().decode()
    assert content == expected
