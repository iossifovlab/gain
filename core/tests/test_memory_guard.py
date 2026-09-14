# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Guard the runaway-allocation bound armed by ``tests.memory_guard`` (#1366).

A test that allocates without bound must fail as itself, with a traceback,
rather than have the kernel kill the process -- and the whole machine's other
work -- from the outside. Two things can quietly take that away: the plugin
not being armed at all, and the bound being armed somewhere ``pytest -n``
cannot carry a report back from. The tests here cover both, the second by
running a genuinely runaway test in a subprocess.

See ``docs/adr/0028-a-test-may-not-grow-the-heap-past-its-own-budget.md``.
"""
import os
import resource
import subprocess
import sys
import types
from pathlib import Path

import pytest

from tests import memory_guard

# Small so the inner runs trip in well under a second and never put the host
# under real memory pressure. The runaway allocates far faster than this.
INNER_BUDGET_GB = "0.25"

# Generous: it bounds interpreter start-up and collection, not the runaway.
# Reaching it means the bound failed to stop the child at all.
OUTER_TIMEOUT = 120.0

CORE_DIR = Path(__file__).resolve().parent.parent

# 16MiB at a time: fast enough to trip a 0.25GiB budget immediately, small
# enough that the failing allocation is the guard's, not a single absurd
# request the allocator would refuse anyway. Every probe below allocates
# through this one line, and one assertion looks for it in a report.
RUNAWAY_ALLOCATION = "chunks.append(bytearray(16 * 1024 * 1024))"

_RUNAWAY_LOOP = f"""\
    chunks = []
    while True:
        {RUNAWAY_ALLOCATION}
"""

RUNAWAY_TEST = "def test_allocates_without_bound():\n" + _RUNAWAY_LOOP + """\


def test_an_ordinary_test_after_it_still_passes():
    assert sum(range(1000)) == 499500
"""

# The same runaway in chunks under glibc's mmap threshold (128 KiB), so
# every allocation comes from the brk heap and the runaway fills it to the
# last byte before the bound refuses it. A 16 MiB chunk is mapped on its own
# and leaves up to 16 MiB of headroom under the ceiling when refused; this
# leaves none, which is the case the guard's own recovery has to survive.
NO_HEADROOM_RUNAWAY_TEST = RUNAWAY_TEST.replace(
    "bytearray(16 * 1024 * 1024)", "bytearray(64 * 1024)")

ARMED_PROBE = """\
import resource

from tests.memory_guard import armed_baseline, armed_limit


def test_the_limit_reaching_the_kernel_is_the_budgeted_one():
    limit = armed_limit()
    assert limit is not None
    assert limit == armed_baseline() + int(0.25 * (1 << 30))
    soft, _hard = resource.getrlimit(resource.RLIMIT_DATA)
    assert soft == limit
"""

DISABLED_PROBE = """\
from tests.memory_guard import armed_limit


def test_the_guard_did_not_arm():
    assert armed_limit() is None
"""

# Resident memory before and after a runaway, read from the tests around it.
# The runaway takes nearly the whole 0.25 GiB budget; "released" means the
# test after it sees the process back within 64 MB of where it was, not a
# budget above it. 16 MiB chunks are mapped individually, so a freed list is
# unmapped at once and the drop shows in RSS on every interpreter.
_RSS_PROBE_PRELUDE = """\
import pytest

from tests.memory_guard import read_proc_status_bytes


def _rss_mb():
    return read_proc_status_bytes("VmRSS") // (1 << 20)


def test_records_rss_before_the_runaway():
    with open("rss_before", "w", encoding="utf-8") as out:
        out.write(str(_rss_mb()))
"""

_RSS_PROBE_EPILOGUE = """\


def test_the_runaways_memory_was_released():
    with open("rss_before", encoding="utf-8") as recorded:
        before = int(recorded.read())
    after = _rss_mb()
    assert after < before + 64, (
        f"still resident: {before} MB before the runaway, {after} MB after")
