# Plan: Phase 5 — Move web_ui CI onto the root-Jenkinsfile pattern

## Context

Phases 1–4 of the gpf-web-annotation merge are complete on `master`:
the code is restructured, lint configs reconciled, `web_api` is a uv
workspace member, and `web_api`'s CI runs in the root `Jenkinsfile`
alongside `core`/`vep_annotator`/`spliceai_annotator`/`demo_annotator`,
with a MailHog sidecar for the email-flow tests.

`web_ui` (the Angular 20 frontend) is the next piece. Its lint and
test still run inside `web_infra/Jenkinsfile` via the conda-era
`frontend-linters` / `frontend-tests` compose services. Phase 5 moves
that work into the root `Jenkinsfile`'s parallel block, mirroring
what Phase 4 did for `web_api`.

JS-side specifics make this not a literal copy of Phase 4:
`web_ui/` has no `package-lock.json`, so `npm ci` would fail. The
existing Dockerfile uses `npm i` which is non-deterministic. Phase 5
generates a lock so the CI image is reproducible.

## Scope

**In scope:**
- Generate and commit `web_ui/package-lock.json` so `npm ci` is the
  canonical install command (matching the role `uv.lock` plays for
  the Python projects).
- Rename `web_ui/Dockerfile` → `web_ui/Dockerfile.production`
  (mirrors the Phase 4 rename of `web_api/Dockerfile` →
  `web_api/Dockerfile.production`; the annotator convention reserves
  `<project>/Dockerfile` for the CI image).
- Create new `web_ui/Dockerfile` — node:22-alpine, two-stage
  (`npm ci` deps layer, then source), default `CMD` runs Jest.
- Add a `web_ui` parallel stage to the root `Jenkinsfile`. Since
  `runProject()` is Python-specific (it runs uv build, pylint,
  mypy, etc.), inline the JS CI commands in the stage rather than
  abstracting a `runFrontendProject()` helper for now — only one
  caller, abstracting wins nothing.
- Add `gain-web-ui-ci` to the root `Jenkinsfile` cleanup list.
- Add a `recordIssues` block in the web_ui stage's `post` to record
  the ESLint and Stylelint Checkstyle reports.
- Retire the conda-era frontend CI machinery in `web_infra/`:
  - Update `web_infra/compose-jenkins.yaml`: remove
    `frontend-tests` and `frontend-linters` services. (`frontend`
    and `frontend-e2e` stay — they build the production image for
    deployment / e2e use.)
  - Update `web_infra/compose.yaml`: remove the matching
    `frontend-tests` extension stub.
  - Update `web_infra/Jenkinsfile`: remove "Run Frontend Linters"
    and "Run Frontend Tests" stages, remove the frontend
    coverage/lint/HTML publishers from `post.always.script`.
  - Delete `web_ui/Dockerfile.dev` (no remaining consumer after
    `frontend-tests`/`frontend-linters` go away; `npm start` covers
    local dev directly without docker).
  - Delete `web_ui/scripts/frontend-linters.sh` (entrypoint of the
    removed `frontend-linters` service; the new CI image inlines
    the commands).
  - Delete `web_ui/scripts/frontend-adjust-coverage-paths.sh` (the
    sed runs inline inside the new CI script, the same way
    `runProject()` rewrites coverage source paths).

