# pylint: disable=W0621,C0114,C0116,W0212
"""The module-level parser builders of the effect annotation tools (gain#1867).

The ``argparse`` directive of ``sphinx-argparse`` renders the option
reference of each tool page from these functions, so each one must build
the full parser of its tool without running the tool.
"""
from gain.effect_annotation import cli


def test_columns_parser_reads_a_typical_command_line() -> None:
    parser = cli._build_columns_argument_parser()

    args = parser.parse_args([
        "-gmri", "hg38/gene_models/MANE", "-rgri", "hg38/genomes/GRCh38",
        "-pl", "100", "--input-sep", ",", "in.tsv", "out.tsv",
    ])

    assert (
        args.gene_models_resource_id, args.reference_genome_resource_id,
        args.promoter_len, args.input_sep,
        args.input_filename, args.output_filename,
    ) == (
        "hg38/gene_models/MANE", "hg38/genomes/GRCh38",
        100, ",", "in.tsv", "out.tsv",
    )
    assert args.annotation_attributes == \
        "worst_effect,gene_effects,effect_details"


def test_vcf_parser_reads_a_typical_command_line() -> None:
    parser = cli._build_vcf_argument_parser()

    args = parser.parse_args([
        "-gmfn", "genes.gtf", "-gmff", "gtf", "-rgfn", "genome.fa",
        "in.vcf.gz", "out.vcf",
    ])

    assert (
        args.gene_models_filename, args.gene_models_fileformat,
        args.reference_genome_filename,
        args.input_filename, args.output_filename,
    ) == ("genes.gtf", "gtf", "genome.fa", "in.vcf.gz", "out.vcf")
    assert args.annotation_attributes == \
        "WE:worst_effect,GE:gene_effects,ED:effect_details"
