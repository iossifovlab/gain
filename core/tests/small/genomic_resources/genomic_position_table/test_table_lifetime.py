"""What a position table may hold, while it is closed and after it is dropped.

Two halves of one question, and this file holds both.  **Dropped**: a closed
and dropped table must be collectable -- nothing may pin it (gain#345, below).
**Closed**: a table that is closed but deliberately kept alive must hold only
what ``open()`` cannot rebuild, so that what a caching holder retains is a
shell rather than a copy of the file (gain#350 --
test_a_closed_table_releases_what_open_established and the two
retention-shape tests are the release policy stated on
``GenomicPositionTable.close()``, checked against all four backends, plus two
chromosome-MAPPED fixtures without which the base class's ``chrom_map`` and
``rev_chrom_map`` are ``None`` throughout and every question asked of them is
answered vacuously).

And a shell that **refuses to be read**: what a closed table answers is the
other side of what it releases, so it is pinned here too --
test_a_closed_table_refuses_its_file_chromosomes holds all four backends to one
``ValueError``, and the header-only fixture beside it holds the guard to being
about the missing handle rather than about an empty contig list, which an open
table may legitimately have (gain#358).  The in-memory
``get_chromosome_length`` half of that contract is pinned where its
empty/unknown-contig policy already lives, in
test_inmemory_genomic_position_table.py.

The second half only matters because the first can be satisfied without it: a
table can be perfectly collectable and still be several megabytes that nobody
will ever read again, for as long as its holder lives.
``_INMEMORY_CNV_CACHE`` is exactly such a holder -- it keeps ``CnvCollection``
scores process-wide, while an annotation pipeline's teardown closes them.

**Why collectability is pinned at all.** The repair path builds a table
**per region task** --
``GenomicScoreImplementation._do_min_max`` and ``._do_histogram`` each call
``build_score_implementation_from_resource``, so a whole-genome
``grr_manage resource-repair`` opens and drops thousands of tables in one
process.  Nothing in that path caps how many may be alive at once, because
nothing needs to: each one is closed and dropped before the next is built.  So
the only thing keeping the process bounded is that a dropped table is actually
*collected*, and that is what this file pins.

It is easy to break from a long way away.  ``@cache``/``@lru_cache`` on any
method of a table is enough on its own: ``functools`` keeps the memo dict on the
class-level function object and keys it by the call arguments, ``self``
included, so the decorator alone is a strong reference to every instance the
method was ever called on, held for the life of the process and never evicted
(gain#345 -- ``get_file_chromosomes`` carried exactly that, and
``_build_chrom_mapping`` calls it from ``open()``, so *every* opened table was
pinned).  A class-level registry, a bound method handed to a module-level
callback, or a self-referencing closure retained past ``close()`` would all do
the same.

None of those announce themselves.  The leak has no failure mode at all until
the process runs long enough for it to matter, and then it surfaces as repair
being OOM-killed, a very long way from the decorator that caused it -- which is
why a test asserts collectability directly rather than waiting for a memory
budget somewhere to be exceeded.

Asked of **all four backends**, not just bigWig: the retention mechanism is a
property of the table base class and its decorators, so a backend is not
special until it is measured to be.  bigWig is merely where it hurt first,
because its instances retain a contig dict and an interval buffer, and it is
the backend whole-genome repair scans.
"""
from __future__ import annotations

import gc
import pathlib
import textwrap
import weakref
from collections.abc import Sized

import pytest
from gain.genomic_resources.genomic_position_table import (
    build_genomic_position_table,
)
from gain.genomic_resources.genomic_position_table.record import (
    PAYLOAD,
    POS_BEGIN,
)
from gain.genomic_resources.genomic_position_table.table import (
    GenomicPositionTable,
)
from gain.genomic_resources.genomic_scores import (
    GenomicScore,
    PositionScore,
    RecordScoreLine,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_resource,
    setup_bigwig,
    setup_directories,
    setup_vcf,
)
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_grr,
    a_position_score,
)

from .test_backend_record_contract import _BACKENDS, Backend


def _build_mapped_tabular(
    tmp_path: pathlib.Path, *, tabix: bool,
) -> Backend:
    """A tabular score whose file contigs are PREFIXED into reference space.

    The four backend fixtures this file borrows from
    test_backend_record_contract.py all configure no ``chrom_mapping``, and a
    table without one never builds ``chrom_map``/``rev_chrom_map`` at all --
    they are ``None`` before its open, after it and after its close.  Every
    question this file asks about the chromosome map is therefore answered
    vacuously by those four: the release policy passes because the field was
    never populated, and the reopen check passes because a map that was never
    built cannot fail to be rebuilt.

    ``add_prefix: chr`` over a file whose contig is ``1`` is the cheapest
    resource that populates both maps, and it makes the two failures visible.
    A closed table that kept them would be caught, and -- the sharper half -- a
    reopened table that failed to rebuild them would answer
    ``unmap_chromosome('chr1') -> 'chr1'`` against a file that has no ``chr1``,
    and return no records at all.
    """
    builder = (
        a_position_score()
        .with_score("s_float", "float")
        .with_chrom_mapping(add_prefix="chr")
        .with_data("""
            chrom  pos_begin  s_float
            1      10         0.5
        """)
    )
    if tabix:
        builder = builder.with_tabix()
    repo = a_grr().with_resource("pos", builder).build_repo(tmp_path)
    return PositionScore(repo.get_resource("pos")), ("chr1", 10, 10)


