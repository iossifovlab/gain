# CI image for `gain-spliceai-annotator`.
#
# Build from the repo root:
#     docker build -f spliceai_annotator/Dockerfile -t gain-spliceai-annotator-ci .
#
# TENSORFLOW (default 1) adds the `tensorflow` extra, the optional backend
# (ADR 0042). Only that build pulls TensorFlow (~1 GB wheel, ~2 GB
# installed), so build time and image size are proportionally large there.
# TensorFlow has no Python 3.14 wheels, so the 3.14 matrix cell builds with
#     --build-arg PYTHON_IMAGE=python:3.14-slim --build-arg TENSORFLOW=0
# and runs the ONNX-only suite.

# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

ARG TENSORFLOW=1

COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /uvx /usr/local/bin/

# Build deps required by transitive gain-core dependencies (pyBigWig).
#
# libhdf5-dev + pkg-config are for h5py, which arrives through
# `gain-core` -> `anndata` and ships no cp314 wheel before 3.15.0, so the
# 3.14 cell builds it from its sdist. See core/Dockerfile for why the lock
# cannot move past h5py 3.14.0, and when these can come back out.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libcurl4-openssl-dev \
        libhdf5-dev \
        pkg-config \
        git \
    && rm -rf /var/lib/apt/lists/*

# Wait out a slow package index, as the other CI images do (#2429): this
# image is a gain-python-matrix cell too.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_HTTP_TIMEOUT=120 \
    UV_LOCK_TIMEOUT=900 \
    PYTHONHASHSEED=0

WORKDIR /workspace

COPY pyproject.toml uv.lock ruff.toml mypy.ini pylintrc ./
# The sdist of each member copies the repository LICENSE from one level
# up (`force-include` of `../LICENSE`), so `uv build` needs it here.
COPY LICENSE ./
COPY core/pyproject.toml core/README.md ./core/
COPY demo_annotator/pyproject.toml ./demo_annotator/
COPY vep_annotator/pyproject.toml ./vep_annotator/
COPY spliceai_annotator/pyproject.toml ./spliceai_annotator/

# gain-core is an editable workspace dep — its source must be present when uv sync
# runs, so copy it upfront. (gain-spliceai-annotator's own source comes later.)
COPY core/gain/ ./core/gain/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package gain-spliceai-annotator --group dev --frozen --no-install-project \
        $([ "$TENSORFLOW" = 1 ] && echo --extra tensorflow)

COPY spliceai_annotator/ ./spliceai_annotator/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package gain-spliceai-annotator --group dev --frozen \
        $([ "$TENSORFLOW" = 1 ] && echo --extra tensorflow)

ENV PATH="/workspace/.venv/bin:$PATH"
WORKDIR /workspace/spliceai_annotator

CMD ["pytest", "-v", "tests/"]
