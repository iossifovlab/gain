// Jenkins pipeline for the GAIn monorepo.
//
// Runs per-sub-project CI in parallel. Each project:
//   1. Builds its Dockerfile image from the repo root build context.
//   2. Inside the container, runs ruff, mypy, pylint, and pytest with JUnit
//      output, plus Cobertura coverage for pytest.
//   3. Publishes JUnit + coverage reports to Jenkins.
//
// Lint / type-check tools (ruff, mypy, pylint) only report via their JUnit
// XML and don't gate the build. Pytest, however, propagates its exit code
// so test failures fail the build (the post.always hook still publishes
// the JUnit + coverage reports either way). The web_ui stage follows the
// same pattern with jest as the gating tool.

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
                pytest_exit=\$?
                # Rewrite container-absolute <source>/workspace/...</source> to a
                # path relative to the Jenkins workspace so recordCoverage can
                # resolve source files.
                sed -i "s#<source>/workspace/\\([^<]*\\)</source>#<source>\\1</source>#g" \\
                    /reports/coverage.xml 2>/dev/null || true
                # Build wheel + sdist for this project. hatch-vcs reads the
                # mounted .git to produce a proper PEP 440 version.
                uv build --package ${distPkg} --out-dir /dist
                chmod -R a+rw /reports /dist
                # Propagate pytest's exit code so test failures fail the
                # build (FAILURE) instead of just being logged via JUnit
                # (UNSTABLE). The post.always publishReports hook still
                # uploads the XML reports either way. Lint / type-check
                # failures from the steps above don't gate here — they
                # surface via their JUnit XMLs only.
                exit \$pytest_exit
            '
    """
}

def publishReports(String name) {
    // Test failures must FAIL the build; lint/type findings only mark it
    // UNSTABLE.
    //
    // `exit $pytest_exit` in runProject was meant to FAIL on test failures, but
    // in practice the shell exit never failed the Jenkins step, so builds only
    // ever went UNSTABLE (via junit marking the test failures) and kept going
    // — e.g. still pushing images. So gate explicitly here: publish the test
    // report with skipMarkingBuildUnstable (junit doesn't touch the result),
    // capture its failure count, and error() -> FAILURE if anything failed.
    // Publish the lint/type reports separately with the default marking so
    // ruff/mypy/pylint findings still surface as UNSTABLE (non-gating).
    def testResults = junit(
        allowEmptyResults: true,
        skipMarkingBuildUnstable: true,
        testResults: "reports/${name}/pytest.xml,reports/${name}/jest.xml",
    )
    junit allowEmptyResults: true,
          testResults: "reports/${name}/ruff.xml," +
                       "reports/${name}/mypy.xml," +
                       "reports/${name}/pylint.xml"
    // Coverage is NOT recorded per-project here. The multibranch
    // "Coverage" column (Coverage plugin's CoverageMetricColumn) shows
    // the *first-registered* CoverageBuildAction and has no way to
    // select among several — so six per-project actions just meant the
    // column showed whichever stage finished first (web_ui). Instead a
    // single aggregating recordCoverage runs once in post.always (the
    // sole coverage action) so the column shows a true combined number;
    // the per-project coverage.xml files it aggregates are still
    // archived under reports/<name>/ for drill-down.
    if (testResults != null && testResults.failCount > 0) {
        error("${name}: ${testResults.failCount} test(s) failed")
    }
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
        stage('Dispatch release') {
            // Phase 10 (extracted): a CalVer tag pushed to the
            // repo lands here as a multibranch tag-build. Fire
            // the gain-release pipelineJob (DSL at
            // jenkins-jobs/release.groovy) and exit. The
            // multibranch tag-build is intentionally a thin shim
            // so this Jenkinsfile stays focused on per-branch CI;
            // gain-release owns the actual release flow.
            //
            // wait:false,propagate:false matches the Trigger
            // web_e2e / Trigger VEP integration patterns: the
            // dispatcher's tag-build always exits SUCCESS once
            // gain-release is queued; the release outcome lives
            // in gain-release's own build history + Zulip
            // notifications.
            //
            // The CalVer regex here mirrors the one in
            // Jenkinsfile.release for defense in depth (the
            // release pipeline re-validates TAG_NAME on entry).
            when {
                buildingTag()
                expression {
                    env.TAG_NAME ==~ /^\d{4}\.\d+\.\d+$/
                }
            }
            steps {
                build(
                    job: '/gain-release',
                    parameters: [
                        string(
                            name: 'TAG_NAME',
                            value: env.TAG_NAME,
                        ),
                    ],
                    wait: false,
                    propagate: false,
                )
            }
        }

        stage('CI') {
            // All per-branch CI work runs under this wrapper so a
            // single `not { buildingTag() }` guard replaces the
            // five guards we used to scatter across child stages.
            // On a tag build, only `Dispatch release` above runs.
            when { not { buildingTag() } }
            stages {
                stage('Start') {
                    steps {
                        // Grant any job permission to copy artefacts
                        // from this per-branch job. Without this,
                        // downstream `copyArtifacts(projectName:
                        // 'iossifovlab/gain/<branch>')` calls fail with
                        // "Unable to find project for artifact copy"
                        // (Jenkins's permission-denied disguise) — even
                        // when the consumer is named explicitly, if the
                        // consumer build's effective user lacks
                        // Item.Read on the iossifovlab folder the
                        // permission check fails before the name list
                        // is consulted. '*' sidesteps that by skipping
                        // the consumer-name match entirely. Idempotent
                        // — applied on every build. BranchJobProperty
                        // (set by the multibranch scan) is preserved.
                        script {
                            properties([
                                copyArtifactPermission('*'),
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

                stage('Detect change scope') {
                    // Sets env.DOCS_ONLY='true' iff every file in this
                    // build's changeset lives under docs/. Heavy stages
                    // (Conda builder image, Sub-projects, Conda packages,
                    // Build & push prod images, downstream triggers) gate
                    // on it and skip when only docs changed. Build docs /
                    // Deploy docs keep their existing `changeset 'docs/**'`
                    // clauses.
                    //
                    // Empty changeset (first build, manual rebuild, no
                    // commits since last build) → DOCS_ONLY='false' →
                    // full CI. Conservative default — only short-circuit
                    // when we positively know it's docs-only.
                    steps {
                        script {
                            def changedFiles = []
                            for (changeSet in currentBuild.changeSets) {
                                for (item in changeSet.items) {
                                    changedFiles.addAll(item.affectedPaths)
                                }
                            }
                            boolean isDocsOnly =
                                !changedFiles.isEmpty() &&
                                changedFiles.every { it.startsWith('docs/') }
                            env.DOCS_ONLY = isDocsOnly ? 'true' : 'false'
                            echo "Changeset: ${changedFiles.size()} file(s); " +
                                 "DOCS_ONLY=${env.DOCS_ONLY}"
                        }
                    }
                }

                stage('Conda builder image') {
                    when { not { environment name: 'DOCS_ONLY', value: 'true' } }
                    steps {
                        sh '''
                            docker build -f conda-builder/Dockerfile \
                                -t gain-conda-builder-ci:${BUILD_NUMBER} conda-builder
                        '''
                    }
                }
        
                stage('Sub-projects') {
                    when { not { environment name: 'DOCS_ONLY', value: 'true' } }
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
                                        // -f docker-compose.yaml skips
                                        // docker-compose.override.yaml
                                        // (the local-dev port-publish file);
                                        // without this, parallel CI builds
                                        // on the same agent collide on host
                                        // ports 28080 / 9000 / 9001.
                                        sh '''
                                            mkdir -p core/tests/.test_grr
                                            docker compose -f docker-compose.yaml \
                                                -p "$COMPOSE_PROJECT" \
                                                up -d --wait apache minio
                                            docker compose -f docker-compose.yaml \
                                                -p "$COMPOSE_PROJECT" \
                                                run --rm minio-client
                                        '''
        
                                        runProject(
                                            name: 'core',
                                            pkg: 'gain',
                                            tests: 'tests',
                                            mypyTarget: 'gain',
                                            mypyExtra: '--config-file /workspace/mypy.ini',
                                            pytestArgs: '-n 5 --enable-http-testing --enable-s3-testing --ignore=tests/integration',
                                            dockerRunExtra:
                                                '--network "$COMPOSE_NETWORK" ' +
                                                '-e HTTP_HOST=apache:80 ' +
                                                '-e MINIO_HOST=minio:9000 ' +
                                                '-v $PWD/core/tests/.test_grr:/workspace/core/tests/.test_grr',
                                        )
                                    } finally {
                                        sh '''
                                            docker compose -f docker-compose.yaml \
                                                -p "$COMPOSE_PROJECT" \
                                                down -v --remove-orphans || true
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
                                        //
                                        // Defensive teardown before up: COMPOSE_PROJECT
                                        // includes only BUILD_NUMBER, so build #N of
                                        // any branch shares the namespace with every
                                        // other branch's build #N. If a prior #N run
                                        // (other branch, manual test, abandoned build)
                                        // left a mail container behind without its
                                        // network, compose `up -d --wait` will reuse
                                        // the container (reported as "Running" with
                                        // no "Created"/"Starting") and skip network
                                        // creation — and `runProject`'s
                                        // `docker run --network <project>_default`
                                        // then fails with "network not found".
                                        // `|| true` because absent state is the
                                        // happy path.
                                        sh '''
                                            docker compose -f docker-compose.yaml \
                                                -p "$COMPOSE_PROJECT" \
                                                down -v --remove-orphans || true
                                            docker compose -f docker-compose.yaml \
                                                -p "$COMPOSE_PROJECT" \
                                                up -d --wait mail
                                        '''
        
                                        runProject(
                                            name: 'web_api',
                                            pkg: 'web_annotation',
                                            tests: 'web_annotation/tests',
                                            mypyTarget: 'web_annotation',
                                            mypyExtra: '--config-file /workspace/web_api/mypy.ini',
                                            pytestArgs: '-n 5',
                                            distPkg: 'gain-web-api',
                                            dockerRunExtra:
                                                '--network "$COMPOSE_NETWORK" ' +
                                                '-e GPFWA_EMAIL_HOST=mail ' +
                                                '-e DJANGO_SETTINGS_MODULE=' +
                                                'web_annotation.test_settings',
                                        )
                                    } finally {
                                        sh '''
                                            docker compose -f docker-compose.yaml \
                                                -p "$COMPOSE_PROJECT" \
                                                down -v --remove-orphans || true
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
                                                jest_exit=\$?
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
                                                # Propagate jest's exit code so test
                                                # failures fail the build (mirrors the
                                                # python projects' pytest gating).
                                                # eslint / stylelint failures don't
                                                # gate; they surface through their
                                                # report XMLs only.
                                                exit \$jest_exit
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

                stage('Core wheel (docs-only)') {
                    when { environment name: 'DOCS_ONLY', value: 'true' }
                    // Invariant: every green iossifovlab/gain/master
                    // build must carry dist/core/*.whl. gpf's root
                    // Jenkinsfile "Fetch gain wheel" copyArtifacts the
                    // wheel from gain/master's lastSuccessful build; a
                    // docs-only commit skips Sub-projects (where
                    // runProject builds the wheel), so without this the
                    // build is a wheel-less green and breaks EVERY gpf
                    // build on every branch until a non-docs gain build
                    // supersedes it. Build just the gain-core wheel
                    // (pure-Python, seconds; no lint/pytest) into
                    // dist/core/ so it matches this exact commit and
                    // the post archiveArtifacts picks it up. Mirrors
                    // runProject()'s wheel recipe; the gain-core-ci
                    // image build is a near-instant cache hit shared
                    // with the Build docs stage below.
                    steps {
                        sh '''
                            docker build -f core/Dockerfile \
                                -t gain-core-ci:${BUILD_NUMBER} .
                            mkdir -p dist/core
                            docker run --rm \
                                -v $PWD/dist/core:/dist \
                                -v $PWD/.git:/workspace/.git:ro \
                                gain-core-ci:${BUILD_NUMBER} \
                                sh -c 'uv build --package gain-core --out-dir /dist && chmod -R a+rw /dist'
                        '''
                    }
                }

                stage('Core conda (docs-only)') {
                    when { environment name: 'DOCS_ONLY', value: 'true' }
                    // Invariant companion to 'Core wheel (docs-only)':
                    // every green iossifovlab/gain/master build must also
                    // carry dist/conda/gain-core-*.conda. gpf-docs-e2e
                    // (iossifovlab/gpf#916) copyArtifacts the gain-core
                    // conda from gain/master's lastSuccessful build; a
                    // docs-only commit skips 'Conda builder image' +
                    // 'Conda packages', so without this the build is a
                    // conda-less green and breaks gpf-docs-e2e until a
                    // non-docs gain build supersedes it (iossifovlab/gain#116).
                    // Build just gain-core (mirrors the docs-only wheel's
                    // scope); the wheel built above supplies VCS_VERSION.
                    // The conda-builder image build is a near-instant cache
                    // hit shared with the non-docs 'Conda builder image'
                    // stage; re-issued here so the stage is self-contained.
                    steps {
                        sh '''
                            docker build -f conda-builder/Dockerfile \
                                -t gain-conda-builder-ci:${BUILD_NUMBER} conda-builder
                            # Derive the hatch-vcs PEP 440 version from the
                            # gain-core wheel built by 'Core wheel (docs-only)'
                            # so the conda version matches it (same recipe as
                            # the 'Conda packages' stage).
                            VCS_VERSION=$(ls dist/core/*.whl | head -1 \
                                | sed 's#.*gain_core-##' \
                                | sed 's#-py3-none-any.whl$##')
                            echo "VCS_VERSION=$VCS_VERSION"
                            mkdir -p dist/conda conda/core
                            # Run as the Jenkins user (not the image's default
                            # mambauser) so rattler-build's 0600 output is
                            # host-readable; HOME=/tmp because /home/mambauser
                            # is not writable by an arbitrary UID. Mirrors the
                            # 'Conda packages' stage.
                            DOCKER_USER="$(id -u):$(id -g)"
                            docker run --rm \
                                --user "$DOCKER_USER" \
                                -e HOME=/tmp \
                                -v $PWD:/workspace \
                                -w /workspace \
                                -e VCS_VERSION="$VCS_VERSION" \
                                gain-conda-builder-ci:${BUILD_NUMBER} \
                                rattler-build build \
                                    --recipe core/conda-recipe/recipe.yaml \
                                    --output-dir conda/core
                            cp conda/core/noarch/*.conda dist/conda/
                        '''
                    }
                }

                stage('Build docs') {
                    when { changeset 'docs/**' }
                    // Migrated from iossifovlab/gpf_documentation
                    // (iossifovlab/gain#6). The Sphinx source tree now
                    // lives in docs/. Build runs inside the core CI image
                    // (which already has gain-core under /workspace/.venv);
                    // the docs dependency group from the root pyproject.toml
                    // is layered on top at run-time.
                    //
                    // Only runs when docs/** changed in this build's
                    // commit range; saves time on code-only changes. A
                    // docstring tweak in core/gain won't refresh the
                    // rendered autodoc page until a docs-side commit
                    // lands — accepted trade-off (same as gpf docs).
                    //
                    // When DOCS_ONLY=true the Sub-projects > core stage
                    // is skipped, so we build the core CI image inline
                    // here. Idempotent — when Sub-projects did run, the
                    // image already exists and the docker build is a
                    // near-instant cache hit; we still re-issue it so the
                    // stage is self-contained and runnable in either mode.
                    steps {
                        sh '''
                            docker build -f core/Dockerfile \
                                -t gain-core-ci:${BUILD_NUMBER} .
                            mkdir -p dist/docs
                            docker run --rm \
                                -v $PWD:/workspace \
                                -v $PWD/.git:/workspace/.git:ro \
                                -w /workspace \
                                gain-core-ci:${BUILD_NUMBER} \
                                sh -c '
                                    set -eu
                                    # The `docs` group is defined on the
                                    # root virtual workspace, so install
                                    # without --package: this installs
                                    # gain-core + gain-web-api (the root
                                    # deps) plus the sphinx toolchain.
                                    uv sync --group docs
                                    bash docs/build_docs.sh
                                '
                            cp docs/gaindocs-html.tar.gz dist/docs/
                        '''
                    }
                }

                stage('Deploy docs') {
                    when {
                        allOf {
                            branch 'master'
                            changeset 'docs/**'
                        }
                    }
                    // Master-only ansible push to iossifovlab.com, only
                    // when docs/** changed. Skipped on every branch
                    // build and on master builds that don't touch the
                    // docs tree, so the live site keeps serving the
                    // last good build's content untouched.
                    //
                    // Reuses the `gpf-docs-deploy` Jenkins SSH credential
                    // (same SSH login + target host as gpf docs; one key
                    // to rotate, not two).
                    steps {
                        withCredentials([sshUserPrivateKey(
                            credentialsId: 'gpf-docs-deploy',
                            keyFileVariable: 'SSH_KEY',
                            usernameVariable: 'SSH_USER',
                        )]) {
                            sh '''
                                docker run --rm \
                                    -v $PWD:/workspace \
                                    -v $SSH_KEY:/deploy.key:ro \
                                    -e SSH_USER \
                                    -w /workspace \
                                    gain-core-ci:${BUILD_NUMBER} \
                                    sh -c '
                                        set -eu
                                        apt-get update
                                        apt-get install -y --no-install-recommends \
                                            ansible openssh-client
                                        mkdir -p /root/.ssh
                                        chmod 700 /root/.ssh
                                        ssh-keyscan -H iossifovlab.com \
                                            > /root/.ssh/known_hosts 2>/dev/null
                                        chmod 600 /root/.ssh/known_hosts
                                        ANSIBLE_PRIVATE_KEY_FILE=/deploy.key \
                                            ANSIBLE_REMOTE_USER="$SSH_USER" \
                                            bash docs/deploy/docs_deploy.sh
                                    '
                            '''
                        }
                    }
                }

                stage('Conda packages') {
                    when { not { environment name: 'DOCS_ONLY', value: 'true' } }
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
                    when { not { environment name: 'DOCS_ONLY', value: 'true' } }
                    // Phase 9 slice 1: build the wheel-based backend +
                    // Apache-based frontend prod images here in the
                    // root build, tag them for registry.seqpipe.org, and
                    // push on master. Branch builds build-but-don't-push
                    // (validates the Dockerfiles + that the wheels
                    // install). Tags pushed on master:
                    //   :${BUILD_NUMBER}  — Jenkins build identity
                    //   :${GIT_SHORT}     — immutable git-anchored handle
                    //   :latest           — moving pointer for prod
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
                            # Pull base images up front so `docker image
                            # inspect` further down can read a populated
                            # RepoDigests. BuildKit-driven `docker build`
                            # pulls images into BuildKit's content store
                            # but doesn't always register them at the
                            # daemon level with a <repo>:<tag> +
                            # RepoDigests entry, which previously left
                            # NODE_IMAGE/HTTPD_IMAGE empty in
                            # dist/base-images.lock and broke the Phase
                            # 10 release pipeline's digest-pinned
                            # rebuild.
                            docker pull python:3.12-slim
                            docker pull node:22.14.0-alpine
                            docker pull httpd:2.4-alpine

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

                            # Fail loud if RepoDigests came back empty.
                            # The release pipeline silently consumes
                            # whatever this file contains and an empty
                            # value only surfaces several stages later
                            # as an opaque `docker build` failure.
                            if grep -E '^[A-Z_]+=$' dist/base-images.lock; then
                                echo "ERROR: empty digest(s) in" \
                                     "dist/base-images.lock — see" \
                                     "lines above. Refusing to" \
                                     "publish a poisoned lockfile." >&2
                                exit 1
                            fi
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
                                    # #10: docker login/logout mutate a
                                    # shared per-user ~/.docker/config.json.
                                    # On a shared agent a concurrent job's
                                    # `docker logout` EXIT trap (e.g. the
                                    # release-pipeline registry preflight)
                                    # wipes our auth between two pushes —
                                    # "no basic auth credentials" mid-push
                                    # (cf. gpf master #5742, iossifovlab/
                                    # gpf#856). Auth instance of the tb-w8d
                                    # race documented below (build #137). A
                                    # per-build DOCKER_CONFIG makes
                                    # login/logout build-local; scoped to
                                    # this sh (not the stage env) so the
                                    # base-image pulls above keep using the
                                    # default config.
                                    export DOCKER_CONFIG="$WORKSPACE/.docker-cfg-$BUILD_NUMBER"
                                    mkdir -p "$DOCKER_CONFIG"
                                    echo "REGISTRY_USER bytes: $(printf '%s' "$REGISTRY_USER" | wc -c)"
                                    echo "REGISTRY_PASS bytes: $(printf '%s' "$REGISTRY_PASS" | wc -c)"
                                    printf '%s' "$REGISTRY_PASS" | docker login \
                                        -u "$REGISTRY_USER" \
                                        --password-stdin "$REGISTRY"
                                    trap 'docker logout "$REGISTRY" || true; rm -rf "$DOCKER_CONFIG"' EXIT
                                    # tb-w8d: tag :latest INSIDE the loop,
                                    # immediately before pushing it. Build
                                    # #137 failed with "tag does not exist:
                                    # …gain-web-api:latest" ~30s after a
                                    # bulk docker tag at the top of this
                                    # block, while concurrent activity on
                                    # the shared daemon (visible as 'Port
                                    # 8787 is already in use' in the log)
                                    # had untagged :latest in between.
                                    # Re-tagging right before push closes
                                    # the race window.
                                    for repo in "$BACKEND_REPO" "$FRONTEND_REPO"; do
                                        docker push "$repo:$BUILD_NUMBER"
                                        docker push "$repo:$GIT_SHORT"
                                        docker tag "$repo:$BUILD_NUMBER" "$repo:latest"
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
        
                stage('Trigger web_e2e') {
                    when { not { environment name: 'DOCS_ONLY', value: 'true' } }
                    // Downstream gate for the gain-web-e2e job (DSL at
                    // web_e2e/jenkins-jobs/e2e.groovy). Runs on every
                    // branch — the e2e job clones the same branch /
                    // commit and copies the wheel artefacts the parent
                    // archived (`dist/core/*.whl` + `dist/web_api/*.whl`).
                    // `wait: false, propagate: false` matches the VEP
                    // integration shape: the parent build moves on while
                    // e2e runs separately, and an e2e regression doesn't
                    // FAILURE the parent.
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
        
                stage('Trigger core integration') {
                    when { not { environment name: 'DOCS_ONLY', value: 'true' } }
                    // Downstream gate for the gain-core-integration job (DSL at
                    // core/jenkins-jobs/integration.groovy). Runs on every
                    // branch — the job checks out the same branch / commit and
                    // runs core/tests/integration against grr-seqpipe.
                    // `wait: false, propagate: false` matches the web_e2e / VEP
                    // integration shape: the parent build moves on while the
                    // integration suite runs separately, and an integration
                    // regression doesn't FAILURE the parent.
                    steps {
                        build(
                            job: '/gain-core-integration',
                            parameters: [
                                string(
                                    name: 'BRANCH_NAME',
                                    value: env.BRANCH_NAME,
                                ),
                                string(
                                    name: 'COMMIT_SHA',
                                    value: env.GIT_COMMIT ?: '',
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
                            not { environment name: 'DOCS_ONLY', value: 'true' }
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
        }
    }

    post {
        always {
            script {
                try {
                    // The one and only coverage action for the build.
                    // publishReports() deliberately records NO per-project
                    // coverage (see the note there): the multibranch "Coverage"
                    // column shows the first-registered CoverageBuildAction and
                    // can't be pointed at a specific one, so the combined report
                    // must be the sole action to own the column. This call globs
                    // all six top-level coverage.xml files — the Coverage plugin
                    // sums their counters into one report — under the plugin's
                    // default id `coverage`. Drill-down into each sub-project
                    // survives as packages inside this one report.
                    //
                    // Lives here (top-level post.always), not in a stage, so it
                    // publishes on red builds too — a failing project's
                    // publishReports error()s and skips later *stages*, but
                    // post.always still runs after all six parallel post blocks
                    // have written reports/*/coverage.xml. The glob matches only
                    // the six top-level files, not the nested
                    // web_ui/coverage/cobertura-coverage.xml, so nothing is
                    // double-counted. failOnError:false makes it a clean no-op
                    // on docs-only / tag builds where no coverage.xml exists.
                    recordCoverage(
                        tools: [[parser: 'COBERTURA', pattern: 'reports/*/coverage.xml']],
                        id: 'coverage',
                        name: 'Combined coverage',
                        skipPublishingChecks: true,
                        failOnError: false,
                    )
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
                # branches. Tag-build release tags (:${TAG_NAME},
                # :stable) are owned by gain-release and cleaned up
                # there, not here.
                GIT_SHORT="${GIT_COMMIT:0:8}"
                for repo in registry.seqpipe.org/gain-web-api \
                            registry.seqpipe.org/gain-web-ui; do
                    for tag in "$BUILD_NUMBER" "$GIT_SHORT" latest; do
                        docker rmi "$repo:$tag" 2>/dev/null || true
                    done
                done
            '''
        }
    }
}
