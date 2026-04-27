# Plan: Phase 10 — tag-driven release pipeline

## Context

Today's `Jenkinsfile` runs per-branch CI in a multibranch project.
On every master commit it builds wheels + sdists for the five
Python packages (`gain-core`, `gain-web-api`, `gain-demo-annotator`,
`gain-vep-annotator`, `gain-spliceai-annotator`), four conda
packages (core, demo, vep, spliceai — `gain-web-api` has no conda
recipe), and two production Docker images
(`registry.seqpipe.org/gain-web-api`,
`registry.seqpipe.org/gain-web-ui`). Docker images are pushed on
master with `:${BUILD_NUMBER}`, `:${GIT_SHORT}`, `:latest`; wheels
and conda packages are archived as Jenkins artefacts but
**published nowhere**.

The repository carries 799 calendar-versioned tags (`2024.1.0`,
`2026.4.0`, …) produced via `hatch-vcs` with
`version_scheme = "no-guess-dev"`. Tags are the existing source of
truth for "real" versions but currently trigger no automation.

**Goal:** make a final CalVer tag push trigger a deliberate release —
rebuild artefacts from the tagged commit so embedded versions are
clean (`2026.4.0`, no `.dev` suffix), then publish wheels to
`https://wheels.seqpipe.org/gain/`, conda packages to the
`iossifovlab` Anaconda.org org, and Docker images with `:${TAG}` +
`:stable` tags.

Full design rationale (15 decisions D1–D15, 11 deferred items, open
risks) lives in
`docs/2026-04-27-phase-10-release-pipeline.md`. This document is the
implementation plan; refer to the design doc for "why" questions.

## Scope

**In scope:**

- **Single-Jenkinsfile, additive.** A new release branch in
  `Jenkinsfile`, gated by
  `when { buildingTag(); expression { TAG_NAME ==~ /^\d{4}\.\d+\.\d+$/ } }`.
  Existing branch-CI stages get the inverse guard so they don't
  rerun on tag builds. No new Jenkins job.
- **Skip tests on tag builds.** Master CI is the test gate. The
  release pipeline does not rerun unit, integration, or e2e tests.
- **Base-image digest capture/replay.** Master CI captures the
  resolved digests of `python:3.12-slim`, `node:22.14.0-alpine`, and
  `httpd:2.4-alpine` into `dist/base-images.lock` (archived with
  `fingerprint: true`). The release pipeline `copyArtifacts` it
  from the matching master build and passes each digest as a Docker
  `--build-arg`, so released images use bit-identical base layers
  to what was tested.
- **Three publish destinations:**
  - Wheels + sdists → `https://wheels.seqpipe.org/gain/` via
    SSH/rsync; `pip-index` regenerates `index.html` after upload.
  - Conda packages → `iossifovlab` org on Anaconda.org via
    `anaconda upload --skip-existing` (idempotent).
  - Prod Docker images → `registry.seqpipe.org` with `:${TAG_NAME}`
    (immutable) + `:stable` (moving). Existing `:latest` keeps
    tracking master, untouched.
- **Fail-fast + idempotent re-run.** Each publish step is a no-op
  if the target already has the bytes. A pre-flight credential
  probe runs before any state mutates.
- **Concurrency lock on publish.** `lock(resource: 'gain-release-publish')`
  serializes publish across overlapping releases; branch builds
  remain freely parallel.
- **Tag mutation rejected** at the pipeline boundary by checking
  destinations for an existing version before publishing.

**Out of scope (per the design doc):**

- Public PyPI publishing.
- conda-forge feedstock.
- Annotator runtime Docker images
  (`registry.seqpipe.org/gain-vep-annotator` etc.).
- Pre-release tag handling (`2026.4.0rc1`, `2026.4.0b1`).
- Artefact signing (sigstore / GPG / cosign).
- SBOM generation, vulnerability scanning.
- Changelog automation, GitHub Releases attachments.
- Documentation publishing on release.
- Yank pipeline. (Roll-forward only — bad release? Cut a new tag.)

