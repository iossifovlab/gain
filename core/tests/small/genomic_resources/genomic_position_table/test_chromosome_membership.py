# pylint: disable=protected-access
"""Contig membership is a predicate, and it answers what the list answers.

``has_chromosome()`` is the yes/no half of ``get_chromosomes()``, split out
because that is all the annotation path ever wanted: every membership screen
in the read path asked ``chrom not in table.get_chromosomes()``, which is a
linear scan whose cost grows with the file's contig count AND with where the
contig sits in the order -- worst for a tail alt contig and worst of all for a
contig the table does not carry, which scans the whole list (gain#1304).

**What is pinned here is agreement, and it is the point.**  A table whose
predicate and whose list can disagree is the failure this change risks, and
it is the quietest possible one: a membership check answering "no" for a
contig the file has makes the read above it report no data on a contig full of
it, and nothing raises.  So the predicate is asked of every backend, against
that backend's own list -- not against a list the test wrote down.

Agreement is asked twice over, because the four-backend sweep alone cannot
see the way it is most likely to break: those fixtures configure no
``chrom_mapping``, so file space and reference space coincide and a predicate
built from the wrong accessor would agree on all four.  A mapped table, where
they differ, is what tells those two derivations apart.

The rest are the properties a memo has to earn: the answer costs no per-call
pass over the contigs and is held as a set rather than a list, a closed table
refuses it exactly as it refuses the list, and a reopened table answers out of
the current file rather than the previous open's set.  Modelled on
test_tabix_chromosome_list.py, which asks the same questions of the mapped
contig list this set is derived from.
"""
from __future__ import annotations

import pathlib

import pytest
import pytest_mock
from gain.genomic_resources.testing import setup_tabix, setup_vcf

from .test_backend_record_contract import build_every_backend
from .test_tabix_chromosome_list import _build_tabix_table
from .test_table_lifetime import _a_mapped_vcf_table

_BACKEND_IDS = ["inmemory", "tabix", "vcf", "bigwig"]


@pytest.mark.parametrize("backend_id", _BACKEND_IDS)
def test_the_predicate_answers_exactly_what_the_ordered_list_holds(
    tmp_path: pathlib.Path, backend_id: str,
) -> None:
    """Every contig the list holds is True; one it does not is False.

    Asked of all four backends because they do not derive the list the same
    way -- three return the stored ``chrom_order``, the tabix family maps and
    filters the file's contigs -- and a predicate that agreed with only some
    of them would be a backend-shaped hole in the read path.

    The list is read off the table rather than written down here, so this
    cannot pass by describing a fixture that changed.  It is asserted
    non-empty first: a backend whose contigs came back empty would satisfy the
    loop vacuously.
    """
    score = build_every_backend(tmp_path)[backend_id]

    with score.open():
        table = score.table
        contigs = table.get_chromosomes()

        assert contigs, f"{backend_id} fixture built a table with no contigs"
        for chrom in contigs:
            assert table.has_chromosome(chrom), (
                f"{chrom} is in {backend_id}'s contig list but the predicate "
                f"says it is not")
        assert not table.has_chromosome("not_a_contig")


