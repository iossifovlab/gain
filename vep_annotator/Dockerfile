# CI image for `gain-vep-annotator`.
#
# Build from the repo root:
#     docker build -f vep_annotator/Dockerfile -t gain-vep-annotator-ci .

# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /uvx /usr/local/bin/

# Build deps required by transitive gain-core dependencies (pyBigWig).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONHASHSEED=0

WORKDIR /workspace

COPY pyproject.toml uv.lock ruff.toml pylintrc ./
COPY core/pyproject.toml core/README.md ./core/
COPY demo_annotator/pyproject.toml ./demo_annotator/
COPY vep_annotator/pyproject.toml ./vep_annotator/
COPY spliceai_annotator/pyproject.toml ./spliceai_annotator/

# gain-core is an editable workspace dep — its source must be present when uv sync
# runs, so copy it upfront. (gain-vep-annotator's own source comes later for caching.)
COPY core/gain/ ./core/gain/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package gain-vep-annotator --group dev --frozen --no-install-project

COPY vep_annotator/ ./vep_annotator/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package gain-vep-annotator --group dev --frozen

ENV PATH="/workspace/.venv/bin:$PATH"
WORKDIR /workspace/vep_annotator

CMD ["pytest", "-v", "vep_annotator/tests/"]
