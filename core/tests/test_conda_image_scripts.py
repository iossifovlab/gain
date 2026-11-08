# pylint: disable=C0116
"""The conda-integration image copies each repo-root script its tests name.

``core/conda-integration/Dockerfile`` copies each repo-root script that a
test of the image needs with its own ``COPY`` line. A test that needs a
script the image lacks passes in the branch build, which runs from the
checkout, and fails only in the image (gain#2284). The guard below reads
the Dockerfile, the ``pytest`` calls of ``run.sh`` and the test files
that those calls run, and fails when a test names a script that no
``COPY`` line copies.

The guard reads the source tree, so ``run.sh`` excludes this file from
the image run.
"""
import pathlib
import re
import shlex
import textwrap

CORE = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = CORE.parent
DOCKERFILE = CORE / "conda-integration" / "Dockerfile"
RUN_SH = CORE / "conda-integration" / "run.sh"


def logical_lines(text: str) -> list[str]:
    """Join the lines that a trailing backslash continues."""
    lines: list[str] = []
    current = ""
    for raw in text.splitlines():
        if raw.rstrip().endswith("\\"):
            current += raw.rstrip()[:-1] + " "
            continue
        lines.append(current + raw)
        current = ""
    if current:
        lines.append(current)
    return lines


def copied_scripts(dockerfile: str) -> tuple[set[str], bool]:
    """Return the script names the ``COPY`` lines copy.

    The flag is true when a ``COPY`` line copies the whole ``scripts``
    directory.
    """
    names: set[str] = set()
    whole = False
    for line in logical_lines(dockerfile):
        words = line.split()
        if not words or words[0].upper() != "COPY":
            continue
        args = [word for word in words[1:] if not word.startswith("--")]
        for source in args[:-1]:
            if source.rstrip("/") == "scripts":
                whole = True
            elif source.startswith("scripts/"):
                names.add(source.removeprefix("scripts/"))
    return names, whole


PytestCall = tuple[list[pathlib.Path], list[pathlib.Path]]


def pytest_calls(run_sh: str, cwd: pathlib.Path) -> list[PytestCall]:
    """Return the targets and the ``--ignore=`` paths of each pytest call.

    A target is an argument after ``pytest`` that names a path under
    ``cwd``, the directory that ``run.sh`` runs pytest from.
    """
    calls: list[PytestCall] = []
    for line in logical_lines(run_sh):
        if line.lstrip().startswith("#") or "pytest" not in line:
            continue
        words = shlex.split(line, comments=True)
        if "pytest" not in words:
            continue
        args = words[words.index("pytest") + 1:]
        targets = [
            cwd / arg for arg in args
            if not arg.startswith("-") and (cwd / arg).exists()]
        ignores = [
            cwd / arg.removeprefix("--ignore=") for arg in args
            if arg.startswith("--ignore=")]
        calls.append((targets, ignores))
    return calls


SCRIPT_PATH = re.compile(
    r"""scripts/([\w.-]+)|["']scripts["']\s*/\s*["']([\w.-]+)["']""")


def named_scripts(text: str, scripts: set[str]) -> set[str]:
    """Return the names in ``scripts`` that ``text`` names by a path.

    A path is ``scripts/<name>`` or ``"scripts" / "<name>"``. A bare
    file name does not count.
    """
    # A path at the end of a sentence carries the period with it.
    found = {
        (match.group(1) or match.group(2)).rstrip(".")
        for match in SCRIPT_PATH.finditer(text)}
    return found & scripts


def files_the_image_runs(
    run_sh: str, cwd: pathlib.Path,
) -> set[pathlib.Path]:
    """Return the test files under a target of a pytest call of run.sh.

    A file that an ``--ignore=`` path of the same call covers is left out.
    """
    files: set[pathlib.Path] = set()
    for targets, ignores in pytest_calls(run_sh, cwd):
        for target in targets:
            candidates = (
                target.rglob("*.py") if target.is_dir() else [target])
            files.update(
                path for path in candidates
                if not any(path.is_relative_to(i) for i in ignores))
    return files