def _build_mapped_inmemory(tmp_path: pathlib.Path) -> Backend:
    return _build_mapped_tabular(tmp_path, tabix=False)


def _build_mapped_tabix(tmp_path: pathlib.Path) -> Backend:
    return _build_mapped_tabular(tmp_path, tabix=True)


# The four backends, plus the two chromosome-MAPPED tabular fixtures that make
# the base class's ``chrom_map``/``rev_chrom_map`` non-vacuous.  Kept here
# rather than in _BACKENDS: that list is "every backend in the tree", which the
# record-contract file it lives in reads as an exhaustiveness claim, and a
# mapped in-memory table is not a sixth backend.
_MAPPED_BACKENDS: list[pytest.param] = [  # type: ignore[valid-type]
    pytest.param(_build_mapped_inmemory, RecordScoreLine, id="inmemory-mapped"),
    pytest.param(_build_mapped_tabix, RecordScoreLine, id="tabix-mapped"),
]
_LIFETIME_BACKENDS: list[pytest.param] = [  # type: ignore[valid-type]
    *_BACKENDS, *_MAPPED_BACKENDS,
]


def _open_scan_close(build: object, tmp_path: pathlib.Path) -> weakref.ref:
    """Run one region task's worth of work, return a ref to its table.

    Deliberately the whole cycle a repair task performs -- build, open, scan,
    close -- and not merely construction: several of the ways a table gets
    pinned only fire on ``open()`` (``_build_chrom_mapping``) or on the first
    fetch, so a test that never opened one would miss them.
    """
    score, region = build(tmp_path)  # type: ignore[operator]
    chrom, pos_begin, pos_end = region
    with score.open():
        # exercise the fetch path: some retention is only established once a
        # backend has parsed and buffered something
        list(score.fetch_region(chrom, pos_begin, pos_end, None))
    ref = weakref.ref(score.table)
    del score
    return ref


@pytest.mark.parametrize("build,_score_line", _LIFETIME_BACKENDS)
def test_a_closed_and_dropped_table_is_collected(
    build: object,
    _score_line: object,
    tmp_path: pathlib.Path,
) -> None:
    """No table may outlive the score that owned it.

    The assertion is on the **weakref**, not on any memory figure: a byte
    threshold would be flaky and would only catch a leak once it was already
    large, whereas one surviving instance is already the bug -- the repair path
    creates them without bound, so a leak of one per task and a leak of ten
    thousand are the same defect caught at different times.
    """
    ref = _open_scan_close(build, tmp_path)
    gc.collect()

    assert ref() is None, (
        f"{type(ref()).__name__} survived close() and being dropped -- "
        f"something holds a strong reference to it. A whole-genome "
        f"resource-repair builds one table per region task, so anything "
        f"retaining them grows without bound (gain#345)."
    )


@pytest.mark.parametrize("build,_score_line", _LIFETIME_BACKENDS)
def test_tables_do_not_accumulate_over_repeated_open_close_cycles(
    build: object,
    _score_line: object,
    tmp_path: pathlib.Path,
) -> None:
    """The repair shape itself: many tables in sequence, none accumulating.

    The single-instance test above is the sharper statement, but it can be
    satisfied by a cache that happens to evict the one entry it holds.  This
    one asks the question repair actually asks -- does the *n*-th task leave
    anything behind -- and so also catches a bounded-but-large cache, which
    would still be several thousand live tables deep into a whole-genome run.
    """
    # a directory per round: the builders realize a resource on disk, and
    # rebuilding into one that is already populated fails
    refs = [
        _open_scan_close(build, tmp_path / f"round{i}")
        for i in range(5)
    ]
    gc.collect()

    alive = [r for r in refs if r() is not None]
    assert not alive, (
        f"{len(alive)} of {len(refs)} tables survived their open/close "
        f"cycle; repair would retain one per region task (gain#345)."
    )


