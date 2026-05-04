# Nightly Python-version matrix

Status: design (grilled 2026-05-04)
Owner: Lubo

## Goal

Run the existing test suites for the GAIn sub-projects under
**Python 3.12, 3.13, and 3.14** every night, so that
forward-compatibility breakage on newer interpreters is caught
incrementally instead of at migration time.

This is a tox-style matrix over the **source tree** (not the
published wheels at `wheels.seqpipe.org`). The published wheels
are already pure-Python `py3-none-any.whl`; what's at risk is
the dependency closure (numpy / pandas / pysam / apsw / pyBigWig
/ aiohttp / Django / psycopg) on each interpreter.

## Decisions

| # | Question | Choice |
|---|---|---|
| Q1 | Test wheels or source? | **(b) source** — re-run CI on each Python |
| Q2 | Which sub-projects? | **(iii)** core, demo_annotator, vep_annotator, web_api (skip spliceai_annotator — `tensorflow>=2.18` has no 3.14 wheels yet; revisit when bumping to TF≥2.20) |
| Q3 | Matrix wiring? | **(A)** parameterize each Dockerfile via `ARG PYTHON_IMAGE=python:3.12-slim` |
| Q4 | Where in Jenkins? | **(a)** new `gain-python-matrix` job + `Jenkinsfile.python-matrix`, triggered from `Jenkinsfile.nightly` |
| Q5 | What runs per cell? | **(i)** pytest only (skip ruff/pylint/mypy — non-interpreter-sensitive, master CI already covers them) |
| Q6 | External services? | **(x)** full parity — Apache, MinIO, MailHog as today (those code paths are exactly where Python-version drift bites) |
| Q7a | uv lockfile? | **(L1)** keep `uv sync --frozen`; sdist-build failures count as legitimate signal |
| Q7b | Failure colour? | **(F1)** any cell failure → whole pipeline FAILURE; existing nightly `failure {}` Zulip fires |
| Q7c | Reporting / placement? | **(R1)** reuse the `nightly` Zulip topic; append as a fourth sequential stage in `Jenkinsfile.nightly` |

## Concrete shape

### Dockerfile change (per project: `core`, `demo_annotator`, `vep_annotator`, `web_api`)

```diff
- FROM python:3.12-slim
+ ARG PYTHON_IMAGE=python:3.12-slim
+ FROM ${PYTHON_IMAGE}
```

The default keeps existing per-branch CI behavior (`docker build -f core/Dockerfile -t gain-core-ci .` continues to land on 3.12 with no flag changes).

The matrix pipeline overrides via `--build-arg PYTHON_IMAGE=python:3.13-slim` (precedent: `Jenkinsfile.release:520`).

### `Jenkinsfile.python-matrix` (new file, root)

Skeleton:

```groovy
pipeline {
    agent { label '!dory' }

    options {
        timeout(time: 3, unit: 'HOURS')
        buildDiscarder(logRotator(numToKeepStr: '40'))
    }

    parameters {
        // Allow ad-hoc invocation outside the nightly cron.
        string(name: 'PYTHON_VERSIONS',
               defaultValue: '3.12,3.13,3.14',
               description: 'Comma-separated minor versions')
    }

    stages {
        stage('Prepare workspace') {
            steps { sh 'rm -rf reports && mkdir -p reports' }
        }

        stage('Matrix') {
            matrix {
                axes {
                    axis { name 'PY';      values '3.12', '3.13', '3.14' }
                    axis { name 'PROJECT'; values 'core', 'demo_annotator',
                                                  'vep_annotator', 'web_api' }
                }
                stages {
                    stage('pytest') {
                        steps {
                            script {
                                runProjectPytest(
                                    project: env.PROJECT,
                                    python:  env.PY,
                                )
                            }
                        }
                        post {
                            always {
                                script {
                                    junit allowEmptyResults: true,
                                          testResults: "reports/${env.PROJECT}-py${env.PY}/pytest.xml"
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    post {
        failure {
            node('!dory') {
                zulipSend(
                    topic: 'nightly',
                    message: "Python matrix FAILED — ${env.BUILD_URL}",
                )
            }
        }
    }
}
```

`runProjectPytest()` is a new helper modeled on the existing
`runProject()` in `Jenkinsfile`, with these differences:

- skips ruff / mypy / pylint (Q5)
- accepts a `python` arg → passes
  `--build-arg PYTHON_IMAGE=python:${python}-slim`
- writes JUnit + coverage to `reports/${project}-py${python}/`
  (avoids collisions when the matrix runs cells in parallel on
  the same agent)
- for `core` and `web_api`, brings up the same compose stacks
  (Apache + MinIO; MailHog) but with the compose project name
  scoped to include `${python}` to avoid container-name
  collisions across cells — same trick the per-branch pipeline
  uses with `${BUILD_NUMBER}`, just extended.

### `Jenkinsfile.nightly` change

Append after the `Run gain-vep-integration` stage:

```groovy
stage('Run gain-python-matrix') {
    steps {
        catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
            build(
                job: '/gain-python-matrix',
                wait: true,
                propagate: true,
            )
        }
    }
}
```

Sequential placement matches the existing pattern; the matrix is
independent of the master rebuild but it costs little to wait,
and serial keeps the orchestrator readable.

### Jenkins job DSL

Add `gain-python-matrix` to `jenkins-jobs/` (a sibling of the
existing seed job). Pipeline source: SCM → `Jenkinsfile.python-matrix`
on master.

## Rollout

1. Add `ARG PYTHON_IMAGE` to the four Dockerfiles. Smoke-test the
   default path (`python:3.12-slim`) on a per-branch build to
   confirm zero behavior change.
2. Add the new pipeline file; first land it as a manually-triggered
   job (no cron, not in `Jenkinsfile.nightly` yet). Run it once by
   hand; expect 3.13 mostly green, 3.14 noisy.
3. Triage 3.14 failures. If the failures are environmental (missing
   wheels) we may temporarily narrow the matrix to `[3.12, 3.13]`
   until the dep ecosystem catches up — but this is **not** a design
   decision, it's a rollout judgment call.
4. Wire the `build('/gain-python-matrix')` stage into
   `Jenkinsfile.nightly`. From this point a 3.14 break makes the
   nightly red.

## Out of scope

- Testing the *published wheels* (option (a) from Q1). Could be a
  follow-up; needs wheel-version pinning and `--find-links`
  install logic.
- `spliceai_annotator` (skipped per Q2). Reintroduce after the
  next TF bump that brings 3.14 wheels.
- mypy `--python-version` matrix (skipped per Q5). If type-system
  drift becomes a real concern, a mypy-only sub-matrix can be
  added without disturbing this one.
- `web_ui` (not Python).
