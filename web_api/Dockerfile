# CI image for `gain-web-api` (web_api).
#
# Build from the repo root so the workspace manifests and uv.lock are in the
# build context:
#     docker build -f web_api/Dockerfile -t gain-web-api-ci .
#
# Default CMD runs the test suite. Override for linters:
#     docker run --rm gain-web-api-ci ruff check web_annotation/
#     docker run --rm gain-web-api-ci mypy web_annotation

# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /uvx /usr/local/bin/

# Build deps for transitive wheels (pyBigWig from gain-core needs gcc +
# libcurl) plus libmagic1 at import-time for python-magic.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libcurl4-openssl-dev \
        libmagic1 \
        git \
    && rm -rf /var/lib/apt/lists/*

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONHASHSEED=0

WORKDIR /workspace

# Workspace manifests first — maximises layer cache hits when only source changes.
COPY pyproject.toml uv.lock ruff.toml mypy.ini pylintrc ./
COPY core/pyproject.toml core/README.md ./core/
COPY demo_annotator/pyproject.toml ./demo_annotator/
COPY vep_annotator/pyproject.toml ./vep_annotator/
COPY spliceai_annotator/pyproject.toml ./spliceai_annotator/
COPY web_api/pyproject.toml ./web_api/

# gain-core is loaded at Django boot via GRR providers; copy the package
# source so uv sync can install it editable (web_api's own source comes
# after the deps layer for cache reuse).
COPY core/gain/ ./core/gain/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package gain-web-api --group dev --frozen --no-install-project

COPY web_api/ ./web_api/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package gain-web-api --group dev --frozen

ENV PATH="/workspace/.venv/bin:$PATH"
WORKDIR /workspace/web_api

CMD ["pytest", "-v", "web_annotation/tests/"]