def test_no_table_method_is_memoised_at_class_level() -> None:
    """Ban the specific decorator that caused gain#345, by name.

    The two tests above catch the *effect*, but only for the four backends
    they instantiate and only along the code path they exercise.  This one
    catches the cause on any method of any table class, including one whose
    call site a test never reaches -- ``functools`` marks every memoised
    wrapper with a ``cache_info`` attribute, so the ban is checkable directly.

    ``staticmethod``/``classmethod`` are unwrapped before the check because
    the descriptor hides the wrapper's attributes: neither keys on ``self``
    and so neither reproduces gain#345 on its own, but
    ``@staticmethod @lru_cache def f(table)`` takes a table as its argument
    and pins it exactly as the original did.

    Per-instance memoisation is unaffected and is the intended replacement:
    ``cached_property`` stores into the instance ``__dict__`` and a plain
    attribute set in ``open()`` is not a wrapper at all, so neither is caught
    here.
    """
    # __subclasses__ only sees classes whose module has been imported, so the
    # backends are imported by name rather than left to whatever an earlier
    # import happened to pull in.  Without this the ban silently becomes
    # vacuous for exactly the case it exists to catch: a new backend in a
    # module no test in this file touches.
    from gain.genomic_resources.genomic_position_table import (  # noqa: F401
        table_bigwig,
        table_inmemory,
        table_tabix,
        table_vcf,
    )
    from gain.genomic_resources.genomic_position_table.table import (
        GenomicPositionTable,
    )

    offenders = []
    for klass in [GenomicPositionTable, *_all_subclasses(GenomicPositionTable)]:
        for name, attr in vars(klass).items():
            target = getattr(attr, "__func__", attr)
            if hasattr(target, "cache_info"):
                offenders.append(f"{klass.__name__}.{name}")

    assert not offenders, (
        f"class-level memoisation on table method(s): {offenders}. "
        f"functools keeps the memo on the class and keys it by self, so this "
        f"pins every instance the method is called on for the life of the "
        f"process (gain#345). Memoise per instance instead -- cached_property, "
        f"or an attribute computed in open()."
    )


def _all_subclasses(klass: type) -> list[type]:
    subs = list(klass.__subclasses__())
    return subs + [s for sub in subs for s in _all_subclasses(sub)]


# The fields a CLOSED table is still allowed to hold, each with the reason it
# is exempt.  This list is the release policy stated on
# ``GenomicPositionTable.close()``, written out as data: everything else a
# table picks up between construction and close is file-derived and must be
# released.
#
# It is an ALLOW-LIST on purpose.  A field added to a backend's ``open()`` or
# fetch path without a matching release fails
# test_a_closed_table_releases_what_open_established, and the only way to make
# that pass without releasing it is to name it here with a reason -- which is a
# decision recorded in a diff, rather than a field that quietly joined what a
# closed table retains.
_MAY_SURVIVE_CLOSE = {
    "header": (
        "the column names -- a *configured* parameter for header_mode 'list' "
        "(set in __init__, never rebuilt by open()) and for the VCF backend, "
        "which reads its INFO metadata at construction; releasing it would "
        "make those two unreopenable. Bounded by the column count."
    ),
    "chrom_key": "core column key: resolved from the definition and header",
    "pos_begin_key": "core column key: resolved from the definition and header",
    "pos_end_key": "core column key: resolved from the definition and header",
    "ref_key": "core column key: resolved from the definition and header",
    "alt_key": "core column key: resolved from the definition and header",
    "definition": (
        "the table's own definition -- configuration, handed in at "
        "construction and never read from the file. get_column_key writes a "
        "resolved column_index back into it, which is why it shows up as "
        "changed at all. Bounded by the config."
    ),
    "_last_call": (
        "the previous query's (chrom, pos_begin, pos_end) -- three values "
        "describing a CALL, not file content, and the tabix read cascade's "
        "own cursor. Bounded at three."
    ),
    "_last_pos": (
        "the previous query's start position -- one int, describing a call "
        "rather than the file, which only routes the next bigWig fetch "
        "between the buffered and direct strategies. Bounded at one."
    ),
}


def _is_released(value: object, before_open: object) -> bool:
    """Whether a field the open/read established has been given up by close().

    Released means one of: ``None``; an empty container; or back to the value
    the table was constructed with -- the three shapes the backends' releases
    actually take (``self.parser = None``, ``self.records_by_chr = {}``,
    ``self._buffer_region = Region("?", -1, -1)``).  What they have in common
    is that the field no longer holds anything read out of the file.
    """
    if value is None:
        return True
    if _held(value) == 0:
        return True
    return value == before_open


