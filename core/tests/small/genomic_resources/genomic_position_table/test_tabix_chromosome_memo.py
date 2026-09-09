"""The tabix table's mapped contig list is derived once per open.

``TabixGenomicPositionTable.get_chromosomes()`` is the odd one out among the
backends: the base class returns a stored ``chrom_order``, while this one maps
every file contig through :meth:`map_chromosome` and filters the unmapped ones
out.  It is on the annotation hot path -- ``GenomicScore.get_all_chromosomes()``
delegates straight to it, and every contig-membership check in the read path
goes through that -- so a single annotated substitution used to pay the whole
rebuild three times over (gain#1173).

What is pinned here is that the *derivation* happens once per open, not that it
is fast: the work is counted, through :meth:`map_chromosome`, rather than
timed.  A timing assertion would be the flakiest possible way to state it, and
counting says the stronger thing anyway -- the per-call cost does not scale
with the contig count because there is no per-call cost at all.

The list's CONTENT is not this module's subject; the mapping tests in
test_genomic_position_table.py own that, and the memo has to keep answering
them.  What is here is the memo's lifetime: derived once, given up on close,
re-derived from the new content on reopen.
"""
from __future__ import annotations

import pathlib

import pytest
import pytest_mock
from gain.genomic_resources.genomic_position_table import (
    build_genomic_position_table,
)
from gain.genomic_resources.genomic_position_table.table_tabix import (
    TabixGenomicPositionTable,
)
from gain.genomic_resources.testing import setup_tabix, setup_vcf
from gain.genomic_resources.testing.builders import a_position_score

from .test_table_lifetime import _a_mapped_vcf_table


