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
about.  ``test_architecture`` reads source too, but note the difference
that matters: it stays inside ``core/``, whereas this module walks the
whole repo and therefore only works where the sibling projects are
present.  ``core/Dockerfile`` copies the job DSL directories into the
gain-core CI image for exactly that reason, and
``test_job_dsl_discovery_finds_every_seeded_job`` fails loudly if that
ever stops being true.

Quoting is asserted because it is load-bearing: Groovy interpolates a
double-quoted ``"${BRANCH_NAME}"`` when the seed job evaluates the DSL,
baking one branch name into the generated job config.  Only the
single-quoted form survives into the config XML for Jenkins's git plugin
to expand per build.
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
    ".git", ".test_grr", ".venv", "__pycache__", "build", "dist",
    "node_modules", "venv",
})

JOB_DSL_DIR = "jenkins-jobs"

# The file that marks GAIN_ROOT as the repo root the seed job globs from.
SEED_MARKER = pathlib.Path(JOB_DSL_DIR) / "Jenkinsfile.seed"

# Every job DSL the seed job applies, pinned explicitly. Discovery is
# asserted to match this exactly, which turns two silent gaps into loud
# failures: an environment missing the sibling projects (the gain-core CI
# image, before core/Dockerfile copied them in) would otherwise check
# fewer files and still pass, and a newly added job would slip in without
# anyone considering the branch invariant for it.
EXPECTED_JOB_DSLS = frozenset({
    "core/jenkins-jobs/integration.groovy",
    "jenkins-jobs/nightly.groovy",
    "jenkins-jobs/python_matrix.groovy",
    "jenkins-jobs/release.groovy",
    "spliceai_annotator/jenkins-jobs/integration.groovy",
    "vep_annotator/jenkins-jobs/integration.groovy",
    "web_e2e/jenkins-jobs/e2e.groovy",
})

# `branch(<literal>)` inside the cpsScm git block.  The quote characters
# are captured deliberately -- which quote was used is the whole point.
BRANCH_CALL_RE = re.compile(
    r"branch\(\s*(?P<literal>'[^']*'|\"[^\"]*\")\s*\)",
)

# `stringParam('BRANCH_NAME', ...)`, wrapped across lines in every DSL.
# Groovy treats both quote styles alike for a plain literal, so both must
# be recognised -- missing one would silently reclassify a job as taking
# no branch parameter, which is the weaker half of this module's pins.
BRANCH_PARAM_RE = re.compile(r"""stringParam\(\s*['"]BRANCH_NAME['"]""")

# The build parameter a branch-parameterized job must defer to.
BRANCH_PARAM_REFERENCE = "${BRANCH_NAME}"

# Marks a job whose pipeline script is loaded from an SCM branch. A DSL
# without it (a view, a folder, a multibranch job that discovers its own
# branches) has no such branch to get wrong.
CPS_SCM_MARKER = "cpsScm"

BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_comments(source: str) -> str:
    """Groovy source with comments removed.

    Quote-aware, because every one of these DSLs contains a
    ``url('https://...')`` whose ``//`` must not read as a comment. The
    house style also puts long explanatory comments right next to
    ``branch()``, so a comment mentioning an old branch value would
    otherwise be picked up as a second, contradictory call.
    """
    source = BLOCK_COMMENT_RE.sub(" ", source)

    kept = []
    for line in source.splitlines():
        quote = ""
        cut = len(line)
        for index, char in enumerate(line):
            if quote:
                if char == quote:
                    quote = ""
            elif char in "'\"":
                quote = char
            elif char == "/" and line[index:index + 2] == "//":
                cut = index
                break
        kept.append(line[:cut])
    return "\n".join(kept)


def _declares_branch_parameter(source: str) -> bool:
    return BRANCH_PARAM_RE.search(_strip_comments(source)) is not None


def _loads_pipeline_from_scm(source: str) -> bool:
    return CPS_SCM_MARKER in _strip_comments(source)


def _branch_literal(source: str) -> str:
    """The single ``branch()`` argument, with its quotes, as written."""
    matches: list[str] = BRANCH_CALL_RE.findall(_strip_comments(source))
    assert len(matches) == 1, (
        f"expected exactly one branch() call in the cpsScm block, found "
        f"{len(matches)}: {matches}"
    )
    return matches[0]


def _references_branch_parameter(literal: str) -> bool:
    """Whether Jenkins will expand this literal per build.

    Requires the single-quoted form -- a double-quoted literal is
    interpolated by Groovy at seed time and never reaches Jenkins. The
    reference may be bare or the git plugin's ``*/`` refspec form; both
    resolve the same way.
    """
    return (
        literal.startswith("'")
        and literal.endswith("'")
        and BRANCH_PARAM_REFERENCE in literal
    )


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
def _source_of(dsl_path: pathlib.Path) -> str:
    return dsl_path.read_text()


def _dsl_id(dsl_path: pathlib.Path) -> str:
    return str(dsl_path.relative_to(GAIN_ROOT))


def _scm_job_dsls() -> list[pathlib.Path]:
    return [
        path
        for path in _job_dsl_files()
        if _loads_pipeline_from_scm(_source_of(path))
    ]


def _branch_parameterized_dsls() -> list[pathlib.Path]:
    return [
        path
        for path in _scm_job_dsls()
        if _declares_branch_parameter(_source_of(path))
    ]


