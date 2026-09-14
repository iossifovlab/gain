"""``repo-repair`` on a score whose payload is a ``.dvc`` pointer (gain#1448).

The shape of every DVC-backed GRR's working copy -- ``grr``, ``grr_bench``,
the single-cell demo: the ``.dvc`` sidecars are checked out, the data files
are not ``dvc pull``ed.  The manifest stage manifests a file from its
sidecar whether or not the bytes are on disk, so a repository whose
statistics are current is consistent as it stands, and a repair of it
must not want the bytes for anything (gain#1444: a gate that opened every
score for its chromosome lengths failed each one on the absent payload).
"""

import hashlib
import pathlib
import textwrap

import pytest_mock
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.genomic_scores import GenomicScore
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_grr,
    a_reference_genome,
)


def _a_repaired_bigwig_score_left_as_a_pointer(
    tmp_path: pathlib.Path,
) -> pathlib.Path:
    """A repository with one DVC-tracked bigWig score -- payload and
    sidecar both on disk, as after ``dvc pull`` -- repaired, and then
    left with the sidecar alone: an unpulled clone of itself.

    Labelled with its genome, as the DVC-backed repositories' scores
    are: the page's coverage denominator then comes from the genome,
    and nothing of the repair has a reason to look at the payload.  An
    UNLABELLED bigWig is a different case -- the page render takes its
    denominator from the header, which needs the bytes -- and is not
    what these tests claim anything about.
    """
    (
        a_grr()
        .with_resource(
            "genome",
            a_reference_genome().with_chromosome("chr1", "A" * 1000))
        .with_resource(
            "score", a_bigwig_score().with_labels(reference_genome="genome"))
        .build_repo(tmp_path)
    )
    payload = tmp_path / "score" / "data.bw"
    content = payload.read_bytes()
    md5 = hashlib.md5(  # ruff: ignore[hashlib-insecure-hash-function]
        content).hexdigest()
    payload.with_suffix(".bw.dvc").write_text(textwrap.dedent(f"""
        outs:
        - md5: {md5}
          size: {len(content)}
          path: {payload.name}
    """))
    cli_manage(["repo-repair", "-R", str(tmp_path), "-j", "1"])
    payload.unlink()
    return tmp_path


def _every_file_of(root: pathlib.Path) -> dict[pathlib.Path, bytes]:
    return {
        path: path.read_bytes()
        for path in sorted(root.rglob("*")) if path.is_file()}


def test_a_statistics_build_persists_nothing_about_chromosome_lengths(
    tmp_path: pathlib.Path,
) -> None:
    """The whole of what a repair writes there: the histograms, the
    coverage counts, the hash and the statistics page.  The lengths are
    computed where they are asked, and no repair has a file of them to
    find missing."""
    root = _a_repaired_bigwig_score_left_as_a_pointer(tmp_path)

    assert sorted(
        path.name for path in (root / "score" / "statistics").iterdir()
    ) == [
        "coverage.json",
        "coverage_segment_lengths.png",
        "histogram_score.json",
        "histogram_score.png",
        "index.html",
        "stats_hash",
    ]


def test_repair_of_a_current_labelled_pointer_only_score_touches_nothing(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    root = _a_repaired_bigwig_score_left_as_a_pointer(tmp_path)
    before = _every_file_of(root)
    opened = mocker.spy(GenomicScore, "open")

    # Exit 0 and no failed resource: the command returns rather than
    # exiting, which it does for any failure.
    cli_manage(["repo-repair", "-R", str(root), "-j", "1"])

    opened.assert_not_called()
    assert _every_file_of(root) == before


def test_a_dry_run_of_a_current_labelled_pointer_only_score_needs_no_update(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    root = _a_repaired_bigwig_score_left_as_a_pointer(tmp_path)
    opened = mocker.spy(GenomicScore, "open")

    # The exit status of a dry run is the count of resources needing an
    # update (gain#364); a plain return is zero.
    cli_manage(["repo-repair", "-R", str(root), "-j", "1", "--dry-run"])

    opened.assert_not_called()
