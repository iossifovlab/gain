"""Render gain's conda environment files from the workspace pyprojects.

The files are generated, not hand-written: each one lists the
dependencies of the pyproject tables that feed it, mapped to conda names,
with the version clauses copied as written.

    python scripts/conda_env.py           # rewrite every file
    python scripts/conda_env.py --check   # exit 1 if any file is stale

``core/tests/test_conda_deps.py`` runs the same comparison in CI.

The rattler-build recipes stay hand-written, and ``check_recipe_run``
holds each recipe's ``requirements.run`` to its pyproject's
dependencies under the same name mapping; the test runs that too.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import tomllib
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from packaging.requirements import Requirement
from packaging.specifiers import Specifier
from packaging.utils import canonicalize_name

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

CHANNELS = (
    "conda-forge",
    # pybigwig and pyliftover are packaged only on bioconda.
    "bioconda",
)

#: Canonical PyPI name -> conda package name, for the packages whose
#: conda name differs. Every other package keeps its canonical PyPI name.
CONDA_NAMES: Mapping[str, str] = MappingProxyType({
    # conda-forge `brotli` is the C library and command-line tools only;
    # the Python binding PyPI calls `brotli` is `brotli-python` (#1400).
    "brotli": "brotli-python",
    # Plain `dask` on conda-forge is a metapackage that also pulls in
    # bokeh, pyarrow, lz4 and cytoolz; bokeh registers its own TRACE
    # level name on import (#1569). dask-core is PyPI's `dask`.
    "dask": "dask-core",
    "docker": "docker-py",
    "matplotlib": "matplotlib-base",
})

#: Canonical PyPI name -> why it is installed by pip rather than conda.
#: Shared by every output: an output renders the entries its own feeds
#: declare under a ``pip:`` sub-list, with ``pip`` itself added as a
#: conda dependency.
PIP_ONLY: Mapping[str, str] = MappingProxyType({
    "adrf": "not packaged on any conda channel",
    "pylint-junit": "not packaged on any conda channel",
    "pytestarch": "not packaged on any conda channel",
    "sphinx-autorun": "not packaged on any conda channel",
    "types-channels": "not packaged on any conda channel",
})


@dataclass(frozen=True)
class Feed:
    """A list of requirement strings inside a workspace pyproject."""

    pyproject: str
    table: tuple[str, ...] = ("project", "dependencies")


@dataclass(frozen=True)
class Output:
    """A generated environment file and the feeds rendered into it."""

    filename: str
    env_name: str
    feeds: tuple[Feed, ...]


_DEV = ("dependency-groups", "dev")

OUTPUTS = (
    Output(
        "environment.yml", "gain",
        (Feed("core/pyproject.toml"), Feed("web_api/pyproject.toml")),
    ),
    # Installed on top of environment.yml into the same env, so it
    # carries only the tools: no runtime feeds.
    Output(
        "dev-environment.yml", "gain",
        (
            Feed("core/pyproject.toml", _DEV),
            Feed("web_api/pyproject.toml", _DEV),
            Feed("pyproject.toml", ("dependency-groups", "docs")),
        ),
    ),
)


@dataclass(frozen=True)
class Section:
    """The requirements one feed contributes, as (canonical name, clauses)."""

    title: str
    requires_python: tuple[str, ...]
    requirements: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass
class _Entry:
    name: str
    clauses: list[str] = field(default_factory=list)

    def add(self, clauses: Iterable[str]) -> None:
        self.clauses.extend(c for c in clauses if c not in self.clauses)

    def render(self) -> str:
        return self.name + ",".join(self.clauses)


def conda_name(pypi_name: str) -> str:
    """Return the conda package name for a PyPI distribution name."""
    canonical = canonicalize_name(pypi_name)
    return CONDA_NAMES.get(canonical, canonical)


def _clauses(text: str, source: str) -> tuple[str, ...]:
    """Split a version specifier into its clauses, in written order.

    The clauses are sliced from the text rather than read back from a
    ``SpecifierSet``: before packaging 26 its iteration order follows
    the string hash, so it would vary from run to run. Each clause is
    still validated by packaging, and arbitrary equality (``===``),
    which conda has no form for, raises.
    """
    text = "".join(text.split())
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    clauses = tuple(text.split(",")) if text else ()
    for clause in clauses:
        if Specifier(clause).operator == "===":
            raise ValueError(
                f"{source}: arbitrary equality (===) is a form "
                f"scripts/conda_env.py does not model")
    return clauses


def _requirement(line: str, source: str) -> tuple[str, tuple[str, ...]]:
    requirement = Requirement(line)
    if requirement.extras or requirement.marker or requirement.url:
        raise ValueError(
            f"{source}: {line!r} carries an extra, a marker or a url, "
            f"which scripts/conda_env.py does not model")
    # Requirement.name keeps the name as written, so what follows it is
    # the specifier text.
    specifier = line.lstrip()[len(requirement.name):]
    return (
        canonicalize_name(requirement.name),
        _clauses(specifier, f"{source}: {line!r}"),
    )


def load_section(root: pathlib.Path, feed: Feed) -> Section:
    """Read one feed's requirements, and its project's requires-python."""
    with (root / feed.pyproject).open("rb") as infile:
        config = tomllib.load(infile)
    lines = config
    for key in feed.table:
        lines = lines[key]
    project = config["project"]
    source = f"{feed.pyproject} [{'.'.join(feed.table)}]"
    return Section(
        title=f"{project['name']} ({source})",
        requires_python=_clauses(
            project["requires-python"], feed.pyproject),
        requirements=tuple(_requirement(line, source) for line in lines),
    )


def workspace_members(root: pathlib.Path) -> frozenset[str]:
    """Return the canonical names of the uv workspace's members."""
    with (root / "pyproject.toml").open("rb") as infile:
        config = tomllib.load(infile)
    sources = config.get("tool", {}).get("uv", {}).get("sources", {})
    return frozenset(
        canonicalize_name(name)
        for name, source in sources.items()
        if isinstance(source, dict) and source.get("workspace"))


