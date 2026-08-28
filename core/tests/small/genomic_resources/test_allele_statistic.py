# pylint: disable=C0114,C0116,W0212,W0621
import json

import pytest
from gain.genomic_resources.statistics.alleles import (
    COMPLEX_GRID_TABLE_MAX_CELLS,
    COMPLEX_LENGTH_CLAMP,
    AlleleDisplay,
    AlleleStatistics,
    RegionAlleles,
)
from gain.genomic_resources.statistics.length_histogram import (
    length_histogram_bin_index,
)


def _region(
    chrom: str = "chr1",
    start: int | None = 1,
    end: int | None = 100,
) -> RegionAlleles:
    return RegionAlleles(chrom, start, end)


def test_a_row_outside_the_region_is_not_counted() -> None:
    region = _region(start=10, end=20)

    region.add_allele(9, "A", "G")
    region.add_allele(21, "A", "G")
    region.add_allele(15, "A", "G")

    assert region.counts().allele_count == 1


def test_rows_at_one_position_count_one_covered_position() -> None:
    region = _region()

    region.add_allele(10, "A", "G")
    region.add_allele(10, "A", "C")
    region.add_allele(11, "A", "G")

    counts = region.counts()
    assert (counts.allele_count, counts.covered_positions) == (3, 2)


def test_substitution_rows_land_in_their_matrix_cells() -> None:
    region = _region()

    region.add_allele(10, "A", "G")
    region.add_allele(11, "A", "G")
    region.add_allele(12, "C", "T")

    matrix = region.counts().substitution_matrix
    assert matrix is not None
    assert matrix["A", "G"] == 2
    assert matrix["C", "T"] == 1
    assert sum(matrix.values()) == 3


def test_soft_masked_and_identity_pairs_land_in_their_cells() -> None:
    region = _region()

    region.add_allele(10, "a", "g")
    region.add_allele(11, "A", "G")
    region.add_allele(12, "T", "T")

    matrix = region.counts().substitution_matrix
    assert matrix is not None
    assert matrix["A", "G"] == 2
    assert matrix["T", "T"] == 1
    assert sum(matrix.values()) == 3
    assert region.counts().class_counts["substitution"] == 3


def test_rows_that_are_not_substitutions_land_in_no_cell() -> None:
    region = _region()

    region.add_allele(10, "N", "A")     # other: N is not a base
    region.add_allele(11, "A", "AT")    # insertion
    region.add_allele(12, "CT", "C")    # deletion
    region.add_allele(13, "AC", "GT")   # complex
    region.add_allele(14, None, None)   # other: missing alleles
    region.add_allele(15, "", "A")      # other: empty string

    matrix = region.counts().substitution_matrix
    assert matrix is not None
    assert sum(matrix.values()) == 0
    assert region.counts().allele_count == 6


def test_merge_adds_the_counts_of_the_adjacent_region() -> None:
    left = _region(start=1, end=10)
    left.add_allele(10, "A", "G")
    right = _region(start=11, end=20)
    right.add_allele(11, "A", "AT")

    left.merge(right)

    counts = left.counts()
    assert (counts.allele_count, counts.covered_positions) == (2, 2)
    assert counts.class_counts["substitution"] == 1
    assert counts.class_counts["insertion"] == 1


def test_merge_adds_the_matrices_of_the_adjacent_regions() -> None:
    left = _region(start=1, end=10)
    left.add_allele(5, "A", "G")
    right = _region(start=11, end=20)
    right.add_allele(11, "A", "G")
    right.add_allele(12, "C", "A")

    left.merge(right)

    matrix = left.counts().substitution_matrix
    assert matrix is not None
    assert matrix["A", "G"] == 2
    assert matrix["C", "A"] == 1
    assert sum(matrix.values()) == 3


def test_merge_refuses_a_pair_from_two_chromosomes() -> None:
    left = _region("chr1", 1, 10)
    right = _region("chr2", 11, 20)

    with pytest.raises(ValueError, match="chromosome boundaries"):
        left.merge(right)


