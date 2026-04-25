# Plan: Phase 4 — Move web_api CI onto the uv-based annotator pattern

## Context

Phases 1–3 of the gpf-web-annotation merge are complete on `master`:
the code is in the right place, lint configs are reconciled, `web_api`
is a uv workspace member, and the Django stack resolves cleanly under
`uv sync`.

The original Phase 4 plan was to consolidate conda environment files.
The user redirected: rather than continue on the conda track, adopt
the uv-based Docker CI pattern that `core/`, `vep_annotator/`,
`spliceai_annotator/`, and `demo_annotator/` already use. Conda env
consolidation gets demoted to Phase 5.

## What "uv-based annotator pattern" looks like

`vep_annotator/Dockerfile` is the canonical template:

- `python:3.12-slim` base + `uv` copied from
  `ghcr.io/astral-sh/uv:0.11.3`.
- Two-stage `uv sync --package <name> --group dev --frozen`: deps
  first (with `--no-install-project`), then the project itself —
  maximises layer-cache reuse when only project source changes.
- Built from the repo root with the workspace manifests in context.
- Default `CMD ["pytest", ...]`; root `Jenkinsfile` overrides it to
  run ruff/mypy/pylint/pytest with JUnit + Cobertura output to
  mounted `/reports`.
- Root `Jenkinsfile`'s `runProject()` helper is the orchestration.

## Scope

**In scope:**
- Create `web_api/Dockerfile` mirroring `vep_annotator/Dockerfile`,
  with `libmagic1` for `python-magic` at import-time.
- Add `gain-core` to `web_api/pyproject.toml` runtime deps so the
  uv-only image can boot Django (the GRR providers are loaded at
  app start; conda used to provide gain-core implicitly via the
  conda env).
- Wire `web_api` into root `Jenkinsfile`:
  - `runProject()` gets an optional `distPkg` parameter so the
    PyPI-name override `django-gpf-web-annotation` flows through
    `uv build`.
  - New `web_api` parallel stage with mypy pointed at
    `/workspace/web_api/mypy.ini`.
  - `gain-web-api-ci` added to the cleanup list.
- Retire the conda-based CI machinery in `web_infra/`:
  - Rename `web_api/Dockerfile` to `web_api/Dockerfile.production`
    so the production runtime image has an explicit name and the
    annotator-convention `<project>/Dockerfile` is free for the new
    CI image.
  - Update `web_infra/compose-jenkins.yaml`,
    `compose-iossifovweb.yaml`, `compose-wigclust.yaml`,
    `compose.yaml` so backend/backend-e2e build from
    `Dockerfile.production`.
  - Delete `backend-tests` and `backend-linters` services from
    `web_infra/compose-jenkins.yaml` (and their extension stubs in
    `compose.yaml`).
  - Remove "Run Backend Linters" and "Run Backend Tests" stages and
    the backend recordCoverage / recordIssues / publishHTML blocks
    from `web_infra/Jenkinsfile`'s post section.
  - Fix the pre-existing typo on the "Run Frontend Tests" stage:
    `docker compose ... build backend-tests` becomes
    `build frontend-tests`.
  - Delete `web_api/scripts/backend_linters.sh` and
    `backend_tests.sh` (no longer invoked).

**Out of scope (later phases):**
- Production runtime image rework. `web_api/Dockerfile.production`,
  `web_infra/Dockerfile.gpf`, `web_infra/Dockerfile.ubuntu` are the
  conda+supervisor+apache chain that ships into deployment. Stays
  for now; reworking to uv is part of any future production-Docker
  unification.
- `backend-dev` service (developer convenience for runserver).
  Stays. Local-dev alternatives via `uv run python manage.py
  runserver` already work for those who prefer uv.
- `web_api/Dockerfile.dev` and `web_api/scripts/backend_run.sh`
  stay (used by `backend-dev`).
- Frontend / e2e CI in `web_infra/Jenkinsfile` (Phase 6/7).
- Conda env file consolidation (Phase 5).
- Conda recipe for web_api (not requested in this phase).

## Critical files

- `/home/lubo/Work/seq-pipeline/gain/web_api/Dockerfile` (created — CI image)
- `/home/lubo/Work/seq-pipeline/gain/web_api/Dockerfile.production`
  (renamed from `web_api/Dockerfile`)
