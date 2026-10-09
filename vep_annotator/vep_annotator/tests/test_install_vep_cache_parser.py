# pylint: disable=C0114,C0116,W0212
from vep_annotator.install_vep_cache import _build_argument_parser


def test_parser_defaults() -> None:
    parser = _build_argument_parser()

    args = parser.parse_args(["/cache"])

    assert args.cache_dir == "/cache"
    assert args.force is False
    assert args.cont is False


def test_parser_long_flags() -> None:
    parser = _build_argument_parser()

    args = parser.parse_args(["/cache", "--force", "--continue"])

    assert args.force is True
    assert args.cont is True


def test_parser_short_flags() -> None:
    parser = _build_argument_parser()

    args = parser.parse_args(["-f", "-c", "/cache"])

    assert args.force is True
    assert args.cont is True
