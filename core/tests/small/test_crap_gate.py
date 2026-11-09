# pylint: disable=W0621,C0116
"""``scripts/crap_gate.sh`` fails a build on a CRAP score above 10.

The core stage of the root ``Jenkinsfile`` runs the script after the
whole-package crapper call (#2123). A stub ``crapper`` first on ``PATH``
records its argv and writes the ``.metrics/crap.edn`` that the test
gives it, so the tests never score real code.
"""
import os
import pathlib
import shutil
import stat
import subprocess
import textwrap

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "crap_gate.sh"

# The stub crapper writes one line per argument. It copies STUB_EDN to
# the .metrics/crap.edn under the --root argument, as crapper does, and
# exits with STUB_CRAPPER_EXIT.
STUB_CRAPPER = textwrap.dedent("""\
    #!/bin/sh
    printf '%s\\n' "$@" > "$STUB_LOG_DIR/crapper.argv"
    root=
    previous=
    for arg in "$@"; do
        if [ "$previous" = --root ]; then
            root="$arg"
        fi
        previous="$arg"
    done
    mkdir -p "$root/.metrics"
    printf '%s' "$STUB_EDN" > "$root/.metrics/crap.edn"
    exit "$STUB_CRAPPER_EXIT"
""")


@pytest.fixture
def sandbox(tmp_path: pathlib.Path) -> pathlib.Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    crapper = bin_dir / "crapper"
    crapper.write_text(STUB_CRAPPER)
    crapper.chmod(crapper.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "logs").mkdir()
    (tmp_path / "core" / "gain").mkdir(parents=True)
    return tmp_path


def add_source(sandbox: pathlib.Path, *paths: str) -> None:
    """Create each repo-relative path under the sandbox."""
    for path in paths:
        target = sandbox / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def f():\n    return 1\n")


def edn(*rows: tuple[str, str, float]) -> str:
    """Render ``.metrics/crap.edn`` rows the way crapper writes them."""
    lines = [
        f'  {{:name "{name}", :namespace "{namespace}", '
        f":complexity 4, :coverage 50.0, :crap {crap}}}"
        for name, namespace, crap in rows]
    return "{:entries [\n" + "\n".join(lines) + "\n]}\n"


def run_gate(
    sandbox: pathlib.Path,
    changed: list[str] | None,
    *,
    crapper_exit: int = 0,
    crap_edn: str = "{:entries []}\n",
) -> subprocess.CompletedProcess[str]:
    if not SCRIPT.exists():
        pytest.fail(f"{SCRIPT} is missing; the CI image must copy scripts/")
    list_file = sandbox / "changed.txt"
    if changed is not None:
        list_file.write_text("".join(f"{path}\n" for path in changed))
    env = {
        "PATH": f"{sandbox / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "STUB_LOG_DIR": str(sandbox / "logs"),
        "STUB_CRAPPER_EXIT": str(crapper_exit),
        "STUB_EDN": crap_edn,
    }
    return subprocess.run(
        [shutil.which("bash") or "bash", str(SCRIPT),
         str(list_file), str(sandbox / "core")],
        cwd=sandbox,
        env=env, capture_output=True, text=True, check=False)


def crapper_called(sandbox: pathlib.Path) -> bool:
    return (sandbox / "logs" / "crapper.argv").exists()


def crapper_argv(sandbox: pathlib.Path) -> list[str]:
    return (sandbox / "logs" / "crapper.argv").read_text().splitlines()


def gate_lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [
        line for line in result.stdout.splitlines()
        if line.startswith("crap gate: ")]


def test_empty_list_passes_without_crapper(sandbox: pathlib.Path) -> None:
    result = run_gate(sandbox, [])

    assert result.returncode == 0
    assert gate_lines(result) == ["crap gate: pass, 0 changed files"]
    assert not crapper_called(sandbox)


def test_score_above_ten_fails_and_names_the_function(
    sandbox: pathlib.Path,
) -> None:
    add_source(sandbox, "core/gain/utils/variant_utils.py")
    crap_edn = edn(
        ("trim_str_left", "gain.utils.variant_utils", 4.5),
        ("maximally_extend_variant", "gain.utils.variant_utils", 10.036),
    )

    result = run_gate(
        sandbox, ["core/gain/utils/variant_utils.py"],
        crapper_exit=2, crap_edn=crap_edn)

    assert result.returncode == 1
    assert gate_lines(result) == [(
        "crap gate: fail, 1 changed files, 1 functions above 10.0: "
        "gain.utils.variant_utils maximally_extend_variant 10.036")]


