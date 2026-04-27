# Plan: Phase 10 — tag-driven release pipeline

## Context

The root `Jenkinsfile` currently runs per-branch CI in a
multibranch project. On every master commit it builds wheels
+ sdists for the five Python packages (`gain-core`,
`gain-web-api`, `gain-demo-annotator`, `gain-vep-annotator`,
`gain-spliceai-annotator`), four conda packages (core, demo,
vep, spliceai — `gain-web-api` has no conda recipe), and two
production Docker images (`registry.seqpipe.org/gain-web-api`,
`registry.seqpipe.org/gain-web-ui`). The Docker images are
pushed on master with `:${BUILD_NUMBER}`, `:${GIT_SHORT}`,
`:latest` tags; wheels and conda packages are archived as
Jenkins artefacts but published nowhere.

The repository carries 799 calendar-versioned tags
(`2024.1.0`, `2026.4.0`, …) produced over the GAIn /
GPF history. `hatch-vcs` with `version_scheme = "no-guess-dev"`
is configured across all five packages — tags are already the
existing source of truth for "real" versions.

Phase 10 promotes the ad-hoc, partially-published artefact
flow into a deliberate **tag-driven release pipeline**:
pushing a final CalVer tag rebuilds and publishes wheels +
conda packages + Docker images to their respective
destinations, derived from the existing master CI build of
the same commit.

## Design decisions

The decisions below were resolved during a `/grill-me` session
on 2026-04-27. Each row records the choice and the reasoning,
so future contributors can judge edge cases without re-deriving
the design.

### D1 — Trigger: git tag push (not master commit, not manual job, not schedule)

A release runs when a tag is pushed. Master commits stay
"continuous dev" (current behavior).

**Why:**
- 799 existing tags + `hatch-vcs no-guess-dev` already make
  tags the existing source of truth for versioning.
- PyPI / conda channels reject re-uploads, so an immutable
  human act (the tag) is the only viable input.
- Lets master keep its current "build everything, push web
  images as `:latest`" flow untouched — the release pipeline
  is purely additive.
- CalVer + tag push is a cheap human gate (no approval
  workflow infrastructure required).

### D2 — Scope per tag: monorepo-wide (not per-package)

A single tag like `2026.4.0` releases all 5 wheels, 4 conda
packages, and 2 Docker images in lockstep.

**Why:**
- Existing 799 tags are flat (`2024.1.0`, not `core/2024.1.0`)
  — switching to per-package would orphan the history.
- `hatch-vcs` reads the most recent tag; a single tag scheme
  means all packages naturally pick up the same version.
  Per-package tags would require teaching hatch-vcs to filter
  by tag prefix per package.
- Packages are not independently consumed — `gain-web-api`
  depends on `gain-core`; annotators depend on `gain-core`.
  Cross-package version skew creates an untested combinatorial
  matrix.
- CalVer already implies "snapshot of the world at date X" —
  per-package tags contradict that.
- Docker images bundle wheels — version mismatch between image
  tag and embedded wheels would be confusing.

The cost — a typo fix in `vep_annotator/README.md` triggers a
full release — is acceptable since artefacts are cheap and
registries dedupe.

### D3 — Tag format: `^\d{4}\.\d+\.\d+$` (final CalVer only)

Pre-release tags (`2026.4.0rc1`, `2026.4.0b1`) are **not**
recognized by the pipeline in V1.

**Why:**
- One pipeline path is simpler than two for V1.
- Bad release? Cut a new final tag (D11, roll-forward only).
- Pre-release support is additive and can be layered in later
  using PEP 440 + Anaconda label + Docker-tag conventions.

### D4 — Destinations

| Artefact | Destination |
|---|---|
| Wheels + sdists (5 packages) | `https://wheels.seqpipe.org/gain/` via SSH/rsync; `pip-index` regenerates `index.html` |
| Conda packages (4 packages) | Anaconda.org under `iossifovlab` org |
| Docker images (web_api, web_ui) | `registry.seqpipe.org` with `:${TAG}` (immutable) + `:stable` (moving). Existing `:latest` keeps tracking master, untouched |

**Why these and not alternatives:**

- **Public PyPI deferred to V2.** PyPI is a one-way door (no
  delete, name squatting forever). Committing the GAIn name to
  PyPI is a strategic decision, not a pipeline decision. Adding
  PyPI later is strictly additive.
- **conda-forge deferred.** Requires a feedstock + per-release
  upstream review; heavyweight relative to internal needs.
