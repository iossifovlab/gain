# pylint: disable=C0114,C0116
import pathlib

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


def test_concurrent_same_name_repos_are_isolated(
    tmp_path: pathlib.Path,
) -> None:
    """Two protocols built from same-named repos must not share state.

    The python-matrix runs the three core cells (py3.12/3.13/3.14) in
    parallel against a single host-mounted core/tests/.test_grr. Each cell
    runs the same tests, so the repo directory names collide. If
    build_http_test_protocol keyed its serving directory on root_path.name
    alone, one cell's rmtree would delete the directory another cell is
    still serving -> FileNotFoundError (gain-python-matrix #30). Opening
    two same-named contexts at once and exiting them in turn reproduces
    that race deterministically.
    """
    repo_a = tmp_path / "a" / "grr_repo"
    repo_b = tmp_path / "b" / "grr_repo"
    setup_directories(repo_a, _RESOURCE)
    setup_directories(repo_b, _RESOURCE)

    with build_http_test_protocol(repo_a, repair=False) as proto_a, \
            build_http_test_protocol(repo_b, repair=False) as proto_b:
        # Same .name must still yield distinct serving locations.
        assert proto_a.url != proto_b.url
    # Reaching here means proto_b's exit (rmtree) did not delete proto_a's
    # directory out from under proto_a's own later rmtree.
