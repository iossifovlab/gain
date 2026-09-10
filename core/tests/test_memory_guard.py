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

RUNAWAY_TEST = """\
def test_allocates_without_bound():
    chunks = []
    while True:
        # 16MiB at a time: fast enough to trip a 0.25GiB budget immediately,
        # small enough that the failing allocation is the guard's, not a
        # single absurd request the allocator would refuse anyway.
        chunks.append(bytearray(16 * 1024 * 1024))


def test_an_ordinary_test_after_it_still_passes():
    assert sum(range(1000)) == 499500
"""

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


def test_a_runaway_test_under_xdist_is_caught_in_the_worker(
    tmp_path: Path,
) -> None:
    """Not redundant with the serial case -- it is the one CI runs.

    ``core`` runs under ``-n``, and a bound armed only in the controller
    would leave every process that actually executes tests unguarded.
    """
    result = _run_inner(tmp_path, RUNAWAY_TEST, "-n", "2", "--tb=line")
    output = result.stdout + result.stderr

    assert result.returncode != 0, output
    assert "test_allocates_without_bound" in output, output
    assert "MemoryError" in output, output
    assert "1 failed, 1 passed" in output, output