def _held(value: object) -> int | None:
    """How many things a field holds, or ``None`` if it is not a container.

    ``len()`` and nothing cleverer, which is what makes this answerable for
    *anything* a backend might hold: a list, a dict, a ``Counter``, a ``Box``
    -- and a table's own helper objects, which define ``__len__`` precisely
    because they are collections (``LineBuffer`` is a deque of records).  A
    str/bytes is not a container of records and is excluded, as is anything
    whose ``len()`` refuses.
    """
    if isinstance(value, str | bytes) or not isinstance(value, Sized):
        return None
    try:
        return len(value)
    except TypeError:  # pragma: no cover - a Sized that will not measure
        return None


@pytest.mark.parametrize("build,_score_line", _LIFETIME_BACKENDS)
def test_a_closed_table_releases_what_open_established(
    build: object,
    _score_line: object,
    tmp_path: pathlib.Path,
) -> None:
    """The release policy, asked of every backend at once.

    A closed table holds only what ``open()`` does not rebuild -- its
    resource, its definition and its configured parameters -- and the check is
    the field NOBODY thought about: a new backend field, or a new one on the
    base class, arrives and is caught here on the day it is added, with
    ``_MAY_SURVIVE_CLOSE`` as the only escape and a written reason as its cost.

    Catching that field takes **two** questions, because a table acquires what
    it holds in two ways and either one alone is a hole big enough to drive the
    whole payload through:

    * what the open **rebound** -- the difference between the constructed
      table's attributes and the opened one's -- must be released.  This is the
      question about handles and parsers, which are replaced wholesale.
    * what the closed table still **holds** -- every attribute that is a
      container -- must be empty.  This is the question about payload, and it
      is the one that does not care *how* the payload arrived: a dict filled by
      ``update()`` is the same object it was at construction, so the rebinding
      diff never sees it, and it is precisely how a contig dict or a fetch
      buffer grows.

    And the table is **read** before it is closed, not merely opened -- twice
    over the same region.  The bigWig interval buffer, the tabix line buffer
    and the read cascade's counters are all populated by a fetch and by nothing
    before it, so an open-then-close test inspects them while they are still
    empty and finds them released whatever ``close()`` does or does not do.
    The repeat is what reaches the *buffered* fetch strategies: a bigWig table
    routes on the distance from the previous query and starts far enough back
    to force the first fetch down the direct path, which never touches the
    buffer at all (see the reopen test for the same rule stated as routing).

    This is also what makes the base class's own chromosome state -- the
    ``get_file_chromosomes`` memo, ``chrom_order`` and the maps
    ``_build_chrom_mapping`` builds -- everyone's problem rather than nobody's:
    it is established by ``open()`` on all four backends, so all four fail
    here if it survives a close.  The two ``-mapped`` fixtures are what make
    ``chrom_map``/``rev_chrom_map`` part of that: without a configured
    ``chrom_mapping`` both stay ``None`` from construction to close, and the
    policy holds for them vacuously.
    """
    score, region = build(tmp_path)  # type: ignore[operator]
    table = score.table
    before = dict(vars(table))

    table.open()
    # READ, don't just open, and read TWICE: the buffers and counters below
    # exist only once a fetch has run through them, and the buffered fetch
    # strategies only once a second, nearby query has been asked.
    assert list(table.get_records_in_region(*region)), (
        f"the fixture region yields no records from a "
        f"{type(table).__name__}: the fetch-path state this test exists to "
        f"inspect was never established")
    list(table.get_records_in_region(*region))

    established = {
        name: value for name, value in vars(table).items()
        if name not in before or before[name] is not value
    }
    # guard against a vacuous pass: the base class's chromosome state is
    # established by every backend's open(), so it must be under test here
    assert {"chrom_order", "_file_chromosomes"} <= set(established), (
        f"{type(table).__name__}.open() no longer establishes the base "
        f"class's chromosome state; this test would be checking less than it "
        f"claims: {sorted(established)}")

    table.close()

    rebound_but_kept = {
        name for name, value in vars(table).items()
        if name in established and not _is_released(value, before.get(name))
    }
    still_holding = {
        name: _held(value) for name, value in vars(table).items()
        if _held(value)
    }
    retained = sorted(
        (rebound_but_kept | still_holding.keys()) - set(_MAY_SURVIVE_CLOSE))
    sizes = {name: still_holding[name]
             for name in retained if name in still_holding}
    assert not retained, (
        f"{type(table).__name__}.close() left {retained} holding what the "
        f"open and the read took out of the file (sizes: {sizes}). "
        f"A closed table keeps only its resource, its definition and its "
        f"configured parameters -- release these in close(), or add them to "
        f"_MAY_SURVIVE_CLOSE with the reason they are exempt (gain#350)."
    )