**Out of scope (later phases):**
- Production frontend Dockerfile rework — `web_ui/Dockerfile.production`
  stays untouched (it's the apache static-serving image).
- `web_e2e/` Playwright tests on root CI — Phase 6.
- backend-dev / `web_api/Dockerfile.dev` cleanup — separate small
  follow-up.
- Conda env file consolidation.
- Retiring the upstream `gpf-conda-packaging` coupling.

## Approach

### `web_ui/package-lock.json` (generate + commit)

Run `npm install` once inside `web_ui/`, which produces
`package-lock.json` from `package.json`'s semver ranges. Commit the
resulting file. From this point forward the CI Dockerfile uses
`npm ci`, which is deterministic and faster than `npm i`.

### `web_ui/Dockerfile` (new CI image)

Modeled on the annotator pattern but for the JS toolchain:

```dockerfile
# CI image for the Angular frontend (web_ui).
#
# Build from the repo root:
#     docker build -f web_ui/Dockerfile -t gain-web-ui-ci .

# syntax=docker/dockerfile:1.7
#
# Pin the same Node version as `Dockerfile.production` so CI exercises
# the same toolchain that ships to production. Bumping is a one-line
# change in both files when an upgrade is wanted.
ARG NODE_VERSION=22.14.0-alpine
FROM node:${NODE_VERSION}

WORKDIR /app

# Deps layer first so source-only edits hit the cache.
COPY web_ui/package.json web_ui/package-lock.json ./

RUN --mount=type=cache,target=/root/.npm npm ci

COPY web_ui/ ./

CMD ["npx", "jest", "--ci", "--collectCoverageFrom=./src/**", "--coverageDirectory=/reports/coverage"]
```

Default `CMD` runs Jest in CI mode; the `web_ui` Jenkins stage
overrides it to also run ESLint + Stylelint and the coverage path
rewrite, all in one `sh -c` so reports land in a single bind mount.

### `web_ui/Dockerfile.production` (rename)

`git mv web_ui/Dockerfile web_ui/Dockerfile.production`. Then update
the `frontend` and `frontend-e2e` build stanzas in
`web_infra/compose-jenkins.yaml`, `compose-iossifovweb.yaml`, and
`compose-wigclust.yaml` to point at `Dockerfile.production` (same
pattern as Phase 4 did for `web_api`).

### Root `Jenkinsfile` — new `web_ui` parallel stage

Add inside the `parallel { ... }` block, after the `web_api` stage:

```groovy
stage('web_ui') {
    steps {
        script {
            String imageTag = "gain-web-ui-ci:${env.BUILD_NUMBER}"
            sh label: 'Build web_ui image', script: """
                docker build -f web_ui/Dockerfile -t ${imageTag} .
            """
            sh label: 'Run web_ui CI', script: """
                mkdir -p reports/web_ui
                docker run --rm \\
                    -v \$PWD/reports/web_ui:/reports \\
                    ${imageTag} \\
                    sh -c '
                        set +e
                        mkdir -p /reports/coverage
                        npx eslint "**/*.{html,ts}" \\
                            --format checkstyle > /reports/ts-lint-report.xml
                        npx stylelint \\
                            --custom-formatter stylelint-checkstyle-formatter \\
                            "**/*.css" > /reports/css-lint-report.xml
                        JEST_JUNIT_OUTPUT_DIR=/reports \\
                            JEST_JUNIT_OUTPUT_NAME=jest.xml \\
                            npx jest --ci \\
                                --collectCoverageFrom=./src/** \\
                                --coverageDirectory=/reports/coverage
                        # Rewrite container-absolute /app paths to web_ui/
                        # so Jenkins coverage source mapping resolves files.
                        sed -i "s#<source>/app</source>#<source>web_ui</source>#g" \\
                            /reports/coverage/cobertura-coverage.xml 2>/dev/null || true
                        cp /reports/coverage/cobertura-coverage.xml \\
                            /reports/coverage.xml 2>/dev/null || true
                        chmod -R a+rw /reports
                        exit 0
                    '
            """
        }
    }
    post {
        always {
            script {
                publishReports('web_ui')
                recordIssues(
                    enabledForFailure: true,
                    aggregatingResults: false,
                    tools: [
                        checkStyle(
                            pattern: 'reports/web_ui/ts-lint-report.xml',
                            reportEncoding: 'UTF-8',
                            id: 'web_ui-eslint',
                            name: 'web_ui ESLint'),
                        checkStyle(
                            pattern: 'reports/web_ui/css-lint-report.xml',
                            reportEncoding: 'UTF-8',
                            id: 'web_ui-stylelint',
                            name: 'web_ui Stylelint'),
                    ],
                    qualityGates: [[threshold: 1, type: 'DELTA', unstable: true]]
                )
            }
        }
    }
}
```

`publishReports()` already handles Jest's `jest.xml` (it picks up
any `reports/${name}/*.xml` for JUnit) and the cobertura
`coverage.xml`. The `recordIssues` block adds the two Checkstyle
reports without modifying the shared helper.

