# pylint: disable=C0114,C0116,W0212,W0621
import json
import pathlib
from collections.abc import Callable
from typing import Any

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.histogram import NumberHistogramConfig
from gain.genomic_resources.implementations.genomic_scores_impl import (
    build_score_implementation_from_resource,
    scan,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.statistics.alleles import (
    ALLELE_COMPLEX_GRID_IMAGE_FILE,
    ALLELE_DELETION_LENGTHS_IMAGE_FILE,
    ALLELE_INSERTION_LENGTHS_IMAGE_FILE,
    ALLELE_STATISTICS_FILE,
    COMPLEX_GRID_TABLE_MAX_CELLS,
    AlleleStatistics,
    serves_allele_arrays,
)
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_position_score,
    an_allele_score,
)

from tests.small.genomic_resources.info_page_html import (
    Cell,
    Table,
    section_after,
    table_after,
)

# One fixture carries every class and both counting rules: three rows at
# chr1:10 (two of them the SAME (chrom, pos, ref, alt) -- legitimate
# per-transcript data), then one row per remaining class, a soft-masked
# and an identity substitution (the matrix's two edge rows), then a
# second contig.
_MIXED_TABLE = """
    chrom  pos_begin  reference  alternative  score
    chr1   10         A          G            0.1
    chr1   10         A          C            0.2
    chr1   10         A          G            0.3
    chr1   20         A          AT           0.4
    chr1   30         CT         C            0.5
    chr1   40         AC         GT           0.6
    chr1   50         N          A            0.7
    chr1   60         a          g            0.8
    chr1   70         T          T            0.9
    chr2   10         G          T            0.8
"""


def _maybe_tabix(builder: Any, *, tabix: bool) -> Any:
    """Put a fixture on the bulk scan path, or leave it on the per-record one.

    A tabix-indexed table serves column arrays and is bulk-eligible; a plain
    text one is not, and ``bulk_scan_eligible`` asks exactly that.  The
    contrast used to be drawn with a ``np_score`` resource, excluded from
    the bulk gate by type until gain#920 removed the type.
    """
    return builder.with_tabix() if tabix else builder


def _hist_conf() -> NumberHistogramConfig:
    return NumberHistogramConfig.from_dict({
        "type": "number",
        "view_range": {"min": 0, "max": 1},
        "number_of_bins": 10,
        "x_log_scale": False,
        "y_log_scale": False,
    })


def _info_page(resource: GenomicResource) -> str:
    """The page a repository renders for ``resource``.

    Through the dispatch rather than a chosen class: which implementation
    an allele score gets is what decides whether its page carries an
    Alleles section at all (gain#1105).
    """
    return build_score_implementation_from_resource(resource).get_info()


def _mixed_allele_score(tmp_path: pathlib.Path) -> GenomicResource:
    return (
        an_allele_score()
        .with_score("score", "float")
        .with_data(_MIXED_TABLE)
        .with_tabix()
        .build_resource(tmp_path)
    )


def _built_statistics(
    tmp_path: pathlib.Path,
    resource: GenomicResource,
) -> AlleleStatistics:
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    return AlleleStatistics.deserialize(
        resource.get_file_content(ALLELE_STATISTICS_FILE))


def test_build_counts_alleles_per_chromosome(
    tmp_path: pathlib.Path,
) -> None:
    resource = _mixed_allele_score(tmp_path)

    stats = _built_statistics(tmp_path, resource)

    assert {
        chrom: counts.allele_count
        for chrom, counts in stats.by_chromosome().items()
    } == {"chr1": 9, "chr2": 1}


def test_build_totals_every_allele_class_globally(
    tmp_path: pathlib.Path,
) -> None:
    # A>G twice, A>C, soft-masked a>g and identity T>T on chr1, G>T on
    # chr2; A>AT anchored insertion, CT>C anchored deletion, AC>GT
    # complex, N>A other.
    resource = _mixed_allele_score(tmp_path)

    stats = _built_statistics(tmp_path, resource)

    assert stats.global_counts().class_counts == {
        "substitution": 6,
        "insertion": 1,
        "deletion": 1,
        "complex": 1,
        "other": 1,
    }


def test_class_totals_sum_to_the_allele_count(
    tmp_path: pathlib.Path,
) -> None:
    resource = _mixed_allele_score(tmp_path)

    stats = _built_statistics(tmp_path, resource)

    for counts in (*stats.by_chromosome().values(), stats.global_counts()):
        assert sum(counts.class_counts.values()) == counts.allele_count


def test_build_stores_a_matrix_that_totals_the_substitution_class(
    tmp_path: pathlib.Path,
) -> None:
    resource = _mixed_allele_score(tmp_path)

    stats = _built_statistics(tmp_path, resource)

    for counts in (*stats.by_chromosome().values(), stats.global_counts()):
        matrix = counts.substitution_matrix
        assert matrix is not None
        assert sum(matrix.values()) == counts.class_counts["substitution"]


def test_the_stored_matrix_merges_lowercase_and_diagonal_rows(
    tmp_path: pathlib.Path,
) -> None:
    # Global A>G is 3 -- two A>G rows plus the soft-masked a>g -- and
    # the identity T>T sits on the diagonal; the global matrix is the
    # elementwise merge of the per-chromosome ones.
    resource = _mixed_allele_score(tmp_path)

    stats = _built_statistics(tmp_path, resource)

    matrix = stats.global_counts().substitution_matrix
    assert matrix is not None
    assert matrix["A", "G"] == 3
    assert matrix["A", "C"] == 1
    assert matrix["T", "T"] == 1
    assert matrix["G", "T"] == 1
    chr2 = stats.by_chromosome()["chr2"].substitution_matrix
    assert chr2 is not None
    assert chr2["G", "T"] == 1
    assert sum(chr2.values()) == 1


def _mixed_per_record_score(tmp_path: pathlib.Path) -> GenomicResource:
    """The same table as ``_mixed_allele_score``, read a record at a time.

    Its twin declares ``.with_tabix()`` and is served by the vectorized
    bulk scan; this one does not, so its backend offers no column arrays
    and the scan falls back to the per-record read.

    Until 2026.8.5 the contrast was drawn with a ``np_score`` resource,
    which the bulk scan excluded by resource type (gain#920 removed the
    type).  The backend is the better discriminator anyway: array support
    is what ``bulk_scan_eligible`` actually asks, where the type was only
    ever a proxy for it.
    """
    return (
        an_allele_score()
        .with_score("score", "float")
        .with_data(_MIXED_TABLE)
        .build_resource(tmp_path)
    )