@pytest.mark.parametrize("build,_score_line", _MAPPED_BACKENDS)
def test_the_mapped_fixtures_really_build_a_chromosome_map(
    build: object,
    _score_line: object,
    tmp_path: pathlib.Path,
) -> None:
    """Keep the ``-mapped`` fixtures from decaying into ordinary ones.

    Everything the two tests above claim about ``chrom_map``/``rev_chrom_map``
    rests on these fixtures actually configuring a ``chrom_mapping``: a table
    with none never builds either map, and both tests then pass for those
    fields without checking anything at all.  That is exactly the hole the
    ``-mapped`` fixtures were added to close, and it would reopen silently --
    a builder rename, a dropped ``with_chrom_mapping`` -- so the premise is
    asserted rather than assumed.
    """
    score, region = build(tmp_path)  # type: ignore[operator]
    table = score.table
    with table:
        assert table.chrom_map, "fixture builds no chromosome map"
        assert table.rev_chrom_map, "fixture builds no reverse chromosome map"
        # and the mapping is the one that MATTERS: reference space differs
        # from the file's, so a lost map cannot answer by accident
        assert table.unmap_chromosome(region[0]) != region[0]


def _read_region(
    score: GenomicScore, region: tuple[str, int, int],
) -> tuple[list[tuple], list]:
    """Read one region twice over: as records, and as score values.

    Only the five DECODED record slots are compared, not the payload: a
    payload is the backend's own object -- a ``pysam.TupleProxy``, a
    ``(VariantRecord, allele index)`` pair -- and two reads of the same file
    hand back two different such objects, which compare unequal (or, for the
    proxies, not at all).  What the payload MEANS is instead compared through
    ``fetch_region``, which is the read the score layer actually performs:
    it resolves each score value out of the payload, so identical values are
    identical payload reads.
    """
    chrom, pos_begin, pos_end = region
    records = [
        record[:PAYLOAD]
        for record in score.table.get_records_in_region(
            chrom, pos_begin, pos_end)
    ]
    values = list(score.fetch_region(chrom, pos_begin, pos_end, None))
    return records, values


@pytest.mark.parametrize("build,_score_line", _LIFETIME_BACKENDS)
def test_a_reopened_table_answers_exactly_as_before_it_was_closed(
    build: object,
    _score_line: object,
    tmp_path: pathlib.Path,
) -> None:
    """Releasing state on close() must cost a reopened table nothing.

    The other half of the release policy, and the half that makes it safe: a
    closed table is a table that has given up everything it read, so the only
    thing standing between it and being *wrong* on reopen is that ``open()``
    rebuilds all of it.  Asked as open -> read -> close -> open -> read on
    every backend, comparing the two reads, because the failure this guards
    against is silent -- a field released but not rebuilt does not raise, it
    answers with less (an empty contig list yields no records at all, a
    half-rebuilt chromosome map drops the records of the contigs it lost).
    """
    score, region = build(tmp_path)  # type: ignore[operator]
    score.open()
    before = _read_region(score, region)
    assert before[0], "the fixture region yields no records: nothing compared"
    score.close()

    score.open()
    after = _read_region(score, region)
    score.close()

    assert after == before, (
        f"{type(score.table).__name__} answered differently after a close/"
        f"reopen cycle: {after} != {before}. open() must rebuild everything "
        f"close() releases (gain#350).")


def _a_mapped_vcf_table(tmp_path: pathlib.Path) -> GenomicPositionTable:
    """A ``vcf_info`` table whose file contig ``chr1`` maps to ``1``.

    The mapping is what makes the answer *checkable*: without one, the file's
    contigs and the reference-space names a caller is handed are the same
    strings, and a table answering from the file rather than from the mapping it
    no longer has looks identical to one answering correctly.
    """
    setup_directories(tmp_path, {"genomic_resource.yaml": textwrap.dedent("""
        tabix_table:
            filename: data.vcf.gz
            format: vcf_info
            chrom_mapping:
                del_prefix: chr
    """)})
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


def test_a_closed_vcf_table_does_not_answer_its_contigs_from_the_file(
    tmp_path: pathlib.Path,
) -> None:
    """Releasing the chromosome state must not make a closed VCF table LIE.

    ``get_chromosomes()`` on the tabix family goes through the
    ``get_file_chromosomes`` memo and maps each contig into reference space --
    and ``close()`` now releases both the memo and the map, so a closed table
    has to reach for the file to answer at all.  Every other backend's
    ``_load_file_chromosomes`` reads the open handle and so refuses (with a
    ``ValueError``, in all four since gain#358 -- the bigWig one asserted, and
    the in-memory one did not refuse at all); the VCF one opened the
    resource's ``.vcf.gz`` *itself*, which is the one implementation that can
    still produce an answer after a close -- an answer with no chromosome map
    left to map it through, so ``map_chromosome`` passes the FILE's contigs
    back unmapped.

    ``['chr1']`` where an open table says ``['1']``: wrong data with no error
    to notice it by, plus a file (or network) open on a table the caller
    believes is closed.  A closed table refuses instead, exactly as its three
    siblings do -- what a closed table does when read is not this issue's to
    change, but answering *differently from every other backend, and wrongly*
    is (gain#350).
    """
    table = _a_mapped_vcf_table(tmp_path)

    table.open()
    assert table.get_chromosomes() == ["1"]
    table.close()

    with pytest.raises(ValueError, match="not open"):
        table.get_chromosomes()


