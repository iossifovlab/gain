# pylint: disable=W0621,C0116
"""``scripts/anaconda_token_probe.sh`` checks the Anaconda.org token.

``gain-release`` runs the script in its ``Pre-flight: credentials``
stage. A stub ``curl`` first on ``PATH`` answers the probe with a fixed
HTTP code, so the tests never reach the network.
"""
import os
import pathlib
import shutil
import stat
import subprocess
import textwrap

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "anaconda_token_probe.sh"

FAKE_TOKEN = "fake-token-not-a-secret"  # ruff: ignore[hardcoded-password-string]

# The stub records its arguments, one per line, in STUB_ARGS_FILE and
# prints STUB_CODE the way `curl -o /dev/null -w '%{http_code}'` does.
# The code "error" imitates a network failure: curl prints 000 and
# exits 6.
STUB_CURL = textwrap.dedent("""\
    #!/bin/sh
    for arg in "$@"; do printf '%s\\n' "$arg" >> "$STUB_ARGS_FILE"; done
    if [ "$STUB_CODE" = "error" ]; then
        printf '000'
        echo "curl: (6) Could not resolve host" >&2
        exit 6
    fi
    printf '%s' "$STUB_CODE"
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
    code: str,
    *,
    token: str | None = FAKE_TOKEN,
) -> subprocess.CompletedProcess[str]:
    if not SCRIPT.exists():
        pytest.fail(f"{SCRIPT} is missing; the CI image must copy scripts/")
    env = {
        "PATH": f"{stub_bin}{os.pathsep}{os.environ['PATH']}",
        "STUB_CODE": code,
        "STUB_ARGS_FILE": str(stub_bin / "args"),
    }
    if token is not None:
        env["ANACONDA_TOKEN"] = token
    return subprocess.run(
        [shutil.which("bash") or "bash", str(SCRIPT)],
        env=env, capture_output=True, text=True, check=False)


def test_accepted_token_exits_0(stub_bin: pathlib.Path) -> None:
    result = run_probe(stub_bin, "200")

    assert result.returncode == 0
    assert "Anaconda token probe: HTTP 200" in result.stdout


@pytest.mark.parametrize("code", ["401", "403"])
def test_rejected_token_names_the_jenkins_credential(
    stub_bin: pathlib.Path, code: str,
) -> None:
    result = run_probe(stub_bin, code)

    assert result.returncode == 1
    assert "anaconda-token-iossifovlab" in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("code", "printed"), [("500", "500"), ("error", "000")])
def test_other_outcome_stops_the_release(
    stub_bin: pathlib.Path, code: str, printed: str,
) -> None:
    result = run_probe(stub_bin, code)

    assert result.returncode == 1
    assert f"Anaconda token probe: HTTP {printed}" in result.stdout


@pytest.mark.parametrize("token", [None, ""], ids=["unset", "empty"])
def test_missing_token_names_the_variable(
    stub_bin: pathlib.Path, token: str | None,
) -> None:
    result = run_probe(stub_bin, "200", token=token)

    assert result.returncode == 2
    assert "ANACONDA_TOKEN" in result.stdout + result.stderr


def test_probe_sends_the_token_to_the_user_endpoint(
    stub_bin: pathlib.Path,
) -> None:
    run_probe(stub_bin, "200")

    args = (stub_bin / "args").read_text().splitlines()
    assert "https://api.anaconda.org/user" in args
    assert f"Authorization: token {FAKE_TOKEN}" in args


JENKINSFILE = REPO_ROOT / "Jenkinsfile.release"


def credentials_stage_body() -> str:
    """Return the text of the credentials stage, up to the next stage."""
    text = JENKINSFILE.read_text()
    start = text.index("stage('Pre-flight: credentials')")
    end = text.index("stage('", start + 1)
    return text[start:end]


def test_credentials_stage_runs_the_probe_from_master() -> None:
    # The CI image has no Jenkinsfile.release: only a checkout runs this.
    if not JENKINSFILE.exists():
        pytest.skip(f"{JENKINSFILE} is not in this checkout")

    body = credentials_stage_body()

    assert "origin/master:scripts/anaconda_token_probe.sh" in body
    assert "anaconda --token" not in body
