# Merge roadmap — gpf-web-annotation into gain

## Context

This doc consolidates the multi-phase merge of the upstream
`iossifovlab/gpf-web-annotation` project (Django backend, Angular
frontend, Playwright e2e suite, supporting compose machinery) into
the `gain` monorepo. It supersedes the roadmap section of
`docs/2026-04-25-merge-gpf-web-annotation-phase-1.md` (kept for
historical record) — the original plan listed eight phases, but
the actual delivery has drifted as the work uncovered new
dependencies and as the Phase 4 CI rollout surfaced lint debt that
needed an unplanned 4.5 to keep master green.

The destination shape is:

- One repo root with `core` + four annotator plugins + `web_api`
  + `web_ui` + `web_e2e` as flat siblings.
- One **uv workspace** with `pyproject.toml` at root and each
  Python sub-project as a member; one committed `uv.lock`.
- One **root `Jenkinsfile`** that runs every project's CI in
  parallel (lint + type-check + tests + coverage), with a
  shared `runProject()` helper for the Python projects and
  inline JS commands for the frontend / e2e.
- One unified **lint config**: root `ruff.toml`, root `mypy.ini`,
  root `pylintrc`, with per-project overrides only where
  framework specifics demand them (`web_api/mypy.ini` for
  Django stubs, `web_api/pylintrc` for `pylint_django`).
- The legacy `web_infra/Jenkinsfile` retires once e2e is on
  root CI; only the compose service definitions in
  `web_infra/` survive (they describe production deployment
  and the e2e fixture stack).

## Phases delivered

| # | Goal | Status | Plan doc |
|---|------|--------|----------|
| 1 | Subtree-merge `gpf-web-annotation`; rename `web/{api,ui,e2e,infra}` to flat siblings | DONE | `docs/2026-04-25-merge-gpf-web-annotation-phase-1.md` |
| 2 | Reconcile Python lint configs into one root `ruff.toml`/`mypy.ini`/`pylintrc` | DONE | `docs/2026-04-25-phase-2-lint-config-reconciliation.md` |
| 3 | Promote `web_api` to a uv workspace member; commit `uv.lock` | DONE | `docs/2026-04-25-phase-3-uv-workspace-promotion.md` |
| 4 | uv-based CI Dockerfile for `web_api`; wire it into root `Jenkinsfile`; MailHog sidecar for email-flow tests | DONE | `docs/2026-04-25-phase-4-uv-based-ci.md` |
| 4.5 | Pay down 690 lint findings + 8 mypy errors + empty `pylint.xml` so master master returns SUCCESS; wire `pylint-django` into web_api CI | DONE | `docs/2026-04-25-phase-4.5-lint-debt-cleanup.md` |
| 5 | `web_ui` (Angular) on root CI: `node:22.14.0-alpine` CI image pinned to production, committed `package-lock.json`, ESLint + Stylelint + Jest in one inline stage; retire `frontend-tests`/`frontend-linters` compose services | DONE | `docs/2026-04-25-phase-5-frontend-ci.md` |
| 6 | `web_e2e` (Playwright) on root CI: deterministic Playwright image (`npm ci`), sequential stage after `Conda packages` that publishes the in-monorepo `gain-*.conda` artefacts to a local channel for `gpf-image`; retire `web_infra/Jenkinsfile` and the upstream `gpf-conda-packaging` coupling for the e2e flow | DONE | `docs/2026-04-25-phase-6-e2e-ci.md` |
| 7 | Tail cleanup: retire orphaned `backend-dev` development image (`web_api/Dockerfile.dev` + `web_api/scripts/backend_run.sh` + `web_api/dev-environment.yml` + `backend-dev` compose service) and stale pre-merge `web_infra/Makefile` / `web_infra/README.md`. Conda dev workflow stays documented as a supported flow | DONE | `docs/2026-04-25-phase-7-tail-cleanup.md` |

Original Phase 1 roadmap drift summary (for the curious): the
original Phase 4 was "consolidate conda environments" — that's
been deferred to a later cleanup phase because uv-promoting
`web_api` (Phase 3) made the conda environment files mostly
vestigial anyway. Phase 5 of the original roadmap was "unified
Python CI" — that landed across Phases 4 (web_api) and the
existing root `Jenkinsfile` from before the merge. Original
Phase 6 ("frontend tooling") and Phase 7 ("e2e") got renumbered
down to 5 and 6 respectively. Phase 4.5 was inserted reactively
when Phase 4's CI rollout surfaced previously hidden lint debt.

