# CI image for the gain-conda-integration job (#1429): installs the
# gain-core .conda the root build archived, the way a user would, and
# runs core/tests/integration against the INSTALLED package.
#
# Same micromamba base as conda-builder/ (which builds the .conda this
# image consumes). The image carries only tooling plus a copy of the
# test suite; the package under test, the node-local grr_seqpipe tree
# and the reports directory are bind-mounted at run time, so the image
# build is cheap and cacheable and the solve happens fresh every run.
#
# Usage (from the repo root; see core/Jenkinsfile.conda-integration):
#   docker build -f core/conda-integration/Dockerfile -t gain-conda-integration-ci .
#   docker run --rm --user "$(id -u):$(id -g)" \
#       -e HOME=/tmp -e MAMBA_ROOT_PREFIX=/tmp/mamba \
#       -v "$PWD/dist/conda:/dist/conda:ro" \
#       -v /data/grr/grr_seqpipe:/grr/grr_seqpipe:ro \
#       -v "$PWD/reports/conda-integration:/reports" \
#       -e GRR_INTEGRATION_DIR=/grr/grr_seqpipe \
#       gain-conda-integration-ci
#
# The container is run as the Jenkins user (not the image's mambauser)
# so the reports it writes are owned by the agent on the host, the same
# reason the root Jenkinsfile's `Conda packages` stage does it. That is
# why the env is created under /tmp (MAMBA_ROOT_PREFIX) rather than in
# /opt/conda, which an arbitrary UID cannot write to.

# syntax=docker/dockerfile:1.7
FROM mambaorg/micromamba:2.3.2

USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

USER mambauser
# rattler-index turns the flat directory of copied .conda files into a
# real channel (noarch/repodata.json) that `-c file://...` can solve
# from. Lighter than conda-index, which drags the whole `conda` python
# package into the base env (what gpf's docs_e2e image does).
RUN micromamba install -n base -y -c conda-forge rattler-index \
    && micromamba clean --all --yes

ENV PATH="/opt/conda/bin:$PATH"

# The test bed: core/tests + core/pytest.ini and NOTHING else from the
# source tree, so core/gain/ can never end up on sys.path and shadow
# the installed package. run.sh asserts that before pytest starts.
COPY --chown=mambauser:mambauser core/tests /testbed/tests
COPY --chown=mambauser:mambauser core/pytest.ini /testbed/pytest.ini
COPY --chown=mambauser:mambauser core/conda-integration/run.sh \
    core/conda-integration/assert_installed.py /opt/conda-integration/

WORKDIR /testbed
ENTRYPOINT ["/bin/bash", "/opt/conda-integration/run.sh"]
