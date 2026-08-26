# pylint: disable=C0114,C0116,W0212,W0621
import json
import pathlib
import re
from collections.abc import Callable
from typing import Any

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.histogram import NumberHistogramConfig
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation,
    build_score_implementation_from_resource,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.statistics.alleles import (
    ALLELE_COMPLEX_GRID_IMAGE_FILE,
    ALLELE_DELETION_LENGTHS_IMAGE_FILE,
    ALLELE_INSERTION_LENGTHS_IMAGE_FILE,
    ALLELE_STATISTICS_FILE,
    AlleleStatistics,
    serves_allele_arrays,
)
from gain.genomic_resources.statistics.length_histogram import (
    length_histogram_bin_index,
)
from gain.genomic_resources.testing.builders import (
    a_position_score,
    an_allele_score,
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
    text one is not, and ``_bulk_scan_eligible`` asks exactly that.  The
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


def test_rows_sharing_a_position_count_one_covered_position(
    tmp_path: pathlib.Path,
) -> None:
    # chr1 carries three rows at position 10 -- two of them the same
    # (chrom, pos, ref, alt) -- and one each at 20, 30, 40, 50, 60
    # and 70.
    resource = _mixed_allele_score(tmp_path)

    stats = _built_statistics(tmp_path, resource)

    assert {
        chrom: counts.covered_positions
        for chrom, counts in stats.by_chromosome().items()
    } == {"chr1": 7, "chr2": 1}


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
    is what ``_bulk_scan_eligible`` actually asks, where the type was only
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

    assert GenomicScoreImplementation._can_bulk_histogram(allele, confs)
    assert not GenomicScoreImplementation._can_bulk_histogram(
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
    assert (counts.allele_count, counts.covered_positions) == (4, 3)
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
    assert counts.covered_positions == 2
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
    assert GenomicScoreImplementation._can_bulk_histogram(resource, confs)
    assert not serves_allele_arrays(score, ["score"])

    def refuse(*_args: Any, **_kwargs: Any) -> dict:
        raise AssertionError("the bulk path must not serve this region")

    monkeypatch.setattr(
        GenomicScoreImplementation, "_do_histogram_bulk", refuse)
    result = GenomicScoreImplementation._do_histogram_task(
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

    per_record = GenomicScoreImplementation._do_histogram(
        resource, {"score": _hist_conf()}, "chr1", 1, 100)
    bulk = GenomicScoreImplementation._do_histogram_bulk(
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


def _alleles_section(page: str) -> str:
    """The rendered Alleles section, whitespace between tags collapsed.

    Scoped to the section rather than searched for across the page: an
    allele score's Coverage section always renders "not computed" (its
    rows have no span to union), so an unscoped assertion on that
    phrase would pass whatever the Alleles section said.
    """
    heading, _, section = page.partition("<h2>Alleles</h2>")
    assert heading != page, "the info page has no Alleles section"
    return re.sub(r">\s+<", "><", section)


def test_info_page_renders_a_row_per_chromosome(
    tmp_path: pathlib.Path,
) -> None:
    resource = _mixed_allele_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    section = _alleles_section(
        GenomicScoreImplementation(resource).get_info())

    assert "<td>chr1</td><td>7</td><td>9</td>" in section
    assert "<td>chr2</td><td>1</td><td>1</td>" in section


def test_info_page_renders_the_global_class_summary(
    tmp_path: pathlib.Path,
) -> None:
    resource = _mixed_allele_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    section = _alleles_section(
        GenomicScoreImplementation(resource).get_info())

    assert "<td>substitution</td><td>6</td>" in section
    assert "<td>insertion</td><td>1</td>" in section
    assert "<td>deletion</td><td>1</td>" in section
    assert "<td>complex</td><td>1</td>" in section
    assert "<td>other</td><td>1</td>" in section


def test_info_page_renders_the_substitution_matrix(
    tmp_path: pathlib.Path,
) -> None:
    resource = _mixed_allele_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    section = _alleles_section(
        GenomicScoreImplementation(resource).get_info())

    # Rows in A, C, G, T order; the A row holds A>C 1 and A>G 3 (the
    # soft-masked a>g merged in), the T row its identity diagonal.
    assert "<th>A</th><td>0</td><td>1</td><td>3</td><td>0</td>" in section
    assert "<th>T</th><td>0</td><td>0</td><td>0</td><td>1</td>" in section


def test_info_page_renders_the_ts_tv_ratio(
    tmp_path: pathlib.Path,
) -> None:
    # Three transitions (A>G twice, a>g) over two transversions (A>C,
    # G>T); the identity T>T is neither.
    resource = _mixed_allele_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    section = _alleles_section(
        GenomicScoreImplementation(resource).get_info())

    assert "1.50" in section


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

    section = _alleles_section(
        GenomicScoreImplementation(resource).get_info())

    assert "not applicable" in section


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

    section = _alleles_section(
        GenomicScoreImplementation(resource).get_info())

    assert "<td>chr1</td><td>7</td><td>9</td>" in section
    assert "<td>substitution</td><td>6</td>" in section
    assert "not computed" in section
    assert "<th>A</th>" not in section


def test_info_page_renders_an_all_other_matrix_as_zeros(
    tmp_path: pathlib.Path,
) -> None:
    # Every row classifies as ``other``, so the matrix is genuinely
    # all-zero -- which renders as a populated table of zeros with an
    # inapplicable ratio, NOT as the matrixless "not computed" above.
    resource = _alt_only_score(tmp_path, tabix=True)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    section = _alleles_section(
        GenomicScoreImplementation(resource).get_info())

    assert "<th>A</th><td>0</td><td>0</td><td>0</td><td>0</td>" in section
    assert "not applicable" in section


def test_info_page_without_the_statistics_file_says_not_computed(
    tmp_path: pathlib.Path,
) -> None:
    # A resource built before this statistic existed: histograms are
    # there, statistics/alleles.json is not.
    resource = _mixed_allele_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    resource.proto.delete_resource_file(resource, ALLELE_STATISTICS_FILE)

    section = _alleles_section(
        GenomicScoreImplementation(resource).get_info())

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
    impl = GenomicScoreImplementation(resource)
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
    assert GenomicScoreImplementation(resource).get_allele_statistics() \
        is None


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
    assert {index: count for index, count in enumerate(chr1) if count} == {
        length_histogram_bin_index(1): 1,
        length_histogram_bin_index(4): 1,
    }
    assert {index: count for index, count in enumerate(chr2) if count} == {
        length_histogram_bin_index(2): 1,
    }


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
    assert {
        index: count
        for index, count in enumerate(counts.insertion_lengths) if count
    } == {
        length_histogram_bin_index(1): 1,
        length_histogram_bin_index(2): 1,
        length_histogram_bin_index(4): 1,
    }
    assert {
        index: count
        for index, count in enumerate(counts.deletion_lengths) if count
    } == {length_histogram_bin_index(3): 1}
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
        assert sum(counts.insertion_lengths) == \
            counts.class_counts["insertion"]
        assert sum(counts.deletion_lengths) == \
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


def test_the_build_writes_one_global_image_per_group(
    tmp_path: pathlib.Path,
) -> None:
    # Three images, each referenced ONCE -- the count is what says there
    # are no per-chromosome images, as the fragments section has it.
    resource = _indel_allele_score(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    section = _alleles_section(
        GenomicScoreImplementation(resource).get_info())

    for image in (
        ALLELE_INSERTION_LENGTHS_IMAGE_FILE,
        ALLELE_DELETION_LENGTHS_IMAGE_FILE,
        ALLELE_COMPLEX_GRID_IMAGE_FILE,
    ):
        assert resource.file_exists(image)
        assert section.count(image) == 1


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
            "insertion_length_histogram",
            "deletion_length_histogram",
            "complex_grid",
        ):
            entry.pop(key, None)
    with resource.proto.open_raw_file(
            resource, ALLELE_STATISTICS_FILE, mode="wt") as outfile:
        outfile.write(json.dumps(stored))

    section = _alleles_section(
        GenomicScoreImplementation(resource).get_info())

    # Bound to their headings: "the page says 'not computed' somewhere"
    # would pass while the wrong section said it.
    assert "<th>A</th>" in section
    assert "<h3>Indel lengths</h3><p>not computed</p>" in section
    assert "<h3>Complex alleles</h3><p>not computed</p>" in section
    assert ALLELE_COMPLEX_GRID_IMAGE_FILE not in section


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

    section = _alleles_section(
        GenomicScoreImplementation(resource).get_info())

    assert "<h3>Complex alleles</h3><p>no complex alleles</p>" in section
    assert "<h3>Complex alleles</h3><p>not computed</p>" not in section
    assert "<p>no deletions</p>" in section
    # A group with nothing to draw writes no image -- for either kind of
    # empty group, not just the complex one.
    assert not resource.file_exists(ALLELE_COMPLEX_GRID_IMAGE_FILE)
    assert not resource.file_exists(ALLELE_DELETION_LENGTHS_IMAGE_FILE)
    assert resource.file_exists(ALLELE_INSERTION_LENGTHS_IMAGE_FILE)