## Implementation steps

### Step 1 — Dockerfile build args

Switch from version-only ARG to full-image ARG so the digest can be
injected. Defaults preserve current behavior for local
`docker build`.

**`web_api/Dockerfile.production`** (lines 17, 21, 34):
- `ARG PYTHON_VERSION=3.12-slim` → `ARG PYTHON_IMAGE=python:3.12-slim`
- `FROM python:${PYTHON_VERSION} AS builder` → `FROM ${PYTHON_IMAGE} AS builder`
- `FROM python:${PYTHON_VERSION} AS runtime` → `FROM ${PYTHON_IMAGE} AS runtime`

**`web_ui/Dockerfile.production`** (lines 21, 26, 54):
- `ARG NODE_VERSION=22.14.0-alpine` → `ARG NODE_IMAGE=node:22.14.0-alpine`
- Add new line: `ARG HTTPD_IMAGE=httpd:2.4-alpine`
- `FROM node:${NODE_VERSION} AS angular` → `FROM ${NODE_IMAGE} AS angular`
- `FROM httpd:2.4-alpine AS runtime` → `FROM ${HTTPD_IMAGE} AS runtime`

`${BACKEND_IMAGE}` (lines 22 / 45) stays as-is — transitively pinned
once the backend release image is built.

### Step 2 — `conda-builder` image: add `anaconda-client`

**`conda-builder/Dockerfile`** (line 25):

Extend the existing `micromamba install` to also install
`anaconda-client`:

```dockerfile
RUN micromamba install -n base -y -c conda-forge \
        rattler-build anaconda-client \
    && micromamba clean --all --yes
```

This makes `anaconda upload` available inside the existing
`gain-conda-builder-ci:${BUILD_NUMBER}` image. No separate image,
no PATH change needed (`/opt/conda/bin` is already on PATH per line
29).

### Step 3 — Master CI changes (retention + base-image lockfile)

**`Jenkinsfile`** changes:

**3a. Bump retention** (line 96):
```groovy
buildDiscarder(logRotator(numToKeepStr: '20'))
// →
buildDiscarder(logRotator(numToKeepStr: '100'))
```

