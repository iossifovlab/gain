# pylint: disable=C0114,C0116,W0212
from gain.genomic_resources.gene_models.to_gpf_gene_models_format import (
    _build_argument_parser,
)


def test_parser_reads_positionals_and_options() -> None:
    parser = _build_argument_parser()

    args = parser.parse_args([
        "in.txt", "out.txt",
        "--gm-format", "refseq",
        "--gm-names", "names.txt",
        "--chrom-mapping", "chroms.txt",
    ])

    assert args.input_gene_models == "in.txt"
    assert args.output_gene_models == "out.txt"
    assert args.gm_format == "refseq"
    assert args.gm_names == "names.txt"
    assert args.chrom_mapping == "chroms.txt"


def test_parser_option_defaults_are_none() -> None:
    parser = _build_argument_parser()

    args = parser.parse_args(["in.txt", "out.txt"])

    assert args.gm_format is None
    assert args.gm_names is None
    assert args.chrom_mapping is None