def check_pip_only(
    sections: Iterable[Section], pip_only: Mapping[str, str],
) -> None:
    """Raise if a pip-only entry is declared by none of the sections."""
    declared = {name for s in sections for name, _ in s.requirements}
    undeclared = sorted(set(pip_only) - declared)
    if undeclared:
        raise ValueError(
            f"pip-only entries no feed declares: {', '.join(undeclared)}")


def render(
    output: Output,
    sections: Sequence[Section],
    members: Iterable[str],
    pip_only: Mapping[str, str],
) -> str:
    """Render one environment file from its feeds' sections."""
    python = _Entry("python")
    entries: dict[str, _Entry] = {}
    pip_entries: dict[str, _Entry] = {}
    by_section: dict[str, list[_Entry]] = {}

    for section in sections:
        python.add(section.requires_python)
        section_entries = by_section.setdefault(section.title, [])
        for name, clauses in section.requirements:
            if name in members:
                continue
            if name in pip_only:
                pip_entries.setdefault(name, _Entry(name)).add(clauses)
                continue
            conda = conda_name(name)
            if conda not in entries:
                entries[conda] = _Entry(conda)
                section_entries.append(entries[conda])
            entries[conda].add(clauses)

    lines = [
        "# Generated by scripts/conda_env.py from the workspace",
        "# pyprojects -- do not edit by hand. Regenerate:",
        "#     python scripts/conda_env.py",
        f"name: {output.env_name}",
        "channels:",
        *(f"  - {channel}" for channel in CHANNELS),
        "dependencies:",
        f"  - {python.render()}",
    ]
    for title, section_entries in by_section.items():
        lines.append(f"  # {title}")
        lines.extend(
            f"  - {entry.render()}"
            for entry in sorted(section_entries, key=lambda e: e.name))
    if pip_entries:
        lines.extend((
            "  # pip-only: PIP_ONLY in scripts/conda_env.py",
            "  - pip",
            "  - pip:",
        ))
        for name in sorted(pip_entries):
            lines.extend((
                f"    # {name}: {pip_only[name]}",
                f"    - {pip_entries[name].render()}",
            ))
    return "\n".join(lines) + "\n"