**3b. Capture base-image digests** at the end of the existing
`Build & push prod images` stage's `sh '''…'''` block (around line
421). Append:

```bash
# Resolve and record the base-image digests this build used,
# so the release pipeline can rebuild from-tag against the
# same base layers (Phase 10 — see
# docs/2026-04-27-phase-10-release-pipeline.md D6).
mkdir -p dist
{
    echo "PYTHON_IMAGE=$(docker image inspect python:3.12-slim \
        --format '{{index .RepoDigests 0}}')"
    echo "NODE_IMAGE=$(docker image inspect node:22.14.0-alpine \
        --format '{{index .RepoDigests 0}}')"
    echo "HTTPD_IMAGE=$(docker image inspect httpd:2.4-alpine \
        --format '{{index .RepoDigests 0}}')"
} > dist/base-images.lock
cat dist/base-images.lock
```

**3c. Archive the lockfile.** The existing `archiveArtifacts` block
in `post.always` (around line 561) needs an additional entry:

```groovy
archiveArtifacts(
    artifacts: 'dist/base-images.lock',
    allowEmptyArchive: false,
    fingerprint: true,
)
```

`fingerprint: true` ensures the file survives even if the build
record itself rotates out (defensive against the retention edge
case).

**3d. Use the new ARG names in master `docker build`** (around
line 425). Pass through floating-tag defaults explicitly via
`--build-arg`. Master keeps floating tags; tag builds inject
digests in step 5.7.

```bash
docker build \
    -f web_api/Dockerfile.production \
    --build-arg PYTHON_IMAGE=python:3.12-slim \
    -t "$BACKEND_REPO:$BUILD_NUMBER" .
```

Same pattern for the web_ui build (`NODE_IMAGE` + `HTTPD_IMAGE`).

### Step 4 — Skip branch-CI stages on tag builds

Add `when { not { buildingTag() } }` guards to:

- `Sub-projects` parallel block — skip the per-project test
  matrix on tag builds.
- `Conda packages` — the Release stage rebuilds wheels with the
  clean tag version and re-runs rattler-build internally.
- `Build & push prod images` — the Release stage does its own
  digest-pinned rebuild and pushes `:${TAG_NAME}` + `:stable`
  instead of `:latest` / `:${BUILD_NUMBER}` / `:${GIT_SHORT}`.
- `Trigger web_e2e`.
- `Trigger VEP integration` — already gated by `branch 'master'`
  and `changeset`; add `not { buildingTag() }` to the existing
  `allOf`.

The `Start`, `Prepare workspace`, and `Conda builder image`
stages run on both branch and tag builds. Tag builds need
`gain-conda-builder-ci:${BUILD_NUMBER}` for the Release stage's
anaconda + rattler-build invocations.

### Step 5 — Add release stages on tag builds

Insert a new `Release` parent stage in `Jenkinsfile` between
`Build & push prod images` and `Trigger web_e2e`, gated on:

```groovy
when {
    buildingTag()
    expression { env.TAG_NAME ==~ /^\d{4}\.\d+\.\d+$/ }
}
```

Internal sub-stages, in order:

**5.1 Pre-flight: master CI gate.** Iterate
`Jenkins.instance.getItemByFullName('iossifovlab/gain')`'s master
branch builds, find the one whose `GIT_COMMIT` matches the tag
commit, assert `result == hudson.model.Result.SUCCESS`. Stash the
build number for `copyArtifacts`. Abort with a clear error if not
found.

**5.2 Pre-flight: tag freshness check.**
- `curl -fsI -o /dev/null
   https://wheels.seqpipe.org/gain/gain_core-${TAG_NAME}-py3-none-any.whl`
  — if 200, abort.
- `docker run --rm gain-conda-builder-ci:${BUILD_NUMBER}
   anaconda show iossifovlab/gain-core/${TAG_NAME}` — if exit 0,
   abort.

**5.3 Pre-flight: credential probe.**
- SSH probe via `sshagent(['wheels-seqpipe-ssh-key'])`:
  `ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new
   wheels-host true`.
- Anaconda whoami: `anaconda --token "$ANACONDA_TOKEN" whoami`
  inside `gain-conda-builder-ci`.
- Docker login already occurs in `Build & push prod images`;
  reuse the same credentials.

**5.4 Fetch base-images.lock.**
```groovy
copyArtifacts(
    projectName: 'iossifovlab/gain/master',
    selector: specific("${UPSTREAM_BUILD_NUMBER}"),
    filter: 'dist/base-images.lock',
    fingerprintArtifacts: true,
)
```
Source it: `set -a; . dist/base-images.lock; set +a` so
`PYTHON_IMAGE` etc. are available to subsequent `docker build`.

**5.5 Build wheels + sdists at the tagged commit.**
```bash
mkdir -p dist/{core,web_api,demo_annotator,vep_annotator,spliceai_annotator}
for pkg in core web_api demo_annotator vep_annotator spliceai_annotator; do
    distpkg="gain-${pkg//_/-}"
    uv build --package "$distpkg" --out-dir "dist/$pkg"
done
```
Verify version: each wheel filename should match
`gain_<name>-${TAG_NAME}-py3-none-any.whl` (no `.dev` suffix). Fail
the build if not — that means hatch-vcs didn't see the tag, almost
certainly a shallow-checkout problem.

**5.6 Build conda packages.** Reuse the existing
`Conda packages` stage logic verbatim (line 355). It already reads
`VCS_VERSION` from a wheel filename, which now resolves to the
clean tag string.

**5.7 Build prod Docker images with digest-pinned bases.** The
earlier `Build & push prod images` stage runs with floating-tag
defaults. For tag builds, **rebuild** the same images with the
lockfile-injected digests, this time tagging directly as
`:${TAG_NAME}`:

```bash
docker build \
    -f web_api/Dockerfile.production \
    --build-arg PYTHON_IMAGE="$PYTHON_IMAGE" \
    -t "$BACKEND_REPO:$TAG_NAME" .

docker build \
    -f web_ui/Dockerfile.production \
    --build-arg NODE_IMAGE="$NODE_IMAGE" \
    --build-arg HTTPD_IMAGE="$HTTPD_IMAGE" \
    --build-arg BACKEND_IMAGE="$BACKEND_REPO:$TAG_NAME" \
    -t "$FRONTEND_REPO:$TAG_NAME" .
```

**5.8 Publish (wrapped in `lock(resource: 'gain-release-publish')`).**
- Wheels: `rsync -av dist/*/*.whl dist/*/*.tar.gz
   $WHEELS_HOST:/srv/wheels/gain/` via
   `sshagent(['wheels-seqpipe-ssh-key'])`, then
   `ssh $WHEELS_HOST 'cd /srv/wheels/gain && pip-index .'`.
- Conda:
   `anaconda --token "$ANACONDA_TOKEN" upload --user iossifovlab
    --skip-existing dist/conda/*.conda` inside
   `gain-conda-builder-ci`.
- Docker: `docker push $BACKEND_REPO:$TAG_NAME`,
   `docker tag $BACKEND_REPO:$TAG_NAME $BACKEND_REPO:stable`,
   `docker push $BACKEND_REPO:stable`. Same for `$FRONTEND_REPO`.

**5.9 Notify.**
```groovy
zulipSend(
    topic: 'releases',
    message: "Released ${env.TAG_NAME} — wheels: " +
             "https://wheels.seqpipe.org/gain/, " +
             "conda: https://anaconda.org/iossifovlab/gain-core, " +
             "docker: ${BACKEND_REPO}:${env.TAG_NAME}",
)
```

### Step 6 — Cleanup additions

Extend `post.cleanup` (line ~575) so tag-built `:${TAG_NAME}` and
`:stable` Docker tags are also `docker rmi`'d from the agent.

## Critical files

| File | Change scope |
|---|---|
| `Jenkinsfile` | Retention bump, base-image-lockfile capture, build-arg pass-through, new `Release` parent stage with 9 sub-stages, `not { buildingTag() }` guards on branch-only stages, cleanup extension |
| `web_api/Dockerfile.production` | `PYTHON_VERSION` → `PYTHON_IMAGE` (3 lines) |
| `web_ui/Dockerfile.production` | `NODE_VERSION` → `NODE_IMAGE`, add `HTTPD_IMAGE` ARG (3 lines) |
| `conda-builder/Dockerfile` | Append `anaconda-client` to `micromamba install` |
| `docs/2026-04-27-phase-10-release-pipeline.md` | Already written — design rationale, decisions, risks |

## Existing utilities reused

- **`runProject()`** (`Jenkinsfile:12`) — not used by tag stages
  (tag builds skip per-project test runs per D5/D15), but its
  `uv build --package <distPkg> --out-dir /dist` pattern is the
  model for step 5.5.
- **`gain-conda-builder-ci:${BUILD_NUMBER}`** image build
  (`Jenkinsfile:129–136`) — runs on tag builds too; tag's
  `anaconda upload` runs inside the same image.
- **`Build & push prod images`** stage (`Jenkinsfile:400–489`) —
  retained for branch builds; tag builds add a second
  build-and-push pass with digest-pinned bases and `:${TAG_NAME}` /
  `:stable` tags.
- **`zulipSend` / `zulipNotification`** (`Jenkinsfile:116–119`,
  `571`) — reused with topic override.
- **`copyArtifacts`** plugin (already used by `gain-web-e2e`
  downstream, per the comment at `Jenkinsfile:103–114`) — same
  plugin serves step 5.4.
- **`registry.seqpipe.org` credentials**
  (`user.registry.seqpipe.org`, `passwd.registry.seqpipe.org`,
  `Jenkinsfile:418–419`) — reused unchanged.

## Pre-launch infra TODOs

These do **not** block landing the Jenkinsfile/Dockerfile changes,
but the first real release tag will fail fast at the credential
probe (step 5.3) until they're done:

1. Provision Jenkins SSH-key credential `wheels-seqpipe-ssh-key`;
   add the public key to `wheels.seqpipe.org`'s
   `authorized_keys`.
2. Provision Jenkins secret-text credential
   `anaconda-token-iossifovlab`.
3. Toggle **Discover tags** in the multibranch Jenkins project
   config.
4. Ensure `wheels.seqpipe.org:/srv/wheels/gain/` exists, is
   writable by the SSH user, and `pip-index` is installed on the
   host.
5. Verify `iossifovlab` Anaconda.org org accepts uploads from the
   token.

## Verification

**Local (no Jenkins, no infra needed):**
- `docker build -f web_api/Dockerfile.production -t test .` from
  the repo root must still succeed with default `PYTHON_IMAGE`
  (regression guard for Step 1).
- `docker build -f web_ui/Dockerfile.production --build-arg
   BACKEND_IMAGE=test -t test-ui .` must succeed with default
  `NODE_IMAGE` and `HTTPD_IMAGE`.
- `docker build -f conda-builder/Dockerfile -t cb conda-builder
   && docker run --rm cb anaconda --version` must print a version
  string, not "command not found" (Step 2 regression guard).

**Master CI (after merge, before any tag):**
- A normal master build runs to completion as before.
- `dist/base-images.lock` is archived and contains three
  `KEY=image@sha256:…` lines.
- `:latest`, `:${BUILD_NUMBER}`, `:${GIT_SHORT}` Docker tags are
  pushed (existing behavior unchanged).
- Branch builds remain unchanged.

**Tag pipeline (requires the 5 infra TODOs above):**
- Push a tag matching `^\d{4}\.\d+\.\d+$` → multibranch picks it
  up, release stages run.
- Push a tag NOT matching the regex (e.g., `v2026.4.0` or
  `2026.4.0rc1`) → multibranch builds it, all release stages
  skipped via `when` guards, build is green-but-noop. Confirm by
  checking the build log that the tag-freshness check did not run.
- Re-push the same tag (force-push) → tag-freshness check (5.2)
  aborts the build with a clear error before any state mutates.
- Push a tag at a commit with no green master build → pre-flight
  master gate (5.1) aborts with a clear error.

**Operational:**
- `pip install --index-url https://wheels.seqpipe.org/gain/
   gain-core==2026.X.Y` resolves and installs.
- `conda install -c iossifovlab gain-core=2026.X.Y` resolves.
- `docker pull registry.seqpipe.org/gain-web-api:2026.X.Y`
  succeeds; `docker pull
  registry.seqpipe.org/gain-web-api:stable` returns the same
  digest.
- Zulip `releases` topic shows the announcement message.

## Risks & flagged items

- **Shallow checkout breaks hatch-vcs.** If Jenkins clones with
  `--depth 1`, the tag may not be discoverable by `git describe`.
  Step 5.5 has an explicit version-string assertion that catches
  this before any artefacts are published.
- **Lockfile rotated out.** If a tag is cut against a master commit
  whose Jenkins build was retention-pruned (>100 builds ago), step
  5.4 fails. Operator can hand-build the lockfile from
  `docker pull` + `docker image inspect` and pass it in via a
  parameterized job re-run — out of V1 scope, document if it ever
  happens.
- **First-release operator burden.** All 5 infra TODOs must be
  complete before the first tag. Recommend coordinating with
  infra owners before pushing the first real tag.