"""

RELEASED_AFTER_RUNAWAY_PROBE = (
    _RSS_PROBE_PRELUDE
    + "\n\ndef test_allocates_without_bound():\n" + _RUNAWAY_LOOP
    + _RSS_PROBE_EPILOGUE)

# A fixture failure is cached for the fixture's whole scope and re-raised to
# every later user, so a session-scoped runaway is pinned for the rest of the
# process unless the guard lets go of it -- the one case where "a budget per
# runaway for the rest of the worker's life" is literally true.
RELEASED_AFTER_FIXTURE_RUNAWAY_PROBE = (
    _RSS_PROBE_PRELUDE
    + '\n\n@pytest.fixture(scope="session")\ndef broken():\n' + _RUNAWAY_LOOP
    + """\


def test_first_user_of_the_fixture(broken):
    pass


def test_second_user_of_the_fixture(broken):
    pass
""" + _RSS_PROBE_EPILOGUE)

# The runaway under ``--pdb``, with a debugger that records what the
# post-mortem was handed instead of prompting. Goes through pytest's real
# post-mortem entry, so it proves the release runs after the debugger.
PDB_SEES_THE_LOCALS_PROBE = """\
import pdb
import traceback


class RecordingPdb(pdb.Pdb):
    def interaction(self, frame, tb_or_exc):
        # 3.13+ post-mortems the exception itself; earlier, its traceback.
        tb = getattr(tb_or_exc, "__traceback__", tb_or_exc)
        deepest, _lineno = list(traceback.walk_tb(tb))[-1]
        print("POST_MORTEM_LOCALS=" + repr(sorted(deepest.f_locals)))


""" + RUNAWAY_TEST


def _run_inner(
    tmp_path: Path,
    body: str,
    *extra_args: str,
    budget_gb: str = INNER_BUDGET_GB,
) -> subprocess.CompletedProcess[str]:
    """Run ``body`` as a test file in a child pytest with the guard armed."""
    (tmp_path / "test_memory_probe.py").write_text(body)
    env = {
        **os.environ,
        "GAIN_PYTEST_MEMORY_LIMIT_GB": budget_gb,
        # The child is not rooted in `core`, so make the plugin importable
        # by the same dotted name `pytest.ini` uses.
        "PYTHONPATH": str(CORE_DIR),
        # The venv's entry-point plugins (anndata's, zarr's, django's...) cost
        # each child ~1.8s of imports and a numba/OpenBLAS thread pool, for
        # nothing the probes use. Anything a probe needs is named with -p.
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    argv = [
        sys.executable, "-m", "pytest",
        "-p", "no:cacheprovider",
        "-p", "tests.memory_guard",
        # Without this the summary arrives with ANSI colour interleaved
        # ("1 failed" and "1 passed" separately wrapped), and asserting on
        # the counts becomes a test of the terminal rather than the guard.
        "--color=no",
        *extra_args,
        "test_memory_probe.py",
    ]
    try:
        return subprocess.run(
            argv, cwd=tmp_path, capture_output=True, text=True,
            timeout=OUTER_TIMEOUT, check=False, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        # The bound failed to stop the child: exactly the bug this file
        # exists to catch. TimeoutExpired shows none of the child's output,
        # so surface it as a readable failure instead.
        msg = (
            f"the runaway test was not stopped within {OUTER_TIMEOUT}s\n"
            f"stdout: {exc.stdout!r}\n"
            f"stderr: {exc.stderr!r}"
        )
        raise AssertionError(msg) from exc


def test_the_guard_is_armed_for_this_very_run() -> None:
    """The suite running this is itself bounded, or nothing here means much."""
    limit = memory_guard.armed_limit()
    assert limit is not None, (
        "tests.memory_guard did not arm; check that pytest.ini's addopts "
        "still registers it and that GAIN_PYTEST_MEMORY_LIMIT_GB is not 0")
    soft, _hard = resource.getrlimit(resource.RLIMIT_DATA)
    assert soft == limit


def test_the_budget_is_measured_from_what_is_already_mapped() -> None:
    """The measured baseline is the point of the design -- see ADR 0028.

    A flat ceiling cannot work: importing numpy alone maps ~1.3GB of VmData
    on a 32-core box (one OpenBLAS thread per core, each reserving address
    space it never makes resident), so a flat 2GiB is mostly spent before the
    first test runs, and the amount spent varies with the core count of
    whatever machine the suite lands on.
    """
    baseline = memory_guard.armed_baseline()
    assert baseline is not None
    assert memory_guard.armed_limit() == baseline + memory_guard.budget_bytes()


def test_re_arming_moves_our_own_previous_bound_along(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-test re-arming is pointless if the guard refuses its own bound.

    The refusal that protects a stricter *inherited* limit must not also
    freeze the guard at the first value it ever set: VmData ratchets upward
    all run, so every later arming raises the previous one.
    """
    calls: list[int] = []
    readings = iter([1000, 5000])
    state = {"soft": resource.RLIM_INFINITY}

    def _set(_res: int, limits: tuple[int, int]) -> None:
        state["soft"] = limits[0]
        calls.append(limits[0])

    monkeypatch.setattr(
        memory_guard, "read_vm_data_bytes", lambda: next(readings))
    monkeypatch.setattr(memory_guard, "budget_bytes", lambda: 100)
    monkeypatch.setattr(
        memory_guard.resource,
        "getrlimit",
        lambda _res: (state["soft"], resource.RLIM_INFINITY))
    monkeypatch.setattr(memory_guard.resource, "setrlimit", _set)
    monkeypatch.setattr(
        memory_guard, "_ARMED", {"baseline": None, "limit": None})

    memory_guard._arm()
    memory_guard._arm()

    assert calls == [1100, 5100]