def test_worst_score_of_exactly_ten_passes(sandbox: pathlib.Path) -> None:
    add_source(sandbox, "core/gain/utils/variant_utils.py")
    crap_edn = edn(
        ("maximally_extend_variant", "gain.utils.variant_utils", 10.0))

    result = run_gate(
        sandbox, ["core/gain/utils/variant_utils.py"],
        crapper_exit=0, crap_edn=crap_edn)

    assert result.returncode == 0
    assert gate_lines(result) == ["crap gate: pass, 1 changed files"]


def test_crapper_gets_the_threshold_and_the_root(
    sandbox: pathlib.Path,
) -> None:
    add_source(sandbox, "core/gain/utils/variant_utils.py")

    run_gate(sandbox, ["core/gain/utils/variant_utils.py"])

    argv = crapper_argv(sandbox)
    assert argv[argv.index("--threshold") + 1] == "10"
    assert argv[argv.index("--root") + 1] == str(sandbox / "core")
    assert "--use-existing-coverage" in argv


NOT_SCORED = [
    "core/gain/utils/deleted.py",
    "core/tests/small/utils/test_variant_utils.py",
    "core/gain/utils/test_helpers.py",
    "core/gain/utils/helpers_test.py",
    "core/gain/conftest.py",
    "core/gain/_version.py",
    "core/gain/templates/info.js",
    "web_api/web_annotation/views.py",
    "scripts/outside_core.py",
]


def test_files_that_crapper_must_not_score_give_count_zero(
    sandbox: pathlib.Path,
) -> None:
    add_source(sandbox, *[
        path for path in NOT_SCORED if path != "core/gain/utils/deleted.py"])

    result = run_gate(sandbox, NOT_SCORED)

    assert result.returncode == 0
    assert gate_lines(result) == ["crap gate: pass, 0 changed files"]
    assert not crapper_called(sandbox)


def test_only_existing_gain_sources_reach_crapper(
    sandbox: pathlib.Path,
) -> None:
    kept = [
        "core/gain/utils/variant_utils.py",
        "core/gain/genomic_resources/testing/builders.py",
    ]
    add_source(sandbox, *kept, *[
        path for path in NOT_SCORED if path != "core/gain/utils/deleted.py"])

    result = run_gate(sandbox, [*NOT_SCORED, *kept])

    paths = [
        arg for arg in crapper_argv(sandbox)
        if arg.endswith(".py")]
    assert paths == [str(sandbox / path) for path in kept]
    assert all(pathlib.Path(path).is_absolute() for path in paths)
    assert all(pathlib.Path(path).is_file() for path in paths)
    assert gate_lines(result) == ["crap gate: pass, 2 changed files"]


def test_missing_list_file_skips_the_gate(sandbox: pathlib.Path) -> None:
    result = run_gate(sandbox, None)

    assert result.returncode == 0
    [line] = gate_lines(result)
    assert line.startswith("crap gate: skipped, ")
    assert not crapper_called(sandbox)


@pytest.mark.parametrize("crapper_exit", [1, 127])
def test_crapper_fault_passes_and_prints_the_code(
    sandbox: pathlib.Path, crapper_exit: int,
) -> None:
    add_source(sandbox, "core/gain/utils/variant_utils.py")

    result = run_gate(
        sandbox, ["core/gain/utils/variant_utils.py"],
        crapper_exit=crapper_exit)

    assert result.returncode == 0
    assert gate_lines(result) == [
        f"crap gate: pass, crapper exited {crapper_exit}, a tool fault"]


def test_exit_two_with_no_function_above_ten_is_a_tool_fault(
    sandbox: pathlib.Path,
) -> None:
    add_source(sandbox, "core/gain/utils/variant_utils.py")
    crap_edn = edn(("f", "gain.utils.variant_utils", 10.0))

    result = run_gate(
        sandbox, ["core/gain/utils/variant_utils.py"],
        crapper_exit=2, crap_edn=crap_edn)

    assert result.returncode == 0
    assert gate_lines(result) == [(
        "crap gate: pass, crapper exited 2 with no function above 10.0, "
        "a tool fault")]


