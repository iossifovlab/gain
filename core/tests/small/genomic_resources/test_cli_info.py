# pylint: disable=W0621,C0114,C0116,W0212,W0613
import os
import pathlib
import textwrap

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.fsspec_protocol import FsspecReadWriteProtocol
from gain.genomic_resources.repository_ids import GR_CONF_FILE_NAME
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    setup_directories,
    setup_tabix,
)


@pytest.fixture
def proto_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[pathlib.Path, FsspecReadWriteProtocol]:
    path = tmp_path_factory.mktemp("cli_info_repo_fixture")
    setup_directories(
        path,
        {
            "one": {
                GR_CONF_FILE_NAME: textwrap.dedent("""
                    type: position_score
                    table:
                        filename: data.txt.gz
                        format: tabix
                    scores:
                        - id: phastCons100way
                          type: float
                          name: s1
                          histogram:
                            type: number
                            number_of_bins: 100
                    """),
            },
            "two": {
                GR_CONF_FILE_NAME: textwrap.dedent("""
                    type: allele_score
                    table:
                        filename: data.txt.gz
                        format: tabix
                        reference:
                            name: REF
                        alternative:
                            name: ALT
                    scores:
                        - id: AC
                          type: int
                          name: AC
                    """),
            },
        })
    setup_tabix(
        path / "one" / "data.txt.gz",
        """
        #chrom  pos_begin  pos_end  s1    s2
        1       10         15       0.02  1.02
        1       17         19       0.03  1.03
        1       22         25       0.04  1.04
        2       5          80       0.01  2.01
        2       81         90       0.02  2.02
        """, seq_col=0, start_col=1, end_col=2)
    setup_tabix(
        path / "two" / "data.txt.gz",
        """
        #chrom  pos_begin    chrom  variant    REF  ALT  AC
        1       12198        1      sub(G->C)  G    C    0
        1       12237        1      sub(G->A)  G    A    0
        1       12259        1      sub(G->C)  G    C    0
        1       12266        1      sub(G->A)  G    A    0
        1       12272        1      sub(G->A)  G    A    0
        1       12554        1      sub(A->G)  A    G    0
        """, seq_col=0, start_col=1, end_col=1)
    proto = build_filesystem_test_protocol(path)
    return path, proto


