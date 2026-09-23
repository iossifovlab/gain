#!/bin/bash
# Install check for the gain conda packages (#1601), run right after
# they are built with `rattler-build build --test skip`, inside the
# gain-conda-builder-ci image with the workspace as the cwd:
#
#   verify_packages.sh <project dir>...
#
# The root Jenkinsfile's 'Conda packages' stage verifies core and
# demo_annotator; gain-spliceai-integration and gain-vep-integration each
# verify their own annotator through conda-builder/annotator_package.sh,
# with the upstream build's gain-core .conda as conda/core.
#
# The checks are the ones each recipe's `tests:` block defines -- the
# entry-point walk, the top-level import, `pip check` -- and the two must
# stay in step: the recipes' blocks still run in Jenkinsfile.release and
# ship inside the .conda for `rattler-build test` users. What differs is
# where the environment comes from. rattler-build's package cache is a
# temp dir deleted with the build, so its test phase downloads every
# dependency (tensorflow for spliceai) on every build; here micromamba's
# package cache is $MAMBA_ROOT_PREFIX/pkgs, which the stage bind-mounts
# from the agent (${HOME}/conda_pkgs_cache) so it outlives the build.
#
# 1. One environment per package, sequentially, solved with
#    --override-channels --strict-channel-priority over the local
#    output dirs, then conda-forge, then bioconda. The iossifovlab channel
#    is deliberately absent so a PUBLISHED package can never shadow the
#    one under test (gpf#916).
# 2. In each: the walk, the import and `pip check`, stopping at the first
#    failure, from a scratch cwd -- run from the workspace, `import
#    demo_annotator` would find the source directory, not the package.
# 3. Every gain package in the environment must be the .conda under test:
#    the sha256 recorded in conda-meta is compared with the file in
#    conda/<proj>/noarch/. The version is only commit + date, so a
#    same-day rebuild of a commit reuses the filename; micromamba refetches
#    a cached package whose sha256 disagrees with the channel's record,
#    and this check is what proves the channel record was ours.
# 4. On exit, pass or fail, this build's own packages and its local
#    channels' repodata are removed from the shared cache. Nothing reuses
#    them -- the next build has a new version -- so leaving them would grow
#    the cache by every build's packages (spliceai alone is ~25 MB).
#    Third-party packages stay; `micromamba clean` by hand if it matters.
#    Two builds of the SAME version at once on one agent (a rebuild
#    overlapping the original) share these entries, and the first to
#    finish removes them under the other's softlinked environment; the
#    other fails and a re-run passes.
#
# Environments are installed with --always-softlink. The cache and the
# env prefixes sit behind different mounts, where micromamba cannot
# hardlink and would otherwise copy every package out of the cache --
# the whole gain-core dependency tree once per environment, tensorflow
# on top for spliceai. The environments live only for the checks, while
# the cache is mounted, and `rm -rf` of an env removes the links, not
# what they point at.
#
# Several executors on one agent share the cache concurrently.
# micromamba's own cache lock is not enough: when another process holds
# it, micromamba warns "Cannot lock" and carries on, treating the cache
# as unwritable for that run. So each `micromamba create`, and the
# cleanup's deletions, run under flock(1) on $PKGS/.verify.lock -- the
# containers share the host kernel and the bind-mounted file, so the
# lock holds across builds. Only the installs serialise (tens of seconds
# each), not the checks or the builds; a Jenkins lock() would serialise
# whole builds. CONDA_PKGS_DIRS pins the cache to that one directory. The
# workspace is mounted at its host path, not
# at a fixed /workspace, so each workspace's local channels have their
# own URL -- and so their own repodata cache entry -- and two builds never
# refresh the same entry with different contents.
set -euo pipefail

: "${VCS_VERSION:?VCS_VERSION must be the version the packages were built with}"
: "${MAMBA_ROOT_PREFIX:?MAMBA_ROOT_PREFIX must be set; its pkgs/ is the package cache}"

