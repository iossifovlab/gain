# pylint: disable=W0621,C0116
"""``scripts/pypi_probe.sh`` decides whether a release tag is on PyPI.

``gain-release`` runs the script in its ``Pre-flight: PyPI`` stage. A
stub ``curl`` first on ``PATH`` answers each probe with a fixed HTTP
code, so the tests never reach the network.
"""
import os
import pathlib
import shutil
import stat
import subprocess
import textwrap

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "pypi_probe.sh"

TAG = "2026.10.2"
WHEELS_HOST = "wheels.example.org"

PYPI_URL = f"https://pypi.org/pypi/gain-core/{TAG}/json"
WHEEL_URL = f"https://{WHEELS_HOST}/gain/gain_core-{TAG}-py3-none-any.whl"

# The stub prints the HTTP code that the test assigns to the probed index,
# the way `curl -o /dev/null -w '%{http_code}'` does. The code "error"
# imitates a network failure: curl prints 000 and exits non-zero. Any URL
# other than PYPI_URL and WHEEL_URL gets 000 too, so a probe of a wrong URL
# never sees a 200 and the exit-1 and dry-run tests fail.
STUB_CURL = textwrap.dedent(f"""\
    #!/bin/sh
    for arg in "$@"; do url="$arg"; done
    case "$url" in
        "{PYPI_URL}") code="$STUB_PYPI_CODE" ;;
        "{WHEEL_URL}") code="$STUB_WHEEL_CODE" ;;
        *) printf '000'; echo "stub curl: unexpected URL $url" >&2; exit 99 ;;
    esac
    if [ "$code" = "error" ]; then
        printf '000'
        echo "curl: (6) Could not resolve host" >&2
        exit 6
    fi
    printf '%s' "$code"
""")


@pytest.fixture
def stub_bin(tmp_path: pathlib.Path) -> pathlib.Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(STUB_CURL)
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def run_probe(
    stub_bin: pathlib.Path,
    pypi_code: str,
    wheel_code: str,
    *,
    dry_run: str = "false",
    unset: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    if not SCRIPT.exists():
        pytest.fail(f"{SCRIPT} is missing; the CI image must copy scripts/")
    env = {
        "PATH": f"{stub_bin}{os.pathsep}{os.environ['PATH']}",
        "TAG_NAME": TAG,
        "WHEELS_HOST": WHEELS_HOST,
        "DRY_RUN": dry_run,
        "STUB_PYPI_CODE": pypi_code,
        "STUB_WHEEL_CODE": wheel_code,
    }
    for name in unset:
        del env[name]
    return subprocess.run(
        [shutil.which("bash") or "bash", str(SCRIPT)],
        env=env, capture_output=True, text=True, check=False)


def test_fully_published_release_exits_1(stub_bin: pathlib.Path) -> None:
    result = run_probe(stub_bin, "200", "200")

    assert result.returncode == 1
    assert f"gain-core {TAG} is already published" in result.stdout


def test_partial_publish_resumes(stub_bin: pathlib.Path) -> None:
    result = run_probe(stub_bin, "200", "404")

    assert result.returncode == 0
    assert "resumes a partial publish" in result.stdout


def test_release_missing_from_pypi_proceeds(stub_bin: pathlib.Path) -> None:
    result = run_probe(stub_bin, "404", "200")

    assert result.returncode == 0
    assert "already published" not in result.stdout


@pytest.mark.parametrize("pypi_code", ["503", "error"])
def test_pypi_failure_does_not_block_the_release(
    stub_bin: pathlib.Path, pypi_code: str,
) -> None:
    result = run_probe(stub_bin, pypi_code, "200")

    assert result.returncode == 0
    assert "already published" not in result.stdout


def test_dry_run_reports_a_published_release(stub_bin: pathlib.Path) -> None:
    result = run_probe(stub_bin, "200", "200", dry_run="true")

    assert result.returncode == 0
    report = [
        line for line in result.stdout.splitlines()
        if line.startswith("DRY RUN:")]
    assert len(report) == 1
    assert f"gain-core {TAG} is already published" in report[0]


@pytest.mark.parametrize("missing", ["TAG_NAME", "WHEELS_HOST"])
def test_missing_input_is_not_reported_as_published(
    stub_bin: pathlib.Path, missing: str,
) -> None:
    result = run_probe(stub_bin, "200", "200", unset=(missing,))

    assert result.returncode not in (0, 1)
    assert missing in result.stderr


JENKINSFILE = REPO_ROOT / "Jenkinsfile.release"


def stage_shell_script(stage: str) -> str:
    """Return the first ``script: '''...'''`` body after ``stage``."""
    text = JENKINSFILE.read_text()
    start = text.index(f"stage('{stage}')")
    body_start = text.index("script: '''", start) + len("script: '''")
    return text[body_start:text.index("'''", body_start)]


@pytest.mark.parametrize("probe_rc", [1, 2])
def test_stage_prints_the_probe_log_when_the_probe_fails(
    tmp_path: pathlib.Path, probe_rc: int,
) -> None:
    # The CI image has no Jenkinsfile.release: only a checkout runs this.
    if not JENKINSFILE.exists():
        pytest.skip(f"{JENKINSFILE} is not in this checkout")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "pypi_probe.sh").write_text(
        f"echo 'probe says hello'\nexit {probe_rc}\n")

    # A Jenkins `sh` step without a shebang runs `sh -xe`.
    result = subprocess.run(
        [shutil.which("sh") or "/bin/sh", "-xe", "-c",
         stage_shell_script("Pre-flight: PyPI")],
        cwd=tmp_path, capture_output=True, text=True, check=False)

    assert result.returncode == probe_rc
    assert "probe says hello" in result.stdout
