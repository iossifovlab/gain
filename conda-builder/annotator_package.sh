#!/bin/bash
# Build and verify one annotator's conda package outside the root
# pipeline: the 'Conda package' stage of gain-spliceai-integration and
# gain-vep-integration runs it inside the gain-conda-builder-ci image with
# the workspace as the cwd (#1601). The root Jenkinsfile's 'Conda
# packages' stage builds and verifies only gain-core and
# gain-demo-annotator.
#
# Usage: annotator_package.sh <project dir>, e.g. spliceai_annotator.
#
# Inputs, copied from the upstream root build by the calling stage:
#   dist/<project dir>/*.whl         the annotator's wheel (exactly one)
#   dist/conda/gain-core-*.conda     the gain-core package (exactly one)
# Both come from the same build, so the gain-core version must equal the
# wheel's; a mismatch means the copy mixed two builds and fails here.
#
# 1. The gain-core .conda becomes a local channel (conda/core), indexed
#    with rattler-index -- the annotators' gain-core run dep resolves
#    from it, as it does from the root build's own output dir.
# 2. The recipe is built with `--test skip`, like the root stage.
# 3. conda-builder/verify_packages.sh runs the recipe's checks from the
#    agent-local package cache the stage mounts.
set -euo pipefail

if [ "$#" -ne 1 ]; then
    echo "usage: $0 <project dir>" >&2
    exit 2
fi
proj="$1"

shopt -s nullglob
wheels=(dist/"$proj"/*.whl)
cores=(dist/conda/gain-core-*.conda)
shopt -u nullglob
if [ "${#wheels[@]}" -ne 1 ] || [ "${#cores[@]}" -ne 1 ]; then
    echo "ERROR: expected exactly one dist/$proj/*.whl and one" \
         "dist/conda/gain-core-*.conda, found ${#wheels[@]} and" \
         "${#cores[@]}" >&2
    ls -la dist/"$proj" dist/conda >&2 || true
    exit 1
fi

# <dist>-<version>-py3-none-any.whl; conda: gain-core-<version>-<build>.conda
VCS_VERSION="$(basename "${wheels[0]}" | sed 's#^[^-]*-##; s#-py3-none-any.whl$##')"
core_stem="$(basename "${cores[0]}" .conda)"
core_stem="${core_stem#gain-core-}"
core_version="${core_stem%-*}"
echo "VCS_VERSION=$VCS_VERSION (gain-core $core_version)"
if [ "$core_version" != "$VCS_VERSION" ]; then
    echo "ERROR: gain-core $core_version and the $proj wheel" \
         "$VCS_VERSION are from different builds" >&2
    exit 1
fi
export VCS_VERSION

rm -rf conda/core "conda/$proj"
# An empty linux-64 alongside noarch keeps libmamba from warning about a
# missing subdir on every solve.
mkdir -p conda/core/noarch conda/core/linux-64 "conda/$proj"
cp "${cores[0]}" conda/core/noarch/
rattler-index fs conda/core

rattler-build build \
    --test skip \
    --recipe "$proj/conda-recipe/recipe.yaml" \
    --output-dir "conda/$proj" \
    -c conda-forge -c bioconda

exec bash conda-builder/verify_packages.sh "$proj"