def render_all(
    root: pathlib.Path = REPO_ROOT,
    outputs: Sequence[Output] = OUTPUTS,
    pip_only: Mapping[str, str] = PIP_ONLY,
) -> dict[str, str]:
    """Render every output under ``root``, keyed by file name."""
    members = workspace_members(root)
    sections = {
        output.filename: [load_section(root, feed) for feed in output.feeds]
        for output in outputs
    }
    check_pip_only(
        (s for per_output in sections.values() for s in per_output),
        pip_only)
    return {
        output.filename: render(
            output, sections[output.filename], members, pip_only)
        for output in outputs
    }


def read_recipe_run(path: pathlib.Path) -> list[str]:
    """Return a recipe's ``requirements.run`` entries, whitespace removed.

    PyYAML is imported here rather than at the top so that rendering the
    environment files keeps needing only the stdlib and packaging.
    """
    import yaml  # pylint: disable=import-outside-toplevel

    with path.open() as infile:
        recipe = yaml.safe_load(infile)
    run = (recipe.get("requirements") or {}).get("run")
    if run is None:
        raise ValueError(f"{path}: no requirements.run list")
    if not isinstance(run, list):
        raise TypeError(f"{path}: requirements.run is not a list")
    entries = []
    for entry in run:
        # A selector (`- if: ... then: ...`) or a bare number is a form
        # the comparison does not model.
        if not isinstance(entry, str):
            raise TypeError(
                f"{path}: run entry {entry!r} is not a plain string, "
                f"which scripts/conda_env.py does not model")
        entries.append("".join(entry.split()))
    return entries


def expected_recipe_run(root: pathlib.Path, package: str) -> list[str]:
    """Return the run list ``<package>``'s pyproject implies.

    That is ``python`` with the ``requires-python`` clauses, then every
    ``[project.dependencies]`` entry under its conda name. Workspace
    members stay in: a plugin's recipe runs on ``gain-core``. Optional
    extras never reach a recipe.
    """
    section = load_section(root, Feed(f"{package}/pyproject.toml"))
    return [
        "python" + ",".join(section.requires_python),
        *(conda_name(name) + ",".join(clauses)
          for name, clauses in section.requirements),
    ]


def check_recipe_run(root: pathlib.Path, package: str) -> None:
    """Raise if ``<package>``'s recipe run list differs from its pyproject.

    Rows are compared as a multiset: their order is free, a repeated row
    is a difference.
    """
    recipe = f"{package}/conda-recipe/recipe.yaml"
    actual = Counter(read_recipe_run(root / recipe))
    expected = Counter(expected_recipe_run(root, package))
    if actual == expected:
        return

    def listed(entries: Counter[str]) -> str:
        return ", ".join(sorted(entries.elements())) or "none"

    raise ValueError(
        f"{recipe}: requirements.run differs from {package}/pyproject.toml "
        f"[project.dependencies]; only in the recipe: "
        f"{listed(actual - expected)}; only in the pyproject: "
        f"{listed(expected - actual)}")


def main(
    argv: Sequence[str] | None = None,
    root: pathlib.Path = REPO_ROOT,
) -> int:
    """Rewrite the environment files, or with ``--check`` report drift."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check", action="store_true",
        help="exit non-zero if a committed file differs from a render")
    args = parser.parse_args(argv)

    rendered = render_all(root)
    if not args.check:
        for filename, text in rendered.items():
            (root / filename).write_text(text)
        return 0
    stale = [
        filename for filename, text in rendered.items()
        if not (root / filename).exists()
        or (root / filename).read_text() != text
    ]
    for filename in stale:
        print(
            f"{root / filename} is stale; regenerate it with "
            f"`python scripts/conda_env.py`", file=sys.stderr)
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