def _fixed_branch_dsls() -> list[pathlib.Path]:
    return [
        path
        for path in _scm_job_dsls()
        if not _declares_branch_parameter(_source_of(path))
    ]


SCM_JOB_TEMPLATE = """\
pipelineJob('gain-example') {{
    parameters {{
        stringParam(
            {param_quote}BRANCH_NAME{param_quote},
            'master',
            'Branch the upstream build was triggered from.',
        )
    }}
    definition {{
        cpsScm {{
            scm {{
                git {{
                    remote {{
                        url('https://github.com/iossifovlab/gain.git')
                    }}
                    {comment}
                    branch({literal})
                }}
            }}
            scriptPath('core/Jenkinsfile.integration')
        }}
    }}
}}
"""


def _a_scm_job(
    literal: str = "'${BRANCH_NAME}'",
    param_quote: str = "'",
    comment: str = "",
) -> str:
    return SCM_JOB_TEMPLATE.format(
        literal=literal, param_quote=param_quote, comment=comment,
    )


@pytest.mark.parametrize("param_quote", ["'", '"'])
def test_branch_parameter_is_recognised_in_either_quote_style(
    param_quote: str,
) -> None:
    """Groovy reads both alike, so a job cannot dodge the pin by quoting.

    Recognising only one style would file the job under the weaker
    "no branch parameter" pin, which permits exactly the defect.
    """
    assert _declares_branch_parameter(_a_scm_job(param_quote=param_quote))


def test_a_comment_mentioning_branch_is_not_read_as_a_call() -> None:
    """The house style puts long comments right beside ``branch()``."""
    source = _a_scm_job(
        comment="// this used to say branch('develop'), see #598",
    )

    assert _branch_literal(source) == "'${BRANCH_NAME}'"


def test_a_url_containing_a_double_slash_is_not_read_as_a_comment() -> None:
    """Every one of these DSLs has a ``url('https://...')``."""
    assert "github.com" in _strip_comments(_a_scm_job())


def test_a_dsl_without_cps_scm_has_no_pipeline_branch_to_pin() -> None:
    """Views, folders and multibranch jobs name no pipeline branch.

    They must not be swept into the pins below, which would fail them
    for having no ``branch()`` call at all.
    """
    assert not _loads_pipeline_from_scm("listView('gain-all') {\n}\n")


@pytest.mark.parametrize("literal", [
    "'${BRANCH_NAME}'",
    "'*/${BRANCH_NAME}'",
])
def test_per_build_expansion_accepts_both_git_plugin_refspec_forms(
    literal: str,
) -> None:
    assert _references_branch_parameter(literal)


@pytest.mark.parametrize("literal", [
    "'master'",
    '"${BRANCH_NAME}"',
])
def test_per_build_expansion_rejects_pinned_and_seed_interpolated_forms(
    literal: str,
) -> None:
    assert not _references_branch_parameter(literal)


def test_job_dsl_discovery_finds_every_seeded_job() -> None:
    """Guard the walk, so the pins below can never under-check.

    Everything else here is parametrized over discovered files, so a
    short discovery would quietly pin fewer jobs than it appears to.
    """
    assert (GAIN_ROOT / SEED_MARKER).is_file(), (
        f"GAIN_ROOT resolved to {GAIN_ROOT}, which does not look like the "
        f"gain repo root ({SEED_MARKER} is missing). If this is the "
        f"gain-core CI image, core/Dockerfile has stopped copying the job "
        f"DSL directories in."
    )

    discovered = {_dsl_id(path) for path in _job_dsl_files()}
    assert discovered == set(EXPECTED_JOB_DSLS), (
        f"the set of job DSLs changed.\n"
        f"  missing:   {sorted(set(EXPECTED_JOB_DSLS) - discovered)}\n"
        f"  unexpected: {sorted(discovered - set(EXPECTED_JOB_DSLS))}\n"
        f"If a job was added, add it to EXPECTED_JOB_DSLS -- and if it "
        f"takes a BRANCH_NAME parameter, make sure its cpsScm block reads "
        f"the pipeline from that branch. If a job went missing, check that "
        f"core/Dockerfile still copies its directory into the CI image."
    )


@pytest.mark.parametrize("dsl_path", _branch_parameterized_dsls(), ids=_dsl_id)
def test_branch_parameterized_job_loads_its_pipeline_from_that_branch(
    dsl_path: pathlib.Path,
) -> None:
    literal = _branch_literal(_source_of(dsl_path))
    assert _references_branch_parameter(literal), (
        f"{_dsl_id(dsl_path)} accepts a BRANCH_NAME parameter but reads "
        f"its pipeline script from {literal}. A branch build would run "
        f"that branch's source through a different branch's Jenkinsfile, "
        f"so changes to the Jenkinsfile cannot be tested before merge "
        f"(gain#272, gain#598). Use the single-quoted "
        f"'{BRANCH_PARAM_REFERENCE}' so Jenkins expands it per build."
    )


@pytest.mark.parametrize("dsl_path", _fixed_branch_dsls(), ids=_dsl_id)
def test_job_without_a_branch_parameter_does_not_reference_one(
    dsl_path: pathlib.Path,
) -> None:
    literal = _branch_literal(_source_of(dsl_path))
    assert BRANCH_PARAM_REFERENCE not in literal, (
        f"{_dsl_id(dsl_path)} reads its pipeline script from {literal} "
        f"but declares no BRANCH_NAME parameter for Jenkins to expand it "
        f"from. Add the parameter or pin a fixed branch."
    )