- **GitHub Releases attachments deferred.** Internal audience
  only for V1; HTTP index URL serves the same purpose.
- **Internal HTTP index** is cheap (existing infra,
  pip-index-generated) and fully PEP 503 compatible.
- **Anaconda.org** avoids hosting a private conda channel
  ourselves while keeping artefacts org-scoped.
- **Docker `:stable`** is the moving release pointer.
  `:latest` remains the master-tip pointer — distinction
  visible to deployers.

### D5 — Build vs promote: rebuild from tag, skip tests

When a tag arrives, the pipeline checks out the tagged commit
and rebuilds wheels + conda + Docker. It does **not** rerun
unit tests. The only "promotion" from master is the
base-image digest lockfile (D6).

**Why rebuild and not promote artefacts:**
- Wheel metadata embedded inside the `.whl` (`METADATA` file)
  carries the version string — `2026.3.0.dev5+gabc123` if
  built on master before the tag existed. Renaming the `.whl`
  file does not change the embedded version. Promoting wheels
  while wanting clean `2026.4.0` versions is a contradiction.
- Same for conda metadata.
- `uv build` and `rattler-build` are deterministic from the
  same source tree (modulo timestamps); rebuilding from the
  tagged commit produces clean version strings without
  introducing test divergence.

**Why skip tests:**
- Master CI on the tagged commit is the test gate. Re-running
  doubles wall time and reduces confidence (flake risk).
- The release pipeline's job is to produce clean-versioned
  artefacts, not to revalidate the commit.

**Pre-flight check (D8) enforces** that the tagged commit had
a green master build before any rebuild begins.

### D6 — Base image digest capture: master CI emits, release CI replays

Master CI captures the resolved digest of every base image
used in the prod Docker builds (`python:3.12-slim`,
`node:22.14.0-alpine`, `httpd:2.4-alpine`) and archives a
`dist/base-images.lock` artefact with `fingerprint: true`. The
release pipeline `copyArtifacts` the lockfile from the
matching master build and passes the digests as Docker
`--build-arg` values.

**Why this and not permanent Dockerfile pinning:**
- Floating tags (`python:3.12-slim`) keep local development
  unencumbered and let security updates flow naturally.
- A digest captured at master-CI time gives bit-identical
  base layers between "what was tested" and "what ships",
  without permanent Dockerfile pinning.
- `dist/base-images.lock` becomes a release-time audit trail.

**Dockerfile changes (one-time, in scope):**
- `web_api/Dockerfile.production`: `ARG PYTHON_VERSION=3.12-slim`
  → `ARG PYTHON_IMAGE=python:3.12-slim`,
  `FROM python:${PYTHON_VERSION}` → `FROM ${PYTHON_IMAGE}`.
  Defaults preserve current behavior for local builds.
- `web_ui/Dockerfile.production`: same treatment for
  `NODE_IMAGE` and `HTTPD_IMAGE`. The `${BACKEND_IMAGE}` arg
  transitively pins itself once the backend image digest is
  fixed.

### D7 — Pipeline location: same `Jenkinsfile`, `when` guards

Release stages live in the existing `Jenkinsfile`, gated by
`when { buildingTag(); expression { TAG_NAME ==~ /^\d{4}\.\d+\.\d+$/ } }`.
A new Jenkins job is not needed.

**Why:**
- Multibranch already builds tags when **Discover tags** is
  enabled — toggle a checkbox, no new job to wire up.
- Helpers (`runProject`, `publishReports`, conda-builder image
  step, registry credentials, Dockerfile build args) are
  already in `Jenkinsfile`. Splitting would duplicate or
  require shared-library extraction.
- The release pipeline diverges from branch CI only in the
  second half — exactly what `when` guards are for.
- One file to maintain when conda-builder, Dockerfile paths,
  or credentials change.

### D8 — Master CI gate: lookup by commit SHA in Jenkins

Before rebuilding anything, the release pipeline iterates
master multibranch builds, finds the one with
`GIT_COMMIT == tag commit`, and asserts `result == SUCCESS`.
The same lookup feeds `copyArtifacts` for `base-images.lock`.

**Why:**
- Required anyway for the lockfile fetch — validating
  `result == SUCCESS` is one extra check at zero cost.
- Bumped retention to 100 (D14) gives months of headroom; tags
  are typically cut shortly after the master build.
- Stronger than checking "master is green" globally — proves
  *that specific commit* was tested.
