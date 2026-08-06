# CI image for `gain-core`.
#
# Build from the repo root so the workspace manifests and uv.lock are in the
# build context:
#     docker build -f core/Dockerfile -t gain-core-ci .
#
# Default CMD runs the test suite. Override for linters:
#     docker run --rm gain-core-ci ruff check core/
#     docker run --rm gain-core-ci mypy gain

# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /uvx /usr/local/bin/

# System deps for building wheels that have no prebuilt binaries for the
# interpreter this image is built on (notably pyBigWig: needs gcc + libcurl).
#
# libhdf5-dev + pkg-config are for h5py, which `gain-core` pulls in through
# `anndata`. h5py ships no cp314 wheel before 3.15.0, and `uv.lock` cannot
# move past 3.14.0 while `spliceai_annotator` holds tensorflow 2.21.0, which
# requires `h5py>=3.11.0,<3.15.0` -- one lock resolves the whole workspace,
# so the cap binds `gain-core` too. On python:3.14-slim uv therefore builds
# h5py from its sdist, and that needs the HDF5 headers. The 3.12 and 3.13
# images resolve to a wheel and never compile it. Droppable once #295/#299
# take tensorflow out and the lock can go to h5py>=3.15.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libcurl4-openssl-dev \
        libhdf5-dev \
        pkg-config \
        git \
    && rm -rf /var/lib/apt/lists/*

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONHASHSEED=0

WORKDIR /workspace

# Workspace manifests first — maximises layer cache hits when only source changes.
COPY pyproject.toml uv.lock ruff.toml mypy.ini pylintrc ./
COPY core/pyproject.toml ./core/
COPY demo_annotator/pyproject.toml ./demo_annotator/
COPY vep_annotator/pyproject.toml ./vep_annotator/
COPY spliceai_annotator/pyproject.toml ./spliceai_annotator/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package gain-core --group dev --frozen --no-install-project

COPY core/ ./core/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package gain-core --group dev --frozen

# Optional gain-core extras, as uv flags -- e.g.
# `--build-arg UV_SYNC_EXTRAS="--extra ann-data-10x"`. Empty by default, so
# the main CI image is unchanged: scanpy is the ann_data_10x extra and pulls
# in numba, llvmlite, scikit-learn and statsmodels for one reader most GRRs
# never touch. Only the integration image asks for it, to run the tests that
# compare gain's own 10x reader against the live scanpy one.
#
# `--directory core` because the extra is declared on the gain-core member,
# and `--extra` resolves against the project uv is pointed at rather than
# against `--package`: run from the workspace root it fails with "Extra
# `ann-data-10x` is not defined in the project's optional-dependencies".
ARG UV_SYNC_EXTRAS=""
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -n "${UV_SYNC_EXTRAS}" ]; then \
        uv sync --directory core --package gain-core --group dev --frozen \
            ${UV_SYNC_EXTRAS}; \
    fi

ENV PATH="/workspace/.venv/bin:$PATH"
WORKDIR /workspace/core

CMD ["pytest", "-v", "-n", "auto", "tests/"]