def test_merge_refuses_regions_that_are_not_adjacent() -> None:
    left = _region(start=1, end=10)
    right = _region(start=15, end=20)

    with pytest.raises(ValueError, match="adjacent-and-in-order"):
        left.merge(right)


def test_serialized_counts_round_trip() -> None:
    statistics = AlleleStatistics()
    region = _region()
    region.add_allele(10, "A", "G")
    region.add_allele(10, "CT", "C")
    statistics.fold_region(region)

    restored = AlleleStatistics.deserialize(statistics.serialize())

    assert restored.by_chromosome() == statistics.by_chromosome()
    assert restored.global_counts() == statistics.global_counts()


def test_deserialize_ignores_keys_it_does_not_know() -> None:
    # Forward compatibility, as the coverage statistic has it: a file
    # carrying fields a later slice added still reads here.
    content = json.dumps({
        "format_version": 1,
        "chromosomes": {
            "chr1": {
                "allele_count": 2,
                "covered_positions": 1,
                "class_counts": {"substitution": 2},
                "ts_tv": 1.0,
            },
        },
        "global": {"allele_count": 2, "covered_positions": 1},
        "unknown_section": [1, 2, 3],
    })

    statistics = AlleleStatistics.deserialize(content)

    counts = statistics.global_counts()
    assert (counts.allele_count, counts.covered_positions) == (2, 1)
    assert counts.class_counts["substitution"] == 2
    assert counts.class_counts["other"] == 0


def test_a_file_without_the_matrix_reads_as_matrix_unknown() -> None:
    # Files written between gain#777 and this slice carry counts and
    # class totals but no matrix.  Unknown must stay distinguishable
    # from a genuine matrix of zeros -- and must not resurface as one
    # on the next write.
    content = json.dumps({
        "format_version": 1,
        "chromosomes": {
            "chr1": {
                "allele_count": 2,
                "covered_positions": 1,
                "class_counts": {"substitution": 2},
            },
        },
    })

    statistics = AlleleStatistics.deserialize(content)

    assert statistics.global_counts().substitution_matrix is None
    assert "substitution_matrix" not in statistics.serialize()


def test_the_global_matrix_is_unknown_when_any_chromosome_lacks_it() -> None:
    statistics = AlleleStatistics()
    scanned = _region("chr1")
    scanned.add_allele(10, "A", "G")
    statistics.fold_region(scanned)
    statistics.fold_region(RegionAlleles.frozen(
        "chr2", 1, 1, {"substitution": 1}))

    counts = statistics.global_counts()

    assert counts.substitution_matrix is None
    assert counts.class_counts["substitution"] == 2


def _display_of(region: RegionAlleles) -> AlleleDisplay | None:
    statistics = AlleleStatistics()
    statistics.fold_region(region)
    return statistics.global_counts().display()


def _complex_display(grid: dict[tuple[int, int], int]) -> AlleleDisplay:
    """A display carrying exactly this complex grid and nothing else."""
    alleles = sum(grid.values())
    display = _display_of(RegionAlleles.frozen(
        "chr1", alleles, alleles, {"complex": alleles}, complex_grid=grid))
    assert display is not None
    return display


def test_ts_tv_splits_the_off_diagonal_and_skips_the_diagonal() -> None:
    region = _region()
    region.add_allele(10, "A", "G")   # transition
    region.add_allele(11, "G", "A")   # transition
    region.add_allele(12, "C", "T")   # transition
    region.add_allele(13, "A", "C")   # transversion
    region.add_allele(14, "A", "A")   # identity: neither

    display = _display_of(region)

    assert display is not None
    assert display.transitions == 3
    assert display.transversions == 1
    assert display.ts_tv == 3.0


def test_ts_tv_is_not_applicable_without_transversions() -> None:
    # All transitions -- the ratio has a zero denominator, and identity
    # rows must not sneak into it as transversions.
    region = _region()
    region.add_allele(10, "A", "G")
    region.add_allele(11, "T", "T")

    display = _display_of(region)

    assert display is not None
    assert display.ts_tv is None