- Avoids a dependency on GitHub status checks.

### D9 — Auto-publish on tag push (no manual approval gate)

Tag push runs the full pipeline to completion without an
intermediate `input` step.

**Why:**
- The tag itself is the human act. A Jenkins `input` would be
  ceremony — same person approving the same commit they just
  tagged.
- `input` blocks the executor, holding a Jenkins agent for
  hours/days.
- No scenario today where tag-cut and release-decision
  diverge — there is no separate release-engineering team.
- Failure of a publish step is recoverable by re-running (D10).

### D10 — Failure mode: fail-fast + idempotent re-run

The publish phase is sequential. The first failure aborts the
build; the operator fixes the cause and re-runs the same
Jenkins build. Each publish step is written to be a no-op
when the target already has the bytes:
- Wheels: `rsync` overwriting same-name file with same bytes.
- Conda: `anaconda upload --skip-existing` (or `--force` for
  identical content).
- Docker: pushing the same tag with the same digest is a
  no-op for the registry.

A **pre-flight credential probe** runs before any state
mutates: `anaconda whoami`, `ssh -T` to the wheels host,
`docker login registry.seqpipe.org`. Catches expired tokens,
network problems, and DNS issues before partial publish.

**Why this and not transactional 2PC or best-effort UNSTABLE:**
- Cross-system atomicity (HTTP + Anaconda + Docker) is not
  achievable; "atomic flip" itself can fail.
- UNSTABLE hides failures; fail-fast forces visible action.
- Idempotent re-run is cheap to engineer (one CLI flag per
  tool) and convergent.

### D11 — Bad release: roll-forward only, no yank pipeline

Documented policy: **Bad release? Cut a new tag (`2026.X.Y+1`).**
The release pipeline auto-publishes the fix; `:stable` Docker
advances; `pip install -U` and `conda update` pull users
forward. No yank command in V1.

**Why:**
- Yanking breaks reproducibility for anyone with
  `gain-core==2026.4.0` pinned in `requirements.txt` /
  `environment.yml`. Blast radius of yank exceeds the original
  bug for non-security issues.
- CalVer fits roll-forward perfectly — newer date = newer.
- Anaconda.org delete is a one-way door for downstream caches.
- Custom HTTP index doesn't implement PEP 691 yanked metadata;
  proper yank-with-grace requires custom index work
  unjustified for V1.
- Critical security cases are rare; handle as one-off
  incidents (manual + advisory) rather than productionizing a
  yank pipeline.

### D12 — Notifications: distinct Zulip topic for releases

Tag builds notify Zulip topic `releases` (not the per-job
topic used for branch CI). The message body includes the
version and artefact URLs.

**Why:**
- The existing per-`JOB_NAME` topic for tag builds
  (`iossifovlab/gain/2026.4.0`) is buried — never reused after
  one tag.
- Releases warrant a fixed topic so consumers (deployers,
  downstream teams) can subscribe.

### D13 — Concurrency: serialize publish only

`disableConcurrentBuilds()` is **not** applied to the whole
job. Instead, the publish stages are wrapped in
`lock(resource: 'gain-release-publish')`.

**Why:**
- Two tags pushed near-simultaneously target disjoint version
  namespaces (different filenames, different image tags) — no
  artefact collision.
- The only contention is `:stable` retag — last-writer-wins
  without a lock, which is fine in degenerate cases but better
  serialized.
- Branch builds remain freely parallel; only release publish
  is serialized.

### D14 — Tag mutation: rejected at pipeline boundary

Before any publish step, the pipeline checks whether the
target version already exists at any destination
(`curl -fsI https://wheels.seqpipe.org/gain/gain_core-${TAG}-py3-none-any.whl`,
`anaconda show iossifovlab/gain-core/${TAG}`). If found, abort
with an explicit error. Master CI retention is bumped from
`numToKeepStr: '20'` to `'100'`, and `dist/base-images.lock`
uses `fingerprint: true` so the lockfile survives even if the
build record rotates.

**Why:**
- Re-publishing the same version with different bytes is
  hostile to consumers (cache invalidation, irreproducibility).
- The check is cheap and forces an operator to either pick a
  new version or hand-yank the existing artefacts (which is
  incident-only per D11).

### D15 — Skipped on tag builds

Stages skipped via `when { not { buildingTag() } }` on tag builds:
- `Sub-projects` parallel block — all per-project test stages
  (`core`, `demo_annotator`, `vep_annotator`,
  `spliceai_annotator`, `web_api`, `web_ui`).
