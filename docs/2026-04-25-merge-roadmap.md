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

## Current state (post-Phase 5)

- Root `Jenkinsfile` parallel block runs: `core`,
  `demo_annotator`, `vep_annotator`, `spliceai_annotator`,
  `web_api`, `web_ui`. Each writes JUnit + coverage to
  `reports/<project>/`. The post block archives reports +
  wheels + sdists + conda packages.
- `runProject()` (root `Jenkinsfile`) is the shared helper for
  the five Python projects: builds the project's `Dockerfile`,
  runs ruff/mypy/pylint/pytest with JUnit output, then
  `uv build` for the wheel/sdist. Honours per-project `pylintrc`
  when one exists.
- `web_ui` runs ESLint + Stylelint + Jest inline in the stage
  body (no shared helper — single JS caller).
- `web_infra/Jenkinsfile` still owns the e2e flow: copy the
  upstream `gpf-conda-packaging` `conda-channel.tar.gz`, build
  `ubuntu-image` and `gpf-image`, build `backend-e2e` and
  `frontend-e2e` from the production Dockerfiles, run the
  `e2e-tests` Playwright service, archive
  `web_e2e/reports/junit-report.xml`. Triggers on the upstream
  conda packaging job's success.
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

### Phase 6 — `web_e2e` (Playwright) on root CI; retire `web_infra/Jenkinsfile`

Plan: `docs/2026-04-25-phase-6-e2e-ci.md` (NEXT).

Move the existing e2e flow off `web_infra/Jenkinsfile` and into
a sequential `web_e2e` stage in the root `Jenkinsfile`, placed
after the existing `Conda packages` stage. Generate a
deterministic `web_e2e/package-lock.json`, switch
`web_e2e/Dockerfile.playwright` to `npm ci`, and have the root
`Jenkinsfile` orchestrate the `compose-jenkins.yaml` services it
already declares (`backend-e2e`, `frontend-e2e`, `mail`,
`gpf-image`, `db`, `e2e-tests`) under a unique compose project
name. The e2e flow's only "external" inputs are the in-repo
subprojects' build artefacts: `gain-*.conda` packages from
`dist/conda/` (built by the existing `Conda packages` stage),
plus `web_api/Dockerfile.production` and
`web_ui/Dockerfile.production` for the backend/frontend images
— **no upstream `gpf-conda-packaging` trigger, no
`copyArtifacts` from outside this repo**. Once the move is
verified, `git rm web_infra/Jenkinsfile` — every remaining stage
in it exists solely to support e2e and the upstream coupling
goes away in the same change.

### Phase 7 — Tail cleanup

No plan doc yet. Likely items, none of which block production:

- Retire conda env files (`environment.yml`,
  `dev-environment.yml`, `web_api/environment.yml`,
  `web_api/dev-environment.yml`) now that uv covers Python
  workflows.
- Decide on `web_api/Dockerfile.dev` and the `backend-dev`
  compose service. Currently `backend-dev` is the only
  consumer; if we keep `npm start` as the local dev story for
  `web_ui` (Phase 5 chose this), the backend equivalent is
  `uv run python manage.py runserver` and `Dockerfile.dev`
  becomes deletable.
- Consider whether `web_infra/Dockerfile.gpf` /
  `Dockerfile.ubuntu` still make sense once Phase 6 has moved
  the e2e flow to root, or whether they can be folded into the
  e2e compose stack.

### Phase 8 — Optional: retire upstream `gpf-conda-packaging` coupling

No plan doc yet. **Phase 6 already removes the e2e flow's
runtime dependency** on
`iossifovlab/gpf-conda-packaging/master` (e2e now builds
`gpf-image` from the root build's own `dist/conda/*.conda`).
What remains for Phase 8 is whatever residual coupling exists
elsewhere in the monorepo's conda-recipe story — e.g., does the
root `Conda packages` stage still depend on upstream-published
parent packages, do any sub-project recipes pin
`gpf-conda-packaging`'s outputs, etc. Investigate and decide
once Phase 6 has settled. Optional and lower priority — the
upstream job is stable and the residual coupling (if any) is
harmless.

## Updates to this doc

This doc is updated alongside each subsequent phase's plan: when
a phase lands, flip its row to DONE here and link the design
doc. The historical Phase 1 doc stays untouched.