def test_class_percentages_are_shares_of_the_allele_count() -> None:
    region = _region()
    region.add_allele(10, "A", "G")
    region.add_allele(11, "A", "C")
    region.add_allele(12, "A", "AT")
    region.add_allele(13, "AT", "A")

    display = _display_of(region)

    assert display is not None
    assert display.class_percentages == {
        "substitution": "50.00%",
        "insertion": "25.00%",
        "deletion": "25.00%",
        "complex": "0.00%",
        "other": "0.00%",
    }


def test_a_class_below_the_display_resolution_is_not_a_zero() -> None:
    # The shape this column exists for, on a real score: ``complex`` is
    # a handful of alleles in tens of thousands -- 0.005%, which two
    # decimals round to 0.00 -- while ``other`` is genuinely empty.  A
    # class that exists and one that does not must not read the same.
    display = _display_of(RegionAlleles.frozen(
        "chr1", 20001, 20001,
        {"substitution": 20000, "complex": 1, "other": 0},
        complex_grid={(64, 1): 1}))

    assert display is not None
    assert display.class_percentages is not None
    assert display.class_percentages["complex"] == "<0.01%"
    assert display.class_percentages["other"] == "0.00%"


def test_no_alleles_is_no_percentage_rather_than_zero_percent() -> None:
    # No denominator resolves, so the share of every class is unknown
    # rather than zero -- the coverage display's answer when it cannot
    # resolve a chromosome length.
    display = _display_of(RegionAlleles.frozen(
        "chr1", 0, 0, {}, complex_grid={}))

    assert display is not None
    assert display.class_percentages is None


def test_a_matrixless_file_yields_no_display() -> None:
    # "Matrix unknown" collapses to None, as the fragment display
    # collapses a file that predates its field; a genuinely empty
    # matrix still yields a display of zeros.
    display = _display_of(
        RegionAlleles.frozen("chr1", 1, 1, {"substitution": 1}))

    assert display is None


def test_a_scanned_display_carries_every_group() -> None:
    region = _region()
    region.add_allele(10, "A", "AT")
    region.add_allele(11, "AT", "ACG")

    display = _display_of(region)

    assert display is not None
    assert display.insertion_lengths is not None
    assert display.deletion_lengths is not None
    assert display.complex_grid == {(2, 3): 1}


def test_a_pre_indel_file_displays_its_matrix_and_nothing_else() -> None:
    # A file written between gain#778 and this slice: the groups are
    # independently optional, so its matrix must still render while the
    # three new sections say "not computed".
    display = _display_of(RegionAlleles.frozen(
        "chr1", 1, 1, {"substitution": 1},
        substitution_matrix={("A", "G"): 1}))

    assert display is not None
    assert display.substitution_matrix is not None
    assert display.insertion_lengths is None
    assert display.deletion_lengths is None
    assert display.complex_grid is None


def test_matrix_cells_carry_their_share_of_the_substitutions() -> None:
    region = _region()
    region.add_allele(10, "A", "G")
    region.add_allele(11, "A", "G")
    region.add_allele(12, "C", "T")
    region.add_allele(13, "T", "T")

    display = _display_of(region)

    assert display is not None
    ref, cells = display.matrix_rows()[0]
    assert ref == "A"
    assert [(cell.alleles, cell.percentage) for cell in cells] == [
        (0, "0.00%"), (0, "0.00%"), (2, "50.00%"), (0, "0.00%")]


def test_the_shares_cover_all_sixteen_cells_and_the_diagonal() -> None:
    # What "the cells total 100%" means exactly: the denominator is the
    # sixteen cells and nothing else.  ADR 0020 classifies A>A as a
    # substitution, so the identity row below is a cell like any other
    # and part of what the shares divide by -- A>G is half of the two
    # substitutions, not all of the one that is off the diagonal.
    #
    # Stated as the denominator rather than as a sum of the rendered
    # strings, which round independently: six substitutions read
    # 16.67 + 50.00 + 16.67 + 16.67 = 100.01%, and apportioning them to
    # force 100.00% would make individual cells wrong.
    region = _region()
    region.add_allele(10, "A", "G")
    region.add_allele(11, "T", "T")

    display = _display_of(region)

    assert display is not None
    cells = [cell for _, row in display.matrix_rows() for cell in row]
    assert len(cells) == 16
    assert all(cell.percentage is not None for cell in cells)
    ref, row = display.matrix_rows()[0]
    assert ref == "A"
    assert row[2].percentage == "50.00%"