def _build_tabix_table(
    tmp_path: pathlib.Path, contigs: list[str],
) -> TabixGenomicPositionTable:
    """A CLOSED tabix table over one record on each of ``contigs``.

    Returned closed so the spy below can be installed BEFORE the open, and any
    contig mapping the open itself does is therefore counted.  Today it does
    none -- ``_build_chrom_mapping`` reads ``get_file_chromosomes`` and builds
    the map from the prefix transforms, never through ``map_chromosome`` -- but
    that is the implementation's choice, not something the test should assume:
    an implementation that derived the list eagerly in ``open()`` would be
    counted honestly here and silently missed by a fixture that handed back an
    already-open table.
    """
    rows = "\n".join(f"                {contig}  10  12  3.14"
                     for contig in contigs)
    res = (
        a_position_score()
        .with_score("c2", "float")
        .with_data(f"""
                chrom  pos_begin  pos_end  c2
{rows}
        """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    assert res.config is not None
    table = build_genomic_position_table(res, res.config["table"])
    assert isinstance(table, TabixGenomicPositionTable)
    return table


@pytest.mark.parametrize("n_contigs", [3, 30])
def test_repeated_calls_on_one_open_table_derive_the_list_once(
    tmp_path: pathlib.Path, n_contigs: int, mocker: pytest_mock.MockerFixture,
) -> None:
    """The derivation costs one pass over the contigs per OPEN, not per call.

    ``map_chromosome`` is the per-contig work ``get_chromosomes()`` does, and
    on a position table it has no other in-tree caller -- so counting it counts
    exactly the derivation under test, and counts nothing else.

    Two contig counts, because the claim is about how the cost scales.  Both
    sizes answer with the same single pass, so the *per call* cost is flat in
    the contig count rather than linear in it; and when the memo regresses the
    two fail with counts proportional to their size, which is the scaling this
    is about made visible in the failure rather than only in a benchmark.

    The first call is asserted to do the full pass before the repeats are
    asked about, which is what keeps this from passing vacuously: an
    implementation that stopped routing through ``map_chromosome`` altogether
    would count zero everywhere and satisfy a bare "no further calls".
    """
    contigs = [f"chr{index}" for index in range(1, n_contigs + 1)]
    table = _build_tabix_table(tmp_path, contigs)
    spy = mocker.spy(table, "map_chromosome")

    with table:
        assert len(table.get_chromosomes()) == n_contigs
        assert spy.call_count == n_contigs, (
            "the first call no longer maps each file contig once; this test "
            "would be counting something other than the derivation it is "
            "about")

        for _ in range(5):
            assert len(table.get_chromosomes()) == n_contigs

        assert spy.call_count == n_contigs, (
            f"{spy.call_count - n_contigs} further contig mappings across 5 "
            f"repeat calls: the mapped contig list is being re-derived per "
            f"call rather than once per open (gain#1173)")


def test_a_closed_table_refuses_its_contigs_after_the_memo_was_filled(
    tmp_path: pathlib.Path,
) -> None:
    """The memo is given up on close, so a closed table refuses as it did.

    The read has to be done through the OPEN table first, because that is what
    fills the memo -- a table closed without ever having been asked refuses
    whether or not the memo is released, and would pass this vacuously.  What
    the refusal protects is the same thing the base class's
    ``get_file_chromosomes`` memo protects (gain#358): an answer served out of
    released state is indistinguishable from a live one at the call site.

    ``test_a_closed_vcf_table_does_not_answer_its_contigs_from_the_file`` in
    test_table_lifetime.py asks the same question of the VCF backend, which
    inherits this ``get_chromosomes()`` and is released by this ``close()``.
    This one is the tabix half: it reaches the refusal through the tabix
    ``_load_file_chromosomes`` and so pins its message rather than the VCF
    one's, which are separate raises with separate texts.
    """
    table = _build_tabix_table(tmp_path, ["chr1", "chr2"])

    with table:
        assert table.get_chromosomes() == ["chr1", "chr2"]

    with pytest.raises(ValueError, match="tabix table not open"):
        table.get_chromosomes()


def test_a_reopened_tabix_table_answers_its_contigs_from_the_current_file(
    tmp_path: pathlib.Path,
) -> None:
    """open() must not serve the previous open's contig list.

    Reopened WITHOUT an intervening ``close()``, which is what makes this a
    test of ``open()`` -- and the reason the memo is given up in
    ``_build_chrom_mapping`` rather than in ``close()`` alone.  ``close()``
    releases it too, but a caller is not required to have called it: this is
    the same invariant, and the same reasoning, as the buffer discard that
    ``TabixGenomicPositionTable.open()`` already carries (#346).

    A stale contig list is the quietest possible failure here.  It is not a
    wrong *value* handed to a caller; it is a membership check answering "no"
    for a contig the current file has, so the read above it reports no data on
    a contig that is full of it, and nothing raises.
    """
    table = _build_tabix_table(tmp_path, ["chr1", "chr2"])

    table.open()
    assert table.get_chromosomes() == ["chr1", "chr2"]

    # same table object, different contigs underneath, and NO close()
    tabix_path = next(tmp_path.rglob("data.txt.gz"))
    setup_tabix(
        tabix_path,
        "#chrom  pos_begin  pos_end  c2\nchr7  10  12  3.14",
        seq_col=0, start_col=1, end_col=2, force=True)

    table.open()
    after = table.get_chromosomes()
    table.close()

    assert after == ["chr7"], (
        f"reopened table answered {after} -- the previous open's contig list, "
        f"not the current file's (gain#1173).")


def test_a_reopened_vcf_table_answers_its_contigs_from_the_current_file(
    tmp_path: pathlib.Path,
) -> None:
    """The VCF subclass reopens as its base does -- through a path of its own.

    ``VCFGenomicPositionTable.open()`` does not call
    ``TabixGenomicPositionTable.open()``; it establishes its own handle and
    parser and calls ``_build_chrom_mapping`` directly.  So an invalidation
    hung off the tabix ``open()`` would leave this backend -- the one whose
    ``get_chromosomes()`` it inherits -- answering out of the previous open's
    memo, and nothing in the tabix test above would notice.

    Borrows test_table_lifetime.py's mapped-VCF fixture, whose ``del_prefix``
    is what makes the answer checkable: reference space differs from the
    file's, so a stale answer cannot look right by accident.
    """
    table = _a_mapped_vcf_table(tmp_path)

    table.open()
    assert table.get_chromosomes() == ["1"]

    # setup_vcf has no overwrite switch; clear what it wrote so it can write
    # the replacement file (and its indexes) in place.
    for stale in tmp_path.glob("data*.gz*"):
        stale.unlink()
    setup_vcf(tmp_path / "data.vcf.gz", """
##fileformat=VCFv4.1
##INFO=<ID=A,Number=1,Type=Integer,Description="Score A">
##contig=<ID=chr7>
#CHROM POS ID REF ALT QUAL FILTER INFO
chr7   5   .  A   T   .    .      A=1
    """)

    table.open()
    after = table.get_chromosomes()
    table.close()

    assert after == ["7"], (
        f"reopened VCF table answered {after} -- the previous open's contig "
        f"list, not the current file's (gain#1173).")
