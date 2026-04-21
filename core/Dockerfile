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
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /uvx /usr/local/bin/

# System deps for building wheels that have no prebuilt Python 3.12 binaries
# (notably pyBigWig: needs gcc + libcurl).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0.dev0

WORKDIR /workspace

# Workspace manifests first — maximises layer cache hits when only source changes.
COPY pyproject.toml uv.lock ./
COPY core/pyproject.toml ./core/
COPY demo_annotator/pyproject.toml ./demo_annotator/
COPY vep_annotator/pyproject.toml ./vep_annotator/
COPY spliceai_annotator/pyproject.toml ./spliceai_annotator/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package gain-core --group dev --frozen --no-install-project

COPY core/ ./core/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package gain-core --group dev --frozen

ENV PATH="/workspace/.venv/bin:$PATH"
WORKDIR /workspace/core

CMD ["pytest", "-v", "-n", "auto", "tests/"]