def test_a_matrix_of_zeros_gets_no_shares_rather_than_zero_percent() -> None:
    # A score whose only row is an insertion: the matrix is KNOWN and
    # empty, so there is no substitution total to take a share of.  The
    # cells must carry no second line at all -- a grid of "0.00%" would
    # claim a denominator that does not exist.
    region = _region()
    region.add_allele(10, "A", "AT")

    display = _display_of(region)

    assert display is not None
    assert display.substitution_percentages is None
    cells = [cell for _, row in display.matrix_rows() for cell in row]
    assert [cell.percentage for cell in cells] == [None] * 16


def test_matrix_shares_are_over_the_substitutions_not_every_allele() -> None:
    # Two substitutions among three alleles: a cell holding one of them
    # is half the substitutions, never a third of the alleles.
    region = _region()
    region.add_allele(10, "A", "G")
    region.add_allele(11, "C", "T")
    region.add_allele(12, "A", "AT")

    display = _display_of(region)

    assert display is not None
    ref, cells = display.matrix_rows()[0]
    assert ref == "A"
    assert cells[2].percentage == "50.00%"


def test_display_rows_follow_nucleotide_order() -> None:
    region = _region()
    region.add_allele(10, "T", "A")

    display = _display_of(region)

    assert display is not None
    assert [ref for ref, _ in display.matrix_rows()] == ["A", "C", "G", "T"]
    _, cells = display.matrix_rows()[3]
    assert [cell.alleles for cell in cells] == [1, 0, 0, 0]


def test_the_statistic_refuses_a_bare_value() -> None:
    with pytest.raises(TypeError, match="use fold_region"):
        AlleleStatistics().add_value(1)


def test_merging_two_statistics_of_one_chromosome_needs_adjacency() -> None:
    # Two DESERIALIZED statistics carry no extents, so their regions
    # refuse to merge -- the same guard the coverage statistic has.
    left = AlleleStatistics()
    left.fold_region(RegionAlleles.frozen("chr1", 1, 1, {"other": 1}))
    right = AlleleStatistics()
    right.fold_region(RegionAlleles.frozen("chr1", 1, 1, {"other": 1}))

    with pytest.raises(ValueError, match="adjacent-and-in-order"):
        left.merge(right)


def test_an_insertion_is_binned_by_the_bases_it_adds() -> None:
    region = _region()

    region.add_allele(10, "A", "ATTT")

    lengths = region.counts().insertion_lengths
    assert lengths is not None
    assert sum(lengths) == 1
    assert lengths[length_histogram_bin_index(3)] == 1


def test_a_deletion_is_binned_by_the_bases_it_removes() -> None:
    region = _region()

    region.add_allele(10, "ACGT", "A")

    lengths = region.counts().deletion_lengths
    assert lengths is not None
    assert sum(lengths) == 1
    assert lengths[length_histogram_bin_index(3)] == 1


def test_the_global_roll_up_sums_the_length_histograms() -> None:
    statistics = AlleleStatistics()
    for chrom in ("chr1", "chr2"):
        region = RegionAlleles(chrom, 1, 100)
        region.add_allele(10, "A", "AT")
        statistics.fold_region(region)

    lengths = statistics.global_counts().insertion_lengths

    assert lengths is not None
    assert lengths[length_histogram_bin_index(1)] == 2


def test_an_unknown_histogram_makes_the_whole_roll_up_unknown() -> None:
    # The all-or-nothing rule the matrix merge already states: a total
    # over a partially-unknown set would silently understate.
    statistics = AlleleStatistics()
    scanned = RegionAlleles("chr1", 1, 100)
    scanned.add_allele(10, "A", "AT")
    statistics.fold_region(scanned)
    statistics.fold_region(RegionAlleles.frozen("chr2", 1, 1, {}))

    assert statistics.global_counts().insertion_lengths is None


