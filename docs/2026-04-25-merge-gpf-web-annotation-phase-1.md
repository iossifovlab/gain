# Plan: Merge `gpf-web-annotation` into the `gain` monorepo — Phase 1

## Context

We have two Git repositories that ship as one product:

- **`gain`** (`/home/lubo/Work/seq-pipeline/gain`) — pure-Python uv workspace
  with four sub-projects (`core/`, `demo_annotator/`, `vep_annotator/`,
  `spliceai_annotator/`). Single root `uv.lock`, root `ruff.toml`/`mypy.ini`/
  `pylintrc`, root `Jenkinsfile`, `environment.yml` + `dev-environment.yml`.
- **`gpf-web-annotation`** (`/home/lubo/Work/seq-pipeline/gpf-web-annotation`,
  GitHub: `iossifovlab/gpf-web-annotation`) — Django 5.2 + DRF + Channels
  backend, Angular 20 + Jest frontend, Playwright e2e tests. No Python-level
  dependency on gain; coupling happens via conda packages built by an upstream
  CI job.

**End goal:** fully integrated monorepo (one CI, one lockfile, unified lint,
shared toolchain) — the "C" path discussed.

**This plan covers Phase 1 only:** lift-and-shift the
`gpf-web-annotation` code into the `gain` repo with history preserved,
restructured into four flat sibling directories. All build/test behaviour
remains driven by the imported configuration. No CI unification, no
workspace-member promotion, no lint reconciliation — those are later phases.

## Roadmap context (recap)

1. **Phase 1 — this plan:** Subtree-merge import + rename to flat siblings.
2. Phase 2 — Reconcile Python lint configs (`mypy.ini`, `pylintrc`).
3. Phase 3 — Promote backend to a uv workspace member.
4. Phase 4 — Consolidate conda environments.
5. Phase 5 — Unified Python CI (one root `Jenkinsfile`).
6. Phase 6 — Frontend tooling integration; strip `web_infra/`.
7. Phase 7 — E2E tests integration.
8. Phase 8 — Optional: retire upstream `gpf-conda-packaging` coupling.

`web_infra/` is explicitly a temporary home for cross-cutting orchestration
files; it gets dismantled as Phases 5–7 land.

## Target layout after Phase 1

```
gain/
├── core/                    (unchanged)
├── demo_annotator/          (unchanged)
├── vep_annotator/           (unchanged)
├── spliceai_annotator/      (unchanged)
├── web_api/                 ← from gpf-web-annotation/backend/
├── web_ui/                  ← from gpf-web-annotation/frontend/
├── web_e2e/                 ← from gpf-web-annotation/e2e-tests/
├── web_infra/               ← from gpf-web-annotation/ root files
│   ├── Jenkinsfile
│   ├── Makefile
│   ├── README.md
│   ├── Dockerfile.ubuntu
│   ├── Dockerfile.gpf
│   ├── compose.yaml
│   ├── compose-jenkins.yaml
│   ├── compose-iossifovweb.yaml
│   ├── compose-wigclust.yaml
│   ├── mypy.ini
│   ├── pylintrc
│   ├── .gitignore
│   └── .dockerignore
├── (existing gain root files — pyproject.toml, uv.lock, Jenkinsfile,
│    environment.yml, dev-environment.yml, ruff.toml, mypy.ini, pylintrc,
│    docker-compose.yaml, Dockerfile, README.md, CLAUDE.md, ...)
```

## Approach

Use **subtree-merge import + `git mv` rename** in a single PR. Two commits:

1. **Import commit:** `git subtree add --prefix=gpf-web-annotation/ <remote>
   master`. Brings the full history of `gpf-web-annotation` under a temporary
   prefix. Cheap (~5 MiB pack, no committed binaries — already verified).
2. **Restructure commit:** `git mv` operations split the imported subtree
   into the four target directories, plus path edits inside `web_infra/`
   files so the imported pipeline still resolves its sibling-relative
   references.

`git log --follow` works across the rename, so per-file history is preserved.

Subtree-merge was chosen over `git-filter-repo` because there are no binary
blobs to filter out and the repo is small. Subtree-merge is one command;
filter-repo is several plus a clone.

## Step-by-step

### Step 1 — Preconditions

