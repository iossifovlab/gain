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

# System deps for building wheels that have no prebuilt Python 3.12 binaries
# (notably pyBigWig: needs gcc + libcurl).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libcurl4-openssl-dev \
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
# in numba, llvmlite, scikit-learn and statsmodels for two readers most GRRs
# never touch. Only the integration image asks for it, to run the test that
# pins scanpy's 10x layout rule against the real reader.
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