def test_resource_info(
    proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    path, _proto = proto_fixture
    assert not (path / "one/index.html").exists()

    cli_manage([
        "resource-info", "-R", str(path), "-r", "one", "-j", "1",
    ])

    assert (path / "one/index.html").exists()
    assert not (path / "two/index.html").exists()
    # The repository index page is repo-scoped; a resource-scoped
    # command leaves it to `repo-index` (gain#760).
    assert not (path / "index.html").exists()

    cli_manage([
        "resource-info", "-R", str(path), "-r", "two", "-j", "1",
    ])

    assert (path / "one/index.html").exists()
    assert (path / "two/index.html").exists()
    assert not (path / "index.html").exists()

    result = (path / "one/index.html").read_text()

    # `in`, never `str.find()`.  What stood here was five
    # `assert result.find("<h3>Score file:</h3>")` calls: `find` returns an
    # index, and -1 when the string is absent, so every one of them passed
    # whenever its markup was *missing* and would have failed only at index
    # 0.  The page had been redesigned out from under them -- none of the
    # markup they named existed in any template -- and the suite stayed
    # green for months (gain#991).
    #
    # These three are checked against generated output, not recalled from
    # it, and they say what this test is actually about: the page
    # `resource-info` writes describes *this* resource, its declared score,
    # and that score's histogram.  Page structure at large is the business
    # of tests/small/genomic_resources/info_pages/.
    # The Id cell holds the id and, since gain#1477, the icon that copies
    # it -- which names the id again, as data.  Both are checked: the
    # cell for the page naming the resource, the icon for copying *this*
    # one.
    assert "<td>one<span" in result, \
        "the generated page does not name the resource it describes"
    assert 'data-copy-id="one"' in result, \
        "the generated page's copy icon does not name the resource"
    assert "<td>phastCons100way</td>" in result, \
        "the generated page does not name the score the resource declares"
    # The thumbnail's class, not `alt="HISTOGRAM FOR ..."`.  That alt text
    # is also rendered by the modal image in `genomic_score.jinja`, which
    # sits outside the `null_histogram` guard and is emitted for every score
    # definition whether a histogram exists or not -- so asserting on it
    # would have passed for a score that renders "No histogram" and has no
    # image on disk at all.  Only the thumbnail is gated on the histogram
    # actually existing, and the file it points at is checked directly.
    assert 'class="histogram-thumbnail"' in result, \
        "the generated page does not render the declared score's histogram"
    assert (path / "one/statistics/histogram_phastCons100way.png").exists(), \
        "the histogram image the page points at was never written"


def test_repo_info(
    proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    path, _proto = proto_fixture

    assert not (path / "one/index.html").exists()

    cli_manage(["repo-info", "-R", str(path), "-j", "1"])

    assert (path / "one/index.html").exists()
    assert (path / "two/index.html").exists()
    assert (path / "index.html").exists()


def test_repo_info_does_not_rewrite_unchanged_pages(
    proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    path, _proto = proto_fixture

    cli_manage(["repo-info", "-R", str(path), "-j", "1"])

    # the per-resource info pages repo-info generated
    pages = [
        p
        for r in ("one", "two")
        for p in (path / r / "index.html",
                  path / r / "statistics/index.html")
        if p.exists()
    ]
    assert pages, "repo-info generated no per-resource index.html"

    # pin each page to a fixed past mtime
    pinned = 1_000_000_000
    for p in pages:
        os.utime(p, (pinned, pinned))

    # re-running on an unchanged repo must skip identical pages
    cli_manage(["repo-info", "-R", str(path), "-j", "1"])

    rewritten = [
        str(p.relative_to(path))
        for p in pages
        if int(p.stat().st_mtime) != pinned
    ]
    assert rewritten == [], f"unchanged repo rewrote info pages: {rewritten}"


#: The two pages ``repo-info`` renders at the root of the repository.
_ROOT_PAGES = ("index.html", "about.html")

#: A fixed past mtime a test pins a page to, so a rewrite shows up.
_PINNED_MTIME = 1_000_000_000


def _pin_root_pages(path: pathlib.Path) -> None:
    for name in _ROOT_PAGES:
        os.utime(path / name, (_PINNED_MTIME, _PINNED_MTIME))


def _rewritten_root_pages(path: pathlib.Path) -> list[str]:
    return [
        name for name in _ROOT_PAGES
        if int((path / name).stat().st_mtime) != _PINNED_MTIME
    ]


def test_repo_info_does_not_rewrite_unchanged_root_pages(
    proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    path, _proto = proto_fixture
    (path / "about.md").write_text("# About\n\nThe first text.\n")
    cli_manage(["repo-info", "-R", str(path), "-j", "1"])
    _pin_root_pages(path)

    cli_manage(["repo-info", "-R", str(path), "-j", "1"])

    assert _rewritten_root_pages(path) == []


def test_repo_info_rewrites_the_about_page_when_about_md_changes(
    proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    path, _proto = proto_fixture
    (path / "about.md").write_text("# About\n\nThe first text.\n")
    cli_manage(["repo-info", "-R", str(path), "-j", "1"])
    _pin_root_pages(path)
    (path / "about.md").write_text("# About\n\nThe second text.\n")

    cli_manage(["repo-info", "-R", str(path), "-j", "1"])

    assert "about.html" in _rewritten_root_pages(path)
    about_page = (path / "about.html").read_text()
    assert "The second text." in about_page
    assert "The first text." not in about_page


#: The link the index page renders in the row of the resource ``three``.
_THREE_ROW_LINK = '<a href="three/index.html">three</a>'


def test_repo_info_rewrites_the_index_page_when_a_resource_is_added(
    proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    path, _proto = proto_fixture
    (path / "about.md").write_text("# About\n\nThe first text.\n")
    cli_manage(["repo-info", "-R", str(path), "-j", "1"])
    _pin_root_pages(path)
    assert _THREE_ROW_LINK not in (path / "index.html").read_text()
    setup_directories(path / "three", {GR_CONF_FILE_NAME: "type: basic\n"})

    cli_manage(["repo-info", "-R", str(path), "-j", "1"])

    assert "index.html" in _rewritten_root_pages(path)
    assert _THREE_ROW_LINK in (path / "index.html").read_text()
