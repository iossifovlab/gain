"""Bound a test process's heap so a runaway cannot OOM the host (#1366).

A single test that allocates without bound takes the whole machine with it.
On 2026-09-10 one grew at ~1GB/s to 121GB and was kernel-OOM-killed four
times in a morning, each time killing sshd and the developer's session with
it: a ``MagicMock()`` standing in for an fsspec filesystem handed
``yaml.safe_load`` a mock file object, and yaml's encoding sniffer looped on
``stream.read()`` because a mock read never returns an empty chunk.  Nothing
in the suite bounded it.

This arms ``RLIMIT_DATA`` so the offending allocation raises ``MemoryError``
at the line that made it, with a traceback and a nodeid, instead of the
kernel killing the process from the outside.  It is the memory counterpart of
the hang diagnostic in ``pytest.ini`` (ADR 0004): both turn a run that
destroys its own evidence into a named failing test.

The bound is re-armed before every test at ``VmData_now + budget``, so the
budget is what one *test* may add rather than an absolute ceiling. See
``docs/adr/0028-a-test-may-not-grow-the-heap-past-its-own-budget.md`` for why
``RLIMIT_DATA`` and not ``RLIMIT_AS`` or an RSS watchdog, and why a measured
budget beats a flat number.

Registered from ``pytest.ini``'s ``addopts`` (``-p tests.memory_guard``),
alongside the dask guard.
"""
import os
import resource
import sys

#: Budget in GiB that any one test may add to the memory already mapped when
#: it starts. ``0`` disables the guard entirely.
BUDGET_ENV_VAR = "GAIN_PYTEST_MEMORY_LIMIT_GB"

#: Ownership token, carrying the limit this guard last armed. rlimits are
#: inherited across ``fork``/``exec``, so an xdist worker starts life under
#: the *controller's* limit -- armed from the controller's much smaller
#: VmData, since it never imports the test modules. Without a way to tell
#: that bound apart from a deliberately stricter outer one, the worker
#: refuses to raise it and runs the whole session under a ceiling meant for
#: a process that does no work. Passing the value through the environment is
#: what lets a child recognise its parent's bound as this guard's own.
OWNED_ENV_VAR = "_GAIN_PYTEST_MEMORY_GUARD_ARMED"
DEFAULT_BUDGET_GB = 2.0

_GIB = 1 << 30

# Single-assignment holder (pylint reads a reassigned module-level name as a
# mis-cased constant), so tests can assert on what was actually armed rather
# than on the guard merely having run.
_ARMED: dict[str, int | None] = {"baseline": None, "limit": None}


def armed_limit() -> int | None:
    """Return the soft ``RLIMIT_DATA`` this guard set, or None if it did not."""
    return _ARMED["limit"]


def armed_baseline() -> int | None:
    """Return the ``VmData`` baseline the budget was measured from."""
    return _ARMED["baseline"]


def budget_bytes() -> int:
    """Return the configured budget in bytes; ``0`` means the guard is off."""
    raw = os.environ.get(BUDGET_ENV_VAR)
    gb = DEFAULT_BUDGET_GB if raw is None or raw == "" else float(raw)
    return 0 if gb <= 0 else int(gb * _GIB)


def read_vm_data_bytes() -> int | None:
    """Return this process's ``VmData``, or None where /proc does not say.

    ``VmData`` is the quantity ``RLIMIT_DATA`` bounds -- the data segment plus
    private anonymous mappings -- which is *not* resident memory.  Reading it
    here is what lets the budget be a budget rather than a guess.
    """
    try:
        with open("/proc/self/status", encoding="utf-8") as status:
            for line in status:
                if line.startswith("VmData:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def pytest_configure() -> None:
    """Arm a first bound covering collection.

    Declared without ``config``: pluggy passes a hook only the arguments its
    signature asks for, and this one needs none.
    """
    _arm()


def pytest_runtest_setup() -> None:
    """Re-measure and re-arm, so the budget is *per test*.

    ``VmData`` is a high-water mark that only ratchets: memory a test frees
    stays mapped in the allocator's arenas, and lazily-created thread pools
    are never given back.  Measured over ``core/tests/small`` at ``-n 5``, a
    worker starts at ~1450MB and ends at 3581-4074MB -- 2.1-2.6GB of growth
    that no single test is responsible for.  A budget measured once at
    startup is therefore a *lifetime* budget for the worker, and gets tighter
    every time the suite grows a test.

    Re-arming here makes the budget mean what it says: how much one test may
    add.  That is both the intent -- no unit test should need much memory --
    and the shape of the failure being guarded, which was a single test
    growing without bound.
    """
    _arm()


def _arm() -> None:
    budget = budget_bytes()
    if budget == 0:
        return
    if not sys.platform.startswith("linux"):
        # RLIMIT_DATA is not reliably enforced elsewhere (notably macOS).
        # CI and the developer boxes that OOM are Linux; the rest is a no-op
        # rather than a false sense of a bound.
        return

    baseline = read_vm_data_bytes()
    if baseline is None:
        return

    limit = baseline + budget
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_DATA)
    except (OSError, ValueError):
        return

    # Never raise an inherited bound: a lower one (a container, a cgroup, an
    # outer test run) is someone deliberately being stricter than us. A bound
    # this guard armed -- here or in the parent we were forked from -- is the
    # exception; moving that one along is the whole point of re-arming.
    if hard != resource.RLIM_INFINITY and hard < limit:
        return
    if (soft != resource.RLIM_INFINITY
            and soft < limit
            and not _is_our_own(soft)):
        return

    try:
        resource.setrlimit(resource.RLIMIT_DATA, (limit, hard))
    except (OSError, ValueError):
        # Best-effort by design; a kernel or sandbox that refuses the call
        # must not take the test run down with it.
        return

    _ARMED["baseline"] = baseline
    _ARMED["limit"] = limit
    os.environ[OWNED_ENV_VAR] = str(limit)


def _is_our_own(soft: int) -> bool:
    """Say whether ``soft`` is a bound this guard armed, here or in a parent."""
    if soft == _ARMED["limit"]:
        return True
    inherited = os.environ.get(OWNED_ENV_VAR)
    if inherited is None:
        return False
    try:
        return int(inherited) == soft
    except ValueError:
        return False