- Confirm a clean working tree on `master` in `gain/`.
- Confirm the local `gpf-web-annotation/` checkout is clean and pushed to
  `origin/master` on GitHub. The plan uses the local path as the import
  source, but pushing first ensures we're not importing local-only commits.
- Confirm `git remote -v` for `gain` does not already use the name
  `gpf-web-annotation` (it doesn't today, but check).

### Step 2 — Subtree import

```bash
cd /home/lubo/Work/seq-pipeline/gain
git remote add gpf-web-annotation /home/lubo/Work/seq-pipeline/gpf-web-annotation
git fetch gpf-web-annotation
git subtree add --prefix=gpf-web-annotation \
    gpf-web-annotation/master \
    -m "Merge gpf-web-annotation into monorepo (subtree import)"
git remote remove gpf-web-annotation
```

After this step the imported tree lives at `gain/gpf-web-annotation/` with
its original internal structure intact. Existing `gain` files are
untouched. The merge commit is reachable; `git log --follow
gpf-web-annotation/backend/manage.py` shows the original history.

### Step 3 — Restructure into flat siblings

```bash
cd /home/lubo/Work/seq-pipeline/gain
git mv gpf-web-annotation/backend    web_api
git mv gpf-web-annotation/frontend   web_ui
git mv gpf-web-annotation/e2e-tests  web_e2e

mkdir -p web_infra
git mv gpf-web-annotation/Jenkinsfile               web_infra/
git mv gpf-web-annotation/Makefile                  web_infra/
git mv gpf-web-annotation/README.md                 web_infra/
git mv gpf-web-annotation/Dockerfile.ubuntu         web_infra/
git mv gpf-web-annotation/Dockerfile.gpf            web_infra/
git mv gpf-web-annotation/compose.yaml              web_infra/
git mv gpf-web-annotation/compose-jenkins.yaml      web_infra/
git mv gpf-web-annotation/compose-iossifovweb.yaml  web_infra/
git mv gpf-web-annotation/compose-wigclust.yaml     web_infra/
git mv gpf-web-annotation/mypy.ini                  web_infra/
git mv gpf-web-annotation/pylintrc                  web_infra/
git mv gpf-web-annotation/.gitignore                web_infra/
git mv gpf-web-annotation/.dockerignore             web_infra/

# Verify the temp dir is empty, then drop it
rmdir gpf-web-annotation     # fails if anything was missed — that's the point
```

`mypy.ini` and `pylintrc` go to `web_infra/` (Phase 2 will reconcile with
root). `.gitignore` goes to `web_infra/` so its data-dir patterns
(`/gpfwa-data`, `/gpfwa-pg-data`, etc.) keep working — runtime mounts will
land under `web_infra/` since that's where compose runs from.

### Step 4 — Update sibling-relative path references

The imported `Jenkinsfile`, compose files, and Makefile reference paths
like `backend/`, `frontend/`, `e2e-tests/`. After the move, those siblings
live at `../web_api/`, `../web_ui/`, `../web_e2e/` relative to
`web_infra/`. Two strategies are equivalent; pick **(a)**:

**(a) Wrap the Jenkinsfile in `dir('web_infra')`** so its working directory
is `web_infra/`, then update compose contexts and any cross-sibling paths.

Concrete edits (paths shown relative to `web_infra/`):

- **`web_infra/Jenkinsfile`**
  - Wrap the `pipeline { ... }` body's stages in `dir('web_infra') { ... }`,
    or set `agent { ... } { dir('web_infra') }` — pick the form that's
    cleanest given Jenkins' DSL.
  - `backend/reports/...`        → `../web_api/reports/...`
  - `frontend/reports/...`       → `../web_ui/reports/...`
  - `e2e-tests/reports/...`      → `../web_e2e/reports/...`
  - `frontend/scripts/...`       → `../web_ui/scripts/...`
  - `mkdir -p frontend/reports`  → `mkdir -p ../web_ui/reports`
  - `mkdir -p e2e-tests/reports` → `mkdir -p ../web_e2e/reports`
  - The volume-clean `docker run` invocation that lists
    `/wd/backend/reports /wd/frontend/reports /wd/e2e-tests/reports
    /wd/gpfwa-logs /wd/gpfwa-data` needs the `/wd/...` paths updated
    consistent with where the volume is now mounted (mount the gain repo
    root, not `web_infra/`, and reference `web_api/reports`, etc.).

- **`web_infra/compose-jenkins.yaml`**
  - `context: backend`   → `context: ../web_api`
  - `context: frontend`  → `context: ../web_ui`
  - `context: e2e-tests` → `context: ../web_e2e`  *(if present — verify)*
  - Volume mounts like `./gpfwa-data`, `./gpfwa-logs`, `./gpfwa-static-data`,
    `./gpfwa-pg-data` stay as-is (they're written under `web_infra/` at
    runtime, which is fine and matches the gitignored patterns now in
    `web_infra/.gitignore`).

- **`web_infra/compose.yaml`, `compose-iossifovweb.yaml`,
  `compose-wigclust.yaml`** — same treatment for any `context:` that points
  at `backend/`, `frontend/`, or `e2e-tests/`.

- **`web_infra/Makefile`** — update `cd frontend` → `cd ../web_ui` (and any
  similar). **Note:** the existing Makefile also references
  `../django-gpf-web-annotation/gpf_web_annotation_frontend/...` — that's a
  reference to a *separate* repo outside this merge's scope. Leave it as-is
  in Phase 1; flag for whoever owns that workflow.

- **`web_infra/Dockerfile.ubuntu`, `Dockerfile.gpf`** — verify they don't
  hardcode `backend/`, `frontend/`, `e2e-tests/` paths. They likely
  reference paths *inside the build context*, so as long as compose passes
  the right context dir they remain correct. Double-check.

### Step 5 — Verification

In order, locally:

1. `git status` — clean working tree apart from staged moves.
2. `git log --follow web_api/manage.py` — shows commits from the original
   `iossifovlab/gpf-web-annotation` history.
3. `git log --follow web_infra/Jenkinsfile` — same.
4. `git ls-files | grep -E '^gpf-web-annotation/'` — empty (no leftovers).
5. From `web_infra/`, run a docker build smoke test:
   ```bash
   cd web_infra
   docker compose -f compose-jenkins.yaml build backend-linters
   docker compose -f compose-jenkins.yaml build frontend-linters
   ```
   Both should succeed.
6. From `web_infra/`, run the backend test container end-to-end:
   ```bash
   docker compose -f compose-jenkins.yaml run --rm backend-tests
   ```
   Tests should pass (or fail in the same places they fail today on the
   `gpf-web-annotation` repo at `master` — diff against a control run).
7. Same for `frontend-tests`. (`e2e-tests` is heavier; deferring to CI is
   acceptable.)
8. Run the existing gain tests to confirm nothing leaked:
   ```bash
   cd /home/lubo/Work/seq-pipeline/gain/core
   pytest -v -n 10 tests/small/
   ```
9. Run the existing gain CI image build to confirm the root `Jenkinsfile`
   still resolves cleanly: `docker build -f Dockerfile.seqpipe .` (if that's
   the conventional smoke test for the gain image).

### Step 6 — Commit and PR

The two commits proposed:

1. `Merge gpf-web-annotation into monorepo (subtree import)` — pure
   subtree-merge commit, no manual edits.
2. `Restructure imported subtree into web_api/web_ui/web_e2e/web_infra` —
   all `git mv` plus path edits in step 4.

PR title: `Merge gpf-web-annotation into the gain monorepo (Phase 1)`.
PR body should explain: this is Phase 1 of a multi-phase migration, no CI
changes yet, the existing `iossifovlab/gpf-web-annotation` repo remains
the source of CI for now (see Operational notes below).

## Files modified / created

**Created (paths moved into):**
- `web_api/**`         — entire backend tree
- `web_ui/**`          — entire frontend tree
- `web_e2e/**`         — entire e2e-tests tree
- `web_infra/**`       — orchestration files (listed in Step 3)

**Edited inside `web_infra/`:**
- `web_infra/Jenkinsfile`
- `web_infra/compose.yaml`
- `web_infra/compose-jenkins.yaml`
- `web_infra/compose-iossifovweb.yaml`
- `web_infra/compose-wigclust.yaml`
- `web_infra/Makefile`
- (possibly) `web_infra/Dockerfile.ubuntu`, `web_infra/Dockerfile.gpf` —
  verify only

**Untouched (existing gain root):**
- Root `pyproject.toml`, `uv.lock`, `Jenkinsfile`, `environment.yml`,
  `dev-environment.yml`, `ruff.toml`, `mypy.ini`, `pylintrc`,
  `docker-compose.yaml`, `Dockerfile`, `Dockerfile.seqpipe`, `README.md`,
  `CLAUDE.md`. Phase 1 explicitly does not touch these.

## Operational notes (CI cutover, out of plan scope but worth flagging)

- The existing Jenkins job for `iossifovlab/gpf-web-annotation` keeps
  working unchanged after this PR (it points at a different repo). Until a
  new Jenkins job is configured for `gain` repo + `web_infra/Jenkinsfile`,
  the merged tree's CI is not running. That's acceptable for Phase 1: the
  source repo stays authoritative for CI until cutover.
- Cutover steps (do *after* Phase 1 lands):
  1. Configure a new Jenkins multibranch job pointing at the `gain` repo
     with Jenkinsfile path `web_infra/Jenkinsfile`.
  2. Confirm it triggers correctly on push to `gain/master` and that the
     upstream `gpf-conda-packaging` artifact copy step still works (it
     references the upstream job by name, not by repo).
  3. Once the new job is green, archive
     `iossifovlab/gpf-web-annotation` (or freeze its branch).
- This cutover is not part of this plan because it touches Jenkins server
  config, not the repo. Add it as a follow-up task.

## Risks and known unknowns

- **`web_infra/.gitignore` interactions with root `.gitignore`.** Both apply
  to their subtree; patterns like `/conda-channel` in `web_infra/.gitignore`
  are now scoped to `web_infra/conda-channel`, which matches where compose
  extracts the tarball at runtime. Root `.gitignore` is unchanged.
  Sanity-check by running `git status` after a local build.
- **Makefile cross-repo reference.** `web_infra/Makefile` references
  `../django-gpf-web-annotation/gpf_web_annotation_frontend/...` — a
  *different* repository entirely. After the move, that relative path
  resolves to `gain/django-gpf-web-annotation/...` which doesn't exist.
  This was likely already broken from arbitrary checkout locations; flag
  the owner of that workflow but don't fix in Phase 1.
- **conda-channel.tar.gz download path.** Jenkinsfile pulls
  `conda-channel.tar.gz` from upstream and extracts it. After the rename,
  the extraction lands at `web_infra/conda-channel/` — verify
  `Dockerfile.gpf` and the compose mounts pick it up from the new path.
- **Volume mounts under `web_infra/`.** Runtime data dirs (`gpfwa-data`,
  `gpfwa-pg-data`, `gpfwa-logs`, `gpfwa-static-data`, `tmp`) will now
  appear at `web_infra/gpfwa-*` instead of repo root. The
  `web_infra/.gitignore` patterns cover this. Anyone with hardcoded
  expectations (e.g., a personal symlink) will need to adjust.

## Critical files to read before executing

- `/home/lubo/Work/seq-pipeline/gpf-web-annotation/Jenkinsfile`
- `/home/lubo/Work/seq-pipeline/gpf-web-annotation/compose-jenkins.yaml`
- `/home/lubo/Work/seq-pipeline/gpf-web-annotation/compose.yaml`
- `/home/lubo/Work/seq-pipeline/gpf-web-annotation/Makefile`
- `/home/lubo/Work/seq-pipeline/gpf-web-annotation/Dockerfile.gpf`
- `/home/lubo/Work/seq-pipeline/gpf-web-annotation/Dockerfile.ubuntu`
- `/home/lubo/Work/seq-pipeline/gpf-web-annotation/.gitignore`
- `/home/lubo/Work/seq-pipeline/gain/Jenkinsfile` (to confirm zero overlap
  with the imported one)

## Out of scope for Phase 1 (later phases)

- Reconciling `web_infra/mypy.ini` + `pylintrc` against root (Phase 2).
- Promoting `web_api/` to a uv workspace member (Phase 3).
- Folding `web_api/environment.yml` and `dev-environment.yml` into the
  root environment files (Phase 4).
- Adding `web_api` to root `Jenkinsfile`'s `runProject` calls and
  retiring `web_infra/Jenkinsfile` (Phase 5).
- Stripping committed `web_ui/node_modules` if present locally
  (already not in git history); introducing root JS toolchain (Phase 6).
- Renaming the Django app `web_annotation` → anything else (separate
  decision, large internal blast radius).
