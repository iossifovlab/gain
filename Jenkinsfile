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
    agent any

    options {
        timeout(time: 1, unit: 'HOURS')
        buildDiscarder(logRotator(numToKeepStr: '20'))
    }

    stages {
        stage('Start') {
            steps {
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
                                    distPkg: 'django-gpf-web-annotation',
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

        stage('web_e2e') {
            // Browser-driven end-to-end suite: brings up the full
            // production stack (db + mail + backend-e2e + frontend-e2e)
            // and runs Playwright against it. Sequential rather than
            // parallel because it consumes the in-monorepo conda
            // packages produced by the `Conda packages` stage above —
            // those become gpf-image's local conda channel, replacing
            // the pre-Phase-6 dependency on
            // iossifovlab/gpf-conda-packaging/master.
            environment {
                COMPOSE_PROJECT =
                    "gain-ci-web-e2e-${env.BUILD_NUMBER}"
                COMPOSE_NETWORK =
                    "gain-ci-web-e2e-${env.BUILD_NUMBER}_default"
            }
            steps {
                script {
                    try {
                        sh '''
                            mkdir -p web_e2e/reports reports/web_e2e
                            # Re-create the channel from scratch so a
                            # stale .conda from a previous build can't
                            # leak into this build's gpf-image.
                            rm -rf web_infra/conda-channel
                            mkdir -p web_infra/conda-channel/noarch
                            # rattler-build publish refuses to write to
                            # an unindexed channel, so seed an empty
                            # noarch repodata.json before publishing —
                            # the publish step then overwrites it with
                            # the real index.
                            echo '{"packages": {}, "packages.conda": {}, "info": {"subdir": "noarch"}}' \
                                > web_infra/conda-channel/noarch/repodata.json
                            # Publish the gain-*.conda artefacts from
                            # `dist/conda/` into the local channel.
                            # `rattler-build publish --to file://...`
                            # both stages the packages and writes the
                            # repodata.json that Dockerfile.gpf's
                            # `mamba install -c file:///conda-channel`
                            # expects.
                            DOCKER_USER="$(id -u):$(id -g)"
                            docker run --rm \
                                --user "$DOCKER_USER" \
                                -e HOME=/tmp \
                                -v $PWD:/workspace \
                                -w /workspace \
                                gain-conda-builder-ci:${BUILD_NUMBER} \
                                rattler-build publish \
                                    --to file:///workspace/web_infra/conda-channel \
                                    dist/conda/*.conda
                            docker compose \
                                -p "$COMPOSE_PROJECT" \
                                -f web_infra/compose-jenkins.yaml \
                                build ubuntu-image gpf-image \
                                      backend-e2e frontend-e2e \
                                      e2e-tests
                            # `compose run --rm e2e-tests` honours the
                            # depends_on conditions on backend-e2e
                            # (service_healthy), frontend-e2e
                            # (service_healthy), and mail
                            # (service_started) declared in
                            # compose-jenkins.yaml, so it starts the
                            # whole stack and blocks on the
                            # healthchecks before invoking
                            # `npx playwright test`.  We deliberately
                            # do NOT call `compose up -d --wait`
                            # first: `--wait` watches every service
                            # in the dependency graph including the
                            # one-shot `static-data` busybox, which
                            # exits 0 and trips a non-zero `--wait`
                            # exit on docker compose v2.x agents.
                            docker compose \
                                -p "$COMPOSE_PROJECT" \
                                -f web_infra/compose-jenkins.yaml \
                                run --rm e2e-tests
                            cp web_e2e/reports/junit-report.xml \
                                reports/web_e2e/junit.xml
                        '''
                    } finally {
                        sh '''
                            docker compose \
                                -p "$COMPOSE_PROJECT" \
                                -f web_infra/compose-jenkins.yaml \
                                down -v --remove-orphans || true
                        '''
                    }
                }
            }
            post {
                always {
                    script {
                        publishReports('web_e2e')
                        archiveArtifacts(
                            artifacts: 'web_e2e/reports/**',
                            allowEmptyArchive: true,
                            fingerprint: false,
                        )
                    }
                }
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
            '''
        }
    }
}