ROOT="$PWD"
PKGS="$MAMBA_ROOT_PREFIX/pkgs"
LOCK="$PKGS/.verify.lock"
export CONDA_PKGS_DIRS="$PKGS"
ENVS=/tmp/verify-envs
WALK="$ROOT/core/conda-recipe/tests/entry_point_walk.py"

# <project dir> <package name> <top-level module>, for every gain recipe.
# The arguments pick which to verify; cleanup sweeps them all, since an
# annotator's environment also pulls in gain-core.
PACKAGES=(
    "core gain-core gain"
    "demo_annotator gain-demo-annotator demo_annotator"
    "vep_annotator gain-vep-annotator vep_annotator"
    "spliceai_annotator gain-spliceai-annotator spliceai_annotator"
)

if [ "$#" -eq 0 ]; then
    echo "usage: $0 <project dir>..." >&2
    exit 2
fi
SELECTED=()
for proj in "$@"; do
    found=""
    for entry in "${PACKAGES[@]}"; do
        if [ "${entry%% *}" = "$proj" ]; then
            found="$entry"
        fi
    done
    if [ -z "$found" ]; then
        echo "ERROR: $proj is not a gain recipe project" >&2
        exit 2
    fi
    SELECTED+=("$found")
done

cleanup() {
    local proj name module started=$SECONDS
    exec 9>"$LOCK"
    flock 9
    for entry in "${PACKAGES[@]}"; do
        read -r proj name module <<< "$entry"
        rm -rf "$PKGS/$name-$VCS_VERSION-"*
    done
    # Repodata cache entries of this workspace's file:// channels; the
    # state file next to each names the URL it was fetched from.
    local state
    for state in "$PKGS"/cache/*.state.json; do
        [ -e "$state" ] || continue
        if grep -q "\"file://$ROOT/conda/" "$state"; then
            rm -f "${state%.state.json}".*
        fi
    done
    flock -u 9
    rm -rf "$ENVS"
    echo "=== cleanup $((SECONDS - started)) s, verify total $SECONDS s"
}
trap cleanup EXIT

mkdir -p "$ENVS"
for entry in "${SELECTED[@]}"; do
    read -r proj name module <<< "$entry"
    env="$ENVS/$proj"
    echo "=== $name==$VCS_VERSION"
    started=$SECONDS
    flock "$LOCK" micromamba create -y -p "$env" \
        --always-softlink \
        --override-channels --strict-channel-priority \
        -c "file://$ROOT/conda/$proj" \
        -c "file://$ROOT/conda/core" \
        -c conda-forge -c bioconda \
        "$name==$VCS_VERSION" pip
    installed=$SECONDS

    (
        cd "$ENVS"
        "$env/bin/python" "$WALK" "$name"
        "$env/bin/python" -c "import $module"
        "$env/bin/python" -m pip check
    )

    # Installed = built, for every gain package the solve pulled in.
    for meta in "$env"/conda-meta/gain-*.json; do
        installed_sha=$("$env/bin/python" -c \
            'import json, sys; print(json.load(open(sys.argv[1]))["sha256"])' \
            "$meta")
        artefact="$(basename "$meta" .json).conda"
        built=""
        for candidate in "$ROOT"/conda/*/noarch/"$artefact"; do
            if [ -e "$candidate" ]; then
                built="$candidate"
            fi
        done
        if [ -z "$built" ]; then
            echo "ERROR: $artefact is installed but is not in conda/*/noarch/" >&2
            exit 1
        fi
        built_sha=$(sha256sum "$built" | cut -d' ' -f1)
        if [ "$installed_sha" != "$built_sha" ]; then
            echo "ERROR: installed $artefact is not the one under test:" >&2
            echo "  installed sha256 $installed_sha" >&2
            echo "  expected  sha256 $built_sha ($built)" >&2
            exit 1
        fi
        echo "installed = built: $artefact ($built_sha)"
    done
    echo "=== $name: solve + install $((installed - started)) s," \
         "checks $((SECONDS - installed)) s"
done
echo "all ${#SELECTED[@]} packages verified: $*"