def test_merge_adds_the_length_histograms_of_the_adjacent_region() -> None:
    left = _region(start=1, end=10)
    left.add_allele(5, "A", "AT")
    right = _region(start=11, end=20)
    right.add_allele(11, "A", "AT")
    right.add_allele(12, "ACGT", "A")

    left.merge(right)

    counts = left.counts()
    assert counts.insertion_lengths is not None
    assert counts.insertion_lengths[length_histogram_bin_index(1)] == 2
    assert counts.deletion_lengths is not None
    assert counts.deletion_lengths[length_histogram_bin_index(3)] == 1


def test_a_complex_row_lands_at_its_exact_length_cell() -> None:
    # The grill's whole point: an unanchored 2->3 and a 3bp MNV are
    # different events and must not share a cell.
    region = _region()

    region.add_allele(10, "AT", "ACG")
    region.add_allele(11, "ATG", "CGA")

    grid = region.counts().complex_grid
    assert grid is not None
    assert grid[2, 3] == 1
    assert grid[3, 3] == 1


def test_lengths_at_or_above_the_clamp_share_the_grid_edge() -> None:
    # The documented caveat: the clamped corner is the one diagonal
    # cell that does not mean "MNV" -- a 5000 -> 70 pair lands there
    # too, and its sides are not equal.
    region = _region()

    region.add_allele(10, "A" * 5000, "C" * 70)
    region.add_allele(11, "G" * COMPLEX_LENGTH_CLAMP, "TT")

    grid = region.counts().complex_grid
    assert grid is not None
    assert grid[COMPLEX_LENGTH_CLAMP, COMPLEX_LENGTH_CLAMP] == 1
    assert grid[COMPLEX_LENGTH_CLAMP, 2] == 1


def test_the_complex_grid_round_trips_through_the_file() -> None:
    statistics = AlleleStatistics()
    region = _region()
    region.add_allele(10, "AT", "ACG")
    region.add_allele(11, "ATG", "CGA")
    statistics.fold_region(region)

    restored = AlleleStatistics.deserialize(statistics.serialize())

    assert restored.by_chromosome() == statistics.by_chromosome()
    assert restored.global_counts().complex_grid == {(2, 3): 1, (3, 3): 1}


def test_a_file_without_the_indel_groups_reads_as_unknown() -> None:
    # Files written between gain#778 and this slice carry the matrix but
    # no lengths.  Unknown must stay distinguishable from "the resource
    # genuinely has none", and must not resurface as zeros on rewrite.
    content = json.dumps({
        "format_version": 1,
        "chromosomes": {
            "chr1": {
                "allele_count": 2,
                "covered_positions": 1,
                "class_counts": {"insertion": 2},
            },
        },
    })

    statistics = AlleleStatistics.deserialize(content)

    counts = statistics.global_counts()
    assert counts.insertion_lengths is None
    assert counts.deletion_lengths is None
    assert counts.complex_grid is None
    written = statistics.serialize()
    assert "insertion_length_histogram" not in written
    assert "complex_grid" not in written


def test_a_resource_with_no_complex_rows_keeps_a_known_empty_grid() -> None:
    # Known-and-empty is not unknown: this resource HAS been scanned and
    # genuinely carries no complex alleles, which the page must be able
    # to say rather than falling back to "not computed".
    statistics = AlleleStatistics()
    region = _region()
    region.add_allele(10, "A", "G")
    statistics.fold_region(region)

    restored = AlleleStatistics.deserialize(statistics.serialize())

    assert restored.global_counts().complex_grid == {}


def test_the_complex_grid_is_written_sorted_not_as_encountered() -> None:
    # The cells are a sparse dict, so the written order is whatever the
    # rows happened to produce.  Both axes are met LARGEST FIRST here --
    # (3,3) before (2,4) before (2,2) -- so an as-encountered write
    # disagrees on the outer key, and one sorted only by the outer key
    # still disagrees on the inner.  Two builds that met the same pairs
    # in different orders would then differ byte for byte while carrying
    # identical counts.
    statistics = AlleleStatistics()
    region = _region()
    region.add_allele(10, "ATG", "CGA")
    region.add_allele(20, "AT", "ACGG")
    region.add_allele(30, "AC", "GT")
    statistics.fold_region(region)

    written = statistics.serialize()

    grid = json.loads(written)["chromosomes"]["chr1"]["complex_grid"]
    assert list(grid) == ["2", "3"]
    assert list(grid["2"]) == ["2", "4"]