def test_fail_line_lists_each_function_above_ten_worst_first(
    sandbox: pathlib.Path,
) -> None:
    add_source(
        sandbox, "core/gain/binning/region.py", "core/gain/utils/regions.py")
    crap_edn = edn(
        ("add_interval_batch", "gain.binning.region.RegionCoverage", 10.0),
        ("collapse", "gain.utils.regions", 42.0),
        ("split", "gain.utils.regions", 12.5),
    )

    result = run_gate(
        sandbox,
        ["core/gain/binning/region.py", "core/gain/utils/regions.py"],
        crapper_exit=2, crap_edn=crap_edn)

    assert result.returncode == 1
    assert gate_lines(result) == [(
        "crap gate: fail, 2 changed files, 2 functions above 10.0: "
        "gain.utils.regions collapse 42.0, gain.utils.regions split 12.5")]


JENKINSFILE = REPO_ROOT / "Jenkinsfile"

WHOLE_PACKAGE_CALL = textwrap.dedent("""\
    crapper --use-existing-coverage --root ${crapRoot} \\\\
        \\$(find ${pkg} -name "*.py" ! -name _version.py | sort) \\\\
        > /reports/crap.txt
""")


@pytest.fixture(scope="module")
def jenkinsfile() -> str:
    # The CI images have no Jenkinsfile: only a checkout runs these tests.
    if not JENKINSFILE.exists():
        pytest.skip(f"{JENKINSFILE} is not in this checkout")
    return JENKINSFILE.read_text()


def between(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    return text[begin:text.index(end, begin + len(start))]


def dedented_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines()]


def crap_step(jenkinsfile: str) -> str:
    """The shell text of ``crapStep``, without its Groovy assignment."""
    opening = jenkinsfile.index('"""\n', jenkinsfile.index("String crapStep"))
    body = jenkinsfile[opening + len('"""\n'):]
    return body[:body.index('\n"""')]


def run_project_head(jenkinsfile: str) -> str:
    """The comments and defaults of ``runProject`` above ``crapStep``."""
    return between(jenkinsfile, "def runProject", "String crapStep")


def prepare_workspace(jenkinsfile: str) -> str:
    return between(
        jenkinsfile, "stage('Prepare workspace')", "stage('Detect change")


def test_whole_package_call_is_unchanged(jenkinsfile: str) -> None:
    step = "\n".join(dedented_lines(crap_step(jenkinsfile)))

    assert "\n".join(dedented_lines(WHOLE_PACKAGE_CALL)) in step


def test_crap_step_runs_the_gate_after_the_whole_package_call(
    jenkinsfile: str,
) -> None:
    step = crap_step(jenkinsfile)

    gate = step.index("/workspace/scripts/crap_gate.sh")
    assert gate > step.index("> /reports/crap.txt")


def test_gate_failure_sets_pytest_exit(jenkinsfile: str) -> None:
    step = crap_step(jenkinsfile)
    gate_call = step[step.index("/workspace/scripts/crap_gate.sh"):]
    gate_call = gate_call.split("\n")[1].strip()

    assert gate_call == (
        "/reports/crap_changed_files.txt ${crapRoot} || pytest_exit=1")


def test_crap_step_holds_no_apostrophe(jenkinsfile: str) -> None:
    assert "'" not in crap_step(jenkinsfile)
    assert "'" not in between(jenkinsfile, "// Keep this text free", "String")


def test_prepare_workspace_writes_the_changed_files(jenkinsfile: str) -> None:
    stage = prepare_workspace(jenkinsfile)

    assert "+refs/heads/master:refs/remotes/origin/master" in stage
    assert "git merge-base origin/master HEAD" in stage
    assert "git diff --name-only" in stage
    assert "reports/core/crap_changed_files.txt" in stage


def test_comments_no_longer_call_crap_report_only(jenkinsfile: str) -> None:
    core_stage = between(
        jenkinsfile, "stage('core')", "stage('demo_annotator')")
    head = " ".join(dedented_lines(run_project_head(jenkinsfile)))

    assert "report only" not in head
    assert "never reaches pytest_exit" not in head
    assert "report only" not in core_stage