### Root `Jenkinsfile` cleanup list

```groovy
for img in gain-core-ci gain-demo-annotator-ci gain-vep-annotator-ci \
           gain-spliceai-annotator-ci gain-web-api-ci gain-web-ui-ci \
           gain-conda-builder-ci; do
```

### `web_infra/Jenkinsfile`

Remove "Run Frontend Linters" stage. Remove "Run Frontend Tests"
stage. From `post.always.script`, remove the frontend
`recordCoverage`, the two `checkStyle` entries (CSS lint, TS lint)
in `recordIssues`, and the frontend `publishHTML`. The "Build
Frontend image", "Build Backend image", and "Run E2E Tests" stages
stay (they belong to the e2e flow, which is Phase 6).

### `web_infra/compose-jenkins.yaml` and `compose.yaml`

Drop `frontend-tests` and `frontend-linters` services /
extension stubs. Update `frontend` and `frontend-e2e` to build from
`Dockerfile.production`.

### Files deleted

- `web_ui/Dockerfile.dev`
- `web_ui/scripts/frontend-linters.sh`
- `web_ui/scripts/frontend-adjust-coverage-paths.sh`

## Step-by-step

1. **Generate `package-lock.json`**: from `web_ui/`, run `npm
   install`. Commit only `web_ui/package-lock.json` (don't commit
   `node_modules/`; it stays gitignored).
2. **Rename**: `git mv web_ui/Dockerfile web_ui/Dockerfile.production`.
3. **Update compose files** to point at `Dockerfile.production` for
   the `frontend` and `frontend-e2e` services in
   `web_infra/compose-jenkins.yaml`, `compose-iossifovweb.yaml`,
   `compose-wigclust.yaml`.
4. **Drop `frontend-tests` and `frontend-linters` services** from
   `web_infra/compose-jenkins.yaml` and the extension stub from
   `web_infra/compose.yaml`.
5. **Edit `web_infra/Jenkinsfile`**: remove the two stages and the
   frontend publishers from the post block.
6. **Delete dead files**: `web_ui/Dockerfile.dev`,
   `web_ui/scripts/frontend-linters.sh`,
   `web_ui/scripts/frontend-adjust-coverage-paths.sh`.
7. **Create new `web_ui/Dockerfile`** (CI image, as drafted above).
8. **Patch root `Jenkinsfile`**: new `web_ui` stage, cleanup list
   entry.
9. **Verify locally**:
   - `docker build -f web_ui/Dockerfile -t gain-web-ui-ci .` succeeds.
   - **Run the Jest test suite on its own** as the first inner-loop
     check, so a real test failure isn't masked by lint noise:
     `docker run --rm -v /tmp/web_ui-reports:/reports gain-web-ui-ci
     sh -c 'JEST_JUNIT_OUTPUT_DIR=/reports
     JEST_JUNIT_OUTPUT_NAME=jest.xml
     npx jest --ci --collectCoverageFrom=./src/**
     --coverageDirectory=/reports/coverage'`. Confirm the run
     reports the expected total (today: the same count
     `web_infra/Jenkinsfile`'s `frontend-tests` stage produces;
     compare to the most recent green `web_infra` build) and
     `/tmp/web_ui-reports/jest.xml` is well-formed JUnit.
   - Then `mkdir -p /tmp/web_ui-reports && docker run --rm -v
     /tmp/web_ui-reports:/reports gain-web-ui-ci sh -c '... full
     CI command ...'` writes `/tmp/web_ui-reports/{jest.xml,
     coverage.xml, ts-lint-report.xml, css-lint-report.xml}`.
   - Existing `gain-core-ci` etc. unaffected: spot-check the root
     `Jenkinsfile` parallel block diff.
