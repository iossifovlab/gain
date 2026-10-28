# pylint: disable=W0621,C0116
"""``scripts/mutator_nightly.sh`` runs mutator on ``gain.utils``.

The ``gain-mutator`` job runs the script every night (#2062). A stub
``mutator`` first on ``PATH`` and a stub interpreter in ``GAIN_PYTHON``
record their argv, so the tests run neither pytest nor mutator.
"""
import os
import pathlib
import shlex
import shutil
import stat
import subprocess
import textwrap

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "mutator_nightly.sh"

# The stub interpreter writes one line per argument. It exits with
# STUB_PYTHON_EXIT. On success it writes the LCOV report that the
# `--cov-report=lcov:<path>` argument names, as pytest-cov does, unless
# STUB_PYTHON_WRITE_LCOV is 0.
STUB_PYTHON = textwrap.dedent("""\
    #!/bin/sh
    printf '%s\\n' "$@" > "$STUB_LOG_DIR/python.argv"
    if [ "$STUB_PYTHON_EXIT" -ne 0 ]; then
        exit "$STUB_PYTHON_EXIT"
    fi
    if [ "$STUB_PYTHON_WRITE_LCOV" = 0 ]; then
        exit 0
    fi
    for arg in "$@"; do
        case "$arg" in
            --cov-report=lcov:*)
                lcov="${arg#--cov-report=lcov:}"
                mkdir -p "$(dirname "$lcov")"
                echo "SF:gain/utils/variant_utils.py" > "$lcov"
                ;;
        esac
    done
""")

# The stub mutator prints the lines of STUB_MUTATOR_REPORT, or one fixed
# line when the variable is empty.
STUB_MUTATOR = textwrap.dedent("""\
    #!/bin/sh
    printf '%s\\n' "$@" > "$STUB_LOG_DIR/mutator.argv"
    if [ -n "$STUB_MUTATOR_REPORT" ]; then
        printf '%s\\n' "$STUB_MUTATOR_REPORT"
    else
        echo "stub mutator report"
    fi
    exit "$STUB_MUTATOR_EXIT"
""")


def _write_stub(path: pathlib.Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def sandbox(tmp_path: pathlib.Path) -> pathlib.Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir / "mutator", STUB_MUTATOR)
    _write_stub(bin_dir / "stub-python", STUB_PYTHON)
    (tmp_path / "logs").mkdir()
    (tmp_path / "work").mkdir()
    return tmp_path


