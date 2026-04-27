// Jenkins pipeline for the GAIn monorepo.
//
// Runs per-sub-project CI in parallel. Each project:
//   1. Builds its Dockerfile image from the repo root build context.
//   2. Inside the container, runs ruff, mypy, pylint, and pytest with JUnit
//      output, plus Cobertura coverage for pytest.
//   3. Publishes JUnit + coverage reports to Jenkins.
//
// The container scripts never exit non-zero on tool failures; instead the
// JUnit plugin reads the XML and marks the build UNSTABLE on failures.

def runProject(Map args) {
    String name           = args.name                          // dir name, e.g. "demo_annotator"
    String pkg            = args.pkg                           // importable Python package, e.g. "demo_annotator"
    String tests          = args.tests                         // pytest target relative to project dir
    String mypyTarget     = args.mypyTarget ?: pkg
    String mypyExtra      = args.mypyExtra ?: ''               // e.g. "--config-file /workspace/mypy.ini"
    String pytestArgs     = args.pytestArgs ?: ''              // e.g. "-n auto"
    String dockerRunExtra = args.dockerRunExtra ?: ''          // extra flags for `docker run` (network, -v, -e, ...)
    String distName       = name.replace('_', '-')
    String distPkg        = args.distPkg ?: "gain-${distName}" // PyPI-style name, e.g. "gain-demo-annotator"
    String imageTag       = "gain-${distName}-ci:${env.BUILD_NUMBER}"

    sh label: "Build ${name} image", script: """
        docker build -f ${name}/Dockerfile -t ${imageTag} .
    """

    // Mount .git read-only so hatch-vcs can derive the version during
    // `uv build`. .git is excluded from the Docker build context via
    // .dockerignore, which keeps the test image small and cacheable;
    // it's only needed at distribution-build time.
    sh label: "Run ${name} CI", script: """
        mkdir -p reports/${name} dist/${name}
        docker run --rm \\
            -v \$PWD/reports/${name}:/reports \\
            -v \$PWD/dist/${name}:/dist \\
            -v \$PWD/.git:/workspace/.git:ro \\
            ${dockerRunExtra} \\
            ${imageTag} \\
            sh -c '
                set +e
                ruff check --output-format=junit --output-file=/reports/ruff.xml .
                mypy ${mypyExtra} ${mypyTarget} --junit-xml=/reports/mypy.xml
                # Prefer a per-project pylintrc when one exists (e.g. web_api
                # ships its own to load pylint_django). Falls back to the
                # repo-root pylintrc otherwise.
                pylint_rcfile=/workspace/${name}/pylintrc
                if [ ! -f "\$pylint_rcfile" ]; then
                    pylint_rcfile=/workspace/pylintrc
                fi
                pylint --rcfile="\$pylint_rcfile" \\
                       --load-plugins=pylint_junit \\
                       --output-format=pylint_junit.JUnitReporter \\
                       --exit-zero ${pkg} > /reports/pylint.xml
                pytest ${pytestArgs} \\
                    --junitxml=/reports/pytest.xml \\
                    --cov=${pkg} --cov-branch \\
                    --cov-report=xml:/reports/coverage.xml \\
                    ${tests}
                # Rewrite container-absolute <source>/workspace/...</source> to a
                # path relative to the Jenkins workspace so recordCoverage can
                # resolve source files.
                sed -i "s#<source>/workspace/\\([^<]*\\)</source>#<source>\\1</source>#g" \\
                    /reports/coverage.xml 2>/dev/null || true
                # Build wheel + sdist for this project. hatch-vcs reads the
                # mounted .git to produce a proper PEP 440 version.
                uv build --package ${distPkg} --out-dir /dist
                chmod -R a+rw /reports /dist
                exit 0
            '
    """
}

def publishReports(String name) {
    junit allowEmptyResults: true, testResults: "reports/${name}/*.xml"
    // `id` gives each sub-project its own coverage URL + sidebar action
    // and `name` the chart title — otherwise every report collides at
    // `/coverage` and is labelled "Code Coverage Trend".
    recordCoverage(
        tools: [[parser: 'COBERTURA', pattern: "reports/${name}/coverage.xml"]],
        id: "${name}-coverage",
        name: "${name} coverage",
        skipPublishingChecks: true,
        failOnError: false,
    )
}