def test_a_never_opened_vcf_table_does_not_answer_its_contigs_either(
    tmp_path: pathlib.Path,
) -> None:
    """The other state that has no handle, and the same refusal.

    ``VCFGenomicPositionTable._load_file_chromosomes`` guards
    ``self.pysam_file``, which is ``None`` both AFTER a close and BEFORE the
    first open -- so refusing covers the never-opened table too.  That is a
    change from when this backend re-opened the resource itself and was the one
    implementation able to answer its contigs with no open table at all, and
    only the after-close half of it was pinned (gain#350, gain#358).

    Worth a test of its own because the two states reach the guard by different
    routes: a closed table has been through ``close()``, which released the
    chromosome map the answer would have been mapped through, while a
    never-opened one never built one -- and a guard written against the
    released state alone (an emptied map, an emptied memo) would let this one
    past and hand back the FILE's contigs where an open table hands back
    reference-space names.
    """
    table = _a_mapped_vcf_table(tmp_path)

    with pytest.raises(ValueError, match="vcf table not open"):
        table.get_file_chromosomes()


@pytest.mark.parametrize("build,_score_line", _BACKENDS)
def test_a_closed_table_refuses_its_file_chromosomes(
    build: object,
    _score_line: object,
    tmp_path: pathlib.Path,
) -> None:
    """One answer to a closed ``get_file_chromosomes()``, from all four.

    What a closed table does when read is the contract gain#358 settles: it
    **refuses** the reads that depend on what ``open()`` took out of the file,
    and the file's own contigs are the first of them.  Three backends already
    refused, each guarding the handle ``open()`` establishes and ``close()``
    drops.  The in-memory one instead handed back the scanned-contigs list that
    its ``close()`` empties -- ``[]``, which
    :meth:`GenomicPositionTable.get_file_chromosomes` then memoised as the
    answer for the rest of the table's life, and which a caller cannot tell
    from an open table over a file with no records.

    Asked of every backend at once so that a future one cannot quietly
    diverge, and asked of the memoising **public** method rather than the
    ``_load_file_chromosomes`` hook behind it, because that is the read a
    caller performs -- and caching the wrong answer is half of what made the
    in-memory divergence hard to see.
    """
    score, _region = build(tmp_path)  # type: ignore[operator]
    table = score.table

    table.open()
    assert table.get_file_chromosomes(), (
        f"the fixture yields no file contigs from a {type(table).__name__}: "
        f"a closed table refusing them would prove nothing")
    table.close()

    with pytest.raises(ValueError, match="not open"):
        table.get_file_chromosomes()


def _a_header_only_inmemory_table(
    tmp_path: pathlib.Path,
) -> GenomicPositionTable:
    """An in-memory table over a file with a header and no data rows."""
    builder = (
        a_position_score()
        .with_score("s_float", "float")
        .with_data("chrom  pos_begin  s_float\n")
    )
    repo = a_grr().with_resource("pos", builder).build_repo(tmp_path)
    return PositionScore(repo.get_resource("pos")).table


def test_an_open_inmemory_table_with_no_data_rows_answers_no_contigs(
    tmp_path: pathlib.Path,
) -> None:
    """An empty contig list is an ANSWER, and only a closed table refuses.

    The trap in the test above's fix, stated as its own case.  The in-memory
    backend's contigs are scanned off the data rows, so a file with a header
    and nothing under it legitimately has none -- and a guard written as "the
    scanned list is empty" would refuse this open table, which is a live read
    path (a resource repaired before its data is written, a chrom_mapping that
    covers no file contig).  Emptiness describes two states at once; the open
    handle describes exactly one, which is why it is what
    ``_load_file_chromosomes`` keys on (gain#358).
    """
    table = _a_header_only_inmemory_table(tmp_path)

    with table:
        assert table.get_file_chromosomes() == []
        assert table.get_chromosomes() == []


def _an_inmemory_score(
    tmp_path: pathlib.Path, n_records: int,
) -> PositionScore:
    """Build an in-memory position score over ``n_records`` rows."""
    rows = "\n".join(
        f"1  {pos}  0.5" for pos in range(1, 10 * n_records + 1, 10))
    builder = (
        a_position_score()
        .with_score("s_float", "float")
        .with_data(f"chrom  pos_begin  s_float\n{rows}")
    )
    repo = a_grr().with_resource("pos", builder).build_repo(tmp_path)
    return PositionScore(repo.get_resource("pos"))