@pytest.mark.parametrize("backend_id", _BACKEND_IDS)
def test_the_ordered_list_is_consulted_once_per_open_however_often_asked(
    tmp_path: pathlib.Path, backend_id: str,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """The derivation happens once per open, and answers out of a set.

    Two assertions, because neither alone says what gain#1304 asked for.

    Counting ``get_chromosomes()`` says the list is not re-derived per call
    -- but a memo holding the LIST would satisfy that and still walk it on
    every screen, staying O(contigs) and index-dependent.  So the second
    assertion names the memo and requires it to be a ``set``: that, not the
    call count, is what makes the answer flat in the contig count and in the
    contig's index.  It is white-box, deliberately -- the property is about
    the shape of what is held, and test_table_lifetime.py already pins the
    sibling memos by name for the same reason.

    Counted rather than timed either way: a timing is the shared box's to
    distort, and a structural fact is exact.

    The first call is asserted to consult the list before the repeats are
    asked about, which is what keeps this from passing vacuously: an
    implementation that never reached ``get_chromosomes()`` would count zero
    everywhere and satisfy a bare "no further calls" -- and would be the very
    implementation that can drift from the list.

    A MISS is asked repeatedly on purpose.  It is the case the old code was
    worst at, because ``not in`` over a list walks every contig before it can
    answer; if anything were still falling back to the list on the way to a
    "no", this is where it would show.
    """
    score = build_every_backend(tmp_path)[backend_id]

    with score.open():
        table = score.table
        contigs = table.get_chromosomes()
        spy = mocker.spy(table, "get_chromosomes")

        assert table.has_chromosome(contigs[0])
        assert spy.call_count == 1, (
            "the predicate no longer derives from get_chromosomes(); this "
            "test would be counting something other than the derivation it "
            "is about, and the predicate could drift from the list")
        held = table._chromosome_index
        assert isinstance(held, set), (
            f"membership is answered out of a {type(held).__name__}, not a "
            f"set -- the answer's cost would still grow with the contig "
            f"count and with the contig's index (gain#1304)")

        for _ in range(5):
            assert table.has_chromosome(contigs[0])
            assert not table.has_chromosome("not_a_contig")

        assert spy.call_count == 1, (
            f"{spy.call_count - 1} further passes over the contig list across "
            f"10 repeat screens: membership is being answered by walking the "
            f"ordered list rather than out of the per-open set (gain#1304)")


def test_a_mapped_table_screens_in_reference_space_not_the_files(
    tmp_path: pathlib.Path,
) -> None:
    """The set follows ``get_chromosomes()``, not the file's own contigs.

    The distinction the four-backend agreement test above cannot make: its
    fixtures configure no ``chrom_mapping``, so file space and reference
    space coincide and a predicate built from ``get_file_chromosomes()``
    would agree with the list on every one of them.

    Here they differ.  The mapped VCF fixture's ``del_prefix`` makes the
    file's ``chr1`` the reference's ``1``, so a predicate derived from the
    wrong accessor screens exactly backwards -- passing the file's name,
    which no caller uses, and refusing the reference name every read asks
    with.  That is a whole resource reading as empty, silently.
    """
    table = _a_mapped_vcf_table(tmp_path)

    with table:
        assert table.get_chromosomes() == ["1"]
        assert table.has_chromosome("1")
        assert not table.has_chromosome("chr1"), (
            "the predicate answered the FILE's contig name; it is derived "
            "from get_file_chromosomes() rather than from get_chromosomes(), "
            "so it disagrees with the list on every mapped table")


@pytest.mark.parametrize("backend_id", _BACKEND_IDS)
def test_a_closed_table_refuses_the_predicate_after_the_set_was_built(
    tmp_path: pathlib.Path, backend_id: str,
) -> None:
    """The set is given up on close, so a closed table refuses as it did.

    The predicate has to be asked through the OPEN table first, because that
    is what builds the set -- a table closed without ever having been asked
    refuses whether or not the set is released, and would pass this
    vacuously.  This is the same protection the two contig-list memos carry
    (gain#358, gain#1173): an answer served out of released state is
    indistinguishable from a live one at the call site, and here it would be
    a screen quietly passing a contig on a table that can no longer read it.

    The refusal is the LIST's, in each backend's own words, because that is
    what the set derives from -- which is why this asserts the type and not a
    message: the four backends word it differently, and this test's subject
    is that the predicate refuses at all, not which text it borrows.
    """
    score = build_every_backend(tmp_path)[backend_id]

    with score.open():
        table = score.table
        assert table.has_chromosome(table.get_chromosomes()[0])

    with pytest.raises(ValueError, match="not open"):
        table.has_chromosome("1")


def test_a_reopened_tabix_table_screens_against_the_current_file(
    tmp_path: pathlib.Path,
) -> None:
    """open() must not screen against the previous open's contigs.

    Reopened WITHOUT an intervening ``close()``, which is what makes this a
    test of ``open()`` -- and the reason the set is given up in
    ``_build_chrom_mapping`` rather than in ``close()`` alone.  ``close()``
    releases it too, but a caller is not required to have called it.

    The set is filled before the reopen, deliberately: a table that was never
    asked has nothing stale to serve, so without that first screen this would
    pass against an implementation with no invalidation at all.

    A stale set is the quietest failure the predicate has, and BOTH
    directions of it are checked here.  A contig the new file dropped still
    screening True lets a read be attempted on data that is gone; a contig
    the new file added screening False makes the read above report no data on
    a contig that is full of it, and nothing raises.

    The tabix family is where the invalidation is easiest to get wrong: its
    ``_build_chrom_mapping`` is an override with a memo of its own to release,
    and it reaches the base's -- the one seam that releases this set -- only
    by calling up into it.
    """
    table = _build_tabix_table(tmp_path, ["chr1", "chr2"])

    table.open()
    assert table.has_chromosome("chr1")
    assert not table.has_chromosome("chr7")

    # same table object, different contigs underneath, and NO close()
    tabix_path = next(tmp_path.rglob("data.txt.gz"))
    setup_tabix(
        tabix_path,
        "#chrom  pos_begin  pos_end  c2\nchr7  10  12  3.14",
        seq_col=0, start_col=1, end_col=2, force=True)

    table.open()
    gone, arrived = table.has_chromosome("chr1"), table.has_chromosome("chr7")
    table.close()

    assert (gone, arrived) == (False, True), (
        f"reopened table screened chr1={gone}, chr7={arrived} -- the previous "
        f"open's contigs, not the current file's (gain#1304).")


def test_a_reopened_vcf_table_screens_against_the_current_file(
    tmp_path: pathlib.Path,
) -> None:
    """The VCF subclass reopens as its base does -- through a path of its own.

    ``VCFGenomicPositionTable.open()`` does not call
    ``TabixGenomicPositionTable.open()``; it establishes its own handle and
    parser and calls ``_build_chrom_mapping`` directly.  So an invalidation
    hung off either of those ``open()`` methods would leave this backend
    screening out of the previous open's set, and the tabix test above would
    not notice.

    Borrows test_table_lifetime.py's mapped-VCF fixture, whose ``del_prefix``
    is what makes the answer checkable: reference space differs from the
    file's, so a stale answer cannot look right by accident.
    """
    table = _a_mapped_vcf_table(tmp_path)

    table.open()
    assert table.has_chromosome("1")
    assert not table.has_chromosome("7")

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
    gone, arrived = table.has_chromosome("1"), table.has_chromosome("7")
    table.close()

    assert (gone, arrived) == (False, True), (
        f"reopened VCF table screened 1={gone}, 7={arrived} -- the previous "
        f"open's contigs, not the current file's (gain#1304).")