pipeline {
    // Run on any agent except `dory` — its docker daemon /
    // resource profile doesn't fit the root build's compose
    // stacks. Other agents are interchangeable.
    agent { label '!dory' }

    options {
        timeout(time: 1, unit: 'HOURS')
        // Bumped from 20 → 100 to give the tag-driven release
        // pipeline (Phase 10) enough headroom to look up the
        // master build matching a tagged commit even after
        // months of master activity. The release pipeline
        // copies dist/base-images.lock from that upstream build.
        buildDiscarder(logRotator(numToKeepStr: '100'))
    }

    stages {
        stage('Start') {
            steps {
                // Grant the gain-web-e2e downstream job
                // permission to copy artefacts from this
                // per-branch job. Without this, the e2e job's
                // `copyArtifacts(projectName: 'iossifovlab/gain/<branch>')`
                // call fails with "Unable to find project for
                // artifact copy" (Jenkins's permission-denied
                // disguise). Idempotent — applied on every build.
                // BranchJobProperty (set by the multibranch scan)
                // is preserved.
                script {
                    properties([
                        copyArtifactPermission('gain-web-e2e'),
                    ])
                }
                zulipSend(
                    message: "Started build #${env.BUILD_NUMBER} of project ${env.JOB_NAME} (${env.BUILD_URL})",
                    topic: "${env.JOB_NAME}",
                )
            }
        }

        stage('Prepare workspace') {
            steps {
                sh 'rm -rf reports dist conda && mkdir -p reports dist conda'
            }
        }

        stage('Conda builder image') {
            steps {
                sh '''
                    docker build -f conda-builder/Dockerfile \
                        -t gain-conda-builder-ci:${BUILD_NUMBER} conda-builder
                '''
            }
        }

        stage('Sub-projects') {
            // Phase 10: tag builds skip per-project test runs —
            // master CI on the same commit was the test gate (see
            // docs/2026-04-27-phase-10-release-pipeline.md D5/D8).
            // The release stage's pre-flight asserts that master
            // build was SUCCESS before any rebuild begins.
            when { not { buildingTag() } }
            parallel {
                stage('core') {
                    environment {
                        COMPOSE_PROJECT = "gain-ci-${env.BUILD_NUMBER}"
                        COMPOSE_NETWORK = "gain-ci-${env.BUILD_NUMBER}_default"
                    }
                    steps {
                        script {
                            try {
                                // Bring up apache (HTTP fixture on :28080 inside the
                                // network) and minio (S3 fixture on :9000). Core tests
                                // reach them by service name via the compose network.
                                // minio-client is a one-shot bucket setup job — run it
                                // inline instead of via `up --wait`, which races with
                                // short-lived services.
                                sh '''
                                    mkdir -p core/tests/.test_grr
                                    docker compose -p "$COMPOSE_PROJECT" \
                                        up -d --wait apache minio
                                    docker compose -p "$COMPOSE_PROJECT" \
                                        run --rm minio-client
                                '''

                                runProject(
                                    name: 'core',
                                    pkg: 'gain',
                                    tests: 'tests',
                                    mypyTarget: 'gain',
                                    mypyExtra: '--config-file /workspace/mypy.ini',
                                    pytestArgs: '-n 5 --enable-http-testing --enable-s3-testing',
                                    dockerRunExtra:
                                        '--network "$COMPOSE_NETWORK" ' +
                                        '-e HTTP_HOST=apache:80 ' +
                                        '-e MINIO_HOST=minio ' +
                                        '-v $PWD/core/tests/.test_grr:/workspace/core/tests/.test_grr',
                                )
                            } finally {
                                sh '''
                                    docker compose -p "$COMPOSE_PROJECT" down -v --remove-orphans || true
                                '''
                            }
                        }
                    }
                    post { always { script { publishReports('core') } } }
                }

                stage('demo_annotator') {
                    steps {
                        script {
                            // demo tests spawn helper containers via the Python
                            // docker SDK; mount the host socket so the SDK can
                            // reach the daemon.
                            runProject(
                                name: 'demo_annotator',
                                pkg: 'demo_annotator',
                                tests: 'demo_annotator/tests',
                                pytestArgs: '-n 5',
                                dockerRunExtra: '-v /var/run/docker.sock:/var/run/docker.sock',
                            )
                        }
                    }
                    post { always { script { publishReports('demo_annotator') } } }
                }

                stage('vep_annotator') {
                    steps {
                        script {
                            // vep tests spawn helper containers via the Python
                            // docker SDK; mount the host socket so the SDK can
                            // reach the daemon.
                            runProject(
                                name: 'vep_annotator',
                                pkg: 'vep_annotator',
                                tests: 'vep_annotator/tests',
                                pytestArgs: '-n 5',
                                dockerRunExtra: '-v /var/run/docker.sock:/var/run/docker.sock',
                            )
                        }
                    }
                    post { always { script { publishReports('vep_annotator') } } }
                }

                stage('spliceai_annotator') {
                    steps {
                        script {
                            runProject(
                                name: 'spliceai_annotator',
                                pkg: 'spliceai_annotator',
                                tests: 'tests',
                                pytestArgs: '-n 5',
                            )
                        }
                    }
                    post { always { script { publishReports('spliceai_annotator') } } }
                }

                stage('web_api') {
                    environment {
                        COMPOSE_PROJECT = "gain-ci-web-api-${env.BUILD_NUMBER}"
                        COMPOSE_NETWORK = "gain-ci-web-api-${env.BUILD_NUMBER}_default"
                    }
                    steps {
                        script {
                            try {
                                // MailHog catches password-reset and account-
                                // activation emails so the user-flow tests can
                                // assert against them via --mailhog.
                                sh '''
                                    docker compose -p "$COMPOSE_PROJECT" \
                                        up -d --wait mail
                                '''

                                runProject(
                                    name: 'web_api',
                                    pkg: 'web_annotation',
                                    tests: 'web_annotation/tests',
                                    mypyTarget: 'web_annotation',
                                    mypyExtra: '--config-file /workspace/web_api/mypy.ini',
                                    pytestArgs: '--mailhog http://mail:8025',
                                    distPkg: 'gain-web-api',
                                    dockerRunExtra:
                                        '--network "$COMPOSE_NETWORK" ' +
                                        '-e GPFWA_EMAIL_HOST=mail ' +
                                        '-e DJANGO_SETTINGS_MODULE=' +
                                        'web_annotation.test_settings',
                                )
                            } finally {
                                sh '''
                                    docker compose -p "$COMPOSE_PROJECT" down -v --remove-orphans || true
                                '''
                            }
                        }
                    }
                    post { always { script { publishReports('web_api') } } }
                }

                stage('web_ui') {
                    steps {
                        script {
                            String imageTag =
                                "gain-web-ui-ci:${env.BUILD_NUMBER}"
                            sh label: 'Build web_ui image', script: """
                                docker build -f web_ui/Dockerfile \
                                    -t ${imageTag} .
                            """
                            // ESLint + Stylelint + Jest run inline because
                            // runProject() is Python-specific (uv build,
                            // pylint, mypy, pytest). Single sh -c so all
                            // four reports land in one bind mount.
                            sh label: 'Run web_ui CI', script: """
                                mkdir -p reports/web_ui
                                docker run --rm \\
                                    -v \$PWD/reports/web_ui:/reports \\
                                    ${imageTag} \\
                                    sh -c '
                                        set +e
                                        mkdir -p /reports/coverage
                                        npx eslint "**/*.{html,ts}" \\
                                            --format checkstyle \\
                                            > /reports/ts-lint-report.xml
                                        npx stylelint \\
                                            --custom-formatter \\
                                            stylelint-checkstyle-formatter \\
                                            "**/*.css" \\
                                            > /reports/css-lint-report.xml
                                        JEST_JUNIT_OUTPUT_DIR=/reports \\
                                        JEST_JUNIT_OUTPUT_NAME=jest.xml \\
                                            npx jest --ci \\
                                                --collectCoverageFrom=./src/** \\
                                                --coverageDirectory=/reports/coverage
                                        # Rewrite container-absolute /app
                                        # paths to web_ui/ so Jenkins coverage
                                        # source mapping resolves files. This
                                        # mirrors the runProject() sed for the
                                        # python projects.
                                        sed -i \\
                                            "s#<source>/app</source>#<source>web_ui</source>#g" \\
                                            /reports/coverage/cobertura-coverage.xml \\
                                            2>/dev/null || true
                                        cp /reports/coverage/cobertura-coverage.xml \\
                                            /reports/coverage.xml \\
                                            2>/dev/null || true
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
            }
        }

        stage('Conda packages') {
            // Phase 10: tag builds skip this stage — the Release
            // stage rebuilds wheels (with the clean tag version
            // from hatch-vcs) and then re-runs the same
            // rattler-build flow internally. Branch builds keep
            // producing snapshot conda packages as before.
            when { not { buildingTag() } }
            steps {
                sh '''
                    # Derive the hatch-vcs PEP 440 version from any wheel name;
                    # pass it to rattler-build via env so the conda package
                    # version matches the wheel's.
                    VCS_VERSION=$(ls dist/core/*.whl | head -1 \
                        | sed 's#.*gain_core-##' \
                        | sed 's#-py3-none-any.whl$##')
                    echo "VCS_VERSION=$VCS_VERSION"

                    mkdir -p dist/conda
                    # Run the conda-builder container as the Jenkins user
                    # (instead of the image's default `mambauser`, UID
                    # 57439). rattler-build creates its output `.conda`
                    # via a 0600-mode tempfile, so files produced by
                    # mambauser end up unreadable to Jenkins on the host;
                    # matching UIDs sidesteps that entirely. HOME is
                    # redirected to /tmp because /home/mambauser is not
                    # writable by an arbitrary UID.
                    DOCKER_USER="$(id -u):$(id -g)"
                    for proj in core demo_annotator vep_annotator spliceai_annotator; do
                        mkdir -p conda/$proj
                        docker run --rm \
                            --user "$DOCKER_USER" \
                            -e HOME=/tmp \
                            -v $PWD:/workspace \
                            -w /workspace \
                            -e VCS_VERSION="$VCS_VERSION" \
                            gain-conda-builder-ci:${BUILD_NUMBER} \
                            rattler-build build \
                                --recipe $proj/conda-recipe/recipe.yaml \
                                --output-dir conda/$proj
                        # Promote the final .conda artefact(s) out of
                        # rattler-build's working tree. conda/$proj/bld/
                        # holds 1000+ symlinks into build-env prefixes;
                        # archiveArtifacts walking that tree has raced
                        # with it. dist/conda/ stays clean and holds
                        # only the published packages.
                        cp conda/$proj/noarch/*.conda dist/conda/
                    done
                '''
            }
        }

        stage('Build & push prod images') {
            // Phase 9 slice 1: build the wheel-based backend +
            // Apache-based frontend prod images here in the
            // root build, tag them for registry.seqpipe.org, and
            // push on master. Branch builds build-but-don't-push
            // (validates the Dockerfiles + that the wheels
            // install). Tags pushed on master:
            //   :${BUILD_NUMBER}  — Jenkins build identity
            //   :${GIT_SHORT}     — immutable git-anchored handle
            //   :latest           — moving pointer for prod
            //
            // Phase 10: tag builds skip this stage — the Release
            // stage does its own digest-pinned rebuild and
            // pushes :${TAG_NAME} + :stable instead.
            when { not { buildingTag() } }
            environment {
                REGISTRY      = 'registry.seqpipe.org'
                BACKEND_REPO  = "${env.REGISTRY}/gain-web-api"
                FRONTEND_REPO = "${env.REGISTRY}/gain-web-ui"
                GIT_SHORT     = "${env.GIT_COMMIT.take(8)}"
                // Two secret-text credentials, set up in Jenkins.
                // Bound here for the whole stage but only used by
                // the master-only push path below.
                REGISTRY_USER = credentials('user.registry.seqpipe.org')
                REGISTRY_PASS = credentials('passwd.registry.seqpipe.org')
            }
            steps {
                sh '''
                    # Build backend; tag with build number first
                    # so the frontend's --build-arg can reference
                    # it. PYTHON_IMAGE is passed explicitly so the
                    # Dockerfile is consistent across master (this
                    # path, floating tag) and tag builds (Phase 10
                    # release stage, digest-pinned).
                    docker build \
                        -f web_api/Dockerfile.production \
                        --build-arg PYTHON_IMAGE=python:3.12-slim \
                        -t "$BACKEND_REPO:$BUILD_NUMBER" .
                    docker tag "$BACKEND_REPO:$BUILD_NUMBER" \
                               "$BACKEND_REPO:$GIT_SHORT"

                    # Build frontend; multi-stages collectstatic
                    # from the backend image we just built.
                    docker build \
                        -f web_ui/Dockerfile.production \
                        --build-arg NODE_IMAGE=node:22.14.0-alpine \
                        --build-arg HTTPD_IMAGE=httpd:2.4-alpine \
                        --build-arg BACKEND_IMAGE="$BACKEND_REPO:$BUILD_NUMBER" \
                        -t "$FRONTEND_REPO:$BUILD_NUMBER" .
                    docker tag "$FRONTEND_REPO:$BUILD_NUMBER" \
                               "$FRONTEND_REPO:$GIT_SHORT"

                    # Resolve and record the base-image digests
                    # this build used, so the Phase 10 release
                    # pipeline can rebuild from a tagged commit
                    # against the same base layers (see
                    # docs/2026-04-27-phase-10-release-pipeline.md
                    # decision D6). Archived below in post.always
                    # with fingerprint:true so the file survives
                    # even if the build record rotates out.
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
                '''
                script {
                    if (env.BRANCH_NAME == 'master') {
                        // `--password-stdin` keeps the secret out
                        // of the process list / shell trace. Use
                        // `printf '%s'` (not `echo`) so the
                        // password is sent byte-for-byte: echo
                        // appends a trailing newline and POSIX
                        // /bin/sh's echo also interprets backslash
                        // escapes — both can silently mangle a
                        // valid password into a 401. The trap
                        // ensures docker logout runs even if a
                        // push fails — agents are shared, don't
                        // leave registry auth lying around.
                        //
                        // The wc -c lines are diagnostics: Jenkins
                        // masks the secret values themselves but
                        // the byte counts are integers and aren't
                        // masked, so they make a credential
                        // anomaly (wrong field, wrapped value,
                        // trailing CR/LF) visible at a glance. The
                        // earlier `jenkins-registry.seqpipe.org.*`
                        // pair returned a 139-byte "password" and
                        // 401'd; this pair (`user.registry.seqpipe.org`
                        // + `passwd.registry.seqpipe.org`) is the
                        // candidate replacement.
                        sh '''
                            echo "REGISTRY_USER bytes: $(printf '%s' "$REGISTRY_USER" | wc -c)"
                            echo "REGISTRY_PASS bytes: $(printf '%s' "$REGISTRY_PASS" | wc -c)"
                            printf '%s' "$REGISTRY_PASS" | docker login \
                                -u "$REGISTRY_USER" \
                                --password-stdin "$REGISTRY"
                            trap 'docker logout "$REGISTRY" || true' EXIT
                            docker tag "$BACKEND_REPO:$BUILD_NUMBER" \
                                       "$BACKEND_REPO:latest"
                            docker tag "$FRONTEND_REPO:$BUILD_NUMBER" \
                                       "$FRONTEND_REPO:latest"
                            for repo in "$BACKEND_REPO" "$FRONTEND_REPO"; do
                                docker push "$repo:$BUILD_NUMBER"
                                docker push "$repo:$GIT_SHORT"
                                docker push "$repo:latest"
                            done
                        '''
                    } else {
                        echo "Skipping registry push: " +
                             "branch is ${env.BRANCH_NAME}, not master"
                    }
                }
            }
        }

        stage('Release') {
            // Phase 10: tag-driven release pipeline. See
            // docs/2026-04-27-phase-10-release-pipeline-plan.md
            // for the implementation plan and
            // docs/2026-04-27-phase-10-release-pipeline.md for
            // the 15 design decisions and rationale.
            //
            // Triggered by a final CalVer tag (^\d{4}\.\d+\.\d+$).
            // Pre-release suffixes (rc, b, a, .dev) intentionally
            // ignored — pre-release support is deferred to V2.
            //
            // Tag builds skip the Sub-projects, Conda packages,
            // Build & push prod images, Trigger web_e2e, and
            // Trigger VEP integration stages — the master CI
            // build of the same commit already validated them.
            // The Pre-flight: master CI gate stage below
            // enforces that gate strictly.
            when {
                buildingTag()
                expression { env.TAG_NAME ==~ /^\d{4}\.\d+\.\d+$/ }
            }
            environment {
                REGISTRY        = 'registry.seqpipe.org'
                BACKEND_REPO    = "${env.REGISTRY}/gain-web-api"
                FRONTEND_REPO   = "${env.REGISTRY}/gain-web-ui"
                REGISTRY_USER   = credentials('user.registry.seqpipe.org')
                REGISTRY_PASS   = credentials('passwd.registry.seqpipe.org')
                ANACONDA_TOKEN  = credentials('anaconda-token-iossifovlab')
                WHEELS_HOST     = 'nemo'
                WHEELS_PATH     = '/data/wheels/gain'
                ANACONDA_USER   = 'iossifovlab'
                CONDA_BUILDER   = "gain-conda-builder-ci:${env.BUILD_NUMBER}"
            }
            stages {
                stage('Pre-flight: master CI gate') {
                    // Find the master multibranch build whose
                    // GIT_COMMIT matches the tagged commit and
                    // assert it succeeded. Stash its build
                    // number for the copyArtifacts call below.
                    // D8 — see design doc.
                    //
                    // Uses Jenkins core APIs that require
                    // in-process script approval the first time
                    // a release runs (admin → "Manage Jenkins"
                    // → "In-process Script Approval").
                    steps {
                        script {
                            def parent = currentBuild.rawBuild.parent.parent
                            def masterJob = parent.getItem('master')
                            if (masterJob == null) {
                                error("Could not locate master branch in ${parent.fullName}")
                            }
                            def matching = null
                            for (b in masterJob.getBuilds()) {
                                def gitData = b.getAction(hudson.plugins.git.util.BuildData)
                                def sha = gitData?.lastBuiltRevision?.sha1String
                                if (sha == env.GIT_COMMIT) {
                                    matching = b
                                    break
                                }
                            }
                            if (matching == null) {
                                error(
                                    "No master build found for commit " +
                                    "${env.GIT_COMMIT}. Tag a commit that " +
                                    "has been merged to master and built " +
                                    "green."
                                )
                            }
                            if (matching.result != hudson.model.Result.SUCCESS) {
                                error(
                                    "Master build #${matching.number} for " +
                                    "commit ${env.GIT_COMMIT} did not " +
                                    "succeed (${matching.result}). Refusing " +
                                    "to release."
                                )
                            }
                            env.UPSTREAM_BUILD_NUMBER = matching.number.toString()
                            echo(
                                "Upstream master build: #${matching.number} " +
                                "(SUCCESS) for commit ${env.GIT_COMMIT}"
                            )
                        }
                    }
                }

                stage('Pre-flight: tag freshness') {
                    // Reject tag mutation: if any destination
                    // already has an artefact for ${TAG_NAME},
                    // abort. D14.
                    steps {
                        sh '''
                            # HEAD on the wheels index — 200
                            # means already published. Anything
                            # else (404, 5xx, network) is treated
                            # as "not there yet, proceed" so a
                            # flaky index doesn't block releases.
                            code=$(curl -sS -o /dev/null \
                                -w '%{http_code}' \
                                "https://${WHEELS_HOST}/gain/gain_core-${TAG_NAME}-py3-none-any.whl")
                            echo "Wheel index probe: HTTP $code"
                            if [ "$code" = "200" ]; then
                                echo "ERROR: gain-core ${TAG_NAME} already" \
                                     "at https://${WHEELS_HOST}/gain/"
                                echo "Tag mutation is rejected." \
                                     "Cut a new tag instead."
                                exit 1
                            fi

                            # Anaconda check: exit 0 means the
                            # package exists; non-zero (1, 2,
                            # network) is treated as "not there
                            # yet". Use --no-progress + 2>&1 to
                            # keep the build log readable.
                            if docker run --rm "${CONDA_BUILDER}" \
                                anaconda show \
                                    "${ANACONDA_USER}/gain-core/${TAG_NAME}" \
                                    >/dev/null 2>&1; then
                                echo "ERROR: gain-core ${TAG_NAME} already" \
                                     "on Anaconda.org/${ANACONDA_USER}"
                                echo "Tag mutation is rejected." \
                                     "Cut a new tag instead."
                                exit 1
                            fi
                        '''
                    }
                }

                stage('Pre-flight: credentials') {
                    // Touch each destination before any state
                    // mutates. Catches expired tokens, network
                    // failures, missing ssh keys before the
                    // publish phase begins. D10.
                    steps {
                        sshagent(credentials: ['wheels-seqpipe-ssh-key']) {
                            sh '''
                                ssh -o BatchMode=yes \
                                    -o StrictHostKeyChecking=accept-new \
                                    "${WHEELS_HOST}" true
                            '''
                        }
                        sh '''
                            docker run --rm \
                                -e ANACONDA_TOKEN \
                                "${CONDA_BUILDER}" \
                                anaconda --token "$ANACONDA_TOKEN" whoami
                        '''
                        sh '''
                            printf '%s' "$REGISTRY_PASS" | docker login \
                                -u "$REGISTRY_USER" \
                                --password-stdin "$REGISTRY"
                            docker logout "$REGISTRY"
                        '''
                    }
                }

                stage('Fetch base-images.lock') {
                    // Pull the digest lockfile from the master
                    // build identified in Pre-flight: master CI
                    // gate. fingerprintArtifacts:true pairs with
                    // the master archiveArtifacts'
                    // fingerprint:true so the file is locatable
                    // even if the build record itself rotated.
                    steps {
                        copyArtifacts(
                            projectName: env.JOB_NAME.replaceAll(
                                /\/[^\/]+$/, '/master'),
                            selector: specific(
                                "${env.UPSTREAM_BUILD_NUMBER}"),
                            filter: 'dist/base-images.lock',
                            fingerprintArtifacts: true,
                        )
                        sh 'cat dist/base-images.lock'
                    }
                }

                stage('Build wheels + sdists') {
                    // hatch-vcs derives the embedded version
                    // from `git describe --tags`; at a tagged
                    // commit this resolves to the clean
                    // ${TAG_NAME} with no .dev suffix. Verified
                    // immediately after build — a shallow clone
                    // would yield a dev version and we abort
                    // before publishing.
                    steps {
                        sh '''
                            mkdir -p dist/core dist/web_api \
                                dist/demo_annotator \
                                dist/vep_annotator \
                                dist/spliceai_annotator
                            for pkg in core web_api \
                                       demo_annotator \
                                       vep_annotator \
                                       spliceai_annotator; do
                                distpkg="gain-${pkg//_/-}"
                                uv build --package "$distpkg" \
                                    --out-dir "dist/$pkg"
                            done

                            bad=0
                            for whl in dist/*/*.whl; do
                                case "$whl" in
                                    *"-${TAG_NAME}-py3-none-any.whl") ;;
                                    *)
                                        echo "$whl does not embed" \
                                             "clean ${TAG_NAME}" >&2
                                        bad=1
                                        ;;
                                esac
                            done
                            if [ "$bad" != 0 ]; then
                                echo "hatch-vcs did not resolve to" \
                                     "${TAG_NAME} — likely a shallow" \
                                     "clone. Aborting." >&2
                                exit 1
                            fi
                        '''
                    }
                }

                stage('Build conda packages') {
                    // Reuses the rattler-build flow from the
                    // (skipped on tag) Conda packages stage,
                    // with VCS_VERSION pulled from the freshly
                    // built wheel filenames so the conda
                    // versions match.
                    steps {
                        sh '''
                            VCS_VERSION=$(ls dist/core/*.whl \
                                | head -1 \
                                | sed 's#.*gain_core-##' \
                                | sed 's#-py3-none-any.whl$##')
                            echo "VCS_VERSION=$VCS_VERSION"

                            mkdir -p dist/conda
                            DOCKER_USER="$(id -u):$(id -g)"
                            for proj in core demo_annotator \
                                        vep_annotator \
                                        spliceai_annotator; do
                                mkdir -p conda/$proj
                                docker run --rm \
                                    --user "$DOCKER_USER" \
                                    -e HOME=/tmp \
                                    -v $PWD:/workspace \
                                    -w /workspace \
                                    -e VCS_VERSION="$VCS_VERSION" \
                                    "${CONDA_BUILDER}" \
                                    rattler-build build \
                                        --recipe $proj/conda-recipe/recipe.yaml \
                                        --output-dir conda/$proj
                                cp conda/$proj/noarch/*.conda dist/conda/
                            done
                        '''
                    }
                }

                stage('Build prod Docker (digest-pinned)') {
                    // Same Dockerfiles the master build uses,
                    // but with PYTHON_IMAGE / NODE_IMAGE /
                    // HTTPD_IMAGE pinned to the digests captured
                    // by the upstream master build. Tagged
                    // directly as :${TAG_NAME}; :stable is
                    // applied at push time.
                    steps {
                        sh '''
                            set -a
                            . dist/base-images.lock
                            set +a
                            echo "PYTHON_IMAGE=${PYTHON_IMAGE}"
                            echo "NODE_IMAGE=${NODE_IMAGE}"
                            echo "HTTPD_IMAGE=${HTTPD_IMAGE}"

                            docker build \
                                -f web_api/Dockerfile.production \
                                --build-arg PYTHON_IMAGE="${PYTHON_IMAGE}" \
                                -t "${BACKEND_REPO}:${TAG_NAME}" .

                            docker build \
                                -f web_ui/Dockerfile.production \
                                --build-arg NODE_IMAGE="${NODE_IMAGE}" \
                                --build-arg HTTPD_IMAGE="${HTTPD_IMAGE}" \
                                --build-arg BACKEND_IMAGE="${BACKEND_REPO}:${TAG_NAME}" \
                                -t "${FRONTEND_REPO}:${TAG_NAME}" .
                        '''
                    }
                }

                stage('Publish') {
                    // Serialize across overlapping releases
                    // (D13). The lock collides only with another
                    // Release stage running concurrently —
                    // branch builds and other tag builds in
                    // their non-publish phases are unaffected.
                    options {
                        lock(resource: 'gain-release-publish')
                    }
                    steps {
                        // 1) Wheels + sdists → wheels.seqpipe.org
                        sshagent(credentials: ['wheels-seqpipe-ssh-key']) {
                            sh '''
                                files=$(ls \
                                    dist/core/gain_core-*.whl \
                                    dist/core/gain_core-*.tar.gz \
                                    dist/web_api/gain_web_api-*.whl \
                                    dist/web_api/gain_web_api-*.tar.gz \
                                    dist/demo_annotator/gain_demo_annotator-*.whl \
                                    dist/demo_annotator/gain_demo_annotator-*.tar.gz \
                                    dist/vep_annotator/gain_vep_annotator-*.whl \
                                    dist/vep_annotator/gain_vep_annotator-*.tar.gz \
                                    dist/spliceai_annotator/gain_spliceai_annotator-*.whl \
                                    dist/spliceai_annotator/gain_spliceai_annotator-*.tar.gz)
                                # Deliberate word-split on $files.
                                rsync -av \
                                    -e "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new" \
                                    $files \
                                    "${WHEELS_HOST}:${WHEELS_PATH}/"
                                ssh -o BatchMode=yes \
                                    -o StrictHostKeyChecking=accept-new \
                                    "${WHEELS_HOST}" \
                                    "cd ${WHEELS_PATH} && pip-index ."
                            '''
                        }

                        // 2) Conda → Anaconda.org/iossifovlab.
                        // --skip-existing makes re-runs after a
                        // partial publish failure idempotent
                        // (D10).
                        sh '''
                            docker run --rm \
                                -e ANACONDA_TOKEN \
                                -v "$PWD/dist/conda:/dist/conda:ro" \
                                "${CONDA_BUILDER}" \
                                anaconda --token "$ANACONDA_TOKEN" \
                                    upload \
                                        --user "${ANACONDA_USER}" \
                                        --skip-existing \
                                        /dist/conda/*.conda
                        '''

                        // 3) Docker → registry.seqpipe.org with
                        // :${TAG_NAME} immutable + :stable
                        // moving. Re-pushing same digest is a
                        // registry no-op (D10).
                        sh '''
                            printf '%s' "$REGISTRY_PASS" | docker login \
                                -u "$REGISTRY_USER" \
                                --password-stdin "$REGISTRY"
                            trap 'docker logout "$REGISTRY" || true' EXIT

                            for repo in "$BACKEND_REPO" "$FRONTEND_REPO"; do
                                docker tag "$repo:$TAG_NAME" \
                                           "$repo:stable"
                                docker push "$repo:$TAG_NAME"
                                docker push "$repo:stable"
                            done
                        '''
                    }
                }

                stage('Notify') {
                    steps {
                        zulipSend(
                            topic: 'releases',
                            message:
                                "Released ${env.TAG_NAME} — wheels: " +
                                "https://${env.WHEELS_HOST}/gain/, " +
                                "conda: https://anaconda.org/" +
                                    "${env.ANACONDA_USER}, " +
                                "docker: ${env.BACKEND_REPO}:" +
                                    "${env.TAG_NAME} + " +
                                "${env.FRONTEND_REPO}:" +
                                    "${env.TAG_NAME} " +
                                "(also tagged :stable)",
                        )
                    }
                }
            }
        }

        stage('Trigger web_e2e') {
            // Downstream gate for the gain-web-e2e job (DSL at
            // web_e2e/jenkins-jobs/e2e.groovy). Runs on every
            // branch — the e2e job clones the same branch /
            // commit and copies the wheel artefacts the parent
            // archived (`dist/core/*.whl` + `dist/web_api/*.whl`).
            // `wait: false, propagate: false` matches the VEP
            // integration shape: the parent build moves on while
            // e2e runs separately, and an e2e regression doesn't
            // FAILURE the parent.
            //
            // Phase 10: tag builds skip e2e — master CI on the
            // same commit already triggered it.
            when { not { buildingTag() } }
            steps {
                build(
                    job: '/gain-web-e2e',
                    parameters: [
                        string(
                            name: 'BRANCH_NAME',
                            value: env.BRANCH_NAME,
                        ),
                        string(
                            name: 'COMMIT_SHA',
                            value: env.GIT_COMMIT ?: '',
                        ),
                        string(
                            name: 'UPSTREAM_PROJECT',
                            value: env.JOB_NAME,
                        ),
                        string(
                            name: 'UPSTREAM_BUILD',
                            value: env.BUILD_NUMBER,
                        ),
                    ],
                    wait: false,
                    propagate: false,
                )
            }
        }

        stage('Trigger VEP integration') {
            // Downstream gate for the gain-vep-integration job (DSL
            // at vep_annotator/jenkins-jobs/integration.groovy). Runs
            // only on master and only when something under
            // vep_annotator/ actually changed - the integration job
            // pulls ensembl-vep and primes a multi-GB cache, so we
            // don't want every master commit to trigger it.
            when {
                allOf {
                    branch 'master'
                    changeset 'vep_annotator/**'
                    not { buildingTag() }
                }
            }
            steps {
                build(
                    job: '/gain-vep-integration',
                    wait: false,
                    propagate: false,
                )
            }
        }
    }

    post {
        always {
            script {
                try {
                    archiveArtifacts(
                        artifacts: 'reports/**/*.xml',
                        allowEmptyArchive: true,
                        fingerprint: false,
                    )
                    archiveArtifacts(
                        artifacts: 'dist/**/*.whl, dist/**/*.tar.gz',
                        allowEmptyArchive: true,
                        fingerprint: true,
                    )
                    archiveArtifacts(
                        artifacts: 'dist/conda/*.conda',
                        allowEmptyArchive: true,
                        fingerprint: true,
                    )
                    // Phase 10: base-image digests captured by
                    // the Build & push prod images stage. The
                    // release pipeline copyArtifacts this from
                    // the master build matching a tagged commit
                    // and replays it via Docker --build-arg.
                    // allowEmptyArchive:true so branch builds
                    // (which may run that stage but skip the
                    // capture if it ever moves) don't fail here.
                    archiveArtifacts(
                        artifacts: 'dist/base-images.lock',
                        allowEmptyArchive: true,
                        fingerprint: true,
                    )
                } finally {
                    zulipNotification(topic: "${env.JOB_NAME}")
                }
            }
        }
        cleanup {
            sh '''
                for img in gain-core-ci gain-demo-annotator-ci gain-vep-annotator-ci gain-spliceai-annotator-ci gain-web-api-ci gain-web-ui-ci gain-conda-builder-ci; do
                    docker rmi "$img:${BUILD_NUMBER}" 2>/dev/null || true
                done
                # Phase 9: registry-prefixed prod images. `:latest`
                # only exists on master but the rmi is harmless on
                # branches. Phase 10: also strip the tag-build
                # release tags (:${TAG_NAME} and :stable) — only
                # exist on tag builds but harmless elsewhere.
                GIT_SHORT="${GIT_COMMIT:0:8}"
                for repo in registry.seqpipe.org/gain-web-api \
                            registry.seqpipe.org/gain-web-ui; do
                    for tag in "$BUILD_NUMBER" "$GIT_SHORT" \
                               latest "$TAG_NAME" stable; do
                        docker rmi "$repo:$tag" 2>/dev/null || true
                    done
                done
            '''
        }
    }
}
