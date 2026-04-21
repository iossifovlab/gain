# GAIn Monorepo — Agent Guide

This file provides guidance to Claude Code when working
with code in this repository.

## Project Overview

GAIn (Genomic Annotation Infrastructure) is the
annotation engine and genomic resource framework used by
the GPF (Genotypes and Phenotypes in Families) system.
This repository hosts `core` plus a set of
annotator plugins.

## Environment Setup

Two supported workflows — pick one.

### Conda/Mamba

```bash
mamba env create --name gain --file ./environment.yml
mamba env update --name gain --file ./dev-environment.yml
conda activate gain

pip install -e core
pip install -e demo_annotator     # optional
pip install -e vep_annotator      # optional
pip install -e spliceai_annotator # optional
```

### uv workspace

The repo root declares a `[tool.uv.workspace]` with the
four sub-projects as members. Runtime deps are in each
member's pyproject; dev tools live in the root `dev`
dependency group. `uv.lock` is committed.

```bash
uv sync --all-packages --all-groups
source .venv/bin/activate   # optional; `uv run` works without activation
```

## Commands

### Testing

```bash
# Run a single test file
cd core && pytest -v tests/small/path/to/test_file.py

# Run a test module
cd core && pytest -v tests/small/module/

# Run GAIn tests in parallel
cd core && pytest -v -n 10 tests/
```

Test markers in `core/pytest.ini`: `grr_rw`,
`grr_ro`, `grr_full`, `grr_http`, `grr_tabix`.

All tests run with `PYTHONHASHSEED=0`.

### Linting and Type Checking

```bash
# Ruff linting (fast, primary linter)
ruff check --fix .

# Type checking (slow)
mypy gain --exclude core/docs/ \
    --exclude core/gain/docs/
```

Config: `ruff.toml` (line-length: 80, target: py310),
`mypy.ini`.

### Pre-commit Hook

```bash
cp pre-commit .git/hooks/
```

The pre-commit hook runs `ruff check` (ignoring FIX
warnings) on staged `.py` files.

### Test Infrastructure (Docker)

Some tests require external services. Start them with:

```bash
docker compose up -d
```

Services defined in `docker-compose.yaml`:
- **MinIO** (ports 9000/9001) — S3-compatible object
  storage for S3 storage tests; credentials
  `minioadmin/minioadmin`, bucket `test-bucket`
- **Apache httpd** (port 28080) — HTTP fixture server
  for `grr_http` tests; serves
  `core/tests/.test_grr/`

## Architecture

### Package Structure

- **`core/`** — GAIn (Genomic Annotation
  Infrastructure): annotation engine, genomic resources,
  effect annotation, task graph, gene scores/sets.
  Python package: `gain`.
- **`spliceai_annotator/`**,
  **`vep_annotator/`**,
  **`demo_annotator/`** — external annotation
  plugins (Docker-based)

### Plugin System

GAIn uses Python entry points for extensibility.

**Defined in `core/setup.py`:**

1. **`gain.genomic_resources.plugins`** — genomic
   context providers (DefaultRepository, CLI,
   CLIAnnotation)
2. **`gain.genomic_resources.implementations`** —
   position/allele/NP scores, liftover chain, genome,
   gene models, CNV collection, annotation pipeline,
   gene score, gene set collection
3. **`gain.annotation.annotators`** — all built-in
   annotator types (score, effect, gene set, liftover,
   normalize allele, CNV collection, chrom mapping,
   gene score, simple effect, debug)

Annotator plugins in this repo register additional
annotators via their own entry points.

### GAIn Submodules (`core/gain/`)

- **`annotation/`** — annotation pipeline engine,
  annotator base classes, all built-in annotators,
  processing pipeline, annotation config parsing
- **`genomic_resources/`** — Genomic Resource Repository
  (GRR): repository hierarchy (cached, group, factory),
  resource implementations, fsspec protocol, genomic
  context system. Sub-packages:
  - `gene_models/` — gene model parsing and
    serialization
  - `genomic_position_table/` — tabular data backends
    (tabix, BigWig, VCF, in-memory)
  - `implementations/` — resource type implementations
    (scores, genome, gene models, liftover, CNV,
    annotation pipeline)
  - `statistics/` — resource statistics (min/max)
- **`effect_annotation/`** — variant effect prediction
  (effect types, effect gene/transcript annotation)
- **`task_graph/`** — DAG-based task orchestration
- **`gene_scores/`** — gene-level score resources and
  implementations
- **`gene_sets/`** — gene set collection resources and
  implementations
- **`dask/`** — dask named cluster configuration
- **`testing/`** — test fixture helpers for study import
  (acgt, alla, foobar, t4c8 datasets)
- **`utils/`** — shared utilities (fs_utils, helpers)

### Test Structure

`core` uses a `tests/small/` vs `tests/integration/`
split:
- `tests/small/` — unit/fast tests (default for
  development and CI)
- `tests/integration/` — tests requiring external
  services or longer runtime

Key conftest patterns:
- **`grr_scheme` parametrization** — tests tagged with
  `grr_rw`, `grr_full`, `grr_http`, `grr_tabix` markers
  are automatically parametrized across GRR protocols
  (inmemory, file, s3, http). Enable S3/HTTP with
  `--enable-s3-testing` / `--enable-http-testing`.
- Architecture tests in `core/tests/` use
  `pytestarch` to enforce the package's internal
  structure.

### CLI Tools

**core CLIs:**
- `grr_manage` — genomic resource repository management
- `grr_browse` — GRR browser
- `annotate_columns` / `annotate_vcf` / `annotate_doc`
  — annotation tools
- `annotate_variant_effects` /
  `annotate_variant_effects_vcf` — effect annotation

## Key Dependencies

- **Python 3.12**
- **DuckDB 1.5**
- **dask** — parallel computing
- **pandas 2.2**, **numpy 2.2**, **pyarrow >=18** — data
  analysis
- **pysam 0.23** — SAM/BAM file handling
- **pydantic 2.8** — data validation
- **lark 1.2** — parsing (GRR search grammar)
- **fsspec / s3fs** — filesystem abstraction + S3 access
- Dev: **ruff 0.14**, **mypy 1.15**, **pytest**,
  **pytest-xdist**, **pytestarch**