def scripts_named_by_image_tests() -> dict[str, list[pathlib.Path]]:
    """Map each repo-root script to the image test files that name it."""
    scripts = {
        path.name for path in (REPO_ROOT / "scripts").iterdir()
        if path.is_file()}
    named: dict[str, list[pathlib.Path]] = {}
    for path in sorted(files_the_image_runs(RUN_SH.read_text(), CORE)):
        for name in named_scripts(path.read_text(), scripts):
            named.setdefault(name, []).append(path)
    return named


def test_copied_scripts_reads_each_source_of_a_continued_copy() -> None:
    dockerfile = (
        "COPY --chown=u:u scripts/a.sh \\\n"
        "    scripts/b.py /testbed/scripts/\n"
        "COPY --chown=u:u core/tests /testbed/core/tests\n"
        "RUN echo scripts/c.sh\n")

    assert copied_scripts(dockerfile) == ({"a.sh", "b.py"}, False)


def test_copied_scripts_drops_a_relative_destination() -> None:
    dockerfile = "COPY scripts/a.sh scripts/\n"

    assert copied_scripts(dockerfile) == ({"a.sh"}, False)


def test_copied_scripts_counts_the_scripts_directory() -> None:
    assert copied_scripts("COPY scripts /testbed/scripts\n")[1]
    assert copied_scripts("COPY scripts/ /testbed/scripts/\n")[1]


def test_pytest_calls_reads_targets_and_ignores_of_each_call(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "tests" / "integration").mkdir(parents=True)
    run_sh = (
        "# python -m pytest tests/commented\n"
        '"$ENV_PREFIX/bin/python" -m pytest -n 5 tests \\\n'
        "    --ignore=tests/integration --ignore=tests/a.py \\\n"
        '    --junitxml="$REPORTS/small.xml"\n'
        '"$ENV_PREFIX/bin/python" -m pytest -v tests/integration\n')

    calls = pytest_calls(run_sh, tmp_path)

    assert calls == [
        ([tmp_path / "tests"],
         [tmp_path / "tests" / "integration", tmp_path / "tests" / "a.py"]),
        ([tmp_path / "tests" / "integration"], []),
    ]


def test_files_the_image_runs_drops_the_files_under_an_ignored_directory(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "tests" / "source_only").mkdir(parents=True)
    (tmp_path / "tests" / "test_a.py").write_text("")
    (tmp_path / "tests" / "source_only" / "test_b.py").write_text("")
    run_sh = "python -m pytest tests --ignore=tests/source_only\n"

    files = files_the_image_runs(run_sh, tmp_path)

    assert files == {tmp_path / "tests" / "test_a.py"}


def test_named_scripts_matches_only_a_path_to_a_repo_root_script() -> None:
    text = textwrap.dedent('''\
        SCRIPT = REPO_ROOT / "scripts" / "a.sh"
        """Runs scripts/b.py."""
        PACKAGE = "__init__.py"
        CONFIG = "web_api/scripts/grr-definition-dir.yaml"
        ''')

    named = named_scripts(text, {"a.sh", "b.py", "__init__.py", "c.sh"})

    assert named == {"a.sh", "b.py"}


def test_the_image_copies_each_script_that_its_tests_name() -> None:
    named = scripts_named_by_image_tests()
    copied, whole = copied_scripts(DOCKERFILE.read_text())

    assert named, "found no test that names a repo-root script"
    missing = set() if whole else set(named) - copied
    assert not missing, (
        f"{DOCKERFILE} copies no "
        + ", ".join(f"scripts/{name}" for name in sorted(missing))
        + "; a COPY line must copy each of them, because these tests "
        "name them: "
        + "; ".join(
            f"scripts/{name}: " + ", ".join(
                str(path.relative_to(CORE)) for path in named[name])
            for name in sorted(missing)))
