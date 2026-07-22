"""A closed and dropped position table must be collectable.

The repair path builds a table **per region task** --
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
import weakref

import pytest

from .test_backend_record_contract import _BACKENDS


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


@pytest.mark.parametrize("build,_score_line", _BACKENDS)
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


@pytest.mark.parametrize("build,_score_line", _BACKENDS)
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

    Per-instance memoisation is unaffected and is the intended replacement:
    ``cached_property`` stores into the instance ``__dict__`` and a plain
    attribute set in ``open()`` is not a wrapper at all, so neither is caught
    here.
    """
    from gain.genomic_resources.genomic_position_table.table import (
        GenomicPositionTable,
    )

    offenders = []
    for klass in [GenomicPositionTable, *_all_subclasses(GenomicPositionTable)]:
        for name, attr in vars(klass).items():
            if hasattr(attr, "cache_info"):
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