def test_a_display_of_the_new_groups_survives_a_missing_matrix() -> None:
    # The seam returns nothing only when EVERY group is unknown, which
    # is observable only the other way round from the pre-indel file:
    # groups present, matrix absent.
    display = _display_of(RegionAlleles.frozen(
        "chr1", 1, 1, {"complex": 1}, complex_grid={(2, 3): 1}))

    assert display is not None
    assert display.substitution_matrix is None
    assert display.transitions is None
    assert display.ts_tv is None
    assert display.complex_grid == {(2, 3): 1}


def test_the_complex_table_threshold_is_inclusive_at_its_constant() -> None:
    # The one value in gain#989 worth arguing about, so it is pinned
    # from BOTH sides: at the constant the cells are tabled, one cell
    # further along they are drawn.  A bound checked from one side only
    # stays green when the comparison slips by one.
    at_threshold = {
        (2, alt_length): 1
        for alt_length in range(2, COMPLEX_GRID_TABLE_MAX_CELLS + 2)
    }
    assert len(at_threshold) == COMPLEX_GRID_TABLE_MAX_CELLS

    assert _complex_display(at_threshold).complex_grid_renders_as_table
    assert not _complex_display(
        {**at_threshold, (3, 2): 1}).complex_grid_renders_as_table


def test_an_empty_cell_is_not_a_table_row() -> None:
    # A zero-count cell draws nothing on the heatmap -- it is masked out
    # rather than coloured -- so it must not become a table row either.
    display = _complex_display({(2, 3): 4, (5, 5): 0})

    assert display.complex_rows() == [("2", "3", 4, "100.00%")]


def test_an_empty_cell_does_not_count_towards_the_table_bound() -> None:
    # The other half of the same rule, and the half a row assertion
    # cannot see: a grid at the bound stays tabled however many empty
    # cells it also carries.
    grid: dict[tuple[int, int], int] = {
        (2, alt_length): 1
        for alt_length in range(2, COMPLEX_GRID_TABLE_MAX_CELLS + 2)
    }
    grid[3, 2] = 0

    assert _complex_display(grid).complex_grid_renders_as_table


def test_the_complex_table_lists_its_cells_most_populated_first() -> None:
    display = _complex_display({(2, 2): 1, (3, 3): 7, (2, 4): 3})

    assert display.complex_rows() == [
        ("3", "3", 7, "63.64%"),
        ("2", "4", 3, "27.27%"),
        ("2", "2", 1, "9.09%"),
    ]


def test_a_clamped_complex_cell_reads_as_the_heatmap_axes_label_it() -> None:
    # The table and the picture must say the same thing about the same
    # cell, so the clamped length is spelled the way the axis spells it
    # and the length just below it is spelled plainly.
    display = _complex_display({
        (COMPLEX_LENGTH_CLAMP, 2): 2,
        (COMPLEX_LENGTH_CLAMP - 1, 2): 1,
    })

    assert [ref_length for ref_length, *_ in display.complex_rows()] == [
        f"≥{COMPLEX_LENGTH_CLAMP}", str(COMPLEX_LENGTH_CLAMP - 1)]


def test_a_cell_too_rare_to_show_is_not_reported_as_absent() -> None:
    # A real score's complex class is 881 alleles in 727 million, which
    # "%.2f" renders 0.00% -- exactly what it renders for a cell that
    # does not exist.  Only the floor keeps the two apart (gain#988).
    #
    # The rest of the grid is split rather than left in one cell, so the
    # fixture does not also exercise the UNdecided mirror of the floor:
    # there is no ceiling, so a cell that is nearly-but-not-quite the
    # whole class renders 100.00% and the column reads as over 100%.
    # That belongs to gain#988, which owns the rule.
    display = _complex_display({(2, 3): 1, (3, 3): 50_000, (4, 4): 49_999})

    assert display.complex_rows()[2] == ("2", "3", 1, "<0.01%")