@pytest.mark.parametrize("n_records", [10, 2000])
def test_a_closed_inmemory_table_retains_no_records(
    n_records: int,
    tmp_path: pathlib.Path,
) -> None:
    """What a closed in-memory table costs must not scale with its file.

    The in-memory backend loads the WHOLE file into ``records_by_chr``, so
    until it releases them a closed table costs one live record tuple per row
    of the file -- and that is not merely untidy, because closed scores are
    deliberately kept alive: ``_INMEMORY_CNV_CACHE`` holds ``CnvCollection``
    scores process-wide while an annotation pipeline's teardown closes them,
    so a cached-and-closed collection pinned every record of its file for the
    life of the process.

    Parametrised over two file sizes two orders of magnitude apart, and
    asserting the same number for both, because the claim is about the
    *shape* of the retention (constant, not proportional) rather than about
    any one file.  ``open()`` rebuilds ``records_by_chr`` from the raw file
    regardless, so the retained copy was never read.
    """
    score = _an_inmemory_score(tmp_path, n_records)
    with score.open():
        assert len(list(score.table.get_all_records())) == n_records
    table = score.table

    retained = sum(len(recs) for recs in table.records_by_chr.values())
    assert retained == 0, (
        f"a closed in-memory table still holds {retained} of its "
        f"{n_records} records; open() rebuilds them from the file, so the "
        f"retention buys nothing and a cached closed score pins the whole "
        f"file (gain#350)")


def test_an_inmemory_scan_in_flight_when_close_lands_raises(
    tmp_path: pathlib.Path,
) -> None:
    """A full scan straddling close() must fail loudly, not come back short.

    The same hazard the bigWig fetch loop guards against, arriving at the
    in-memory backend by the same route: ``close()`` now empties
    ``records_by_chr``, and ``get_all_records`` re-reads that dict once per
    contig -- *after* ``get_chromosomes()`` has been evaluated into the
    for-loop's own list, which the release cannot reach.  So a scan interrupted
    by a close resumes over a contig list it still has and a record store that
    is now empty, and runs to a clean end: a shorter result set that is
    indistinguishable from a complete one at the call site.

    Consuming a scan lazily outside the block that opened the table is easy to
    write by accident -- ``gen = score.fetch_region(...)`` inside a ``with``,
    ``list(gen)`` after it -- and the in-memory backend is the one deliberately
    shared past a close: ``_INMEMORY_CNV_CACHE`` hands the same score to every
    holder in the process, so one pipeline's teardown can close it while
    another scan is mid-flight.  For an annotation read a silently truncated
    scan is wrong data rather than an error (gain#350).
    """
    builder = (
        a_position_score()
        .with_score("s_float", "float")
        .with_data("""
            chrom  pos_begin  s_float
            1      10         0.5
            1      20         0.5
            2      10         0.5
            2      20         0.5
        """)
    )
    repo = a_grr().with_resource("pos", builder).build_repo(tmp_path)
    table = PositionScore(repo.get_resource("pos")).table

    table.open()
    assert len(list(table.get_all_records())) == 4

    records = table.get_all_records()
    # consume contig 1 entirely, so the scan is suspended between contigs --
    # exactly where the next records_by_chr lookup happens
    assert [next(records)[POS_BEGIN], next(records)[POS_BEGIN]] == [10, 20]

    table.close()

    with pytest.raises(AssertionError, match="in flight"):
        list(records)


def _a_bigwig_score(
    tmp_path: pathlib.Path, n_contigs: int,
) -> PositionScore:
    """Build a bigWig position score over ``n_contigs`` contigs."""
    contigs = [f"chr{i}" for i in range(1, n_contigs + 1)]
    builder = (
        a_bigwig_score()
        .with_score("score", "float")
        .with_data("\n".join(f"{chrom}  0  10  0.5" for chrom in contigs))
        .with_chrom_lens(dict.fromkeys(contigs, 1000))
    )
    repo = a_grr().with_resource("bw", builder).build_repo(tmp_path)
    return PositionScore(repo.get_resource("bw"))


@pytest.mark.parametrize("n_contigs", [1, 200])
def test_a_closed_bigwig_table_retains_no_contigs(
    n_contigs: int,
    tmp_path: pathlib.Path,
) -> None:
    """What a closed bigWig table costs must not scale with its file either.

    ``BigWigTable.chroms`` is the file's whole contig dictionary -- one entry
    per contig, which on a real hg38 track is ~600 of them, and on an
    assembly with unplaced scaffolds a good deal more.  ``open()`` reads it
    back off the handle unconditionally, so a closed table that keeps it is
    holding a copy nothing will ever read.

    Parametrised over two contig counts for the same reason as the in-memory
    test above: the claim is that the retention is constant rather than
    proportional to the file, which one file size cannot state.
    """
    score = _a_bigwig_score(tmp_path, n_contigs)
    with score.open():
        assert len(score.table.get_chromosomes()) == n_contigs
    table = score.table

    assert len(table.chroms) == 0, (
        f"a closed bigWig table still holds {len(table.chroms)} contigs; "
        f"open() reads the contig dict off the handle every time, and every "
        f"reader of it is already behind an `assert self._bw_file is not "
        f"None` (gain#350)")


