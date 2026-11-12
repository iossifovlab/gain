# pylint: disable=W0621,C0116
"""``scripts/release_notes.py`` checks the release notes of a tag.

The ``Pre-flight: release notes`` stage of ``Jenkinsfile.release`` runs
``check <tag>`` on the ``changes.rst`` of ``origin/master``. The tests
run ``main(argv)`` on a small ``changes.rst`` in ``tmp_path``.
"""
import importlib.util
import pathlib
import re
import sys
import textwrap
from types import ModuleType

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "release_notes.py"

CHANGES = textwrap.dedent("""\
    Release Notes
    =============

    * 2026.11.1
        * Annotation: a new annotator.

    * 2026.11.0
        * GRR: a new statistic.
""")


@pytest.fixture(scope="module")
def release_notes() -> ModuleType:
    if not SCRIPT.exists():
        pytest.fail(f"{SCRIPT} is missing")
    spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def changes(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "changes.rst"
    path.write_text(CHANGES)
    return path


def test_check_passes_when_the_tag_is_the_first_section(
    release_notes: ModuleType, changes: pathlib.Path,
) -> None:
    assert release_notes.main(
        ["check", "2026.11.1", "--changes", str(changes)]) == 0


def test_check_fails_when_the_tag_is_in_a_later_section(
    release_notes: ModuleType, changes: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = release_notes.main(
        ["check", "2026.11.0", "--changes", str(changes)])

    assert code != 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert "2026.11.0" in lines[0]
    assert "2026.11.1" in lines[0]


def test_check_fails_when_the_tag_is_not_in_the_file(
    release_notes: ModuleType, changes: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = release_notes.main(
        ["check", "2026.12.0", "--changes", str(changes)])

    assert code != 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert "2026.12.0" in lines[0]
    assert "2026.11.1" in lines[0]


def test_check_ignores_a_nested_bullet_that_looks_like_a_version(
    release_notes: ModuleType, tmp_path: pathlib.Path,
) -> None:
    changes = tmp_path / "changes.rst"
    changes.write_text(textwrap.dedent("""\
        Release Notes
        =============

          * 2026.12.0

        * 2026.11.1
            * Annotation: a new annotator.
    """))

    assert release_notes.main(
        ["check", "2026.11.1", "--changes", str(changes)]) == 0


def test_check_fails_when_the_first_heading_is_not_a_bare_version(
    release_notes: ModuleType, tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    changes = tmp_path / "changes.rst"
    changes.write_text(textwrap.dedent("""\
        Release Notes
        =============

        * 2026.12.0 (draft)

        * 2026.11.1
            * Annotation: a new annotator.
    """))

    code = release_notes.main(
        ["check", "2026.11.1", "--changes", str(changes)])

    assert code != 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert "2026.12.0 (draft)" in lines[0]


def test_check_fails_when_the_file_has_no_section(
    release_notes: ModuleType, tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    changes = tmp_path / "changes.rst"
    changes.write_text(textwrap.dedent("""\
        Release Notes
        =============

          * 2026.11.1
              * Annotation: a new annotator.
    """))

    code = release_notes.main(
        ["check", "2026.11.1", "--changes", str(changes)])

    assert code != 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert "2026.11.1" in lines[0]
    assert "no section" in lines[0]


def test_check_reads_docs_source_changes_rst_by_default(
    release_notes: ModuleType, tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The Jenkins stage runs `check <tag>` without --changes.
    changes = tmp_path / "docs" / "source" / "changes.rst"
    changes.parent.mkdir(parents=True)
    changes.write_text(CHANGES)
    monkeypatch.chdir(tmp_path)

    assert release_notes.main(["check", "2026.11.1"]) == 0
    assert release_notes.main(["check", "2026.11.0"]) != 0


JENKINSFILE = REPO_ROOT / "Jenkinsfile.release"
RELEASE_JOB = REPO_ROOT / "jenkins-jobs" / "release.groovy"
STAGE = "Pre-flight: release notes"


def require(path: pathlib.Path) -> str:
    # The CI image has no Jenkinsfile.release: only a checkout runs this.
    if not path.exists():
        pytest.skip(f"{path} is not in this checkout")
    return path.read_text()


def stage_names(text: str) -> list[str]:
    return re.findall(r"^\s*stage\('([^']+)'\)", text, flags=re.MULTILINE)


def stage_body(text: str, stage: str) -> str:
    """Return the text from ``stage`` to the next stage."""
    start = text.index(f"stage('{stage}')")
    end = text.find("stage('", start + 1)
    return text[start:end if end != -1 else len(text)]


def test_stage_comes_right_after_the_tag_checkout() -> None:
    names = stage_names(require(JENKINSFILE))

    assert names[names.index("Validate + checkout tag") + 1] == STAGE


def test_stage_runs_the_script_with_the_system_python() -> None:
    body = stage_body(require(JENKINSFILE), STAGE)

    assert re.search(r"/usr/bin/python3 \S*release_notes\.py check", body)


def test_stage_runs_on_a_dry_run_too() -> None:
    body = stage_body(require(JENKINSFILE), STAGE)

    assert not re.search(r"when\s*\{[^}]*DRY_RUN", body)


@pytest.mark.parametrize("path", [
    "scripts/release_notes.py",
    "docs/source/changes.rst",
])
def test_stage_reads_the_file_from_origin_master(path: str) -> None:
    body = stage_body(require(JENKINSFILE), STAGE)

    assert f"git show origin/master:{path}" in body


def test_stage_aborts_with_error() -> None:
    body = stage_body(require(JENKINSFILE), STAGE)

    assert "error(" in body


@pytest.mark.parametrize("path", [JENKINSFILE, RELEASE_JOB])
def test_dry_run_description_lists_the_release_notes(
    path: pathlib.Path,
) -> None:
    # Join the concatenated literals of the description into one.
    text = re.sub(r"'\s*\+\s*'", "", require(path))
    after_name = text[text.index("'DRY_RUN'"):]
    description = re.search(r"'(Run every pre-flight[^']*)'", after_name)

    assert description is not None
    assert "release notes" in description.group(1)