- `/home/lubo/Work/seq-pipeline/gain/web_api/pyproject.toml` (gain-core added)
- `/home/lubo/Work/seq-pipeline/gain/Jenkinsfile` (web_api stage + distPkg)
- `/home/lubo/Work/seq-pipeline/gain/uv.lock` (regenerated for gain-core dep)
- `/home/lubo/Work/seq-pipeline/gain/web_infra/Jenkinsfile`
- `/home/lubo/Work/seq-pipeline/gain/web_infra/compose-jenkins.yaml`
- `/home/lubo/Work/seq-pipeline/gain/web_infra/compose.yaml`
- `/home/lubo/Work/seq-pipeline/gain/web_infra/compose-iossifovweb.yaml`
- `/home/lubo/Work/seq-pipeline/gain/web_infra/compose-wigclust.yaml`
- `/home/lubo/Work/seq-pipeline/gain/web_api/scripts/backend_linters.sh` (deleted)
- `/home/lubo/Work/seq-pipeline/gain/web_api/scripts/backend_tests.sh` (deleted)

## Reference files

- `/home/lubo/Work/seq-pipeline/gain/vep_annotator/Dockerfile` —
  primary template.
- `/home/lubo/Work/seq-pipeline/gain/core/Dockerfile` — secondary
  template.
- `/home/lubo/Work/seq-pipeline/gain/Jenkinsfile` — `runProject()`
  helper and the parallel sub-project stage block.

## Risks and known unknowns

- **gain-core as a runtime dep**: making this explicit in
  `web_api/pyproject.toml` is necessary for the uv-only image. The
  conda chain installed gain-core implicitly; uv won't unless we
  declare it. This is a small documentation-of-reality, not a
  behaviour change.
- **`distPkg` override**: existing callers (core, demo_annotator,
  vep_annotator, spliceai_annotator) don't pass `distPkg`, so the
  Elvis operator preserves their behaviour exactly.
- **Typo fix is a behaviour change**: `build frontend-tests` instead
  of `build backend-tests` is what the stage was always meant to
  do; fixing it changes which image the stage rebuilds. Strictly
  more correct.
- **Conda packaging stage**: the root Jenkinsfile's "Conda
  packages" stage loops over `core demo_annotator vep_annotator
  spliceai_annotator`. We don't add `web_api` to that loop because
  it has no `conda-recipe/`. If/when web_api wants a conda package
  shipped from gain CI, that's a follow-up.
- **Lint findings count**: the new uv-based ruff run surfaces ~683
  findings inside `web_annotation/`. They're not regressions — the
  conda-based lint was running with a slightly different config and
  was not blocking. The root pipeline produces JUnit XML for
  unstable-build signalling rather than failing on lint, so this
  doesn't break CI; it just exposes the work that exists.

## Verification (run during execution)

```bash
cd /home/lubo/Work/seq-pipeline/gain

# Image builds (cold)
docker build -f web_api/Dockerfile -t gain-web-api-ci .
# OK: bytes installed, image tag created.

# Pytest collects (Django settings + GRR providers boot inside the
# uv-only image)
docker run --rm gain-web-api-ci pytest --collect-only -q \
  web_annotation/tests/test_utils.py
# OK: 29 tests collected.

# Pytest runs
docker run --rm gain-web-api-ci pytest -v web_annotation/tests/test_utils.py
# OK: 29 passed in 12.76s.

# Ruff runs end-to-end (683 findings; not a failure, just feedback)
docker run --rm gain-web-api-ci ruff check web_annotation/
```

## Outcome

Phase 4 landed on `master` as two commits:

- `0bba3c5c3` — Retire web_api conda-based CI machinery: renames
  `web_api/Dockerfile` to `web_api/Dockerfile.production`, deletes
  the `backend-tests`/`backend-linters` services, removes the
  corresponding stages and post-block publishers in
  `web_infra/Jenkinsfile`, fixes the Frontend Tests typo, and
  deletes the backend_linters.sh / backend_tests.sh scripts.
- `221ac75df` — Add uv-based CI Dockerfile for web_api and wire it
  into root Jenkinsfile: new `web_api/Dockerfile`, `gain-core` added
  to `web_api/pyproject.toml`, `uv.lock` regenerated, root
  `Jenkinsfile` patched with the `web_api` stage and `distPkg`
  override, cleanup list updated.

Verification commands above all pass.

## After Phase 4

Phase 5 (revised) — Conda env file consolidation:
- Decide fate of `web_api/environment.yml` and
  `web_api/dev-environment.yml`. Production Docker chain still uses
  `web_api/environment.yml`. `web_api/dev-environment.yml` is now
  used only by `backend-dev` (developer convenience).
- Possibly fold matching deps into root `environment.yml` /
  `dev-environment.yml` for a single conda-mamba dev workflow at
  the repo root.

Phase 6+ unchanged: frontend tooling, e2e tests, production-Docker
rework, retire upstream `gpf-conda-packaging` coupling.
