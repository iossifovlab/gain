
import argparse
import sys
import itertools
from pysam import VariantFile

def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct and configure argument parser."""
    parser = argparse.ArgumentParser(
        description="",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "input",
        help="the input vcf file")
    parser.add_argument(
        "--limit", default=None, type=int, nargs="?",
        help="variants limit")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Validate VCF file."""
    if not argv:
        argv = sys.argv[1:]
    arg_parser = _build_argument_parser()
    args = vars(arg_parser.parse_args(argv))

    vcf_file = VariantFile(args["input"])
    variants_limit = args["limit"]

    header = vcf_file.header.copy()
    variants = itertools.islice(
        vcf_file.fetch(),
        None,
        variants_limit + 1 if variants_limit else None,
    )

    for i, variant in enumerate(variants):
        if variants_limit and i >= variants_limit:
            print("exceeded", file=sys.stdout)
            return 0
        variant.translate(header)

    print("valid", file=sys.stdout)
    return 0


if __name__ == '__main__':
    main()
