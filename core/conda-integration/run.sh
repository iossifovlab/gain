#!/bin/bash
# Entry point of the gain-conda-integration image (#1429). Runs inside
# the container; see the Dockerfile next to it for the mounts and the
# UID it expects.
#
# 1. Index the archived gain-core .conda into a local file channel.
# 2. Solve `gain-core==<version off the filename>` plus the test-only
#    packages from `file:///channel, conda-forge, bioconda`, in that
#    order, with --override-channels --strict-channel-priority. The
#    iossifovlab channel is deliberately absent so a PUBLISHED gain-core
#    can never shadow the package under test (same reasoning as
#    gpf#916 for gpf-docs-e2e).
# 3. Assert the installed gain is the one from the .conda, then run
#    the small tier (core/tests minus tests/integration), then
#    tests/integration, from the test bed with python -m pytest (#1514).
#
# Every input has a default matching the Dockerfile's mounts; the job
# overrides only PYTHON_VERSION (empty = unpinned, what a user's
# `mamba create` sees; the nightly matrix of #1430 pins it).
set -euo pipefail

CONDA_DIST="${CONDA_DIST:-/dist/conda}"
REPORTS="${REPORTS:-/reports}"
TESTBED="${TESTBED:-/testbed/core}"
PYTHON_VERSION="${PYTHON_VERSION:-}"
: "${GRR_INTEGRATION_DIR:?GRR_INTEGRATION_DIR must point at the mounted grr_seqpipe tree}"

CHANNEL=/tmp/channel
ENV_PREFIX=/tmp/env

# --- 1. exactly one gain-core artefact, version off its filename -------
# rattler-build names it <name>-<version>-<build string>.conda, e.g.
# gain-core-2026.9.3.post1.dev13+gef302691a-pyh4616a5c_0.conda. The
# version is everything between the name and the LAST dash.
shopt -s nullglob
artefacts=("$CONDA_DIST"/gain-core-*.conda)
shopt -u nullglob
if [ "${#artefacts[@]}" -ne 1 ]; then
    echo "ERROR: expected exactly one gain-core-*.conda in $CONDA_DIST," \
         "found ${#artefacts[@]}:" >&2
    printf '  %s\n' "${artefacts[@]}" >&2
    exit 1
fi
artefact="${artefacts[0]}"
stem="$(basename "$artefact" .conda)"
stem="${stem#gain-core-}"
VERSION="${stem%-*}"
echo "package under test: $(basename "$artefact")"
echo "version: $VERSION"

# --- 2. local channel + solve -----------------------------------------
rm -rf "$CHANNEL" "$ENV_PREFIX"
# The package is noarch, but libmamba also asks the channel for the
# host platform's subdir and warns for every missing repodata variant;
# an empty linux-64 gets indexed too and keeps the log clean.
mkdir -p "$CHANNEL/noarch" "$CHANNEL/linux-64"
cp "$artefact" "$CHANNEL/noarch/"
rattler-index fs "$CHANNEL"
test -f "$CHANNEL/noarch/repodata.json" \
    || { echo "ERROR: rattler-index wrote no noarch/repodata.json" >&2; exit 1; }

# Test-only packages: what the tiers import that gain-core does not pull
# in. pytest-xdist for the small tier's -n 5; fonttools + brotli because
# the vendored-fonts tests decode WOFF2 directly; scanpy for the
# integration tier's reader-drift tests. pytestarch is deliberately
# absent: test_architecture.py is excluded below.
specs=("gain-core==$VERSION" "pytest>=9" "pytest-mock" "pytest-xdist"
       "fonttools" "brotli" "scanpy")
if [ -n "$PYTHON_VERSION" ]; then
    specs+=("python=$PYTHON_VERSION")
fi
echo "solving: ${specs[*]}"
# Printed with the flags spelled out so the console log is the evidence
# that no other channel took part (acceptance criterion of #1429).
set -x
micromamba create -y -p "$ENV_PREFIX" \
    --override-channels --strict-channel-priority \
    -c "file://$CHANNEL" -c conda-forge -c bioconda \
    "${specs[@]}"
set +x

# Explicit lockfile of what the solve picked, archived by the job so a
# red caused by conda-forge/bioconda moving is a one-diff diagnosis
# against the last green.
mkdir -p "$REPORTS"
micromamba env export -p "$ENV_PREFIX" --explicit > "$REPORTS/conda-env.explicit.txt"
echo "explicit lockfile: $REPORTS/conda-env.explicit.txt" \
     "($(wc -l < "$REPORTS/conda-env.explicit.txt") lines)"

# --- 3. shadowing guard, then the suite --------------------------------
cd "$TESTBED"
# Abort loudly if the `gain` the test bed imports is not the package
# from the artefact -- anything else (a stray sys.path entry, a second
# gain-core from another channel) would make a green run meaningless --
# or if pytest.ini's early plugins would not load from the test bed.
"$ENV_PREFIX/bin/python" /opt/conda-integration/assert_installed.py \
    "$ENV_PREFIX" "$VERSION" "$TESTBED"

# `python -m pytest`, not `pytest`: pytest.ini loads tests.dask_guard and
# tests.memory_guard as EARLY plugins (-p), which pytest resolves through
# sys.path before rootdir/conftest handling. In the uv image that works
# only because the editable install's .pth puts core/ on sys.path; here
# nothing does, so the cwd (the test bed) has to be added -- which is
# what -m does. Only the test bed's core/ (tests, pytest.ini,
# pyproject.toml) is added, never the source core/, so the assertion
# above still holds.
#
# Two tiers, both always run: the small tier first (#1514), then
# tests/integration. A red in one must not hide the other, so each gets
# its own JUnit file and the container exits non-zero if either did.
# test_architecture.py evaluates the SOURCE tree (it expects the gain
# package next to tests/, which this test bed deliberately lacks), so it
# is excluded here; the root pipeline keeps running it from the source.
# --enable-http-testing / --enable-s3-testing are not passed: without them
# the conftest does not generate the http/s3 scheme parametrizations, so
# the tier collects fewer items than the root's run, not more skips.
#
# Deselected, in this job only: the three TRACE-level tests, which fail
# whenever bokeh -- pulled in by conda-forge's dask metapackage, absent
# from the uv env -- has been imported earlier in the same worker and
# replaced logging.Logger.trace with its own (#1569). That is a real
# finding about the conda install, tracked there; here it would only be
# an order-dependent red.
set +e
"$ENV_PREFIX/bin/python" -m pytest -n 5 tests \
    --ignore=tests/integration --ignore=tests/test_architecture.py \
    --deselect tests/small/utils/test_log_levels.py::test_trace_emits_record \
    --deselect tests/small/utils/test_log_levels.py::test_trace_record_points_at_caller \
    --deselect tests/small/utils/test_log_levels.py::test_trace_honors_caller_supplied_stacklevel \
    --junitxml="$REPORTS/pytest-small.xml"
small_exit=$?
echo "pytest exit code (small tier): $small_exit"

"$ENV_PREFIX/bin/python" -m pytest -v tests/integration \
    --junitxml="$REPORTS/pytest-integration.xml"
integration_exit=$?
echo "pytest exit code (integration tier): $integration_exit"
set -e
if [ "$small_exit" -ne 0 ]; then exit "$small_exit"; fi
exit "$integration_exit"
