# GAIn: Genomic Annotation Infrastructure

GAIn is the annotation engine and genomic resource
framework used by the GPF (Genotypes and Phenotypes in
Families) system. It provides the annotation pipeline,
the Genomic Resource Repository (GRR), effect annotation,
task graph orchestration, and gene scores/sets.

User documentation: see the GPF documentation at
https://iossifovlab.com/gpfuserdocs/.

## Repository overview

- **`gain_core/`** — GAIn core: annotation engine,
  genomic resources, effect annotation, task graph,
  gene scores/sets. Python package: `gain`.
- **`gain_spliceai_annotator/`**,
  **`gain_vep_annotator/`**,
  **`gain_demo_annotator/`** — external annotation
  plugins (Docker-based).

Primary stack: Python 3.12, dask, pandas, pyarrow,
duckdb, pysam, pytest, mypy, ruff.

## Development

We recommend using a Conda/Mamba environment. All
development tools (pytest, ruff, mypy) are installed via
Conda, not system pip.

### 1) Create and activate the environment

From the repository root:

```bash
mamba env create --name gain --file ./environment.yml
mamba env update --name gain --file ./dev-environment.yml

conda activate gain
```

Notes:
- Prefer `environment.yml` over the legacy
  `requirements.txt`.
- Always activate the `gain` environment before running
  tools or tests.

### 2) Install core package in editable mode

```bash
pip install -e gain_core
```

Annotator plugins are optional; install only the ones
you plan to use or develop:

```bash
pip install -e gain_demo_annotator
pip install -e gain_vep_annotator
pip install -e gain_spliceai_annotator
```

Tip: after changing package code, re-run the editable
installs if imports fail.

### 3) Run tests

Quick cycles (examples):

```bash
cd gain_core
pytest -v tests/small/test_file.py
pytest -v tests/small/module/
```

Full suite (parallel):

```bash
cd gain_core
conda run -n gain pytest -v -n 10 tests/
```

Test markers and configuration are defined in
`gain_core/pytest.ini` (e.g., `grr_rw`, `grr_ro`,
`grr_full`, `grr_http`, `grr_tabix`).

### 4) Linting and type checking

```bash
ruff check --fix .
mypy gain --exclude gain_core/docs/ \
    --exclude gain_core/gain/docs/
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

- Always activate the `gain` Conda environment before
  running commands: `conda activate gain`.
- Prefer `environment.yml` over `requirements.txt`
  (legacy).
- If imports fail after changes, re-run
  `pip install -e gain_core`.
- Some tests may be flaky with high parallelism; reduce
  `-n` or run without it.

## License

This project is licensed under the MIT License. See
`LICENSE` for details.