def run_script(
    sandbox: pathlib.Path,
    *,
    mutator_exit: int = 0,
    python_exit: int = 0,
    write_lcov: bool = True,
    report_lines: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    if not SCRIPT.exists():
        pytest.fail(f"{SCRIPT} is missing; the CI image must copy scripts/")
    env = {
        "PATH": f"{sandbox / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "GAIN_PYTHON": str(sandbox / "bin" / "stub-python"),
        "MUTATOR_MAX_WORKERS": "4",
        "MUTATOR_REPORT_DIR": str(sandbox / "reports"),
        "STUB_LOG_DIR": str(sandbox / "logs"),
        "STUB_MUTATOR_EXIT": str(mutator_exit),
        "STUB_MUTATOR_REPORT": "\n".join(report_lines or []),
        "STUB_PYTHON_EXIT": str(python_exit),
        "STUB_PYTHON_WRITE_LCOV": "1" if write_lcov else "0",
    }
    return subprocess.run(
        [shutil.which("bash") or "bash", str(SCRIPT)],
        cwd=sandbox / "work",
        env=env, capture_output=True, text=True, check=False)


def mutator_argv(sandbox: pathlib.Path) -> list[str]:
    return (sandbox / "logs" / "mutator.argv").read_text().splitlines()


def option_value(argv: list[str], option: str) -> str:
    return argv[argv.index(option) + 1]


def test_mutator_reads_the_existing_coverage(sandbox: pathlib.Path) -> None:
    run_script(sandbox)

    argv = mutator_argv(sandbox)
    assert "--use-existing-coverage" in argv
    assert option_value(argv, "--max-workers") == "4"


def test_test_command_runs_the_gain_interpreter(
    sandbox: pathlib.Path,
) -> None:
    run_script(sandbox)

    command = shlex.split(
        option_value(mutator_argv(sandbox), "--test-command"))
    assert command[:3] == [
        str(sandbox / "bin" / "stub-python"), "-m", "pytest"]
    assert "tests/small/utils" in command
    assert "-n" not in command


def test_mutator_mutates_only_gain_utils(sandbox: pathlib.Path) -> None:
    run_script(sandbox)

    argv = mutator_argv(sandbox)
    option_values = {
        option_value(argv, "--max-workers"),
        option_value(argv, "--test-command"),
    }
    positional = [
        arg for arg in argv
        if not arg.startswith("--") and arg not in option_values]
    assert positional == ["gain/utils"]


def test_coverage_step_writes_lcov_from_the_scoped_run(
    sandbox: pathlib.Path,
) -> None:
    run_script(sandbox)

    argv = (sandbox / "logs" / "python.argv").read_text().splitlines()
    assert argv[:2] == ["-m", "pytest"]
    assert "--cov-report=lcov:target/coverage/python/lcov.info" in argv
    assert argv[-1] == "tests/small/utils"


@pytest.mark.parametrize("mutator_exit", [0, 3])
def test_killed_or_surviving_mutants_succeed(
    sandbox: pathlib.Path, mutator_exit: int,
) -> None:
    result = run_script(sandbox, mutator_exit=mutator_exit)

    assert result.returncode == 0
    report = (sandbox / "reports" / "mutator.txt").read_text()
    assert "stub mutator report" in report


@pytest.mark.parametrize("mutator_exit", [2, 1])
def test_mutator_failure_exits_non_zero(
    sandbox: pathlib.Path, mutator_exit: int,
) -> None:
    result = run_script(sandbox, mutator_exit=mutator_exit)

    assert result.returncode == mutator_exit


def test_coverage_failure_skips_mutator(sandbox: pathlib.Path) -> None:
    result = run_script(sandbox, python_exit=1)

    assert result.returncode != 0
    assert not (sandbox / "logs" / "mutator.argv").exists()
    assert "the coverage step failed" in result.stdout


def test_coverage_without_lcov_skips_mutator(sandbox: pathlib.Path) -> None:
    result = run_script(sandbox, write_lcov=False)

    assert result.returncode == 1
    assert not (sandbox / "logs" / "mutator.argv").exists()
    assert "wrote no" in result.stdout


def summary_lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [
        line for line in result.stdout.splitlines()
        if line.startswith("mutator: exit ")]


def test_summary_line_counts_the_report_lines(sandbox: pathlib.Path) -> None:
    report = [
        "WARNING: a mutant timed out",
        "KILLED gain/utils/a.py:10 replace + with -",
        "KILLED gain/utils/a.py:11 replace < with <=",
        "SURVIVED gain/utils/a.py:12 replace == with !=",
        "KILLED gain/utils/b.py:20 negate the condition",
        "UNCOVERED gain/utils/b.py:30 replace 1 with 0",
        "UNCOVERED gain/utils/b.py:31 delete the call",
        "Total  286  68  81  435  80.8%",
    ]

    result = run_script(sandbox, mutator_exit=3, report_lines=report)

    [summary] = summary_lines(result)
    assert summary.startswith("mutator: exit 3, took ")
    assert summary.endswith(
        "killed 3, survived 1, uncovered 2, score 75.0%")
    assert result.returncode == 0


def test_summary_score_is_na_without_killed_or_survived(
    sandbox: pathlib.Path,
) -> None:
    report = [
        "UNCOVERED gain/utils/b.py:30 replace 1 with 0",
        "UNCOVERED gain/utils/b.py:31 delete the call",
    ]

    result = run_script(sandbox, report_lines=report)

    [summary] = summary_lines(result)
    assert summary.endswith(
        "killed 0, survived 0, uncovered 2, score n/a")
