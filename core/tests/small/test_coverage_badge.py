# pylint: disable=W0621,C0116
"""``scripts/coverage_badge.py`` writes the shields.io coverage badge."""
import importlib.util
import json
import pathlib
import sys
import textwrap
from types import ModuleType

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "coverage_badge.py"


@pytest.fixture(scope="module")
def coverage_badge() -> ModuleType:
    if not SCRIPT.exists():
        pytest.fail(f"{SCRIPT} is missing; the CI image must copy scripts/")
    spec = importlib.util.spec_from_file_location(
        "coverage_badge", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_cobertura(path: pathlib.Path, line_rate: str) -> pathlib.Path:
    path.write_text(textwrap.dedent(f"""\
        <?xml version="1.0" ?>
        <coverage version="7.6.1" line-rate="{line_rate}"
                  branch-rate="0.1234" lines-covered="1" lines-valid="1">
            <packages/>
        </coverage>
        """))
    return path


@pytest.mark.parametrize(("line_rate", "message", "color"), [
    ("0.8432", "84%", "green"),
    ("0.7996", "80%", "green"),
    ("0.72", "72%", "yellow"),
    ("0.596", "60%", "yellow"),
    ("0.55", "55%", "red"),
])
def test_badge_has_the_rounded_line_rate_and_its_colour(
    coverage_badge: ModuleType,
    tmp_path: pathlib.Path,
    line_rate: str,
    message: str,
    color: str,
) -> None:
    source = write_cobertura(tmp_path / "coverage.xml", line_rate)
    target = tmp_path / "badge.json"

    rc = coverage_badge.main(["coverage_badge.py", str(source), str(target)])

    assert rc == 0
    assert json.loads(target.read_text()) == {
        "schemaVersion": 1,
        "label": "coverage",
        "message": message,
        "color": color,
    }


def test_missing_input_writes_no_badge_and_exits_0(
    coverage_badge: ModuleType,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "coverage.xml"
    target = tmp_path / "badge.json"

    rc = coverage_badge.main(["coverage_badge.py", str(source), str(target)])

    assert rc == 0
    assert not target.exists()
    assert str(source) in capsys.readouterr().out


def test_badge_creates_the_missing_output_directory(
    coverage_badge: ModuleType,
    tmp_path: pathlib.Path,
) -> None:
    source = write_cobertura(tmp_path / "coverage.xml", "0.9")
    target = tmp_path / "html" / "_badges" / "coverage.json"

    rc = coverage_badge.main(["coverage_badge.py", str(source), str(target)])

    assert rc == 0
    assert json.loads(target.read_text())["message"] == "90%"


def test_build_docs_writes_the_badge_into_the_tarball() -> None:
    script = (REPO_ROOT / "docs" / "build_docs.sh").read_text()
    lines = script.replace("\\\n", " ").splitlines()
    commands = [line.split() for line in lines
                if line.strip() and not line.lstrip().startswith("#")]

    def index_of(program: str) -> int:
        matches = [i for i, words in enumerate(commands)
                   if program in words[:2]]
        assert len(matches) == 1, f"{program}: {matches}"
        return matches[0]

    sphinx = index_of("sphinx-build")
    badge = index_of("scripts/coverage_badge.py")
    tar = index_of("tar")

    assert sphinx < badge < tar
    assert commands[badge][2:] == [
        "reports/core/coverage.xml",
        "docs/build/html/_badges/coverage.json",
    ]
    assert commands[tar][-2:] == ["docs/build/", "html/"]