def test_a_bound_inherited_from_our_own_parent_is_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The xdist case, and the one a single-process run cannot show.

    A worker inherits the controller's soft limit, armed from the
    controller's much smaller VmData because it never imports the test
    modules. Treating that as a stricter outer bound leaves every worker --
    which is to say every process that actually runs a test -- pinned under a
    ceiling meant for a process that does no work.
    """
    calls: list[int] = []
    inherited_soft = 1500

    monkeypatch.setattr(memory_guard, "read_vm_data_bytes", lambda: 5000)
    monkeypatch.setattr(memory_guard, "budget_bytes", lambda: 100)
    monkeypatch.setattr(
        memory_guard.resource,
        "getrlimit",
        lambda _res: (inherited_soft, resource.RLIM_INFINITY))
    monkeypatch.setattr(
        memory_guard.resource,
        "setrlimit",
        lambda _res, limits: calls.append(limits[0]))
    # A fresh process: nothing armed here yet, only the parent's token.
    monkeypatch.setattr(
        memory_guard, "_ARMED", {"baseline": None, "limit": None})
    monkeypatch.setenv(memory_guard.OWNED_ENV_VAR, str(inherited_soft))

    memory_guard._arm()

    assert calls == [5100]


def test_a_stricter_bound_from_anyone_else_is_still_respected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The token must not turn into a licence to raise any limit at all."""
    calls: list[int] = []

    monkeypatch.setattr(memory_guard, "read_vm_data_bytes", lambda: 5000)
    monkeypatch.setattr(memory_guard, "budget_bytes", lambda: 100)
    monkeypatch.setattr(
        memory_guard.resource,
        "getrlimit",
        lambda _res: (1500, resource.RLIM_INFINITY))
    monkeypatch.setattr(
        memory_guard.resource,
        "setrlimit",
        lambda _res, limits: calls.append(limits[0]))
    monkeypatch.setattr(
        memory_guard, "_ARMED", {"baseline": None, "limit": None})
    # A token naming some *other* limit: this 1500 is not ours.
    monkeypatch.setenv(memory_guard.OWNED_ENV_VAR, "9999")

    memory_guard._arm()

    assert not calls, f"a foreign bound was raised: {calls}"