- `Conda packages` — the Release stage rebuilds wheels with
  the clean tag version and re-runs the same rattler-build
  flow internally.
- `Build & push prod images` — the Release stage does its
  own digest-pinned rebuild and pushes `:${TAG_NAME}` +
  `:stable` instead of `:latest` / `:${BUILD_NUMBER}` /
  `:${GIT_SHORT}`.
- `Trigger web_e2e` — master CI already triggered e2e on the
  same commit; if it's green, the gate is satisfied.
- `Trigger VEP integration` — master CI handles this when
  `vep_annotator/**` changes; tag builds don't need to
  retrigger.

The `Start`, `Prepare workspace`, and `Conda builder image`
stages **do** run on tag builds. The conda-builder image is a
build-time dependency: the Release stage uses
`gain-conda-builder-ci:${BUILD_NUMBER}` for `anaconda upload`,
`anaconda show`, `anaconda whoami`, and `rattler-build`.

## Out of scope (V1)

The following are deliberately deferred:

- **Public PyPI publishing.** Strategic decision; revisit when
  the GAIn name is ready to be claimed publicly.
- **conda-forge feedstock.** Heavyweight; revisit when
  external conda users need it.
- **Annotator runtime Docker images** (`registry.seqpipe.org/gain-vep-annotator`
  etc.). Annotators are consumed via wheels/conda for V1.
- **Pre-release tag handling.** Final CalVer only; pre-releases
  layerable later via PEP 440 + Anaconda label + Docker tag
  conventions.
- **Artefact signing** (sigstore for wheels, GPG for Anaconda,
  cosign for Docker). Internal-only audience for V1.
- **SBOM generation.**
- **Vulnerability scan gate.**
- **Changelog automation.** No `CHANGELOG.md` in the repo
  today; commit message hygiene is a separate skill.
- **Documentation publishing on release.**
- **Yank pipeline.** Roll-forward only; security incidents
  handled manually and out-of-band.
- **GitHub Releases attachments.** Internal audience for V1;
  HTTP index URL is sufficient.

## Files to change

- `Jenkinsfile`:
  - Bump `numToKeepStr: '20'` → `'100'`.
  - In the existing `Build & push prod images` stage: capture
    base-image digests after each `docker build` and write
    `dist/base-images.lock`. Archive with `fingerprint: true`.
  - Pass `--build-arg PYTHON_IMAGE=python:3.12-slim` (and
    similar for node/httpd) on master builds — same value as
    today's behavior, but routed through the new ARG so the
    Dockerfile is consistent across master and tag builds.
  - Add a `when { buildingTag(); expression { TAG_NAME ==~ /^\d{4}\.\d+\.\d+$/ } }`
    branch:
    - `Pre-flight gate` — Jenkins API lookup of master build
      by `GIT_COMMIT`, assert `result == SUCCESS`.
    - `Tag freshness check` — `curl` wheels index +
      `anaconda show`; abort if existing.
    - `Credential probe` — `anaconda whoami`, `ssh -T` to
      wheels host, `docker login`.
    - `Fetch lockfile` — `copyArtifacts` `dist/base-images.lock`
      from upstream master build.
    - `Build wheels + sdists` at the tagged commit.
    - `Build conda packages` (existing rattler-build flow with
      `VCS_VERSION=${TAG}`).
    - `Build prod Docker images` with `--build-arg PYTHON_IMAGE=$PYTHON_IMAGE`
      etc., tagged `:${TAG}` and `:stable`.
    - `Publish` (wrapped in `lock('gain-release-publish')`):
      `rsync` wheels → `wheels.seqpipe.org`, then
      `ssh wheels-host pip-index ...`; `anaconda upload`;
      `docker push :${TAG}` + `docker push :stable`.
    - `Notify` Zulip `releases` topic.
  - Add `when { not { buildingTag() } }` (or equivalent) to
    skip per-project test stages, `Trigger web_e2e`, and
    `Trigger VEP integration` on tag builds.
- `web_api/Dockerfile.production`:
  - `ARG PYTHON_VERSION=3.12-slim` → `ARG PYTHON_IMAGE=python:3.12-slim`.
  - Update both `FROM python:${PYTHON_VERSION}` lines.
- `web_ui/Dockerfile.production`:
  - `ARG NODE_VERSION=22.14.0-alpine` → `ARG NODE_IMAGE=node:22.14.0-alpine`.
  - Add `ARG HTTPD_IMAGE=httpd:2.4-alpine`.
  - Update `FROM node:${NODE_VERSION}` and
    `FROM httpd:2.4-alpine` lines.
