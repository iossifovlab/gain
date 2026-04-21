# GAIn: Genomic Annotation Infrastructure

GAIn is the annotation engine and genomic resource
framework used by the GPF (Genotypes and Phenotypes in
Families) system. It provides the annotation pipeline,
the Genomic Resource Repository (GRR), effect annotation,
task graph orchestration, and gene scores/sets.

User documentation: see the GPF documentation at
https://iossifovlab.com/gpfuserdocs/.

## Repository overview

- **`core/`** — GAIn core: annotation engine,
  genomic resources, effect annotation, task graph,
  gene scores/sets. Python package: `gain`.
- **`spliceai_annotator/`**,
  **`vep_annotator/`**,
  **`demo_annotator/`** — external annotation
  plugins (Docker-based).

Primary stack: Python 3.12, dask, pandas, pyarrow,
duckdb, pysam, pytest, mypy, ruff.

## Development

Two supported workflows: Conda/Mamba (long-standing) and
uv (pyproject-driven). Pick one.

### Option A: Conda/Mamba

```bash
mamba env create --name gain --file ./environment.yml
mamba env update --name gain --file ./dev-environment.yml
conda activate gain

pip install -e core
# Optional annotator plugins:
pip install -e demo_annotator
pip install -e vep_annotator
pip install -e spliceai_annotator
```

Notes:

- Always activate the `gain` environment before running
  tools or tests.
- After changing package code, re-run the editable
  installs if imports fail.

### Option B: uv workspace

This repo is a uv workspace (see root `pyproject.toml`).
Runtime dependencies are declared per sub-project;
dev tools (pytest, ruff, mypy, stubs) live in the root
`dev` dependency group.

```bash
# Create .venv and install every workspace member + dev tools
uv sync --all-packages --all-groups

# Activate the venv (optional; `uv run` works without it)
source .venv/bin/activate
```

The lockfile (`uv.lock`) is committed. Use `uv lock
--upgrade` to refresh.

### 3) Run tests

Quick cycles (examples):

```bash
cd core
pytest -v tests/small/test_file.py
pytest -v tests/small/module/
```

Full suite (parallel):

```bash
cd core
conda run -n gain pytest -v -n 10 tests/
```

Test markers and configuration are defined in
`core/pytest.ini` (e.g., `grr_rw`, `grr_ro`,
`grr_full`, `grr_http`, `grr_tabix`).

### 4) Linting and type checking

```bash
ruff check --fix .
mypy gain --exclude core/docs/ \
    --exclude core/gain/docs/
```

### Pre-commit lint check hook

A git pre-commit hook for lint checking with Ruff is
included. Install it from the repository root:

```bash
cp pre-commit .git/hooks/
```

To bypass the pre-commit hook when committing:

```bash
git commit --no-verify
```

## Common pitfalls

- Conda users: always activate the `gain` environment
  before running commands (`conda activate gain`), and
  re-run `pip install -e core` if imports fail.
- uv users: prefer `uv run <cmd>` over activating the
  venv, and re-run `uv sync --all-packages --all-groups`
  after pulling changes to pick up lockfile updates.
- Some tests may be flaky with high parallelism; reduce
  `-n` or run without it.

## License

This project is licensed under the MIT License. See
`LICENSE` for details.
