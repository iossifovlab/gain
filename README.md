# GAIn: Genomic Annotation Infrastructure

GAIn is the annotation engine and genomic resource
framework used by the GPF (Genotypes and Phenotypes in
Families) system. It provides the annotation pipeline,
the Genomic Resource Repository (GRR), effect annotation,
task graph orchestration, and gene scores/sets.

User documentation: see the GPF documentation at
https://iossifovlab.com/gpfuserdocs/.

## Repository overview

Python packages (uv workspace members):

- **`core/`** — GAIn core: annotation engine, genomic
  resources, effect annotation, task graph, gene
  scores/sets. Python package: `gain`.
- **`web_api/`** — Django backend serving the GAIn
  web API. Python package: `gain-web-api`.
- **`spliceai_annotator/`**, **`vep_annotator/`**,
  **`demo_annotator/`** — external annotation plugins
  (Docker-based, optional workspace members).

Web stack and deployment:

- **`web_ui/`** — Angular frontend, served behind Apache
  in production.
- **`web_e2e/`** — Playwright end-to-end tests
  exercising the full web_api + web_ui stack.
- **`web_infra/`** — Docker compose files for deploying
  the production web stack.

CI / release plumbing:

- **`Jenkinsfile`** — root multibranch CI: lint, tests,
  per-package wheels and conda builds, prod Docker
  images, dispatches the release pipeline on CalVer
  tags.
- **`Jenkinsfile.release`** — tag-driven release
  pipeline (`gain-release`): rebuilds wheels, conda
  packages, and digest-pinned prod Docker images for a
  tagged commit, then publishes wheels to
  `wheels.seqpipe.org`, conda to Anaconda.org, and
  Docker images to `registry.seqpipe.org`.
- **`conda-builder/`** — Docker image carrying the conda
  build/upload toolchain (rattler-build, anaconda-client,
  uv) used by both pipelines.
- **`jenkins-jobs/`** — Jenkins job DSLs
  (`release.groovy`, `Jenkinsfile.seed`).
- **`docker-compose.yaml`** — local fixture services
  (MinIO + Apache httpd) for tests that need S3 or
  HTTP-fetched genomic resources.

Other:

- **`docs/`** — design notes (one file per phase, dated).
- **`scripts/`** — helper scripts (lint output
  conversion, `wait-for-it.sh`).
- **`typings/`** — type stubs.

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
pip install -e web_api

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
Runtime dependencies are declared per sub-project; dev
tools live in each sub-project's own `dev` dependency
group. The root `pyproject.toml` is a virtual coordinator
(`[tool.uv] package = false`) that defaults to installing
just `gain-core` + `gain-web-api` — the
annotator plugins are workspace members but optional.

```bash
# Default: install gain-core + gain-web-api
# (no annotators, no dev tools)
uv sync

# Everything: all workspace members + every dev group
uv sync --all-packages --all-groups

# A single sub-project (matches the per-project CI Dockerfiles)
uv sync --package gain-spliceai-annotator --group dev

# Activate the venv (optional; `uv run` works without it)
source .venv/bin/activate
```

The lockfile (`uv.lock`) is committed. Use `uv lock
--upgrade` to refresh.

### Run tests

Quick cycles (examples):

```bash
cd core
pytest -v tests/small/test_file.py
pytest -v tests/small/module/
```

Full suite (parallel):

```bash
cd core
pytest -v -n 10 tests/
```

Test markers and configuration are defined in
`core/pytest.ini` (e.g., `grr_rw`, `grr_ro`,
`grr_full`, `grr_http`, `grr_tabix`). Tests tagged
`grr_http` / `grr_full` need fixture services from
`docker-compose.yaml` running locally
(`docker compose up -d`) and are gated behind
`--enable-s3-testing` / `--enable-http-testing`.

### Linting and type checking

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
  venv, and re-run `uv sync` (or `uv sync --all-packages
  --all-groups` if you've installed the optional annotator
  members) after pulling changes to pick up lockfile
  updates.
- Some tests may be flaky with high parallelism; reduce
  `-n` or run without it.

## License

This project is licensed under the MIT License. See
`LICENSE` for details.