- `conda-builder/Dockerfile`:
  - Install `anaconda-client` (so `anaconda upload` is
    available inside the existing image).

## Pre-launch infra TODOs

These are not Jenkinsfile changes; they must be completed
before the first real release runs.

1. **Provision Jenkins credential `wheels-seqpipe-ssh-key`** —
   SSH private key for `rsync`/`scp` to `wheels.seqpipe.org`.
   The corresponding public key must be in the `authorized_keys`
   of the user owning `/srv/wheels/gain/` on
   `wheels.seqpipe.org`.
2. **Provision Jenkins credential `anaconda-token-iossifovlab`** —
   Anaconda.org API token (secret-text) with upload permission
   on the `iossifovlab` org.
3. **Toggle "Discover tags"** in the multibranch Jenkins
   project config so tag pushes produce builds.
4. **Ensure `wheels.seqpipe.org:/srv/wheels/gain/`** exists,
   is writable by the SSH user, and `pip-index` (or
   `dumb-pypi`) is installed on the host so the index can be
   regenerated after each upload.
5. **Verify `iossifovlab` Anaconda.org org exists** and is
   configured to accept uploads from the token.

## Open risks

- **Docker base image churn between master CI and tag.** If a
  master build runs against `python:3.12-slim@sha256:A`, then
  `python:3.12-slim` floats to digest `B` before the tag is
  cut, the lockfile pins to `A` correctly. But if Jenkins
  build retention rotates the master build away before the
  tag arrives, the lockfile is gone and the release pipeline
  cannot find it. Mitigation: D14's retention bump to 100;
  `fingerprint: true` keeps the file even if the build record
  is rotated; in the worst case the operator can hand-build
  the lockfile from `docker pull` of the floating tags and
  feed it in.
- **`hatch-vcs` version derivation.** The release pipeline
  relies on `git describe --tags` resolving to a clean
  `2026.4.0` at the tagged commit. Verify in a dry-run that
  `uv build` produces `gain_core-2026.4.0-py3-none-any.whl`
  (no `.dev` suffix, no `+g<sha>` local part).
- **First-release operator burden.** All five infra TODOs
  above must be in place before the first tag is pushed.
  Recommend a dry-run with a throwaway tag (e.g.,
  `2026.99.0-test`) on a private branch first — but final
  CalVer regex rejects it. Consider a one-time `RELEASE_DRY_RUN`
  Jenkins job parameter that runs the build stages but skips
  publish; not in V1 scope but worth noting.

## Addendum: extraction into a separate pipelineJob

Phase 10 originally embedded the release pipeline as a
`stage('Release') { when { buildingTag() } ... }` inside the
root `Jenkinsfile`. After it shipped (commit `1d527c36f`), the
embedded form turned out to be the wrong delivery vehicle: the
root `Jenkinsfile` ballooned past 1000 lines, and the
`when { not { buildingTag() } }` guards needed to keep CI
stages from running on tag builds proliferated across five
sibling stages. The release was extracted into a dedicated
pipelineJob, `gain-release`, on top of the same 15 D1–D15
decisions captured above. None of those decisions changed in
substance — only their *location*. This addendum records the
move and adds D16.

### What moved

- The Release stage (and its sub-stages D8 master CI gate,
  D14 tag freshness, D10 pre-flight credentials, fetch
  base-images.lock, build wheels + sdists, build conda
  packages, build prod Docker, D13 publish lock, notify)
  moved verbatim into a new `Jenkinsfile.release` at repo
  root.
- A new Jenkins Job DSL definition at
  `jenkins-jobs/release.groovy` declares the `gain-release`
  pipelineJob (declared at the Jenkins root, sibling of
  `gain-seed`, `gain-web-e2e`, and `gain-vep-integration`,
  for the same Org-Folder reason).
- The `Jenkinsfile.seed`'s existing
  `**/jenkins-jobs/*.groovy` glob picks up the new file
  with no seed change.

### What changed in substance

- **Master-build lookup.** The embedded version used
  `currentBuild.rawBuild.parent.parent.getItem('master')`,
  which works only because the embedded Release ran inside
  the multibranch. `gain-release` is a separate
  pipelineJob, so it uses
  `Jenkins.instance.getItemByFullName(params.UPSTREAM_PROJECT)`
  with `UPSTREAM_PROJECT` defaulted to
  `iossifovlab/gain/master`. D8 semantics unchanged; D8
  mechanics newer.
