"""Render gain's conda environment file from the workspace pyprojects.

``environment.yml`` at the repo root is generated, not hand-written: its
entries are the ``[project.dependencies]`` of gain-core and gain-web-api,
mapped to conda names, with the specifiers copied verbatim.

    python scripts/conda_env.py           # rewrite environment.yml
    python scripts/conda_env.py --check   # exit 1 if it is stale

``core/tests/test_conda_deps.py`` runs the ``--check`` comparison in CI.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

ENVIRONMENT_FILE = "environment.yml"

#: The pyprojects whose runtime dependencies feed ``environment.yml``,
#: in the order their sections are rendered.
ENVIRONMENT_FEEDS = (
    "core/pyproject.toml",
    "web_api/pyproject.toml",
)

CHANNELS = (
    "conda-forge",
    # pybigwig and pyliftover are packaged only on bioconda.
    "bioconda",
)

#: Canonical PyPI name -> conda package name, for the packages whose
#: conda name differs. Every other package keeps its canonical PyPI name.
CONDA_NAMES: Mapping[str, str] = MappingProxyType({
    # Plain `dask` on conda-forge is a metapackage that also pulls in
    # bokeh, pyarrow, lz4 and cytoolz; bokeh registers its own TRACE
    # level name on import (#1569). dask-core is PyPI's `dask`.
    "dask": "dask-core",
    "docker": "docker-py",
    "matplotlib": "matplotlib-base",
})

#: Canonical PyPI name -> why it is installed by pip rather than conda.
#: Rendered under the ``pip:`` sub-list, with ``pip`` itself added as a
#: conda dependency.
PIP_ONLY: Mapping[str, str] = MappingProxyType({
    "adrf": "not packaged on any conda channel",
})

_NAME = re.compile(r"\s*[A-Za-z0-9][A-Za-z0-9._-]*")
#: One version clause conda's match spec accepts as written.
_CLAUSE = re.compile(r"(==|!=|<=|>=|<|>|~=)[A-Za-z0-9.*+!_-]+")


@dataclass
class _Entry:
    name: str
    clauses: list[str] = field(default_factory=list)

    def add(self, clauses: Sequence[str]) -> None:
        self.clauses.extend(c for c in clauses if c not in self.clauses)

    def render(self) -> str:
        return self.name + ",".join(self.clauses)


@dataclass
class _Section:
    title: str
    entries: list[_Entry] = field(default_factory=list)


def conda_name(pypi_name: str) -> str:
    """Return the conda package name for a PyPI distribution name."""
    canonical = canonicalize_name(pypi_name)
    return CONDA_NAMES.get(canonical, canonical)


def _clauses(text: str, source: str) -> list[str]:
    """Split a version specifier into its clauses, as written.

    A clause conda cannot read as written (``===``, a parenthesised
    list) raises rather than being rewritten.
    """
    specifier = "".join(text.split())
    clauses = specifier.split(",") if specifier else []
    for clause in clauses:
        if not _CLAUSE.fullmatch(clause):
            raise ValueError(
                f"{source}: version clause {clause!r} has a form "
                f"scripts/conda_env.py does not model")
    return clauses


def _requirement(
    line: str, pyproject: pathlib.Path,
) -> tuple[str, list[str]]:
    """Split a requirement line into its canonical name and clauses.

    A requirement carrying an extra, a marker or a url raises: the
    generator has no conda rendering for any of them.
    """
    requirement = Requirement(line)
    if requirement.extras or requirement.marker or requirement.url:
        raise ValueError(
            f"{pyproject}: {line!r} carries an extra, a marker or a url, "
            f"which scripts/conda_env.py does not model")
    match = _NAME.match(line)
    assert match is not None
    return (
        canonicalize_name(requirement.name),
        _clauses(line[match.end():], f"{pyproject}: {line!r}"),
    )


def _workspace_members(root: pathlib.Path) -> set[str]:
    with (root / "pyproject.toml").open("rb") as infile:
        config = tomllib.load(infile)
    sources = config.get("tool", {}).get("uv", {}).get("sources", {})
    return {
        canonicalize_name(name)
        for name, source in sources.items()
        if isinstance(source, dict) and source.get("workspace")
    }


def render_environment(
    root: pathlib.Path = REPO_ROOT,
    pip_only: Mapping[str, str] = PIP_ONLY,
) -> str:
    """Render ``environment.yml`` from the feeds under ``root``."""
    members = _workspace_members(root)
    python = _Entry("python")
    entries: dict[str, _Entry] = {}
    pip_entries: dict[str, _Entry] = {}
    sections: list[_Section] = []

    for feed in ENVIRONMENT_FEEDS:
        pyproject = root / feed
        with pyproject.open("rb") as infile:
            project = tomllib.load(infile)["project"]
        python.add(_clauses(project["requires-python"], str(pyproject)))
        section = _Section(f"{project['name']} ({feed})")
        sections.append(section)
        for line in project.get("dependencies", []):
            name, clauses = _requirement(line, pyproject)
            if name in members:
                continue
            if name in pip_only:
                target, conda = pip_entries, name
            else:
                target, conda = entries, conda_name(name)
            if conda not in target:
                target[conda] = _Entry(conda)
                if target is entries:
                    section.entries.append(target[conda])
            target[conda].add(clauses)

    undeclared = sorted(set(pip_only) - set(pip_entries))
    if undeclared:
        raise ValueError(
            f"pip-only entries no feed declares: {', '.join(undeclared)}")

    lines = [
        "# Generated by scripts/conda_env.py from the runtime dependencies",
        "# of the workspace pyprojects -- do not edit by hand. Regenerate:",
        "#     python scripts/conda_env.py",
        "name: gain",
        "channels:",
        *(f"  - {channel}" for channel in CHANNELS),
        "dependencies:",
        f"  - {python.render()}",
    ]
    for section in sections:
        lines.append(f"  # {section.title}")
        lines.extend(
            f"  - {entry.render()}"
            for entry in sorted(section.entries, key=lambda e: e.name))
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


def main(
    argv: Sequence[str] | None = None,
    root: pathlib.Path = REPO_ROOT,
) -> int:
    """Rewrite ``environment.yml``, or with ``--check`` report drift."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check", action="store_true",
        help="exit non-zero if the committed file differs from a render")
    args = parser.parse_args(argv)

    target = root / ENVIRONMENT_FILE
    rendered = render_environment(root)
    if args.check:
        if not target.exists() or target.read_text() != rendered:
            print(
                f"{target} is stale; regenerate it with "
                f"`python scripts/conda_env.py`", file=sys.stderr)
            return 1
        return 0
    target.write_text(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
