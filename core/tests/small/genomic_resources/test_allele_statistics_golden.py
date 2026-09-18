# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Golden test locking the allele statistics file and page byte-for-byte.

The indel groups of an allele score are stored as an exact ``{length:
count}`` map plus four scalars (gain#1118), and gain#1542 lifts that record
out of the indel module so segments and fragments can share it.  That move
is a pure refactor, and this file is what makes "pure" checkable: the
``alleles.json`` a statistics build writes and the ``index.html`` the info
build renders from it are pinned against checked-in expected files that
were generated BEFORE the move.  A rename that leaked into the file, a
map written unsorted, a row cell that changed how a length is spelled --
each shows up here as a diff.

Two allele scores, because the page has two answers per indel group:

* ``mixed`` carries insertions and deletions, one deletion longer than the
  length-map clamp, so the overflow bucket, the ``>=`` median floor and the
  unclamped ``max`` all reach the file and the page.  Tabix-backed, so it
  takes the vectorized scan.
* ``substitutions`` carries no indel at all: both groups are SCANNED and
  EMPTY, which the page renders as a count of ``0`` beside empty length
  cells -- distinct from the "not computed" a pre-map file gets.  Plain
  text, so it takes the per-record scan.

The indel PNGs are deliberately not pinned here.  Their bytes depend on the
matplotlib and freetype the run happens to have, so a checked-in image
would fail CI for reasons that have nothing to do with the statistics.
The chart's BINS are pinned by the ladder tests beside this file.

Regenerate the expected files with::

    GAIN_UPDATE_GOLDEN=1 pytest tests/small/genomic_resources/\
test_allele_statistics_golden.py

The test fails when it regenerates, so a regeneration can never be mistaken
for a passing run.  Inspect the diff before committing it.
"""
import os
import pathlib

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.testing.builders import (
    GRRBuilder,
    a_grr,
    an_allele_score,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
ALLELES_GOLDEN = FIXTURES / "allele_statistics_alleles_golden.json"
PAGE_GOLDEN = FIXTURES / "allele_statistics_page_golden.html"
UPDATE_ENV = "GAIN_UPDATE_GOLDEN"

#: Longer than the length-map clamp (8192), so the deletions that remove
#: them land in the overflow bucket while ``max`` and ``sum`` keep their
#: true lengths.  Two of them, of DIFFERENT lengths: a clamp that leaked
#: into ``max`` would still read 8192 for both, and a map that kept them
#: apart would carry two overflow keys instead of one.
_LONG_REFERENCE = "A" * 8201
_LONGER_REFERENCE = "A" * 8202

# Three substitutions, two insertions of different lengths, four
# deletions of which two are past the clamp.  The insertions' lengths
# {1, 2} put their median at 1.5, exact.  The deletions' {1, 3, 8200,
# 8201} put the middle pair at {3, 8192}: one side exact, the other in
# the overflow bucket -- so the page prints the median as the floor
# ">=4097.5", the straddling case the record's docstring singles out.
MIXED_DATA = f"""
    chrom  pos_begin  reference            alternative  score
    chr1   10         A                    G            0.1
    chr1   10         A                    C            0.2
    chr1   20         A                    AT           0.3
    chr1   30         A                    AGG          0.4
    chr1   40         CT                   C            0.5
    chr1   50         CTTT                 C            0.6
    chr1   60         {_LONG_REFERENCE}    A            0.7
    chr1   70         {_LONGER_REFERENCE}  A            0.8
    chr2   10         G                    T            0.9
"""

SUBSTITUTIONS_DATA = """
    chrom  pos_begin  reference  alternative  score
    chr1   10         A          G            0.1
    chr1   20         C          T            0.2
"""


def _golden_grr_builder() -> GRRBuilder:
    return (
        a_grr()
        .with_resource(
            "mixed",
            an_allele_score()
            .with_score("score", "float")
            .with_data(MIXED_DATA)
            .with_tabix(),
        )
        .with_resource(
            "substitutions",
            an_allele_score()
            .with_score("score", "float")
            .with_data(SUBSTITUTIONS_DATA),
        )
    )


@pytest.fixture
def built_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """Build the GRR, its statistics and its pages; return the repo root."""
    _golden_grr_builder().build_repo(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    cli_manage(["repo-info", "-R", str(tmp_path), "-j", "1"])
    return tmp_path


def _collect(repo_root: pathlib.Path, relative: str) -> str:
    """Concatenate one file from every resource, keyed by resource id.

    Sorted by resource id so the blob is stable across runs; the repo's
    temporary root is scrubbed out so a page that happened to embed it
    would not differ from run to run.
    """
    parts: list[str] = []
    for resource_dir in sorted(p for p in repo_root.iterdir() if p.is_dir()):
        path = resource_dir / relative
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        parts.append(
            f"# {resource_dir.name}/{relative}\n"
            + text.replace(str(repo_root), "<repo>"))
    # Not a bare ``assert`` -- that is stripped under ``python -O``, which
    # would let a build that wrote nothing pass this test vacuously.
    if len(parts) != 2:
        pytest.fail(
            f"expected {relative} under both resources, found {len(parts)}")
    return "\n\n".join(parts) + "\n"


def _assert_golden(golden_path: pathlib.Path, actual: str) -> None:
    if os.environ.get(UPDATE_ENV):
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(actual, encoding="utf-8")
        pytest.fail(
            f"golden file regenerated at {golden_path}; review the diff, "
            f"then re-run without {UPDATE_ENV}")

    assert golden_path.exists(), (
        f"missing golden file {golden_path}; regenerate it with "
        f"{UPDATE_ENV}=1")
    expected = golden_path.read_text(encoding="utf-8")
    if actual != expected:
        pytest.fail(
            f"allele statistics output changed.\n"
            f"--- expected ({golden_path})\n{expected}\n"
            f"--- actual\n{actual}")


def test_alleles_json_golden(built_repo: pathlib.Path) -> None:
    _assert_golden(
        ALLELES_GOLDEN, _collect(built_repo, "statistics/alleles.json"))


def test_allele_page_golden(built_repo: pathlib.Path) -> None:
    _assert_golden(PAGE_GOLDEN, _collect(built_repo, "index.html"))


def test_an_indel_image_is_written_exactly_when_the_group_has_alleles(
    built_repo: pathlib.Path,
) -> None:
    """The PNG bytes are not pinned (see the module docstring), but WHICH
    images exist is machine-independent and is the half of the build the
    page golden cannot see: the build's gate and the template's gate are
    separate code, and a build that wrote an image for an empty group --
    or skipped one for a populated group -- would leave a file nothing
    links or a thumbnail linking nothing."""
    images = [
        "statistics/allele_insertion_lengths.png",
        "statistics/allele_deletion_lengths.png",
    ]

    assert [(built_repo / "mixed" / image).exists() for image in images] \
        == [True, True]
    assert [(built_repo / "substitutions" / image).exists()
            for image in images] == [False, False]