- **Workspace checkout.** The embedded version got the
  tagged commit checked out for free by Jenkins's
  multibranch tag-build automatic SCM step. `gain-release`
  declares `options { skipDefaultCheckout(true) }` and a
  dedicated `Validate + checkout tag` stage that does an
  explicit `checkout` of `refs/tags/${TAG_NAME}` with
  `shallow: false, noTags: false` so hatch-vcs's
  `git describe --tags` resolves the clean tag version.
- **Conda builder image.** The embedded version reused the
  multibranch's `Conda builder image` stage. `gain-release`
  builds its own copy in a `Setup` stage (placed after the
  master CI gate so doomed releases don't pay the docker
  build cost).
- **`copyArtifactPermission`.** The root `Jenkinsfile` now
  grants both `gain-web-e2e,gain-release` (was just
  `gain-web-e2e`); `gain-release`'s `Fetch
  base-images.lock` step uses `copyArtifacts` against the
  master build, which would otherwise hit Jenkins's
  permission-denied disguise ("Unable to find project for
  artifact copy").
- **Failure announcements.** The embedded Release shared the
  multibranch's `post { always { ... zulipNotification ... } }`
  failure path. `gain-release` declares its own
  `post { failure { zulipSend(topic: 'releases', ...) } }`
  so a tag-driven release failure still surfaces to Zulip
  even though the multibranch's tag-build (now a 5-line
  shim) exits SUCCESS once the dispatch fires.

### What changed in shape (root Jenkinsfile)

- All per-branch CI stages were wrapped in a single
  `stage('CI') { when { not { buildingTag() } } stages { ... } }`,
  collapsing five scattered `when { not { buildingTag() } }`
  guards (Sub-projects, Conda packages, Build & push prod
  images, Trigger web_e2e, Trigger VEP integration) into one.
- A new sibling `stage('Dispatch release')` was added with
  `when { buildingTag(); expression { TAG_NAME ==~ /^\d{4}\.\d+\.\d+$/ } }`,
  whose only step is
  `build job: '/gain-release', parameters: [string(name: 'TAG_NAME', value: env.TAG_NAME)], wait: false, propagate: false`.
- The embedded `stage('Release') { ... }` (≈380 lines) was
  deleted.

### D16 — Extract release into a dedicated pipelineJob

The release runs in `gain-release` (declared via
`jenkins-jobs/release.groovy`, script
`Jenkinsfile.release`), triggered by a thin dispatcher
stage in the root `Jenkinsfile` on tag builds matching the
CalVer regex.

**Why:**
- Keeps the root `Jenkinsfile` focused on per-branch CI; the
  root file shrunk from ~1040 to ~690 lines and lost five
  `when { not { buildingTag() } }` guards.
- Mirrors the established pattern of `gain-web-e2e` and
  `gain-vep-integration` (both declared at the Jenkins root,
  both kicked off via `build job:` from the multibranch with
  `wait: false, propagate: false`).
- Makes `gain-release` self-contained: a manual UI re-run
  needs only `TAG_NAME` (after a transient publish failure,
  for example), independent of the multibranch.
- Allows `Jenkinsfile.release` itself to be patched on
  master without retagging, while the *workspace* stays
  pinned to the tag — the cpsScm fetches the script from
  master tip, but the pipeline's first stage explicitly
  checks out the tagged commit.

**Downsides accepted:**
- The conda-builder docker image is built twice (once in
  master CI, once in `gain-release`). Docker layer caching
  makes the second build near-instant on a warm agent;
  worth the small DRY violation to keep the two pipelines
  decoupled.
- `gain-release`'s `Pre-flight: master CI gate` triggers
  in-process script approval the first time it runs (one-
  time admin step in "Manage Jenkins" → "In-process Script
  Approval"). The embedded version had the same requirement.

### Concurrency unchanged from D13

`gain-release` deliberately does *not* declare
`disableConcurrentBuilds()`. Pre-publish work (wheels,
conda, docker) runs in separate Jenkins workspaces with
distinct `${BUILD_NUMBER}` so two overlapping releases (e.g.,
a hotfix tag pushed shortly after a planned release) progress
in parallel up to the `lock(resource: 'gain-release-publish')`
on the Publish stage. The lock is the only required
serialization point — same design as D13, just enforced from
a separate job.
