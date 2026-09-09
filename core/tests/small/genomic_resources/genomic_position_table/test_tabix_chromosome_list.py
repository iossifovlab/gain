"""The tabix table's contig list is derived once per open, into ``chrom_order``.

The tabix family answers ``get_chromosomes()`` with a projection: every contig
the FILE has, in the file's order, mapped through :meth:`map_chromosome`, with
the ones a ``chrom_mapping`` does not cover dropped.  The base class instead
answers with the contig list a mapping names, in the mapping's own order.  Both
store their answer in ``chrom_order`` and both are read out of it by the single
base-class :meth:`GenomicPositionTable.get_chromosomes` -- since gain#1303,
which deleted the tabix override and the private list behind it.  Before that
the projection lived in a field of its own and ``chrom_order`` was built, and
released, and read by nobody on these two backends.

The list was on the annotation hot path when gain#1173 made it derive once per
open -- ``GenomicScore.get_all_chromosomes()`` delegates straight to it, and
every contig-membership check in the read path went through that, so a single
annotated substitution paid the whole rebuild three times over.

**No membership check reaches it any more.**  gain#1304 moved every screen onto
``has_chromosome``, whose per-open set derives from this list once.  What is
pinned below is unchanged and matters more for it: the set inherits whatever
staleness this list has, so "derived by the open, given up on close and on
reopen" is now a property the predicate rests on as well as a property of the
list.

What is pinned here is *where and when* the derivation happens, not that it is
fast: the work is counted, through :meth:`map_chromosome`, rather than timed.
A timing assertion would be the flakiest possible way to state it, and counting
says the stronger thing anyway -- the per-read cost does not scale with the
contig count because there is no per-read cost at all.

The list's CONTENT is mostly not this module's subject; the mapping tests in
test_genomic_position_table.py own that.  The exception is the
absent-mapped-contig fixture below.  The base class and this family enumerate
different things -- the mapping and the file respectively -- so telling which
of them ``chrom_order`` holds needs a configuration where the two answers
actually differ, and only a ``chrom_mapping: filename`` reaches one: a prefix
mapping is built by zipping over the file's contigs, so under it the two
coincide however either is written.  What is otherwise here is the list's
lifetime: derived by the open, given up on close, re-derived from the new
content on reopen.
"""
from __future__ import annotations

import pathlib
import textwrap

import pytest
import pytest_mock
from gain.genomic_resources.genomic_position_table import (
    build_genomic_position_table,
)
from gain.genomic_resources.genomic_position_table.table import (
    GenomicPositionTable,
)
from gain.genomic_resources.genomic_position_table.table_tabix import (
    TabixGenomicPositionTable,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_resource,
    convert_to_tab_separated,
    setup_directories,
    setup_tabix,
    setup_vcf,
)
from gain.genomic_resources.testing.builders import a_position_score

from .test_table_lifetime import _a_mapped_vcf_table

# The mapping both absent-contig fixtures are built on: ``kept`` onto a file
# contig the data has, ``empty`` onto one it does not.  Shared between them
# because they are one scenario asked of two backends -- edited apart, one of
# them would quietly stop being the case its docstring describes.  The same
# scenario is spelled out once more in test_inmemory_genomic_position_table.py,
# which pins the BASE class's half of the divergence (gain#509); that copy is
# deliberate rather than shared, since the point of it is that the two answers
# differ.
_MAPPING_NAMING_AN_ABSENT_CONTIG = convert_to_tab_separated("""
    chrom   file_chrom
    kept    chr1
    empty   chr99
""")