def test_the_two_fixtures_take_different_scan_paths(
    tmp_path: pathlib.Path,
) -> None:
    # The premise of the parity test below: only one of the two backends
    # serves column arrays, so the identical tables are read by genuinely
    # different scans.
    allele = _mixed_allele_score(tmp_path / "allele")
    per_record = _mixed_per_record_score(tmp_path / "per_record")
    confs: dict = {"score": _hist_conf()}

    assert scan.can_bulk_histogram(allele, confs)
    assert not scan.can_bulk_histogram(
        per_record, confs)


def test_the_per_record_statistics_match_the_bulk_ones_byte_for_byte(
    tmp_path: pathlib.Path,
) -> None:
    allele = _mixed_allele_score(tmp_path / "allele")
    per_record = _mixed_per_record_score(tmp_path / "per_record")

    cli_manage(["repo-stats", "-R", str(tmp_path / "allele"), "-j", "1"])
    cli_manage(["repo-stats", "-R", str(tmp_path / "per_record"), "-j", "1"])

    assert per_record.get_file_content(ALLELE_STATISTICS_FILE) \
        == allele.get_file_content(ALLELE_STATISTICS_FILE)


@pytest.mark.parametrize("region_size", [10, 20, 7, 1])
def test_statistics_are_chunk_invariant(
    tmp_path: pathlib.Path,
    region_size: int,
) -> None:
    # Region sizes 10 and 20 land chunk boundaries exactly on rows
    # (positions 10, 20, 30, 40 and 50 are all multiples of 10), which
    # is the shape a per-region ownership rule gets wrong by
    # double-counting a position that two chunks both see.
    whole = _mixed_allele_score(tmp_path / "whole")
    chunked = _mixed_allele_score(tmp_path / "chunked")

    cli_manage(["repo-stats", "-R", str(tmp_path / "whole"), "-j", "1"])
    cli_manage([
        "repo-stats", "-R", str(tmp_path / "chunked"), "-j", "1",
        "--region-size", str(region_size)])

    assert chunked.get_file_content(ALLELE_STATISTICS_FILE) \
        == whole.get_file_content(ALLELE_STATISTICS_FILE)


# An allele row may carry a ``pos_end`` reaching well past the point it
# collapses to (the golden fixture has one).  Such a row is answered to
# EVERY region query its span touches, so a statistic that counted the
# rows a region was handed rather than the rows it owns would count it
# once per chunk.
_WIDE_TABLE = """
    chrom  pos_begin  pos_end  reference  alternative  score
    chr1   10         40       A          G            0.1
    chr1   10         40       A          C            0.2
    chr1   25         60       CT         C            0.3
    chr1   50         55       A          AT           0.4
"""


def _wide_score(
    tmp_path: pathlib.Path, *, tabix: bool,
) -> GenomicResource:
    builder = (
        an_allele_score()
        .with_score("score", "float")
        .with_data(_WIDE_TABLE)
    )
    return _maybe_tabix(builder, tabix=tabix).build_resource(tmp_path)


@pytest.mark.parametrize("tabix", [True, False])
@pytest.mark.parametrize("region_size", [10, 20, 7])
def test_a_row_spanning_several_chunks_is_counted_once(
    tmp_path: pathlib.Path,
    tabix: bool,
    region_size: int,
) -> None:
    # Parametrized over both scan paths because the ownership rule is
    # stated twice -- scalar on the per-record path (no column arrays
    # from the backend) and vectorized on the bulk one -- and a rule
    # stated twice can drift.
    whole = _wide_score(tmp_path / "whole", tabix=tabix)
    chunked = _wide_score(tmp_path / "chunked", tabix=tabix)

    cli_manage(["repo-stats", "-R", str(tmp_path / "whole"), "-j", "1"])
    cli_manage([
        "repo-stats", "-R", str(tmp_path / "chunked"), "-j", "1",
        "--region-size", str(region_size)])

    counts = AlleleStatistics.deserialize(
        whole.get_file_content(ALLELE_STATISTICS_FILE)).global_counts()
    assert counts.allele_count == 4
    assert counts.substitution_matrix is not None
    assert sum(counts.substitution_matrix.values()) \
        == counts.class_counts["substitution"]
    assert chunked.get_file_content(ALLELE_STATISTICS_FILE) \
        == whole.get_file_content(ALLELE_STATISTICS_FILE)


_KEYLESS_TABLE = """
    chrom  pos_begin  score
    chr1   10         0.1
    chr1   10         0.2
    chr1   20         0.3
"""


def _keyless_score(
    tmp_path: pathlib.Path, *, tabix: bool,
) -> GenomicResource:
    builder = (
        an_allele_score()
        .with_score("score", "float")
        .without_key_columns("reference", "alternative")
        .with_data(_KEYLESS_TABLE)
    )
    return _maybe_tabix(builder, tabix=tabix).build_resource(tmp_path)


@pytest.mark.parametrize("tabix", [True, False])
def test_a_table_with_no_key_columns_counts_every_row_as_other(
    tmp_path: pathlib.Path,
    tabix: bool,
) -> None:
    # ``reference`` and ``alternative`` are independently optional, and
    # a row missing an allele is still a row: it classifies as ``other``
    # rather than being dropped or raising (ADR 0020).
    resource = _keyless_score(tmp_path, tabix=tabix)

    stats = _built_statistics(tmp_path, resource)

    counts = stats.global_counts()
    assert counts.allele_count == 3
    assert counts.class_counts["other"] == 3
    assert counts.substitution_matrix is not None
    assert sum(counts.substitution_matrix.values()) == 0


