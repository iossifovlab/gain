# pylint: disable=C0114,C0116
import pathlib
from urllib.parse import urlparse

import pytest
from gain.genomic_resources import testing as gr_testing
from gain.genomic_resources.testing import (
    build_http_test_protocol,
    setup_directories,
)

_RESOURCE = {
    "score_one": {
        "genomic_resource.yaml": (
            "type: position_score\n"
            "table:\n"
            "  filename: data.mem\n"
            "  format: mem\n"
            "scores:\n"
            "- id: score\n"
            "  type: float\n"
        ),
        "data.mem": "chrom\tpos_begin\tpos_end\tscore\nchr1\t1\t1\t0.1\n",
    },
}


def _served_segment(url: str) -> str:
    """The per-invocation directory name Apache is asked to serve."""
    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]


def test_http_grr_dir_env_relocates_the_served_tree(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP_GRR_DIR names the directory the copy for Apache lands in.

    The default derives that directory from the package's own location,
    which is the source tree's core/tests/.test_grr only under an
    editable install. A job that tests an INSTALLED gain (the conda
    integration job, #1571) imports it from site-packages, so it has to
    say where Apache's bind mount actually is.
    """
    served = tmp_path / "served"
    served.mkdir()
    monkeypatch.setenv("HTTP_GRR_DIR", str(served))
    repo = tmp_path / "grr_repo"
    setup_directories(repo, _RESOURCE)

    with build_http_test_protocol(repo, repair=False) as proto:
        children = list(served.iterdir())
        assert len(children) == 1
        assert children[0].name == _served_segment(proto.url)
        assert children[0].name.startswith("grr_repo-")
        assert (children[0] / "score_one" / "genomic_resource.yaml").is_file()

    # Exit removes the per-invocation copy and nothing else.
    assert served.is_dir()
    assert not list(served.iterdir())


def test_http_grr_dir_is_created_when_missing(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    served = tmp_path / "not" / "yet" / "there"
    monkeypatch.setenv("HTTP_GRR_DIR", str(served))
    repo = tmp_path / "grr_repo"
    setup_directories(repo, _RESOURCE)

    with build_http_test_protocol(repo, repair=False) as proto:
        assert (served / _served_segment(proto.url)).is_dir()


def test_default_serving_dir_is_derived_from_the_package_location(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset, the served tree lands where it always has.

    Pinned against the installed package's location rather than the
    source tree on purpose: under an editable install the two coincide
    (core/tests/.test_grr, what docker-compose.yaml mounts into httpd),
    and where they do not, this is exactly the path a job must override.
    """
    monkeypatch.delenv("HTTP_GRR_DIR", raising=False)
    expected = pathlib.Path(gr_testing.__file__).parents[3] \
        / "tests" / ".test_grr"
    repo = tmp_path / "grr_repo"
    setup_directories(repo, _RESOURCE)

    with build_http_test_protocol(repo, repair=False) as proto:
        assert (expected / _served_segment(proto.url)).is_dir()


def test_empty_http_grr_dir_means_unset(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_GRR_DIR", "")
    expected = pathlib.Path(gr_testing.__file__).parents[3] \
        / "tests" / ".test_grr"
    repo = tmp_path / "grr_repo"
    setup_directories(repo, _RESOURCE)

    with build_http_test_protocol(repo, repair=False) as proto:
        assert (expected / _served_segment(proto.url)).is_dir()
