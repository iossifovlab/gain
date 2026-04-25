# Plan: Phase 7 — Tail cleanup of the post-Phase-6 monorepo

## Context

Phases 1–6 of the gpf-web-annotation merge are done. The root
`Jenkinsfile` owns CI for every project (Python sub-projects in
parallel, then sequential `Conda packages` and `web_e2e`
stages); `web_infra/Jenkinsfile` was retired in Phase 6.

After that move, a small set of files have no live consumers
left in the monorepo. They were either:

- consumed only by `web_infra/Jenkinsfile` (now gone), or
- consumed only by the `backend-dev` compose service (a
  pre-merge developer-runserver workflow that has no CI
  consumer and is superseded by `uv run python manage.py
  runserver` directly), or
- pre-merge documentation/tooling that no longer matches the
  repo's structure.

Phase 7 retires those orphans. **Scope is intentionally
conservative** — the conda-based development flow stays
documented (CLAUDE.md / root `README.md`), `web_api/environment.yml`
stays because `web_api/Dockerfile.production` consumes it,
`web_infra/Dockerfile.gpf` and `Dockerfile.ubuntu` stay because
the e2e and production stacks build on them. `web_infra/` itself
stays as the production-deploy artefact directory.

## Scope

**In scope:**

- **Retire `backend-dev` development image and its compose
  service**:
  - Delete `web_api/Dockerfile.dev` (only consumer is
    `backend-dev`).
  - Delete `web_api/scripts/backend_run.sh` (only consumer is
    `backend-dev`'s entrypoint).
  - Delete `web_api/dev-environment.yml` (only consumer is
    `Dockerfile.dev`).
  - Drop the `backend-dev` service stanza from
    `web_infra/compose-jenkins.yaml` (nothing else references
    it; not present in `compose.yaml` /
    `compose-iossifovweb.yaml` / `compose-wigclust.yaml`).
- **Retire stale `web_infra/` documentation/tooling**:
  - Delete `web_infra/Makefile` (its targets reference paths
    from the pre-merge era — `../web_ui/dist/frontend/browser`,
    `django-gpf-web-annotation/` — that no longer exist).
  - Delete `web_infra/README.md` (same vintage; the post-merge
    deploy story for `web_infra/` is the compose YAMLs, not
    this README).
- **Sweep root docs for now-stale references**:
  - Audit `README.md` and `CLAUDE.md` for any reference to
    `backend-dev`, `Dockerfile.dev`, `dev-environment.yml`,
    `backend_run.sh`, `web_infra/Makefile`,
    `web_infra/README.md`, or `web_infra/Jenkinsfile`. Edit
    out anything that's now wrong.

**Out of scope (intentionally):**

- **Root `environment.yml` and `dev-environment.yml`** — the
  conda/mamba developer workflow is documented in CLAUDE.md
  as one of two supported flows (the other being uv). Retiring
  it is a Phase 8+ design decision, not a Phase 7 sweep.
- **`web_api/environment.yml`** — `Dockerfile.production`
  still copies and `mamba env update`s from it.
- **`web_infra/Dockerfile.gpf` / `Dockerfile.ubuntu`** — e2e
  and production both build on them.
- **Repo-root `Dockerfile` and `Dockerfile.seqpipe`** —
  legacy seqpipe-flow images. Not invoked by current root
  Jenkinsfile but they may have out-of-tree consumers
  (deployment scripts elsewhere). Decide separately.
- **`web_api/coveragerc`** — `spliceai_annotator/scripts/run_tests.sh`
  references it.
- **`spliceai_annotator/spliceai-environment.yml`** — used by
  `Dockerfile.runner`.
- **Retiring upstream `gpf-conda-packaging` coupling** — Phase 8.

## Approach

### Retire `backend-dev` and its dev-image stack

Files to delete:

- `web_api/Dockerfile.dev`
- `web_api/scripts/backend_run.sh`
- `web_api/dev-environment.yml`

In `web_infra/compose-jenkins.yaml`, drop the entire
`backend-dev:` service block (currently lines ~127–142):

```yaml
  backend-dev:
    build:
      context: ../web_api
      dockerfile: Dockerfile.dev
      ...
    entrypoint: /wd/web_api/scripts/backend_run.sh
```

The local-developer story it supported was "run Django's
runserver inside a docker container with the source bind-
mounted." Post-Phase-3 (uv workspace) the equivalent is
simply:

```bash
uv run --package django-gpf-web-annotation \
    python web_api/manage.py runserver
```

(plus a postgres + mailhog brought up via `docker compose
up -d db mail`, exactly mirroring the `web_ui` story Phase 5
chose for frontend dev.)

### Retire stale `web_infra/` docs

Delete:

- `web_infra/Makefile`
- `web_infra/README.md`

`web_infra/` keeps its compose YAMLs
(`compose.yaml`, `compose-jenkins.yaml`,
`compose-iossifovweb.yaml`, `compose-wigclust.yaml`) and the
two production-base Dockerfiles (`Dockerfile.gpf`,
`Dockerfile.ubuntu`).

### Sweep root docs

`grep -rE 'backend-dev|Dockerfile\\.dev|dev-environment|backend_run|web_infra/Makefile|web_infra/README|web_infra/Jenkinsfile' README.md CLAUDE.md docs/`
and cleanly edit out anything that's now wrong. Keep this
edit minimal — just remove dead references, don't expand the
docs in scope.

## Step-by-step

1. **Delete the four `backend-dev` stack files** (`git rm
   web_api/Dockerfile.dev web_api/scripts/backend_run.sh
   web_api/dev-environment.yml`) and edit
   `web_infra/compose-jenkins.yaml` to remove the
   `backend-dev:` service block.
2. **Delete the two stale `web_infra/` files** (`git rm
   web_infra/Makefile web_infra/README.md`).
3. **Sweep root docs** (`README.md`, `CLAUDE.md`) for any
   reference to the retired bits and edit them out.
4. **Verify locally**:
   - `git grep -nE 'backend-dev|Dockerfile\.dev|dev-environment|backend_run|web_infra/Makefile|web_infra/README|web_infra/Jenkinsfile'` returns no live references (only matches in this plan doc and `docs/2026-04-25-merge-roadmap.md` are acceptable).
   - `docker compose -f web_infra/compose-jenkins.yaml config` still parses cleanly (no syntax errors, no dangling references).
   - Smoke-rebuild the e2e stack to confirm the
     compose-jenkins.yaml edit didn't disturb the still-live
     services: `docker compose -p gain-ci-e2e-verify -f web_infra/compose-jenkins.yaml build ubuntu-image gpf-image backend-e2e frontend-e2e e2e-tests` — all five should build.
   - Smoke-rebuild `web_api/Dockerfile.production` standalone
     (`docker build -f web_api/Dockerfile.production
     web_api/`) — should succeed because `web_api/environment.yml`
     is still present.
5. **Update `docs/2026-04-25-merge-roadmap.md`** — flip the
   Phase 7 row to DONE and link this plan doc.
6. **Commit in three logical chunks**:
   - *(a) Retire backend-dev development image and compose
     service* — Dockerfile.dev + backend_run.sh +
     dev-environment.yml deletes + compose-jenkins.yaml edit.
   - *(b) Retire stale web_infra/ docs and Makefile* —
     Makefile + README.md deletes + any root README/CLAUDE.md
     sweeps.
   - *(c) Mark Phase 7 DONE in the merge roadmap*.

## Critical files

- `/home/lubo/Work/seq-pipeline/gain/web_api/Dockerfile.dev`
  (deleted)
- `/home/lubo/Work/seq-pipeline/gain/web_api/scripts/backend_run.sh`
  (deleted)
- `/home/lubo/Work/seq-pipeline/gain/web_api/dev-environment.yml`
  (deleted)
- `/home/lubo/Work/seq-pipeline/gain/web_infra/compose-jenkins.yaml`
  (drop the `backend-dev:` service)
- `/home/lubo/Work/seq-pipeline/gain/web_infra/Makefile`
  (deleted)
- `/home/lubo/Work/seq-pipeline/gain/web_infra/README.md`
  (deleted)
- `/home/lubo/Work/seq-pipeline/gain/README.md`
  (sweep stale references; keep conda dev-workflow section
  intact)
- `/home/lubo/Work/seq-pipeline/gain/CLAUDE.md`
  (sweep stale references; keep conda dev-workflow section
  intact)
- `/home/lubo/Work/seq-pipeline/gain/docs/2026-04-25-merge-roadmap.md`
  (Phase 7 row → DONE on completion)

## Reference files

- `/home/lubo/Work/seq-pipeline/gain/web_infra/compose-jenkins.yaml`
  — confirm the surviving services after the edit:
  `ubuntu-image`, `gpf-image`, `pg-data`, `static-data`, `db`,
  `mail`, `backend`, `backend-e2e`, `frontend`,
  `frontend-e2e`, `e2e-tests`. (Pre-Phase-7 has all of those
  plus `backend-dev`.)
- `/home/lubo/Work/seq-pipeline/gain/web_api/Dockerfile.production`
  — keep working (still uses `web_api/environment.yml`).
- `/home/lubo/Work/seq-pipeline/gain/web_infra/Dockerfile.gpf`
  / `web_infra/Dockerfile.ubuntu` — unchanged; still feed the
  e2e and production image stacks.

## Risks and known unknowns

- **Out-of-tree consumers of `backend-dev`**. The compose
  service was developer-facing — a developer's `~/.bashrc`
  alias might `docker compose -f web_infra/compose-jenkins.yaml
  up backend-dev` to run a local Django server. After Phase 7
  that command stops working. Mitigation: mention the
  replacement (`uv run python web_api/manage.py runserver`)
  in the commit body so anyone reading the diff sees the
  migration path.
- **Stale README content elsewhere**. The agent flagged
  `web_infra/README.md` as "obsolete from pre-merge"; if the
  root `README.md` borrowed from it, removing wholesale could
  delete still-useful information. The Phase 7 sweep is
  read-and-edit, not delete-and-rewrite, for the root docs —
  only the `web_infra/README.md` itself is deleted outright.
- **Compose YAML edit risk**. Dropping a service block in
  YAML can introduce subtle indentation or anchor issues. The
  smoke-test (`docker compose ... config`) catches that
  cheaply; the e2e-stack rebuild catches deeper breakage.
- **No CI test for `backend-dev` removal**. Since CI never
  invoked it, removing it can't surface in master's green.
  The local smoke-test above is the only verification; if
  someone does have an out-of-tree workflow, they'll surface
  it after the change lands. Acceptable risk given the file
  ages and the documented uv replacement.

## Verification end-to-end

```bash
cd /home/lubo/Work/seq-pipeline/gain

# 1. No stale references remain
git grep -nE 'backend-dev|Dockerfile\.dev|dev-environment\.yml|backend_run\.sh|web_infra/Makefile|web_infra/README\.md|web_infra/Jenkinsfile' \
    -- ':!docs/'
# expect: no matches (the docs/ exclusion lets the merge-
# roadmap doc and Phase 7 plan keep their historical references)

# 2. compose-jenkins.yaml still parses
docker compose -f web_infra/compose-jenkins.yaml config \
    > /dev/null

# 3. The surviving e2e stack still builds
docker compose -p gain-phase7-verify \
    -f web_infra/compose-jenkins.yaml build \
        ubuntu-image gpf-image backend-e2e frontend-e2e e2e-tests

# 4. Production backend image still builds standalone
docker build -f web_api/Dockerfile.production -t backend-prod-smoke web_api/

# 5. Spot-check that web_api/environment.yml and the conda
#    workflow docs are still in place
ls web_api/environment.yml environment.yml dev-environment.yml
grep -A3 '^### Conda' CLAUDE.md
```

If steps 1–5 succeed, push the commits. The next master build
should run identically to the previous green run — Phase 7's
changes don't touch any CI-invoked path.

## After Phase 7

- **Phase 8 — optional retirement of any residual upstream
  `gpf-conda-packaging` coupling**, plus optional retirement
  of the conda dev-workflow (root `environment.yml` /
  `dev-environment.yml` + the matching CLAUDE.md / README.md
  sections) if the team wants to commit fully to uv.
- The repo-root `Dockerfile` and `Dockerfile.seqpipe` audit
  also belongs in Phase 8 / a follow-up — the in-tree
  Jenkinsfile doesn't reference them, but out-of-tree
  deployment automation might.

The merge roadmap doc gets the Phase 7 row flipped to DONE in
the same change that lands this work.
