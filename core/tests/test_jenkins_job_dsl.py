"""Every branch-parameterized Jenkins job must load its own pipeline.

A ``pipelineJob``'s ``cpsScm`` block names the branch the *pipeline
script* is read from.  That is a separate thing from the branch the job
checks out into its workspace, which the ``BRANCH_NAME`` build parameter
governs.  Pinning ``cpsScm`` to ``master`` while accepting a
``BRANCH_NAME`` parameter means a branch build runs master's Jenkinsfile
against the branch's source: a change to that Jenkinsfile silently
no-ops on the branch introducing it and only takes effect once merged.

gain#272 fixed this for ``gain-web-e2e``; gain#598 fixed the same defect
in ``gain-core-integration``.  This module pins the invariant so the next
job to grow a ``BRANCH_NAME`` parameter cannot reintroduce it.

The invariant is deliberately conditional rather than a blanket "never
pin master".  Jobs that take no ``BRANCH_NAME`` parameter --
``gain-vep-integration`` (triggered only ``when { branch 'master' }``),
``gain-release`` (pins master so pipeline fixes ship without retagging),
``gain-nightly`` and ``gain-python-matrix`` -- are correct as written and
must keep pinning master; a ``${BRANCH_NAME}`` reference there would
expand to nothing.

The DSL source is the seam.  The behaviour these files describe is only
observable on a Jenkins controller running the Job DSL plugin, which no
test here can reach, so this reads the files the constraint is actually
about -- the same approach ``test_architecture`` takes to the import
graph.  It is also why the quoting matters enough to assert on: Groovy
interpolates a double-quoted ``"${BRANCH_NAME}"`` when the seed job
evaluates the DSL, baking one branch name into the generated job config.
Only the single-quoted form survives into the config for Jenkins's git
plugin to expand per build.
"""
import functools
import os
import pathlib
import re

import pytest

# core/tests/ -> core/ -> the gain repo root, which is where the seed job
# globs `**/jenkins-jobs/*.groovy` from.
GAIN_ROOT = pathlib.Path(__file__).parents[2]

# Directories that cannot hold a job DSL but are expensive to walk.
PRUNED_DIRS = frozenset({
    ".git", ".venv", "__pycache__", "node_modules", "venv",
})

JOB_DSL_DIR = "jenkins-jobs"

# `branch(<literal>)` inside the cpsScm git block.  The quote characters
# are captured deliberately -- which quote was used is the whole point.
BRANCH_CALL_RE = re.compile(
    r"branch\(\s*(?P<literal>'[^']*'|\"[^\"]*\")\s*\)",
)

# `stringParam('BRANCH_NAME', ...)`, wrapped across lines in every DSL.
BRANCH_PARAM_RE = re.compile(r"stringParam\(\s*'BRANCH_NAME'")

# The literal a branch-parameterized job must hand to `branch()`, quotes
# included.
BRANCH_PARAM_REFERENCE = "'${BRANCH_NAME}'"


@functools.cache
def _job_dsl_files() -> tuple[pathlib.Path, ...]:
    """Every job DSL the seed job would pick up, at any depth.

    Cached: parametrize calls this at collection time and the guard
    test calls it again, and one walk of the repo is enough.
    """
    found: list[pathlib.Path] = []
    for dirpath, dirnames, filenames in os.walk(GAIN_ROOT):
        dirnames[:] = [name for name in dirnames if name not in PRUNED_DIRS]
        if pathlib.Path(dirpath).name != JOB_DSL_DIR:
            continue
        found.extend(
            pathlib.Path(dirpath) / filename
            for filename in filenames
            if filename.endswith(".groovy")
        )
    return tuple(sorted(found))


@functools.cache
def _declares_branch_parameter(dsl_path: pathlib.Path) -> bool:
    return BRANCH_PARAM_RE.search(dsl_path.read_text()) is not None


def _dsl_id(dsl_path: pathlib.Path) -> str:
    return str(dsl_path.relative_to(GAIN_ROOT))


def _branch_parameterized_dsls() -> list[pathlib.Path]:
    return [
        path
        for path in _job_dsl_files()
        if _declares_branch_parameter(path)
    ]


def _fixed_branch_dsls() -> list[pathlib.Path]:
    return [
        path
        for path in _job_dsl_files()
        if not _declares_branch_parameter(path)
    ]


def _branch_literal(dsl_path: pathlib.Path) -> str:
    """The single `branch()` argument, with its quotes, as written."""
    matches: list[str] = BRANCH_CALL_RE.findall(dsl_path.read_text())
    assert len(matches) == 1, (
        f"{_dsl_id(dsl_path)} has {len(matches)} branch() calls; this "
        f"module assumes exactly one (the cpsScm git block) and would "
        f"otherwise pin the wrong one."
    )
    return matches[0]


def test_job_dsl_discovery_finds_the_seeded_jobs() -> None:
    """Guard the walk, so the pins below can never pass vacuously.

    Everything else here is parametrized over discovered files: a wrong
    ``GAIN_ROOT`` would collect zero cases and report green.
    """
    assert (GAIN_ROOT / JOB_DSL_DIR / "Jenkinsfile.seed").is_file(), (
        f"GAIN_ROOT resolved to {GAIN_ROOT}, which is not the gain repo "
        f"root -- the walk below would find nothing."
    )

    discovered = {_dsl_id(path) for path in _job_dsl_files()}
    assert "core/jenkins-jobs/integration.groovy" in discovered
    assert "web_e2e/jenkins-jobs/e2e.groovy" in discovered

    assert _branch_parameterized_dsls(), (
        "no job DSL declares a BRANCH_NAME parameter, so the invariant "
        "below is vacuous"
    )
    assert _fixed_branch_dsls(), (
        "no job DSL pins a fixed branch, so the converse below is vacuous"
    )


@pytest.mark.parametrize("dsl_path", _branch_parameterized_dsls(), ids=_dsl_id)
def test_branch_parameterized_job_loads_its_pipeline_from_that_branch(
    dsl_path: pathlib.Path,
) -> None:
    assert _branch_literal(dsl_path) == BRANCH_PARAM_REFERENCE, (
        f"{_dsl_id(dsl_path)} accepts a BRANCH_NAME parameter but reads "
        f"its pipeline script from {_branch_literal(dsl_path)}. A branch "
        f"build would run that branch's source through a different "
        f"branch's Jenkinsfile, so changes to the Jenkinsfile cannot be "
        f"tested before merge (gain#272, gain#598). Use the single-quoted "
        f"{BRANCH_PARAM_REFERENCE} so Jenkins expands it per build."
    )


@pytest.mark.parametrize("dsl_path", _fixed_branch_dsls(), ids=_dsl_id)
def test_job_without_a_branch_parameter_pins_a_fixed_branch(
    dsl_path: pathlib.Path,
) -> None:
    literal = _branch_literal(dsl_path)
    assert "${BRANCH_NAME}" not in literal, (
        f"{_dsl_id(dsl_path)} reads its pipeline script from {literal} "
        f"but declares no BRANCH_NAME parameter for Jenkins to expand it "
        f"from. Add the parameter or pin a fixed branch."
    )