def test_a_bigwig_fetch_in_flight_when_close_lands_raises(
    tmp_path: pathlib.Path,
) -> None:
    """A fetch straddling close() must fail loudly, not come back short.

    Consuming a fetch lazily outside the block that opened the table is easy to
    write by accident -- ``gen = score.fetch_region(...)`` inside a ``with``,
    ``list(gen)`` after it -- and the buffered bigWig path resumes from a
    buffer, so it does not have to touch the closed file handle to keep going.
    Whatever it does then, it must not be *silently* short: a truncated score
    list is indistinguishable from a complete one at the call site, and for an
    annotation read that is wrong data rather than an error.

    This is a live risk of the gain#345 fix specifically.  ``close()`` now
    discards the buffer, and the fetch loop's guard was the buffer itself, so
    an interrupted scan stopped looking exhausted-and-correct rather than
    hitting the file and raising.  The handle, not the buffer, is what says the
    table is still usable.
    """
    chrom_lens = {"chr1": 1000}
    data = "\n".join(f"chr1  {i * 10}  {i * 10 + 10}  0.5" for i in range(50))
    builder = (
        a_bigwig_score()
        .with_score("score", "float")
        .with_data(data)
        .with_chrom_lens(chrom_lens)
    )
    repo = a_grr().with_resource("bw", builder).build_repo(tmp_path)
    table = PositionScore(repo.get_resource("bw")).table

    table.open()
    # the first fetch takes _fetch_direct, which never touches the buffer;
    # warming it -- so that the interrupted fetch below is the buffered one --
    # takes a second, nearby query (see the reopen test for the routing rule)
    list(table.get_records_in_region("chr1", 1, 20))

    records = table.get_records_in_region("chr1", 21, 500)
    next(records)
    assert table._buffer, "buffer not warm: the fetch would not be resumable"

    table.close()

    with pytest.raises(AssertionError, match="in flight"):
        list(records)


def test_a_reopened_bigwig_table_does_not_answer_from_the_old_buffer(
    tmp_path: pathlib.Path,
) -> None:
    """open() must not serve the previous open's buffered values.

    ``BigWigTable`` buffers fetched intervals and keys that buffer by
    **region**, not by file or by open handle -- and a buffer hit never falls
    through to the file.  So a table reopened over changed data answered any
    query landing inside the retained span from the old data, silently and
    with no error to attach a report to.

    Driven against the **table**, and reopening WITHOUT an intervening
    ``close()``, which is what makes this a test of ``open()``.  Routed through
    ``GenomicScore`` instead it would prove nothing about ``open()`` at all:
    ``score.open()`` early-returns on an already-open score, so the only way to
    reach a second open is via the ``with`` block's ``close()`` -- and
    ``close()`` discards the buffer too, for memory. That variant passes with
    the ``open()`` discard removed, which is exactly why it is not the test
    written here.
    """
    chrom_lens = {"chr1": 1000}
    builder = (
        a_bigwig_score()
        .with_score("score", "float")
        .with_data("chr1  0  100  0.11")
        .with_chrom_lens(chrom_lens)
    )
    repo = a_grr().with_resource("bw", builder).build_repo(tmp_path)
    table = PositionScore(repo.get_resource("bw")).table

    def values_at(begin: int, end: int) -> list[float]:
        return [
            rec[5][3]
            for rec in table.get_records_in_region("chr1", begin, end)
        ]

    table.open()
    # The FIRST fetch takes _fetch_direct, which never touches _buffer:
    # get_records_in_region routes on `pos_begin - _last_pos`, and _last_pos
    # starts below -use_buffered_threshold precisely to force that.  So warming
    # the buffer -- what this test is about -- takes a second, nearby query.
    assert values_at(5, 10) == [pytest.approx(0.11)]
    assert values_at(20, 30) == [pytest.approx(0.11)]
    assert table._buffer, "buffer not warm: the test would prove nothing"

    # same table object, different data underneath, and NO close()
    setup_bigwig(next(tmp_path.rglob("*.bw")), "chr1  0  100  0.99",
                 chrom_lens)

    table.open()
    after = values_at(20, 30)
    table.close()

    assert after == [pytest.approx(0.99)], (
        f"reopened table served {after} -- the previous open's buffered "
        f"intervals, not the current file (gain#345)."
    )