10. **Commit** as a few logical commits (matching the rhythm of
    Phase 4):
    a. *Generate web_ui package-lock.json* — single new file.
    b. *Retire web_ui conda-based CI machinery* — rename Dockerfile,
       drop services + stages + scripts, update compose dockerfile
       refs, delete dead scripts and `Dockerfile.dev`.
    c. *Add uv-style CI Dockerfile for web_ui and wire into root
       Jenkinsfile* — new Dockerfile, root Jenkinsfile patch.
    d. *Add design doc for Phase 5 frontend uv-style CI* — this
       file, kept up to date with what actually landed.

## Critical files

- `/home/lubo/Work/seq-pipeline/gain/web_ui/package.json`
  (referenced; not modified)
- `/home/lubo/Work/seq-pipeline/gain/web_ui/package-lock.json` (created)
- `/home/lubo/Work/seq-pipeline/gain/web_ui/Dockerfile` (created — CI image)
- `/home/lubo/Work/seq-pipeline/gain/web_ui/Dockerfile.production`
  (renamed from `web_ui/Dockerfile`)
- `/home/lubo/Work/seq-pipeline/gain/web_ui/Dockerfile.dev` (deleted)
- `/home/lubo/Work/seq-pipeline/gain/web_ui/scripts/frontend-linters.sh` (deleted)
- `/home/lubo/Work/seq-pipeline/gain/web_ui/scripts/frontend-adjust-coverage-paths.sh` (deleted)
- `/home/lubo/Work/seq-pipeline/gain/Jenkinsfile` (web_ui stage + cleanup list)
- `/home/lubo/Work/seq-pipeline/gain/web_infra/Jenkinsfile`
- `/home/lubo/Work/seq-pipeline/gain/web_infra/compose-jenkins.yaml`
- `/home/lubo/Work/seq-pipeline/gain/web_infra/compose.yaml`
- `/home/lubo/Work/seq-pipeline/gain/web_infra/compose-iossifovweb.yaml`
- `/home/lubo/Work/seq-pipeline/gain/web_infra/compose-wigclust.yaml`

## Reference files

- `/home/lubo/Work/seq-pipeline/gain/Jenkinsfile` — `runProject()`
  helper, the pattern the `web_ui` stage adapts.
- `/home/lubo/Work/seq-pipeline/gain/web_ui/jest.config.js` — Jest
  config including `jest-junit` reporter (writes `jest.xml` to the
  configured output dir; the `JEST_JUNIT_OUTPUT_DIR` env var
  overrides).
- `/home/lubo/Work/seq-pipeline/gain/web_ui/.eslintrc.json` — ESLint
  config; runs the same checks today, just inside the new container.
- `/home/lubo/Work/seq-pipeline/gain/web_ui/.stylelintrc.json` —
  Stylelint config.

## Risks and known unknowns

- **`stylelint-checkstyle-formatter` is in `dependencies`, not
  `devDependencies`.** package.json lists it under `dependencies`
  for historical reasons. With `npm ci`, that's fine — both are
  installed. Worth a comment but no action.
- **First-time `package-lock.json` generation** picks specific minor
  versions for every transitive dep. Reviewing the lock diff would
  be tedious; commit it without line-by-line review unless something
  obviously breaks.
- **Coverage path rewrite**: the old script was
  `s/>\/app</>web_ui</g` (matches the closing tag). The new inline
  sed is `s#<source>/app</source>#<source>web_ui</source>#g`,
  which is the exact form `runProject()` uses for `gain-core` (more
  precise: only the `<source>` element gets touched, not random
  `/app` occurrences inside paths). If the Cobertura XML has
  different source elements, the sed may need adjustment — verified
  during Step 9.