def test_the_default_budget_is_two_gib(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(memory_guard.BUDGET_ENV_VAR, raising=False)
    assert memory_guard.budget_bytes() == 2 * (1 << 30)


@pytest.mark.parametrize("value", ["0", "0.0"])
def test_zero_disables_the_budget(
    monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    monkeypatch.setenv(memory_guard.BUDGET_ENV_VAR, value)
    assert memory_guard.budget_bytes() == 0


def test_an_explicit_budget_is_honoured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(memory_guard.BUDGET_ENV_VAR, "4")
    assert memory_guard.budget_bytes() == 4 * (1 << 30)


@pytest.mark.parametrize(
    ("inherited", "branch"),
    [
        ((resource.RLIM_INFINITY, 1024), "hard"),
        ((1024, resource.RLIM_INFINITY), "soft"),
    ],
)
def test_a_lower_inherited_limit_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
    inherited: tuple[int, int],
    branch: str,
) -> None:
    """A stricter outer bound is someone being deliberate; never raise it.

    The two cases are separate because they are separate branches and the
    hard one is checked first: a single fixture with *both* limits lowered
    would return at the hard check and leave the soft check unproven.
    """
    calls: list[tuple[int, tuple[int, int]]] = []
    monkeypatch.setattr(
        memory_guard.resource, "getrlimit", lambda _res: inherited)
    monkeypatch.setattr(
        memory_guard.resource,
        "setrlimit",
        lambda res, limits: calls.append((res, limits)))
    monkeypatch.setattr(
        memory_guard, "_ARMED", {"baseline": None, "limit": None})

    memory_guard._arm()

    assert not calls, f"the guard raised a stricter inherited bound: {calls}"
    assert memory_guard.armed_limit() is None


def test_a_refused_setrlimit_does_not_take_the_run_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _refuse(_res: int, _limits: tuple[int, int]) -> None:
        raise OSError("not permitted here")

    monkeypatch.setattr(memory_guard.resource, "setrlimit", _refuse)
    monkeypatch.setattr(
        memory_guard, "_ARMED", {"baseline": None, "limit": None})

    memory_guard._arm()

    assert memory_guard.armed_limit() is None


def _run_away() -> None:
    """Raise a ``MemoryError`` from a frame with something in it to release."""
    hoard = bytearray(1)
    raise MemoryError(len(hoard))


def _memory_error_call() -> pytest.CallInfo[None]:
    return pytest.CallInfo.from_call(_run_away, when="call")


def _deepest_frame(call: pytest.CallInfo[None]) -> types.FrameType:
    assert call.excinfo is not None
    return call.excinfo.traceback[-1].frame.raw


def test_a_memory_error_widens_our_own_bound_by_a_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ceiling moves *before* the report is built, and without a read.

    After a runaway the process sits at the ceiling with the failing frame
    still pinned, and the next test's re-arm has to open ``/proc`` before it
    can raise anything -- which is exactly the allocation that fails there
    (#1449). So the widening must not measure; it adds a budget to the bound
    it already knows.
    """
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(memory_guard, "budget_bytes", lambda: 100)
    monkeypatch.setattr(
        memory_guard, "read_vm_data_bytes",
        lambda: pytest.fail("the widening must not read /proc"))
    monkeypatch.setattr(
        memory_guard.resource,
        "getrlimit",
        lambda _res: (5100, resource.RLIM_INFINITY))
    monkeypatch.setattr(
        memory_guard.resource,
        "setrlimit",
        lambda _res, limits: calls.append(limits))
    monkeypatch.setattr(
        memory_guard, "_ARMED", {"baseline": 5000, "limit": 5100})

    memory_guard.pytest_runtest_makereport(_memory_error_call())

    assert calls == [(5200, resource.RLIM_INFINITY)]
    assert memory_guard.armed_limit() == 5200
    assert memory_guard.armed_baseline() == 5100


def test_a_memory_error_under_a_foreign_bound_leaves_it_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(memory_guard, "budget_bytes", lambda: 100)
    monkeypatch.setattr(
        memory_guard.resource,
        "getrlimit",
        lambda _res: (1500, resource.RLIM_INFINITY))
    monkeypatch.setattr(
        memory_guard.resource,
        "setrlimit",
        lambda _res, limits: calls.append(limits))
    monkeypatch.setattr(
        memory_guard, "_ARMED", {"baseline": 5000, "limit": 5100})
    monkeypatch.delenv(memory_guard.OWNED_ENV_VAR, raising=False)

    memory_guard.pytest_runtest_makereport(_memory_error_call())

    assert not calls, f"a foreign bound was widened: {calls}"


def test_an_ordinary_failure_does_not_touch_the_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(memory_guard, "budget_bytes", lambda: 100)
    monkeypatch.setattr(
        memory_guard.resource,
        "getrlimit",
        lambda _res: (5100, resource.RLIM_INFINITY))
    monkeypatch.setattr(
        memory_guard.resource,
        "setrlimit",
        lambda _res, limits: calls.append(limits))
    monkeypatch.setattr(
        memory_guard, "_ARMED", {"baseline": 5000, "limit": 5100})

    def _raise() -> None:
        raise ValueError("not a memory error")

    memory_guard.pytest_runtest_makereport(
        pytest.CallInfo.from_call(_raise, when="call"))
    memory_guard.pytest_runtest_makereport(
        pytest.CallInfo.from_call(lambda: None, when="call"))

    assert not calls


def test_a_memory_error_has_its_frames_locals_released(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The traceback survives, source and line still render; the data does not.

    Reading ``f_locals`` here resyncs it, so this cannot see the 3.12 snapshot
    trap; the subprocess test reading ``VmRSS`` is what proves the release.
    """
    monkeypatch.setattr(memory_guard, "budget_bytes", lambda: 100)
    call = _memory_error_call()
    frame = _deepest_frame(call)
    assert "hoard" in frame.f_locals

    memory_guard.pytest_exception_interact(call)

    assert frame.f_locals == {}
    assert frame.f_code.co_name == "_run_away"


def test_an_ordinary_failure_keeps_its_frames_locals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_guard, "budget_bytes", lambda: 100)

    def _fail() -> None:
        evidence = "kept"
        raise ValueError(evidence)

    call = pytest.CallInfo.from_call(_fail, when="call")

    memory_guard.pytest_exception_interact(call)

    assert _deepest_frame(call).f_locals == {"evidence": "kept"}


def test_a_disabled_guard_releases_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``0`` switches the whole plugin off, this half of it included."""
    monkeypatch.setattr(memory_guard, "budget_bytes", lambda: 0)
    call = _memory_error_call()

    memory_guard.pytest_exception_interact(call)

    assert "hoard" in _deepest_frame(call).f_locals


def test_a_frame_that_refuses_to_be_cleared_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Best-effort: an executing frame in the chain must not take the run down.

    Re-raising an exception keeps its old traceback entries, so this test's
    own -- still executing -- frame ends up in the chain the guard walks,
    between the re-raise and the frame that first raised.
    """
    monkeypatch.setattr(memory_guard, "budget_bytes", lambda: 100)
    with pytest.raises(MemoryError) as caught:
        _run_away()
    runaway = caught.value

    def _reraise() -> None:
        raise runaway

    call = pytest.CallInfo.from_call(_reraise, when="call")
    assert call.excinfo is not None
    assert sys._getframe() in [e.frame.raw for e in call.excinfo.traceback]

    memory_guard.pytest_exception_interact(call)

    assert "runaway" in sys._getframe().f_locals
    # The refusal is per frame: the ones past it are still released.
    assert _deepest_frame(call).f_locals == {}


def test_the_budgeted_limit_is_what_reaches_the_kernel(tmp_path: Path) -> None:
    """Assert the value, not merely that the code ran."""
    result = _run_inner(tmp_path, ARMED_PROBE)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_env_var_zero_really_disarms_it(tmp_path: Path) -> None:
    result = _run_inner(tmp_path, DISABLED_PROBE, budget_gb="0")
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_runaway_test_fails_as_itself_instead_of_being_oom_killed(
    tmp_path: Path,
) -> None:
    result = _run_inner(tmp_path, RUNAWAY_TEST, "--tb=line")
    output = result.stdout + result.stderr

    assert result.returncode != 0, output
    # Named, and named as a MemoryError: the two things the kernel's OOM
    # kill destroys and this exists to restore.
    assert "test_allocates_without_bound" in output, output
    assert "MemoryError" in output, output
    # The run survives the one runaway rather than collapsing behind it.
    assert "1 failed, 1 passed" in output, output


def test_a_runaway_that_leaves_no_headroom_still_lets_the_next_test_run(
    tmp_path: Path,
) -> None:
    """The recovery must not depend on how much the runaway left unused.

    Seen on python-matrix #167 under 3.14 with the 16 MiB runaway above: the
    next test errored at setup with a bare ``MemoryError`` raised from the
    guard's own ``/proc/self/status`` read, because the re-arm has to
    allocate before it can raise the bound. Small chunks make that
    deterministic (#1449).
    """
    result = _run_inner(tmp_path, NO_HEADROOM_RUNAWAY_TEST, "--tb=line")
    output = result.stdout + result.stderr

    assert result.returncode != 0, output
    assert "test_allocates_without_bound" in output, output
    assert "MemoryError" in output, output
    assert "1 failed, 1 passed" in output, output


def test_a_runaway_test_under_xdist_is_caught_in_the_worker(
    tmp_path: Path,
) -> None:
    """Not redundant with the serial case -- it is the one CI runs.

    ``core`` runs under ``-n``, and a bound armed only in the controller
    would leave every process that actually executes tests unguarded.
    """
    result = _run_inner(
        tmp_path, RUNAWAY_TEST, "-p", "xdist", "-n", "2", "--tb=line")
    output = result.stdout + result.stderr

    assert result.returncode != 0, output
    assert "test_allocates_without_bound" in output, output
    assert "MemoryError" in output, output
    assert "1 failed, 1 passed" in output, output


def test_a_runaways_memory_is_released_once_it_is_reported(
    tmp_path: Path,
) -> None:
    """The report is the last thing that needs the runaway's data (#1451).

    Left alone, the failing frame's locals stay reachable through the
    traceback pytest keeps until the next test's call phase, and after that
    through a reference cycle the next full collection gets to -- tens of
    tests later, with the ceiling floating a budget higher meanwhile.
    """
    result = _run_inner(tmp_path, RELEASED_AFTER_RUNAWAY_PROBE, "--tb=line")
    output = result.stdout + result.stderr

    assert "test_allocates_without_bound" in output, output
    assert "1 failed, 2 passed" in output, output


def test_a_scoped_fixtures_runaway_is_released_and_still_reported(
    tmp_path: Path,
) -> None:
    """A cached fixture failure is the case that never self-heals.

    Releasing it must not cost the later users their report: the second
    test still errors as a ``MemoryError`` at the fixture's own line.

    Not a fence for the 3.12 ``f_locals`` resync: formatting that second
    report re-reads ``f_locals`` on the cleared frames, which resyncs them
    for free. The test-body probe above is the one that catches it.
    """
    result = _run_inner(
        tmp_path, RELEASED_AFTER_FIXTURE_RUNAWAY_PROBE, "--tb=short")
    output = result.stdout + result.stderr

    assert "ERROR at setup of test_second_user_of_the_fixture" in output, output
    assert output.count(RUNAWAY_ALLOCATION) >= 2, output
    assert "2 passed, 2 errors" in output, output


def test_the_debugger_still_gets_the_runaways_locals_under_pdb(
    tmp_path: Path,
) -> None:
    """``--pdb`` post-mortems the same frames; it must see them intact."""
    result = _run_inner(
        tmp_path, PDB_SEES_THE_LOCALS_PROBE,
        "--pdb", "--pdbcls=test_memory_probe:RecordingPdb")
    output = result.stdout + result.stderr

    assert "POST_MORTEM_LOCALS=['chunks']" in output, output
    assert "1 failed, 1 passed" in output, output
