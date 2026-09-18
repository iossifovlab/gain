# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Golden test locking the allele statistics file and page byte-for-byte.

The indel groups of an allele score are stored as an exact ``{length:
count}`` map plus four scalars, and gain#1542 lifts that record out of the
indel module so segments and fragments can share it.  That move is a pure
refactor, and this file is what makes "pure" checkable: the ``alleles.json``
a statistics build writes and the Alleles section of the ``index.html`` the
info build renders from it are pinned against checked-in expected files
that were generated BEFORE the move.  A rename that leaked into the file, a
map written unsorted, a row cell that changed how a length is spelled --
each shows up here as a diff.

Two allele scores, because the page has two answers per indel group:

* ``mixed`` carries insertions and deletions, two deletions longer than
  the length-map clamp, so the overflow bucket, the ``>=`` median floor
  and the unclamped ``max`` all reach the file and the page.
  Tabix-backed, so it takes the vectorized scan.
* ``substitutions`` carries no indel at all: both groups are SCANNED and
  EMPTY, which the page renders as a count of ``0`` beside empty length
  cells -- distinct from the "not computed" a pre-map file gets.  Plain
  text, so it takes the per-record scan.

Only the Alleles SECTION of the page is pinned, not the whole page.  The
section is exactly what ``alleles.json`` renders into; the rest is shared
CSS and script that every page carries, and a Files table whose bgzip and
tabix md5s are the local htslib's output -- environment-dependent, and
nothing to do with the statistics.  The indel PNGs are not pinned for the
same reason: their bytes depend on the matplotlib and freetype of the run.
Which images EXIST is pinned, because that is machine-independent.

Regenerate the expected files with::

    GAIN_UPDATE_GOLDEN=1 pytest tests/small/genomic_resources/\
test_allele_statistics_golden.py

The test fails when it regenerates, so a regeneration can never be mistaken
for a passing run.  Inspect the diff before committing it.
"""
import pathlib

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.testing.builders import a_grr, an_allele_score

from tests.small.genomic_resources.conftest import assert_golden

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
ALLELES_GOLDEN = FIXTURES / "allele_statistics_alleles_golden.json"
PAGE_GOLDEN = FIXTURES / "allele_statistics_page_golden.html"

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

RESOURCE_IDS = ("mixed", "substitutions")

INDEL_IMAGES = (
    "statistics/allele_insertion_lengths.png",
    "statistics/allele_deletion_lengths.png",
)


@pytest.fixture(scope="module")
def built_repo(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """The GRR, its statistics and its pages, built once for the module.

    Every test here only reads the built tree, so one build serves all.
    """
    root = tmp_path_factory.mktemp("allele_statistics_golden")
    (
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
    ).build_repo(root)
    cli_manage(["repo-stats", "-R", str(root), "-j", "1"])
    cli_manage(["repo-info", "-R", str(root), "-j", "1"])
    return root


def _alleles_section(page: str) -> str:
    """The page from its Alleles heading up to the next top heading."""
    start = page.index("<h2>Alleles</h2>")
    end = page.index("<h2", start + 1)
    return page[start:end]


def _collect(repo_root: pathlib.Path, relative: str, *,
             section: bool = False) -> str:
    """One file from each resource, concatenated under a ``# id`` header."""
    parts = []
    for resource_id in RESOURCE_IDS:
        text = (repo_root / resource_id / relative).read_text(
            encoding="utf-8")
        if section:
            text = _alleles_section(text)
        parts.append(f"# {resource_id}/{relative}\n{text}")
    return "\n\n".join(parts) + "\n"


def test_alleles_json_golden(built_repo: pathlib.Path) -> None:
    assert_golden(
        ALLELES_GOLDEN,
        _collect(built_repo, "statistics/alleles.json"),
        what="alleles.json")


def test_allele_page_section_golden(built_repo: pathlib.Path) -> None:
    assert_golden(
        PAGE_GOLDEN,
        _collect(built_repo, "index.html", section=True),
        what="the page's Alleles section")


def test_an_indel_image_is_written_exactly_when_the_group_has_alleles(
    built_repo: pathlib.Path,
) -> None:
    """The half of the build the page golden cannot see: the build's gate
    and the template's gate are separate code, and a build that wrote an
    image for an empty group -- or skipped one for a populated group --
    would leave a file nothing links or a thumbnail linking nothing."""
    assert [(built_repo / "mixed" / image).exists()
            for image in INDEL_IMAGES] == [True, True]
    assert [(built_repo / "substitutions" / image).exists()
            for image in INDEL_IMAGES] == [False, False]
