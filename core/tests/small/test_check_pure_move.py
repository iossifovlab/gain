# pylint: disable=C0116,W0621
"""``scripts/check_pure_move.py`` proves a definition moved unchanged."""
import os
import pathlib
import shutil
import subprocess
import sys
import textwrap

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "check_pure_move.py"
GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(GIT is None, reason="git is not on PATH")

OLD_MODULE = textwrap.dedent('''\
    """The module that loses the function."""
    LIMIT = 3


    def clamp(value: int) -> int:
        """Return ``value``, never above the limit."""
        return min(value, LIMIT)


    def stays() -> None:
        """Stay where it is."""
    ''')

MOVED_CLAMP = textwrap.dedent('''\
    """The module that gains the function."""
    LIMIT = 3


    def clamp(value: int) -> int:
        """Return ``value``, never above the limit."""
        return min(value, LIMIT)
    ''')

OLD_AFTER_MOVE = textwrap.dedent('''\
    """The module that loses the function."""
    from new import LIMIT, clamp


    def stays() -> None:
        """Stay where it is."""
    ''')


def git(repo: pathlib.Path, *args: str) -> str:
    """Run git in the repo with a fixed identity and no global config."""
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    result = subprocess.run(
        [GIT or "git", "-c", "user.name=Test",
         "-c", "user.email=test@example.com", "-C", str(repo), *args],
        env=env, check=True, capture_output=True, text=True)
    return result.stdout


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """A repo whose first commit holds ``old.py`` before the move."""
    if not SCRIPT.exists():
        pytest.fail(f"{SCRIPT} is missing; the CI image must copy scripts/")
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    (root / "old.py").write_text(OLD_MODULE)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    (root / "old.py").write_text(OLD_AFTER_MOVE)
    return root


def check(repo: pathlib.Path, *names: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--base", "HEAD",
         "--old", "old.py", "--new", "new.py", *names],
        cwd=repo, capture_output=True, text=True, check=False)


def test_a_moved_function_and_constant_pass(repo: pathlib.Path) -> None:
    (repo / "new.py").write_text(MOVED_CLAMP)

    result = check(repo, "clamp", "LIMIT")

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["same clamp", "same LIMIT"]


def test_a_changed_body_fails(repo: pathlib.Path) -> None:
    (repo / "new.py").write_text(MOVED_CLAMP.replace(
        "min(value, LIMIT)", "max(value, LIMIT)"))

    result = check(repo, "clamp", "LIMIT")

    assert result.returncode == 1
    assert result.stdout.splitlines() == ["DIFFERENT clamp", "same LIMIT"]


def test_a_changed_docstring_fails(repo: pathlib.Path) -> None:
    (repo / "new.py").write_text(MOVED_CLAMP.replace(
        "never above", "not above"))

    result = check(repo, "clamp")

    assert result.returncode == 1
    assert result.stdout.splitlines() == ["DIFFERENT clamp"]


def test_a_name_missing_from_the_new_module_fails(
    repo: pathlib.Path,
) -> None:
    (repo / "new.py").write_text(MOVED_CLAMP)

    result = check(repo, "stays")

    assert result.returncode == 1
    assert result.stdout.splitlines() == ["MISSING stays"]