def _a_tabix_table_whose_mapping_names_an_absent_contig(
    tmp_path: pathlib.Path,
) -> TabixGenomicPositionTable:
    """A CLOSED tabix table whose ``chrom_mapping`` names a contig it lacks.

    A configuration in which the base class's ``chrom_order`` and the tabix
    projection disagree -- which takes a mapping FILE.
    ``_build_prefix_chrom_mapping`` zips over the file's own contigs, so an
    ``add_prefix``/``del_prefix`` mapping makes the two coincide whatever
    either of them does, and a table with no mapping at all makes them coincide
    twice over.  A mapping file is free to name a reference contig the data
    file has never heard of, and then the base lists it while a projection over
    the file's contigs drops it.

    Membership is the divergence used here because it is the one the gain#509
    empty-contig policy rides on.  It is not the only one: a mapping file also
    fixes its own row ORDER, which the base adopts and the projection ignores
    in favour of the file's -- so two tables can hold the same contigs and
    still disagree.

    Mirrors ``_empty_mapped_contig_table`` in
    test_inmemory_genomic_position_table.py -- deliberately the same shape and
    the same names, because that fixture's backend is the one whose answer must
    NOT change here (gain#509).
    """
    setup_directories(tmp_path, {
        "genomic_resource.yaml": """
            table:
                format: tabix
                filename: data.txt.gz
                chrom_mapping:
                    filename: chrom_map.txt
            scores:
            - id: c2
              name: c2
              type: float""",
        "chrom_map.txt": _MAPPING_NAMING_AN_ABSENT_CONTIG})
    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom pos_begin pos_end  c2
        chr1   10        12       3.14
        """, seq_col=0, start_col=1, end_col=2)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None
    table = build_genomic_position_table(res, res.config["table"])
    assert isinstance(table, TabixGenomicPositionTable)
    return table


def test_chrom_order_holds_the_list_the_tabix_table_answers_with(
    tmp_path: pathlib.Path,
) -> None:
    """``chrom_order`` IS the answer, rather than sitting beside it unread.

    On the tabix family ``chrom_order`` used to be built by every open and
    released by every close while nothing read it: the backend answered from a
    private list of its own, so the base class's field was live state that no
    caller could observe (gain#1303).  Storing the projection there is what
    makes the contract written on :meth:`GenomicPositionTable.close` -- that
    ``get_chromosomes`` refuses once ``chrom_order`` is released -- true of this
    backend rather than merely true of the two that never overrode the reader.

    The content assertion is not decoration: it is what keeps the equality
    honest.  Two fields agreeing on the WRONG list would satisfy the second
    assertion alone, and dropping the absent contig is the tabix answer this
    change must carry over unchanged.
    """
    table = _a_tabix_table_whose_mapping_names_an_absent_contig(tmp_path)

    with table:
        contigs = table.get_chromosomes()

        assert contigs == ["kept"], (
            "the tabix projection no longer drops a mapped contig the data "
            "file lacks; the equality below would be pinning the wrong list")
        assert table.chrom_order == contigs, (
            f"chrom_order is {table.chrom_order} where get_chromosomes() "
            f"answers {contigs}: the backend is answering from state other "
            f"than chrom_order (gain#1303)")


def _build_tabix_table(
    tmp_path: pathlib.Path, contigs: list[str],
) -> TabixGenomicPositionTable:
    """A CLOSED tabix table over one record on each of ``contigs``.

    Returned closed so the spy below can be installed BEFORE the open, and the
    contig mapping the open does is therefore counted.  Since gain#1303 that is
    where the whole derivation happens -- ``_build_chrom_mapping`` runs
    ``map_chromosome`` over every file contig -- so a fixture handing back an
    already-open table would count zero and silently turn the test below into a
    statement about nothing.

    It mattered before that change too, when the open genuinely did no mapping
    and the first read did it all: counting the open honestly is what let the
    same test say where the work moved to, rather than only how much of it
    there was.
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


def test_the_contig_list_is_not_the_file_contig_memo_itself(
    tmp_path: pathlib.Path,
) -> None:
    """Deriving into ``chrom_order`` must not alias the file-contig memo.

    With no ``chrom_mapping`` the projection is elementwise the identity, and
    the cheapest way to write that is to skip the pass and store what
    ``get_file_chromosomes()`` already returned -- which is exactly what the
    BASE class does, and it hands both accessors the same list object, so a
    caller mutating one silently rewrites the other.  The eager derivation this
    backend now pays per open (gain#1303) makes that shortcut tempting for
    precisely the tables where it costs most.

    Taking it would be a regression, not an optimisation: ``get_chromosomes()``
    returns the stored list by design -- the package ledger's "copy before
    mutating" rule -- so aliasing the memo turns one documented sharp edge into
    two coupled ones.  Asserted through behaviour rather than ``is not``,
    because what matters is that corrupting one answer does not corrupt the
    other, not which objects happen to be distinct.
    """
    table = _build_tabix_table(tmp_path, ["chr1", "chr2"])

    with table:
        table.get_chromosomes().append("chr99")

        assert table.get_file_chromosomes() == ["chr1", "chr2"], (
            "mutating the contig list also rewrote the file-contig memo: "
            "chrom_order is aliasing _file_chromosomes (gain#1303)")


def _a_vcf_table_whose_mapping_names_an_absent_contig(
    tmp_path: pathlib.Path,
) -> GenomicPositionTable:
    """The VCF twin of the fixture above, for the same reason.

    ``_a_mapped_vcf_table`` in test_table_lifetime.py maps by ``del_prefix``,
    and a prefix mapping zips over the file's own contigs -- so the base
    class's ``chrom_order`` and this family's projection agree, and a test
    built on it cannot tell which of them answered.  A mapping FILE can name a
    contig the data lacks, which separates them.
    """
    setup_directories(tmp_path, {
        "genomic_resource.yaml": textwrap.dedent("""
            tabix_table:
                filename: data.vcf.gz
                format: vcf_info
                chrom_mapping:
                    filename: chrom_map.txt
        """),
        "chrom_map.txt": _MAPPING_NAMING_AN_ABSENT_CONTIG})
    setup_vcf(tmp_path / "data.vcf.gz", textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=A,Number=1,Type=Integer,Description="Score A">
##contig=<ID=chr1>
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T   .    .      A=1
    """))
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None
    return build_genomic_position_table(res, res.config["tabix_table"])


def _build_tabix_table_over_two_contigs(
    tmp_path: pathlib.Path,
) -> TabixGenomicPositionTable:
    """``_build_tabix_table`` with its contigs fixed, to parametrize over."""
    return _build_tabix_table(tmp_path, ["chr1", "chr2"])


@pytest.mark.parametrize("n_contigs", [3, 30])
def test_the_contig_list_is_derived_by_the_open_and_not_by_the_reads(
    tmp_path: pathlib.Path, n_contigs: int, mocker: pytest_mock.MockerFixture,
) -> None:
    """The derivation costs one pass over the contigs per OPEN, not per read.

    ``map_chromosome`` is the per-contig work the derivation does, and on a
    position table it has no other in-tree caller -- so counting it counts
    exactly the derivation under test, and counts nothing else.  The table is
    built CLOSED and the spy installed before the open, which is what lets the
    open's own work be counted at all.

    Two contig counts, because the claim is about how the cost scales.  Both
    sizes answer every read with the same single pass paid by the open, so the
    *per read* cost is flat in the contig count rather than linear in it; when
    this regresses the two fail with counts proportional to their size, which
    is the scaling made visible in the failure rather than only in a benchmark.

    The open is asserted to do the full pass before the reads are asked about,
    which is what keeps this from passing vacuously: an implementation that
    stopped routing through ``map_chromosome`` altogether would count zero
    everywhere and satisfy a bare "the reads add nothing".
    """
    contigs = [f"chr{index}" for index in range(1, n_contigs + 1)]
    table = _build_tabix_table(tmp_path, contigs)
    spy = mocker.spy(table, "map_chromosome")

    with table:
        assert spy.call_count == n_contigs, (
            f"the open mapped {spy.call_count} contigs, not {n_contigs}: the "
            f"list is no longer derived by _build_chrom_mapping, so this test "
            f"would be counting something other than the derivation it is "
            f"about (gain#1303)")

        for _ in range(5):
            assert len(table.get_chromosomes()) == n_contigs

        assert spy.call_count == n_contigs, (
            f"{spy.call_count - n_contigs} further contig mappings across 5 "
            f"reads: the contig list is being re-derived per read rather than "
            f"once per open (gain#1173)")


def test_a_closed_table_refuses_the_contigs_it_derived_while_open(
    tmp_path: pathlib.Path,
) -> None:
    """A derived list is given up on close, not kept and served afterwards.

    The read is done through the OPEN table first, and that is the whole point
    of the test: it is what puts a derived list there to be given up.  A table
    closed without ever having been asked has nothing to release and refuses
    whatever ``close()`` does, which is the vacuous version of this.  What the
    refusal protects is what the base class's ``get_file_chromosomes`` memo
    protects (gain#358): an answer served out of released state is
    indistinguishable from a live one at the call site.

    Since gain#1303 the release is the base class's -- ``close()`` drops
    ``chrom_order``, which is where this backend's derived list now lives -- so
    the message is the base's too.  It is the same ``ValueError`` this has
    always raised, which is what the package ledger contracts; only the text
    moved.  The never-opened half of that -- the state this one cannot reach,
    because nothing has run to be released -- is pinned by the next test down.
    """
    table = _build_tabix_table(tmp_path, ["chr1", "chr2"])

    with table:
        assert table.get_chromosomes() == ["chr1", "chr2"]

    with pytest.raises(ValueError, match="genomic table not open"):
        table.get_chromosomes()


@pytest.mark.parametrize("build", [
    pytest.param(_build_tabix_table_over_two_contigs, id="tabix"),
    pytest.param(_a_mapped_vcf_table, id="vcf"),
])
def test_a_never_opened_table_refuses_its_contigs_as_the_base_class_does(
    build: object,
    tmp_path: pathlib.Path,
) -> None:
    """One refusal, from the base class, on the backends that used to have two.

    A table that was never opened has no ``chrom_order``, and that -- not a
    handle check of its own -- is now what refuses (gain#1303).  Both backends
    reach the same raise, where before this each answered from its own
    ``_load_file_chromosomes`` and so said "tabix table not open" and "vcf table
    not open" respectively.  The exception TYPE is unchanged and is what the
    package ledger contracts; the text is not, and moving it is what this issue
    bought the deletion with.

    The never-opened state, rather than the closed one, because it is the half
    that cannot be reached by releasing anything: nothing has run, so an
    implementation that refused only by giving state up in ``close()`` would let
    this through and hand back the FILE's contigs -- unmapped -- where an open
    table answers in reference space (gain#358).
    """
    table = build(tmp_path)  # type: ignore[operator]

    with pytest.raises(ValueError, match="genomic table not open"):
        table.get_chromosomes()


def test_a_reopened_tabix_table_answers_its_contigs_from_the_current_file(
    tmp_path: pathlib.Path,
) -> None:
    """open() must not serve the previous open's contig list.

    Reopened WITHOUT an intervening ``close()``, which is what makes this a
    test of ``open()`` -- and the reason the list is re-derived in
    ``_build_chrom_mapping`` rather than relying on ``close()`` alone.
    ``close()`` releases it too, but a caller is not required to have called
    it: this is the same invariant, and the same reasoning, as the buffer
    discard that ``TabixGenomicPositionTable.open()`` already carries
    (gain#346).

    A stale contig list is the quietest possible failure here.  It is not a
    wrong *value* handed to a caller; it is a membership check answering "no"
    for a contig the current file has, so the read above it reports no data on
    a contig that is full of it, and nothing raises.

    Uses the absent-mapped-contig fixture, and must: the base class rebuilds
    ``chrom_order`` on the way through every open too, and for a prefix mapping
    or no mapping its answer is elementwise the projection's, so a version of
    this test over an ordinary fixture passes whether or not THIS class
    re-derives anything -- measured, on the implementation this issue landed
    (gain#1303).  A mapping FILE is what separates the two answers, and only a
    fixture that separates them can fail.
    """
    table = _a_tabix_table_whose_mapping_names_an_absent_contig(tmp_path)

    table.open()
    assert table.get_chromosomes() == ["kept"]

    # Same table object, different contigs underneath, and NO close().  The
    # replacement carries the contig the mapping covers and the first file
    # lacked -- and drops the one it had -- so a stale answer and a live one
    # differ in both directions rather than by a single addition.
    setup_tabix(
        tmp_path / "data.txt.gz",
        "#chrom  pos_begin  pos_end  c2\nchr99  10  12  3.14",
        seq_col=0, start_col=1, end_col=2, force=True)

    table.open()
    after = table.get_chromosomes()
    table.close()

    assert after == ["empty"], (
        f"reopened table answered {after} -- not the current file's contigs "
        f"projected into reference space (gain#1173, gain#1303).")


def test_a_reopened_vcf_table_answers_its_contigs_from_the_current_file(
    tmp_path: pathlib.Path,
) -> None:
    """The VCF subclass reopens as its base does -- through a path of its own.

    ``VCFGenomicPositionTable.open()`` does not call
    ``TabixGenomicPositionTable.open()``; it establishes its own handle and
    parser and calls ``_build_chrom_mapping`` directly.  So a derivation hung
    off the tabix ``open()`` would leave this backend -- the one whose contig
    list it inherits -- answering out of the previous open's, and nothing in
    the tabix test above would notice.

    Uses a mapping FILE rather than test_table_lifetime.py's ``del_prefix``
    fixture, for the reason set out on
    ``_a_vcf_table_whose_mapping_names_an_absent_contig``: under a prefix
    mapping the base class's own rebuild of ``chrom_order`` agrees with this
    family's projection, so such a test passes without this class re-deriving
    anything and states nothing about the path it claims to cover.
    """
    table = _a_vcf_table_whose_mapping_names_an_absent_contig(tmp_path)

    table.open()
    assert table.get_chromosomes() == ["kept"]

    # setup_vcf has no overwrite switch; clear what it wrote so it can write
    # the replacement file (and its indexes) in place.
    for stale in tmp_path.glob("data*.gz*"):
        stale.unlink()
    setup_vcf(tmp_path / "data.vcf.gz", """
##fileformat=VCFv4.1
##INFO=<ID=A,Number=1,Type=Integer,Description="Score A">
##contig=<ID=chr99>
#CHROM POS ID REF ALT QUAL FILTER INFO
chr99  5   .  A   T   .    .      A=1
    """)

    table.open()
    after = table.get_chromosomes()
    table.close()

    assert after == ["empty"], (
        f"reopened VCF table answered {after} -- not the current file's "
        f"contigs projected into reference space (gain#1173, gain#1303).")