## Current state (post-Phase 7)

- Root `Jenkinsfile` parallel block runs: `core`,
  `demo_annotator`, `vep_annotator`, `spliceai_annotator`,
  `web_api`, `web_ui`. Each writes JUnit + coverage to
  `reports/<project>/`. The post block archives reports +
  wheels + sdists + conda packages.
- After the parallel block, the root `Jenkinsfile` runs
  `Conda packages` (rattler-build for each gain-* recipe) and
  then `web_e2e`, which publishes the gain-*.conda artefacts to
  a local conda channel and drives the production-image stack
  through Playwright.
- `web_infra/Jenkinsfile` is gone (Phase 6); the stale
  pre-merge `web_infra/Makefile` and `web_infra/README.md` are
  also gone (Phase 7). `web_infra/` retains only the compose
  YAMLs (`compose-jenkins.yaml`, `compose.yaml`,
  `compose-iossifovweb.yaml`, `compose-wigclust.yaml`) and the
  two production-base Dockerfiles (`Dockerfile.gpf`,
  `Dockerfile.ubuntu`); these still describe the production
  deployment story and the e2e fixture stack consumed by root.
- `web_api/Dockerfile.dev` + `web_api/scripts/backend_run.sh` +
  `web_api/dev-environment.yml` + the `backend-dev` compose
  service are also retired (Phase 7); local-dev backend
  workflow is now `uv run python web_api/manage.py runserver`,
  matching `npm start` for `web_ui`.
- `runProject()` (root `Jenkinsfile`) is the shared helper for
  the five Python projects: builds the project's `Dockerfile`,
  runs ruff/mypy/pylint/pytest with JUnit output, then
  `uv build` for the wheel/sdist. Honours per-project `pylintrc`
  when one exists.
- `web_ui` runs ESLint + Stylelint + Jest inline in the stage
  body (no shared helper — single JS caller).
- Conventions in effect:
  - `<project>/Dockerfile` is the **CI image**.
  - `<project>/Dockerfile.production` is the production image
    (where applicable: `web_api`, `web_ui`).
  - Per-project `pylintrc` / `mypy.ini` are honoured by
    `runProject()` when present (preferred over the root
    config for that project).
  - Test fixtures and stub passwords live behind
    `[lint.per-file-ignores]` for `test*.py` / `conftest.py`
    in the root `ruff.toml`.
  - Node version is pinned consistently across CI and
    production (`node:22.14.0-alpine` via a shared
    `NODE_VERSION` ARG in `web_ui/Dockerfile`(`.production`)).

## Phases remaining

### Phase 8 — Optional: residual cleanup tail

No plan doc yet. **Phase 6 already removes the e2e flow's
runtime dependency** on
`iossifovlab/gpf-conda-packaging/master` (e2e now builds
`gpf-image` from the root build's own `dist/conda/*.conda`).
**Phase 7 already retires** the orphaned `backend-dev`
development stack and the stale pre-merge `web_infra/`
Makefile/README.

What remains for Phase 8, if/when the team wants to commit
fully to uv:

- Retire the conda dev workflow itself — root
  `environment.yml` + `dev-environment.yml`, plus the
  matching Conda/Mamba section in CLAUDE.md / README.md.
  Phase 7 deliberately kept these because CLAUDE.md still
  documents conda as one of two supported flows.
- Audit the repo-root `Dockerfile` and `Dockerfile.seqpipe`
  legacy seqpipe-flow images. The current root Jenkinsfile
  doesn't invoke them, but out-of-tree deployment automation
  may.
- Investigate any residual `gpf-conda-packaging` coupling in
  the conda-recipe story (do sub-project recipes pin
  upstream-published parent packages?).

Optional and lower priority — the upstream job is stable, the
conda dev workflow is harmless, and these legacy images don't
break anything.

## Updates to this doc

This doc is updated alongside each subsequent phase's plan: when
a phase lands, flip its row to DONE here and link the design
doc. The historical Phase 1 doc stays untouched.