- **Node version alignment**: the new CI Dockerfile pins the same
  `node:22.14.0-alpine` tag as `Dockerfile.production` (via a shared
  `NODE_VERSION` build-arg pattern, kept in lockstep across both
  files). This eliminates "works in CI, fails in prod" drift on
  Angular's TypeScript / Jest toolchain. When a Node bump is wanted,
  bump both files in the same commit. Alpine is correct for CI here:
  none of the dev tooling (Angular CLI, Jest, ESLint, Stylelint,
  jest-junit, stylelint-checkstyle-formatter) needs glibc-only
  native binaries.
- **No tests run yet locally**: web_ui has no test runner installed
  on the host (no `node_modules`). Verification must happen inside
  the docker container.

## Verification end-to-end

```bash
cd /home/lubo/Work/seq-pipeline/gain

# 1. Lock file generated
ls -l web_ui/package-lock.json

# 2. Image builds
docker build -f web_ui/Dockerfile -t gain-web-ui-ci .

# 3. Jest suite passes on its own. Done first so a real test
#    regression isn't hidden behind ESLint / Stylelint output in
#    the bundled command below. Compare the "Tests:" line and the
#    JUnit testcase count to the most recent green web_infra
#    build's frontend-tests artefact.
mkdir -p /tmp/web_ui-reports
docker run --rm -v /tmp/web_ui-reports:/reports gain-web-ui-ci sh -c '
    mkdir -p /reports/coverage
    JEST_JUNIT_OUTPUT_DIR=/reports JEST_JUNIT_OUTPUT_NAME=jest.xml \
        npx jest --ci \
            --collectCoverageFrom=./src/** \
            --coverageDirectory=/reports/coverage
'
ls -l /tmp/web_ui-reports/jest.xml   # expect non-zero size
grep -E '<testsuite[s]? ' /tmp/web_ui-reports/jest.xml | head
# expect: tests="<N>" failures="0" errors="0"

# 4. Full CI command runs and writes the four report files
docker run --rm -v /tmp/web_ui-reports:/reports gain-web-ui-ci sh -c '
    set +e
    mkdir -p /reports/coverage
    npx eslint "**/*.{html,ts}" --format checkstyle > /reports/ts-lint-report.xml
    npx stylelint --custom-formatter stylelint-checkstyle-formatter "**/*.css" > /reports/css-lint-report.xml
    JEST_JUNIT_OUTPUT_DIR=/reports JEST_JUNIT_OUTPUT_NAME=jest.xml \
        npx jest --ci \
            --collectCoverageFrom=./src/** \
            --coverageDirectory=/reports/coverage
    sed -i "s#<source>/app</source>#<source>web_ui</source>#g" \
        /reports/coverage/cobertura-coverage.xml 2>/dev/null || true
    cp /reports/coverage/cobertura-coverage.xml /reports/coverage.xml || true
'
ls /tmp/web_ui-reports/   # expect: jest.xml, coverage.xml, ts-lint-report.xml, css-lint-report.xml, coverage/
head /tmp/web_ui-reports/jest.xml
head /tmp/web_ui-reports/ts-lint-report.xml

# 5. Existing gain-core CI image still builds
docker build -f core/Dockerfile -t gain-core-ci-smoke .

# 6. Production frontend image still builds (renamed Dockerfile reachable)
cd web_infra && docker compose -f compose-jenkins.yaml build frontend
```

If steps 1–6 all succeed, Phase 5 is done.

## After Phase 5

Phase 6 — `web_e2e` (Playwright) on root CI: similar shape but with
sidecars for the production backend + frontend images on a docker
network, the same way `core` brings up apache + minio. Larger than
Phase 5 because it depends on both web_api and web_ui images being
built.

Other follow-ups: backend-dev cleanup, conda env file consolidation,
retire upstream `gpf-conda-packaging` coupling.
