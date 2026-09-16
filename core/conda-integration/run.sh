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
#    tests/integration from the test bed with python -m pytest.
#
# Every input has a default matching the Dockerfile's mounts; the job
# overrides only PYTHON_VERSION (empty = unpinned, what a user's
# `mamba create` sees; the nightly matrix of #1430 pins it).
set -euo pipefail

CONDA_DIST="${CONDA_DIST:-/dist/conda}"
REPORTS="${REPORTS:-/reports}"
TESTBED="${TESTBED:-/testbed}"
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

specs=("gain-core==$VERSION" "pytest>=9" "pytest-mock" "scanpy")
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
# what -m does. Only /testbed is added, never core/, so the assertion
# above still holds.
# cache_dir: the test bed is owned by the image user and the container
# runs as the agent's UID, so the default /testbed/.pytest_cache is not
# writable and every run would end on a PytestCacheWarning.
set +e
"$ENV_PREFIX/bin/python" -m pytest -v tests/integration \
    -o cache_dir=/tmp/pytest-cache \
    --junitxml="$REPORTS/pytest.xml"
pytest_exit=$?
set -e
echo "pytest exit code: $pytest_exit"
exit "$pytest_exit"
