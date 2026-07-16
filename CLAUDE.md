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

The repo root is a virtual `gain-monorepo` project
(`[tool.uv] package = false`) that coordinates a
`[tool.uv.workspace]` of five members: `core`, `web_api`,
`demo_annotator`, `vep_annotator`, `spliceai_annotator`.
Runtime deps live in each member's pyproject; dev tools
live in each member's own `dev` dependency group.
`uv.lock` is committed. Default `uv sync` installs only
`gain-core` + `gain-web-api`; the annotator
plugins are workspace members but optional.

```bash
uv sync                              # core + web_api only
uv sync --all-packages --all-groups  # everything
uv sync --package gain-spliceai-annotator --group dev   # just one
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

Run these from `core/`, as CI does — the `core` image's
WORKDIR is `/workspace/core`, and `gain` is `core/gain`.

```bash
# Ruff linting (fast, primary linter)
cd core && ruff check --fix .

# Type checking (slow)
cd core && mypy --config-file ../mypy.ini gain

# Pylint (CI runs this too — see below)
cd core && pylint --rcfile=../pylintrc gain
```

Config: `ruff.toml` (line-length: 80, target: py312),
`mypy.ini`, `pylintrc` — all at the **repo root**, hence
the explicit `--config-file` / `--rcfile`. Ruff needs no
flag: it searches upward and finds `ruff.toml` on its own.

The cwd matters, and the two tools disagree about why:
`mypy gain` reads `gain` as a *path*, so it fails from the
root (`can't read file 'gain'`), while `pylint gain` reads
it as an installed *module* and works from either. Passing
`mypy.ini` explicitly is what makes the local run match CI
— without it, mypy finds no config from `core/` (there is
no `core/mypy.ini`) and silently falls back to defaults
looser than the ones CI enforces.

**CI runs three Python linters, not two.** The `Jenkinsfile`
lint stage runs **ruff + mypy + pylint** on each package
(plus eslint + stylelint for `web_ui`), and any finding from
any of them marks the build **UNSTABLE**. Running only
`ruff` + `mypy` locally is *not* enough to predict the lint
stage — always run `pylint --rcfile=../pylintrc gain` from
`core/` before committing too. A common pylint-only catch
ruff/mypy miss:
`C0103` on a module-level `UPPER_CASE` constant that is
*reassigned* (e.g. inside a `try`/`except`), which pylint then
treats as a snake_case variable — assign such constants
exactly once.

### Pre-commit Hook

```bash
cp pre-commit .git/hooks/
```

The pre-commit hook runs `ruff check` (ignoring FIX
warnings) on staged `.py` files.

### Documentation (`docs/`)

The Sphinx user docs (rendered at
<https://iossifovlab.com/gaindocs/>) live in `docs/`. The
build pulls an auto-generated module tree from `core/gain`.

```bash
# Install Sphinx toolchain
uv sync --group docs

# Build HTML + tarball
bash docs/build_docs.sh
open docs/build/html/index.html
```

The Jenkinsfile has `Build docs` (every branch) and
`Deploy docs` (master only, ansible to iossifovlab.com).
Pre-move history lives in `iossifovlab/gpf_documentation`.

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
- `annotate_tabular` / `annotate_vcf` / `annotate_doc`
  — annotation tools (`annotate_columns` is a deprecated
  alias of `annotate_tabular`)
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


<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
<!-- SPECKIT END -->