def test_a_backend_refusing_the_nucleotides_takes_the_per_record_path(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The bulk histogram scan WOULD serve this resource -- asserted
    # below -- but the bulk read cannot hand it nucleotides, so the
    # region must go back to the per-record read rather than to a
    # statistic with no class data.  Making the bulk path fatal is what
    # turns this from a restatement of the two predicates into an
    # observation of the routing.
    resource = _keyless_score(tmp_path, tabix=True)
    confs: dict = {"score": _hist_conf()}
    score = build_score_implementation_from_resource(resource).score
    assert scan.can_bulk_histogram(resource, confs)
    assert not serves_allele_arrays(score, ["score"])

    def refuse(*_args: Any, **_kwargs: Any) -> dict:
        raise AssertionError("the bulk path must not serve this region")

    monkeypatch.setattr(scan, "do_histogram_bulk", refuse)
    result = scan.do_histogram_task(
        resource, confs, "chr1", 1, 100)

    assert result.alleles is not None
    assert result.alleles.counts().class_counts["other"] == 3


def test_the_reroute_leaves_the_histograms_unchanged(
    tmp_path: pathlib.Path,
) -> None:
    # The nucleotide gate moves the HISTOGRAM build off the bulk path
    # too, for a resource the bulk path would otherwise have served.
    # The two paths must still agree on what they build.
    resource = _keyless_score(tmp_path, tabix=True)

    per_record = scan.do_histogram(
        resource, {"score": _hist_conf()}, "chr1", 1, 100)
    bulk = scan.do_histogram_bulk(
        resource, {"score": _hist_conf()}, "chr1", 1, 100)

    assert per_record["score"].to_dict() == bulk["score"].to_dict()


def test_the_noregion_build_produces_the_same_statistics(
    tmp_path: pathlib.Path,
) -> None:
    # ``--region-size 0`` is a third entry point into the scan, and one
    # that has been bypassed before (gain#587).
    whole = _mixed_allele_score(tmp_path / "whole")
    noregion = _mixed_allele_score(tmp_path / "noregion")

    cli_manage(["repo-stats", "-R", str(tmp_path / "whole"), "-j", "1"])
    cli_manage([
        "repo-stats", "-R", str(tmp_path / "noregion"), "-j", "1",
        "--region-size", "0"])

    assert noregion.get_file_content(ALLELE_STATISTICS_FILE) \
        == whole.get_file_content(ALLELE_STATISTICS_FILE)


@pytest.mark.parametrize("region_size", [10, 7])
def test_the_two_paths_agree_at_a_chunked_region_size(
    tmp_path: pathlib.Path,
    region_size: int,
) -> None:
    # The byte-identical criterion at the same region size, across the
    # two paths rather than within one: the wide-``pos_end`` fixture is
    # the shape where a chunk boundary and a path difference could
    # compound.
    allele = _wide_score(tmp_path / "allele", tabix=True)
    per_record = _wide_score(tmp_path / "per_record", tabix=False)

    for name in ("allele", "per_record"):
        cli_manage([
            "repo-stats", "-R", str(tmp_path / name), "-j", "1",
            "--region-size", str(region_size)])

    assert per_record.get_file_content(ALLELE_STATISTICS_FILE) \
        == allele.get_file_content(ALLELE_STATISTICS_FILE)


_ALT_ONLY_TABLE = """
    chrom  pos_begin  alternative  score
    chr1   10         G            0.1
    chr1   10         C            0.2
    chr1   20         AT           0.3
"""


def _alt_only_score(
    tmp_path: pathlib.Path, *, tabix: bool,
) -> GenomicResource:
    builder = (
        an_allele_score()
        .with_score("score", "float")
        .without_key_columns("reference")
        .with_data(_ALT_ONLY_TABLE)
    )
    return _maybe_tabix(builder, tabix=tabix).build_resource(tmp_path)


def test_a_table_declaring_only_one_key_column_is_still_bulk_served(
    tmp_path: pathlib.Path,
) -> None:
    # The bulk read serves a table declaring EITHER key column, yielding
    # the missing side as the ``None`` the record carries -- which is
    # what keeps it and the per-record read the same answer.  A class
    # needs both, so such a resource is all ``other``; that is the
    # statistic this slice wants, taken deliberately rather than
    # inherited (gain#777).
    resource = _alt_only_score(tmp_path, tabix=True)
    score = build_score_implementation_from_resource(resource).score

    assert serves_allele_arrays(score, ["score"])


@pytest.mark.parametrize("tabix", [True, False])
def test_a_table_declaring_only_one_key_column_counts_every_row_as_other(
    tmp_path: pathlib.Path,
    tabix: bool,
) -> None:
    resource = _alt_only_score(tmp_path, tabix=tabix)

    stats = _built_statistics(tmp_path, resource)

    counts = stats.global_counts()
    assert counts.allele_count == 3
    assert counts.class_counts["other"] == 3
    assert counts.substitution_matrix is not None
    assert sum(counts.substitution_matrix.values()) == 0


#: Rows of a table keyed by the first cell, so an assertion names the row
#: it is about.  Whole rows: gain#988 added a column to one of these tables
#: and an assertion on a row's first few cells would not have noticed.
def _rows_by_first_cell(
    table: Table, *, own: bool = False,
) -> dict[str, list[str]]:
    """Rows keyed by their first cell, so an assertion names the row it means.

    ``own`` reads each cell's own text instead of all of it -- what the
    substitution matrix needs, where every count carries a muted share
    nested under it that the whole text would run together with the count.
    """
    def read(cell: Cell) -> str:
        return cell.own_text if own else cell.text

    by_first = {read(row[0]): [read(cell) for cell in row]
                for row in table.rows}
    # Keying on the first cell drops a duplicate silently, which would let
    # an assertion about "the substitution row" pass while two disagreed.
    assert len(by_first) == len(table.rows), "two rows share a first cell"
    return by_first


def test_info_page_renders_a_row_per_chromosome(
    tmp_path: pathlib.Path,
) -> None:
    resource = _mixed_allele_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    table = table_after(
        _info_page(resource), "<h2>Alleles</h2>")

    # Whole rows, so a stray extra column leaves no unanchored
    # assertion passing.  The covered-position column that sat between
    # chromosome and alleles is gone and five class shares follow the
    # allele count (gain#1118).  chr1's nine alleles are five
    # substitutions and one each of the rest; chr2 carries one
    # substitution and nothing else.
    assert [[cell.text for cell in row] for row in table.rows] == [
        ["chr1", "9", "55.56%", "11.11%", "11.11%", "11.11%", "11.11%"],
        ["chr2", "1", "100.00%", "0.00%", "0.00%", "0.00%", "0.00%"]]


def test_info_page_titles_the_total_row_with_the_global_class_counts(
    tmp_path: pathlib.Path,
) -> None:
    # What the removed "Allele classes" table's Alleles column said
    # (gain#1118).  It is the pinned total row's hover titles now, so
    # the resource-wide count per class stays readable without adding
    # up the chromosomes' own titles by hand.
    resource = _mixed_allele_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    total = table_after(_info_page(resource), "<h2>Alleles</h2>").head[1]

    assert [cell.attrs.get("title") for cell in total[2:]] == [
        "6 alleles", "1 alleles", "1 alleles", "1 alleles", "1 alleles"]


def test_info_page_renders_each_class_as_a_share_of_the_alleles(
    tmp_path: pathlib.Path,
) -> None:
    # Ten alleles over the two chromosomes: six substitutions and one
    # each of the other four classes.  These shares were the removed
    # classes table's "% of alleles" column; they are the pinned total
    # row now, and the column headings name the classes.
    resource = _mixed_allele_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    table = table_after(_info_page(resource), "<h2>Alleles</h2>")

    assert [cell.text for cell in table.head[0]] == [
        "Chromosome", "Alleles", "substitution %", "insertion %",
        "deletion %", "complex %", "other %"]
    assert [cell.text for cell in table.head[1]] == [
        "all chromosomes", "10",
        "60.00%", "10.00%", "10.00%", "10.00%", "10.00%"]


def test_info_page_tells_a_rare_class_from_an_empty_one(
    tmp_path: pathlib.Path,
) -> None:
    # The shape the column exists for, and the one a bare "%.2f%%"
    # collapses: one complex allele in 20,001 against an ``other`` that
    # is genuinely empty.  The counts are doctored rather than scanned
    # because reaching the display resolution needs tens of thousands
    # of rows.
    #
    # The same fixture straddles both display boundaries: 20000 of the
    # 20001 rounds UP to 100.00%, so the substitution row carries the
    # ceiling while the complex row carries the floor.
    #
    # Both are asserted ESCAPED, the leading "<" and ">" alike: rendered
    # raw the floor would open a bogus tag and the browser would swallow
    # the cell.
    resource = _mixed_allele_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    stored = json.loads(resource.get_file_content(ALLELE_STATISTICS_FILE))
    entry = stored["chromosomes"]["chr1"]
    entry["allele_count"] = 20001
    entry["class_counts"] = {
        "substitution": 20000, "insertion": 0, "deletion": 0,
        "complex": 1, "other": 0,
    }
    stored["chromosomes"] = {"chr1": entry}
    with resource.proto.open_raw_file(
            resource, ALLELE_STATISTICS_FILE, mode="wt") as outfile:
        outfile.write(json.dumps(stored))

    page = _info_page(resource)

    total = table_after(page, "<h2>Alleles</h2>").head[1]
    assert [cell.text for cell in total] == [
        "all chromosomes", "20001",
        ">99.99%", "0.00%", "0.00%", "<0.01%", "0.00%"]
    # The counts the shares round off stay exact on the hover titles.
    assert [cell.attrs.get("title") for cell in total[2:]] == [
        "20000 alleles", "0 alleles", "0 alleles", "1 alleles",
        "0 alleles"]
    # Both bounds read off the MARKUP, because that is the only place the
    # two forms differ: the parser above resolves the entities, so a cell
    # cannot tell a rendered "&lt;"/"&gt;" from a raw "<"/">" -- and a raw
    # one would open a bogus tag and have the browser swallow the cell.
    alleles = section_after(page, "<h2>Alleles</h2>")
    assert "&lt;0.01%" in alleles
    assert "&gt;99.99%" in alleles


def test_info_page_renders_the_substitution_matrix(
    tmp_path: pathlib.Path,
) -> None:
    resource = _mixed_allele_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    table = table_after(
        _info_page(resource),
        "<h3>Substitution matrix</h3>")

    # own_text, not text: every count carries a muted share nested under
    # it, and the two run together read as neither number.
    rows = _rows_by_first_cell(table, own=True)
    # Rows in A, C, G, T order; the A row holds A>C 1 and A>G 3 (the
    # soft-masked a>g merged in), the T row its identity diagonal.
    assert list(rows) == ["A", "C", "G", "T"]
    assert rows["A"] == ["A", "0", "1", "3", "0"]
    assert rows["T"] == ["T", "0", "0", "0", "1"]


def test_matrix_cells_carry_a_muted_share_on_a_second_line(
    tmp_path: pathlib.Path,
) -> None:
    # Three of the six substitutions are A>G.  The share sits in its own
    # muted element rather than beside the count: on a real score the
    # counts are nine digits wide and a parenthetical is unreadable.
    resource = _mixed_allele_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    section = section_after(
        _info_page(resource), "<h2>Alleles</h2>")

    assert '<td>3<div class="text-muted">50.00%</div></td>' in section
    assert '<td>0<div class="text-muted">0.00%</div></td>' in section


def test_info_page_renders_the_ts_tv_ratio(
    tmp_path: pathlib.Path,
) -> None:
    # Three transitions (A>G twice, a>g) over two transversions (A>C,
    # G>T); the identity T>T is neither.
    resource = _mixed_allele_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    section = section_after(
        _info_page(resource), "<h2>Alleles</h2>")

    assert "1.50" in section


def test_info_page_shows_what_the_ts_tv_ratio_is_made_of(
    tmp_path: pathlib.Path,
) -> None:
    # The counts behind the ratio are computed today and were rendered
    # nowhere (gain#1118).  Showing them lets a reader see a resource
    # with too few transversions for the ratio to mean anything --
    # 3/2 and 3,000,000/2,000,000 are the same number and not at all
    # the same claim.
    resource = _mixed_allele_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    section = section_after(
        _info_page(resource), "<h2>Alleles</h2>")

    assert "3 transitions" in section
    assert "2 transversions" in section


def test_info_page_without_transversions_says_not_applicable(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        an_allele_score()
        .with_score("score", "float")
        .with_data("""
            chrom  pos_begin  reference  alternative  score
            chr1   10         A          G            0.1
            chr1   20         C          T            0.2
        """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    section = section_after(
        _info_page(resource), "<h2>Alleles</h2>")

    assert "not applicable" in section


def test_info_page_over_a_pre_display_file_still_renders_the_shares(
    tmp_path: pathlib.Path,
) -> None:
    # A statistics file as gain#777 wrote it: counts and class totals,
    # and none of the OPTIONAL groups.  Its shares are resolvable --
    # the class counts and the allele total are stored fields every
    # file carries -- so the column renders.  gain#988 computed the
    # shares on the payload that collapses without those groups, which
    # dropped the column here; iossifovlab/gain#1002 moved them onto
    # the section display, which does not collapse.
    resource = _mixed_allele_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    stored = json.loads(resource.get_file_content(ALLELE_STATISTICS_FILE))
    for entry in (*stored["chromosomes"].values(), stored["global"]):
        for group in (
            "substitution_matrix", "insertion_length_histogram",
            "deletion_length_histogram", "complex_grid",
        ):
            entry.pop(group, None)
    with resource.proto.open_raw_file(
            resource, ALLELE_STATISTICS_FILE, mode="wt") as outfile:
        outfile.write(json.dumps(stored))

    table = table_after(_info_page(resource), "<h2>Alleles</h2>")

    assert [cell.text for cell in table.head[1]] == [
        "all chromosomes", "10",
        "60.00%", "10.00%", "10.00%", "10.00%", "10.00%"]
    # The per-chromosome shares resolve off the same stored fields, so
    # the rows carry theirs too rather than going empty.
    assert [cell.text for cell in table.rows[0]][2:] == [
        "55.56%", "11.11%", "11.11%", "11.11%", "11.11%"]


def test_info_page_over_a_matrixless_file_says_matrix_not_computed(
    tmp_path: pathlib.Path,
) -> None:
    # A statistics file written between gain#777 and this slice: counts
    # and class totals, no matrix.  The section must keep its tables and
    # mark the matrix not computed -- never render a 4x4 of zeros next
    # to a non-zero substitution total.
    resource = _mixed_allele_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    stored = json.loads(resource.get_file_content(ALLELE_STATISTICS_FILE))
    for entry in (*stored["chromosomes"].values(), stored["global"]):
        del entry["substitution_matrix"]
    with resource.proto.open_raw_file(
            resource, ALLELE_STATISTICS_FILE, mode="wt") as outfile:
        outfile.write(json.dumps(stored))

    page = _info_page(resource)

    assert [cell.text for cell in table_after(
        page, "<h2>Alleles</h2>").rows[0]] == [
            "chr1", "9", "55.56%", "11.11%", "11.11%", "11.11%", "11.11%"]
    # The class composition survives the missing matrix: it is read off
    # the stored counts, which every file carries.
    assert [cell.text for cell in table_after(
        page, "<h2>Alleles</h2>").head[1]][:3] == [
            "all chromosomes", "10", "60.00%"]
    # Bound to the subsection that must say it, not to the Alleles section
    # at large -- which renders "not computed" for other groups too.
    assert "<p>not computed</p>" in section_after(
        page, "<h3>Substitution matrix</h3>")
    assert "<th>A</th>" not in section_after(page, "<h2>Alleles</h2>")


def test_info_page_renders_an_all_other_matrix_as_zeros(
    tmp_path: pathlib.Path,
) -> None:
    # Every row classifies as ``other``, so the matrix is genuinely
    # all-zero -- which renders as a populated table of zeros with an
    # inapplicable ratio, NOT as the matrixless "not computed" above.
    resource = _alt_only_score(tmp_path, tabix=True)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    page = _info_page(resource)

    rows = _rows_by_first_cell(
        table_after(page, "<h3>Substitution matrix</h3>"), own=True)
    assert rows["A"] == ["A", "0", "0", "0", "0"]
    assert "not applicable" in section_after(page, "<h2>Alleles</h2>")


def test_info_page_without_the_statistics_file_says_not_computed(
    tmp_path: pathlib.Path,
) -> None:
    # A resource built before this statistic existed: histograms are
    # there, statistics/alleles.json is not.
    resource = _mixed_allele_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    resource.proto.delete_resource_file(resource, ALLELE_STATISTICS_FILE)

    section = section_after(
        _info_page(resource), "<h2>Alleles</h2>")

    assert "not computed" in section


def test_one_page_render_reads_the_statistics_file_once(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The Alleles section asks for the statistic twice -- the tables and
    # the matrix payload -- and over an HTTP or S3 repository each ask
    # would be a network round trip, so the read is cached per
    # implementation object, as the coverage statistic's is.
    resource = _mixed_allele_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    impl = build_score_implementation_from_resource(resource)
    reads = []
    original = type(resource).get_file_content

    def counting(self: GenomicResource, path: str, **kwargs: Any) -> Any:
        if path == ALLELE_STATISTICS_FILE:
            reads.append(path)
        return original(self, path, **kwargs)

    monkeypatch.setattr(type(resource), "get_file_content", counting)
    impl.get_info()

    assert len(reads) == 1


def test_statistics_hash_is_untouched_by_the_allele_build(
    tmp_path: pathlib.Path,
) -> None:
    # The rollout is lazy: the section appears as resources are rebuilt
    # for other reasons, never because this statistic forced a rebuild.
    resource = _mixed_allele_score(tmp_path)
    before = build_score_implementation_from_resource(
        resource).calc_statistics_hash()

    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    after = build_score_implementation_from_resource(
        resource).calc_statistics_hash()
    assert after == before


def test_a_position_score_writes_no_allele_statistics(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        a_position_score()
        .with_score("score", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  score
            chr1   5          9        0.1
            chr1   10         14       0.2
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )

    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    assert not resource.file_exists(ALLELE_STATISTICS_FILE)
    implementation = build_score_implementation_from_resource(resource)
    assert implementation.get_allele_statistics() is None


def test_the_alleles_section_is_absent_on_a_position_score(
    tmp_path: pathlib.Path,
) -> None:
    # Not "Alleles: not computed" forever -- a position score never
    # reads alleles, so the section does not exist at all, the way the
    # Fragments section is absent on every kind but a fragment score.
    resource = (
        a_position_score()
        .with_score("score", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  score
            chr1   5          9        0.1
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    page = _info_page(resource)

    assert "Alleles" not in page


def test_the_coverage_section_is_absent_on_an_allele_score(
    tmp_path: pathlib.Path,
) -> None:
    # The converse of the two tests around it, and the reason gain#1118
    # could drop the count: an allele row collapses to a point, so the
    # span union is never scanned for this kind
    # (``_COVERAGE_SCAN_RESOURCE_TYPES`` excludes it) and the distinct
    # count that stood in for it is gone.  Nothing can ever fill the
    # section, so it does not exist -- the same rule the Alleles section
    # follows on a position score.
    resource = _mixed_allele_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    page = _info_page(resource)

    assert "Coverage" not in page


def test_an_unbuilt_position_score_still_offers_to_compute_coverage(
    tmp_path: pathlib.Path,
) -> None:
    # The other half of gain#1118's gating rule, and the reason it is
    # keyed on the KIND rather than on the statistic being absent.
    # Statistics are never built here, so the section has nothing to
    # show -- but a rebuild WOULD fill it, and "not computed" is the
    # wording that says so.  Gating on the payload instead would
    # collapse *never applicable* and *not yet built* into one blank.
    resource = (
        a_position_score()
        .with_score("score", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  score
            chr1   5          9        0.1
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )

    page = _info_page(resource)

    assert "<h2>Coverage</h2>" in page
    assert "<p>not computed</p>" in section_after(page, "<h2>Coverage</h2>")


def test_the_coverage_section_survives_on_a_fragment_score(
    tmp_path: pathlib.Path,
) -> None:
    # A fragment score IS coverage-scanned -- it sits in
    # ``_COVERAGE_SCAN_RESOURCE_TYPES`` beside position scores -- so
    # emptying the block for allele scores must not empty it here.
    resource = (
        a_fragment_score()
        .with_score("score", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  score
            chr1   10         100      0.1
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    page = _info_page(resource)

    assert "<h2>Coverage</h2>" in page
    assert "<p>not computed</p>" not in section_after(
        page, "<h2>Coverage</h2>")


def test_the_alleles_section_is_absent_on_a_fragment_score(
    tmp_path: pathlib.Path,
) -> None:
    # The kind the section reads most misleadingly on: a fragment row
    # has no ref/alt at all, so "not computed" suggests a rebuild would
    # fill it in, and none ever would.
    resource = (
        a_fragment_score()
        .with_score("score", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  score
            chr1   10         100      0.1
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    page = _info_page(resource)

    assert "Alleles" not in page


# A second fixture, deliberately separate from ``_MIXED_TABLE``: that one
# carries one indel of each direction at the SAME minimum length, which
# reconciles but cannot tell one length bin from another.  This one
# varies the lengths and spreads the complex pairs on and off the
# diagonal.
#
#   chr1: +1 -> bin 0, +4 -> bin 2, -3 -> bin 1, (2,2) MNV, (2,3)
#   chr2: +2 -> bin 1, (3,3) MNV
#
# Every row carries a WIDE ``pos_end``, deliberately: an allele row
# collapses to the point it sits at, but tabix answers it to every
# region its span touches, so without one a point row reaches exactly
# one region and the chunk-invariance parametrization below cannot tell
# "the rows a region OWNS" from "the rows a region was HANDED".  The
# spans change no count here -- an allele's pos_end reaches over
# nothing -- they only make the double-hand case happen.
_INDEL_TABLE = """
    chrom  pos_begin  pos_end  reference  alternative  score
    chr1   10         45       A          AT           0.1
    chr1   20         55       A          ATTTT        0.2
    chr1   30         60       ACGT       A            0.3
    chr1   40         70       AC         GT           0.4
    chr1   50         80       AT         ACG          0.5
    chr2   10         40       A          AGG          0.6
    chr2   20         50       ATG        CGA          0.7
"""


def _indel_allele_score(tmp_path: pathlib.Path) -> GenomicResource:
    return (
        an_allele_score()
        .with_score("score", "float")
        .with_data(_INDEL_TABLE)
        .with_tabix()
        .build_resource(tmp_path)
    )


def _indel_per_record_score(tmp_path: pathlib.Path) -> GenomicResource:
    """``_indel_allele_score``'s table, read per-record (see gain#920)."""
    return (
        an_allele_score()
        .with_score("score", "float")
        .with_data(_INDEL_TABLE)
        .build_resource(tmp_path)
    )


def test_build_stores_the_length_histograms_per_chromosome(
    tmp_path: pathlib.Path,
) -> None:
    resource = _indel_allele_score(tmp_path)

    stats = _built_statistics(tmp_path, resource)

    chromosomes = stats.by_chromosome()
    chr1 = chromosomes["chr1"].insertion_lengths
    chr2 = chromosomes["chr2"].insertion_lengths
    assert chr1 is not None
    assert chr2 is not None
    assert chr1.lengths == {1: 1, 4: 1}
    assert (chr1.alleles, chr1.sum, chr1.min, chr1.max) == (2, 5, 1, 4)
    assert chr2.lengths == {2: 1}
    assert (chr2.alleles, chr2.sum, chr2.min, chr2.max) == (1, 2, 2, 2)


def test_build_stores_the_complex_grid_per_chromosome(
    tmp_path: pathlib.Path,
) -> None:
    resource = _indel_allele_score(tmp_path)

    stats = _built_statistics(tmp_path, resource)

    chromosomes = stats.by_chromosome()
    assert chromosomes["chr1"].complex_grid == {(2, 2): 1, (2, 3): 1}
    assert chromosomes["chr2"].complex_grid == {(3, 3): 1}


def test_the_global_groups_are_the_merge_of_the_chromosomes(
    tmp_path: pathlib.Path,
) -> None:
    resource = _indel_allele_score(tmp_path)

    stats = _built_statistics(tmp_path, resource)

    counts = stats.global_counts()
    assert counts.insertion_lengths is not None
    assert counts.deletion_lengths is not None
    assert counts.insertion_lengths.lengths == {1: 1, 2: 1, 4: 1}
    assert (counts.insertion_lengths.min, counts.insertion_lengths.max) \
        == (1, 4)
    assert counts.insertion_lengths.sum == 7
    assert counts.deletion_lengths.lengths == {3: 1}
    assert counts.complex_grid == {(2, 2): 1, (2, 3): 1, (3, 3): 1}


def test_the_group_totals_reconcile_with_the_class_counts(
    tmp_path: pathlib.Path,
) -> None:
    # The acceptance criterion that ties this slice to gain#777: every
    # insertion, deletion and complex row is accounted for exactly once.
    resource = _indel_allele_score(tmp_path)

    stats = _built_statistics(tmp_path, resource)

    for counts in (*stats.by_chromosome().values(), stats.global_counts()):
        assert counts.insertion_lengths is not None
        assert counts.deletion_lengths is not None
        assert counts.complex_grid is not None
        assert counts.insertion_lengths.alleles == \
            counts.class_counts["insertion"]
        assert counts.deletion_lengths.alleles == \
            counts.class_counts["deletion"]
        # The clamp is TOTAL: every indel lands in exactly one bucket,
        # so the map's values sum to the group's count too.
        assert sum(counts.insertion_lengths.lengths.values()) == \
            counts.class_counts["insertion"]
        assert sum(counts.deletion_lengths.lengths.values()) == \
            counts.class_counts["deletion"]
        assert sum(counts.complex_grid.values()) == \
            counts.class_counts["complex"]


def test_the_indel_groups_match_across_the_two_scan_paths(
    tmp_path: pathlib.Path,
) -> None:
    # The gain#777 parity check, extended to the new groups: only one of
    # the two backends serves column arrays, so these two identical tables
    # are read by genuinely different code paths.
    allele = _indel_allele_score(tmp_path / "allele")
    per_record = _indel_per_record_score(tmp_path / "per_record")

    cli_manage(["repo-stats", "-R", str(tmp_path / "allele"), "-j", "1"])
    cli_manage(["repo-stats", "-R", str(tmp_path / "per_record"), "-j", "1"])

    assert per_record.get_file_content(ALLELE_STATISTICS_FILE) \
        == allele.get_file_content(ALLELE_STATISTICS_FILE)


@pytest.mark.parametrize(
    "builder", [_indel_allele_score, _indel_per_record_score])
@pytest.mark.parametrize("region_size", [10, 20, 7, 1])
def test_the_indel_groups_are_chunk_invariant(
    tmp_path: pathlib.Path,
    region_size: int,
    builder: Callable[[pathlib.Path], GenomicResource],
) -> None:
    # Chunking meets the sparse grid's cells in different orders, which
    # is why they are written sorted rather than as encountered.
    #
    # Parametrized over BOTH fixtures because the region's ownership
    # rule is stated twice -- scalar on the per-record path, vectorized
    # on the bulk one -- and only the tabix-backed fixture serves column
    # arrays.  With only the bulk-eligible fixture, breaking the scalar
    # predicate leaves this green.
    whole = builder(tmp_path / "whole")
    chunked = builder(tmp_path / "chunked")

    cli_manage(["repo-stats", "-R", str(tmp_path / "whole"), "-j", "1"])
    cli_manage([
        "repo-stats", "-R", str(tmp_path / "chunked"), "-j", "1",
        "--region-size", str(region_size)])

    assert chunked.get_file_content(ALLELE_STATISTICS_FILE) \
        == whole.get_file_content(ALLELE_STATISTICS_FILE)


def test_info_page_renders_the_indel_statistics_table(
    tmp_path: pathlib.Path,
) -> None:
    # chr1 carries a 1bp and a 4bp insertion, chr2 a 2bp one, and there
    # is one 3bp deletion.  Insertions: min 1, max 4, mean 7/3 = 2.33,
    # and with three alleles the median is the middle one, 2.
    resource = _indel_allele_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    table = table_after(_info_page(resource), "<h3>Indel lengths</h3>")

    assert [cell.text for cell in table.head[0]] == [
        "", "alleles", "min", "max", "mean", "median"]
    assert [[cell.text for cell in row] for row in table.rows] == [
        ["insertions", "3", "1", "4", "2.33", "2"],
        ["deletions", "1", "3", "3", "3", "3"],
    ]


def test_the_indel_figures_open_in_the_modal(
    tmp_path: pathlib.Path,
) -> None:
    # Half-width thumbnails lose no detail because each opens the
    # full-size image in the modal this page already uses for the score
    # histograms.  A trigger whose modal was never rendered opens
    # nothing, so both halves are asserted together.
    resource = _indel_allele_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    section = section_after(_info_page(resource), "<h2>Alleles</h2>")

    for group in ("insertion", "deletion"):
        assert f'data-modal-trigger="modal-allele-{group}-lengths"' \
            in section
        assert f'id="modal-allele-{group}-lengths"' in section


def test_the_build_writes_one_global_image_per_group(
    tmp_path: pathlib.Path,
) -> None:
    # Three images, one per group -- the count is what says there are no
    # per-chromosome images, as the fragments section has it.
    #
    # The two indel images are referenced TWICE each: once by the
    # half-width thumbnail and once inside the modal it opens
    # (gain#1118).  The complex grid is still a single full-width
    # figure, so it appears once -- and that asymmetry is the assertion
    # that would catch a thumbnail rendered with no modal behind it, or
    # a modal nothing opens.
    #
    # The complex group needs a grid dense enough to be drawn at all: a
    # sparse one publishes a table and no image (gain#989), which is
    # asserted on its own rather than folded into this count.
    resource = _all_groups_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    section = section_after(
        _info_page(resource), "<h2>Alleles</h2>")

    for image, references in (
        (ALLELE_INSERTION_LENGTHS_IMAGE_FILE, 2),
        (ALLELE_DELETION_LENGTHS_IMAGE_FILE, 2),
        (ALLELE_COMPLEX_GRID_IMAGE_FILE, 1),
    ):
        assert resource.file_exists(image)
        assert section.count(image) == references


def _allele_score_over(
    tmp_path: pathlib.Path, rows: list[tuple[int, str, str]],
) -> GenomicResource:
    """A tabix allele score over ``(position, reference, alternative)``.

    Rows go through ``with_score_line`` rather than a hand-written table
    string: these rows are GENERATED from a count, and the builder owns
    the header text and the column order that a literal would have to
    restate.  The hand-written ``_MIXED_TABLE`` / ``_INDEL_TABLE`` above
    stay as they are -- they are authored and commented row by row.
    """
    builder = an_allele_score().with_score("score", "float")
    for pos, reference, alternative in rows:
        builder = builder.with_score_line(
            chrom="chr1", pos_begin=pos, reference=reference,
            alternative=alternative, score=0.1)
    return builder.with_tabix().build_resource(tmp_path)


def _complex_cell_rows(cell_count: int) -> list[tuple[int, str, str]]:
    """``cell_count`` rows, each occupying a distinct complex cell.

    Reference and alternative differ at their first base, so no pair is
    an insertion or a deletion, and each row's pair of lengths is unique
    -- so the occupied-cell count is the row count, which is the number
    the threshold is about.  Sizing the fixture is not enough on its own:
    the same row count landing in fewer cells would test a threshold
    that is not this one.
    """
    return [
        ((index + 1) * 10,
         "A" + "C" * (1 + index // 6),
         "G" + "T" * (1 + index % 6))
        for index in range(cell_count)
    ]


def _dense_complex_score(tmp_path: pathlib.Path) -> GenomicResource:
    """A score whose complex grid has one cell more than the threshold."""
    return _allele_score_over(
        tmp_path, _complex_cell_rows(COMPLEX_GRID_TABLE_MAX_CELLS + 1))


def _sparse_complex_score(tmp_path: pathlib.Path) -> GenomicResource:
    """Three complex cells, of three DIFFERENT sizes.

    Distinct counts, and a cell whose two lengths differ: a fixture of
    equal counts would order the same ascending as descending, and one
    of square cells would render the same with its two length columns
    swapped.
    """
    return _allele_score_over(tmp_path, [
        (10, "AC", "GT"),
        (20, "AC", "GT"),
        (30, "AC", "GT"),
        (40, "AT", "ACG"),
        (50, "AT", "ACG"),
        (60, "ATG", "CGA"),
    ])


def _all_groups_score(tmp_path: pathlib.Path) -> GenomicResource:
    """Every gain#779 group populated, the complex one too dense to table.

    An insertion, a deletion, and enough complex cells that the grid is
    drawn rather than tabled -- which is what it takes for all three
    images to be written at once.
    """
    return _allele_score_over(tmp_path, [
        (1, "A", "AT"),
        (5, "ACGT", "A"),
        *_complex_cell_rows(COMPLEX_GRID_TABLE_MAX_CELLS + 1),
    ])


def test_info_page_draws_a_complex_grid_with_more_cells_than_the_threshold(
    tmp_path: pathlib.Path,
) -> None:
    # One cell over the threshold: the heatmap is what a grid this
    # populated is for, and the table is what it is not (gain#989).
    resource = _dense_complex_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    stats = AlleleStatistics.deserialize(
        resource.get_file_content(ALLELE_STATISTICS_FILE))
    section = section_after(
        _info_page(resource), "<h2>Alleles</h2>")

    # The fixture's granularity is the point: a table of the intended
    # size that lands in fewer cells would test the wrong threshold.
    grid = stats.global_counts().complex_grid
    assert grid is not None
    assert len(grid) == COMPLEX_GRID_TABLE_MAX_CELLS + 1
    assert section.count(ALLELE_COMPLEX_GRID_IMAGE_FILE) == 1
    assert "<th>% of complex</th>" not in section
    assert resource.file_exists(ALLELE_COMPLEX_GRID_IMAGE_FILE)


def test_info_page_tables_a_sparse_complex_grid_instead_of_drawing_it(
    tmp_path: pathlib.Path,
) -> None:
    # Three occupied cells of the 64x64 square: the heatmap would be
    # three lit pixels in a field of white, while the table states in
    # three rows what the picture is hiding (gain#989).
    #
    # The whole table body is asserted, not just its headings: the rows
    # are what carries the answer, and a heading assertion alone stays
    # green while the template swaps the two length columns or drops the
    # percentage from every row.
    resource = _sparse_complex_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    page = _info_page(resource)

    table = table_after(page, "<h3>Complex alleles</h3>")
    # Whole rows, headings included: an assertion on a row's first few
    # cells would still pass with a fifth column appended to every one.
    assert [cell.text for cell in table.head[0]] == [
        "reference length", "alternative length", "alleles", "% of complex"]
    assert [[cell.text for cell in row] for row in table.rows] == [
        ["2", "2", "3", "50.00%"],
        ["2", "3", "2", "33.33%"],
        ["3", "3", "1", "16.67%"],
    ]
    assert ALLELE_COMPLEX_GRID_IMAGE_FILE not in section_after(
        page, "<h2>Alleles</h2>")


def test_the_build_writes_no_complex_image_when_the_table_is_rendered(
    tmp_path: pathlib.Path,
) -> None:
    # Below the threshold no page references that image, so it is not
    # written at all -- the same rule the length histograms follow, that
    # a group the resource publishes nothing for writes no image.
    resource = _indel_allele_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    assert not resource.file_exists(ALLELE_COMPLEX_GRID_IMAGE_FILE)


def test_info_page_calls_an_all_empty_complex_grid_no_complex_alleles(
    tmp_path: pathlib.Path,
) -> None:
    # A scan never writes a zero-count cell, so this takes a hand-edited
    # file -- but the page must read emptiness off the OCCUPIED cells,
    # the way the threshold and the rows already do, rather than off the
    # keys.  Reading the keys renders a table of headings and no rows.
    resource = _sparse_complex_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    stored = json.loads(resource.get_file_content(ALLELE_STATISTICS_FILE))
    for entry in (*stored["chromosomes"].values(), stored["global"]):
        entry["complex_grid"] = {"3": {"3": 0}}
    with resource.proto.open_raw_file(
            resource, ALLELE_STATISTICS_FILE, mode="wt") as outfile:
        outfile.write(json.dumps(stored))

    page = _info_page(resource)

    # Bound by reading the subsection itself, rather than by requiring the
    # message to sit immediately after its heading in the markup.
    complex_section = section_after(page, "<h3>Complex alleles</h3>")
    assert "<p>no complex alleles</p>" in complex_section
    assert "% of complex" not in complex_section


def test_info_page_over_a_pre_indel_file_says_the_groups_not_computed(
    tmp_path: pathlib.Path,
) -> None:
    # A file written between gain#778 and this slice: matrix, no
    # lengths.  The groups are independently optional, so the matrix
    # must still render while the three new sections say so.
    resource = _indel_allele_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    stored = json.loads(resource.get_file_content(ALLELE_STATISTICS_FILE))
    for entry in (*stored["chromosomes"].values(), stored["global"]):
        for key in (
            "insertion_lengths",
            "deletion_lengths",
            "complex_grid",
        ):
            entry.pop(key, None)
    with resource.proto.open_raw_file(
            resource, ALLELE_STATISTICS_FILE, mode="wt") as outfile:
        outfile.write(json.dumps(stored))

    page = _info_page(resource)

    # Bound to their headings: "the page says 'not computed' somewhere"
    # would pass while the wrong section said it.
    assert "<th>A</th>" in section_after(page, "<h3>Substitution matrix</h3>")
    # The whole paragraph, not the phrase: the same subsection also renders
    # "insertion lengths not computed" and "deletion lengths not computed",
    # so a substring check cannot tell the group saying it has nothing from
    # a group reporting on one of its parts.
    assert "<p>not computed</p>" in section_after(
        page, "<h3>Indel lengths</h3>")
    assert "<p>not computed</p>" in section_after(
        page, "<h3>Complex alleles</h3>")
    assert ALLELE_COMPLEX_GRID_IMAGE_FILE not in section_after(
        page, "<h2>Alleles</h2>")


def test_info_page_says_a_resource_genuinely_has_no_complex_alleles(
    tmp_path: pathlib.Path,
) -> None:
    # Known-and-empty is not unknown: this resource was scanned and
    # carries no complex row, which the page states rather than falling
    # back to the "not computed" of a file that never had the group.
    resource = (
        an_allele_score()
        .with_score("score", "float")
        .with_data(
            """
            chrom  pos_begin  reference  alternative  score
            chr1   10         A          G            0.1
            chr1   20         A          AT           0.2
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    page = _info_page(resource)

    complex_section = section_after(page, "<h3>Complex alleles</h3>")
    assert "<p>no complex alleles</p>" in complex_section
    assert "not computed" not in complex_section
    assert "<p>no deletions</p>" in section_after(
        page, "<h3>Indel lengths</h3>")
    # A group with nothing to draw writes no image -- for either kind of
    # empty group, not just the complex one.
    assert not resource.file_exists(ALLELE_COMPLEX_GRID_IMAGE_FILE)
    assert not resource.file_exists(ALLELE_DELETION_LENGTHS_IMAGE_FILE)
    assert resource.file_exists(ALLELE_INSERTION_LENGTHS_IMAGE_FILE)
