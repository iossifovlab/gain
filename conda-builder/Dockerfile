# CI image used to build conda packages for every sub-project with
# rattler-build. Intentionally minimal: just rattler-build itself plus `git`
# (some recipes embed vcs-sourced metadata via hatch-vcs when building from
# source trees).
#
# Usage:
#   docker build -f conda-builder/Dockerfile -t gain-conda-builder-ci .
#   docker run --rm \
#       -v $PWD:/workspace \
#       -w /workspace \
#       gain-conda-builder-ci \
#       rattler-build build --recipe core/conda-recipe/recipe.yaml \
#           --output-dir conda/core

# syntax=docker/dockerfile:1.7
FROM mambaorg/micromamba:2.3.2

USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
    && rm -rf /var/lib/apt/lists/*

USER mambauser
RUN micromamba install -n base -y -c conda-forge rattler-build \
    && micromamba clean --all --yes

# Make the rattler-build binary callable directly without `micromamba run`.
ENV PATH="/opt/conda/bin:$PATH"

WORKDIR /workspace
