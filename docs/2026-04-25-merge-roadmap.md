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
| 8 | Production-image modernization: wheel-based `python:3.12-slim` backend image (gain-core + django-gpf-web-annotation only, single-process daphne); `httpd:2.4-alpine` frontend image with Django collectstatic baked in via multi-stage from the backend image (no shared `static-data` volume); one-shot `backend-migrate` compose service; retire `gpf-image` / `ubuntu-image` / supervisord / `environment.yml` | DONE | `docs/2026-04-25-phase-8-prod-image-modernization.md` |
| 9.1 | Build prod images in the root `Jenkinsfile` and push them to `registry.seqpipe.org` (tags: build number, 8-char git short SHA, `latest`); master-only push, branch builds validate Dockerfiles without pushing | DONE | `docs/2026-04-25-phase-9-image-push.md` |
| 9.2 | Default prod compose files (`web_infra/compose-iossifovweb.yaml`, `compose-wigclust.yaml`) to `registry.seqpipe.org/gain-web-{api,ui}:latest`; `${BACKEND_IMAGE:-...}` indirection retained for pinning. Prod operators run `docker compose pull && up -d` instead of build-on-host | DONE | `docs/2026-04-25-phase-9.2-pull-deploy.md` |
| 9.3 | `gain-web-e2e` pulls the parent-pushed prod images from `registry.seqpipe.org` on master triggers (~30s) instead of rebuilding from wheels (~5 min). Branch triggers and manual runs keep the build-from-wheels fallback | DONE | `docs/2026-04-25-phase-9.3-e2e-pull.md` |

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

## Current state (post-Phase 8)

- Root `Jenkinsfile` parallel block runs: `core`,
  `demo_annotator`, `vep_annotator`, `spliceai_annotator`,
  `web_api`, `web_ui`. Each writes JUnit + coverage to
  `reports/<project>/`. The post block archives reports +
  wheels + sdists + conda packages.
- After the parallel block, the root `Jenkinsfile` runs
  `Conda packages` (rattler-build for each gain-* recipe;
  release artefacts only — no longer feed any in-tree image)
  and then `Trigger web_e2e`, which kicks off the downstream
  `gain-web-e2e` Jenkins job (DSL at
  `web_e2e/jenkins-jobs/e2e.groovy`, pipeline at
  `web_e2e/Jenkinsfile.e2e`). That job clones the same
  branch / commit, copies the parent's wheel artefacts via
  `copyArtifacts`, builds the wheel-based backend prod image
  + Apache-based frontend prod image, and runs Playwright
  against them. `wait: false, propagate: false` — same
  shape as `Trigger VEP integration` — so the parent build
  moves on, and an e2e regression doesn't FAILURE the
  parent.
- **Production images**: `python:3.12-slim` backend with
  `gain-core` + `django-gpf-web-annotation` wheels (single
  foreground daphne); `httpd:2.4-alpine` frontend with the
  Angular SPA + Django collectstatic baked in via multi-stage
  from the backend image. No shared `/static` volume.
  Migrations run as a one-shot `backend-migrate` compose
  service.
- `web_infra/Jenkinsfile` is gone (Phase 6); the stale
  pre-merge `web_infra/Makefile` and `web_infra/README.md`
  are also gone (Phase 7); `web_infra/Dockerfile.gpf` /
  `Dockerfile.ubuntu` are gone (Phase 8 — superseded by the
  wheel-based backend and Apache-only frontend). `web_infra/`
  now contains only the four compose YAMLs.
- `web_api/Dockerfile.dev` + `web_api/scripts/backend_run.sh` +
  `web_api/dev-environment.yml` + the `backend-dev` compose
  service are retired (Phase 7); local-dev backend
  workflow is `uv run python web_api/manage.py runserver`,
  matching `npm start` for `web_ui`.
- supervisord and the `web_api/scripts/{supervisord*,
  wait-for-it.sh}` + `web_ui/scripts/{localhost.conf,
  supervisord*,wait-for-it.sh}` retired (Phase 8); each
  production container runs a single foreground process
  (daphne / httpd-foreground).
- `web_api/environment.yml` retired (Phase 8 — the production
  image now installs from the wheels the root Jenkinsfile
  produces).
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

### Phase 9 — Optional: deployment modernization tail

First slice (image registry push, 9.1) landed; remaining
slices are still optional. Most of the originally-imagined
Phase 8 has landed already:
- **Phase 6** removed the e2e flow's runtime dependency on
  `iossifovlab/gpf-conda-packaging/master`.
- **Phase 7** retired the orphaned `backend-dev` development
  stack and the stale pre-merge `web_infra/` Makefile/README.
- **Phase 8** retired the entire conda-pack production stack
  (gpf-image / ubuntu-image / supervisord / web_api's
  `environment.yml`) — production images are now wheel-based
  `python:3.12-slim` (backend) + `httpd:2.4-alpine` (frontend
  with Django collectstatic baked in).

What remains for Phase 9, if/when the team wants more:

- **Image registry + pull-deploy** —
  - **9.1: Push (DONE)**. Root `Jenkinsfile` now builds and
    pushes `registry.seqpipe.org/gain-web-api` and
    `registry.seqpipe.org/gain-web-ui` (tags: build number,
    8-char git SHA, `latest`) on master. Branch builds
    validate the Dockerfiles without pushing. See
    `docs/2026-04-25-phase-9-image-push.md`.
  - **9.2: Pull-deploy on prod hosts (DONE)**. Defaults in
    `web_infra/compose-iossifovweb.yaml` and
    `compose-wigclust.yaml` now point at
    `registry.seqpipe.org/gain-web-{api,ui}:latest`. The
    `${BACKEND_IMAGE:-...}` env-var indirection is kept so
    operators can pin a specific tag for rollout/rollback.
    Prod hosts run `docker compose pull && up -d` instead
    of build-on-host. See
    `docs/2026-04-25-phase-9.2-pull-deploy.md`.
  - **9.3: e2e pulls instead of rebuilds (DONE)**.
    `gain-web-e2e` now detects when it's triggered
    downstream of a master parent (`BRANCH_NAME=='master'`
    and `UPSTREAM_BUILD` set) and `docker pull`s the
    parent-pushed `:${UPSTREAM_BUILD}` tag from
    `registry.seqpipe.org`, saving ~5 min per e2e run.
    Branch triggers and manual no-upstream runs still use
    the build-from-wheels path. See
    `docs/2026-04-25-phase-9.3-e2e-pull.md`.
- **TLS modernization**. Caddy or Traefik in front for
  automatic TLS + cleaner reverse-proxy config.
- **Observability lite**. Loki + Promtail + Grafana as a
  small stack for container logs / metrics.
- **Retire the conda dev workflow** itself — root
  `environment.yml` + `dev-environment.yml`, plus the
  matching Conda/Mamba section in CLAUDE.md / README.md.
  Phase 7 deliberately kept these because CLAUDE.md still
  documents conda as one of two supported flows.
- **Audit repo-root `Dockerfile` and `Dockerfile.seqpipe`**
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
