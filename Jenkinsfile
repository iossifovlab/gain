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
    String distPkg        = "gain-${distName}"                 // PyPI-style name, e.g. "gain-demo-annotator"
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
                pylint --rcfile=/workspace/pylintrc \\
                       --load-plugins=pylint_junit \\
                       --output-format=pylint_junit.JUnitReporter \\
                       --exit-zero ${pkg} > /reports/pylint.xml
                pytest ${pytestArgs} \\
                    --junitxml=/reports/pytest.xml \\
                    --cov=${pkg} --cov-report=xml:/reports/coverage.xml \\
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
    recordCoverage(
        tools: [[parser: 'COBERTURA', pattern: "reports/${name}/coverage.xml"]],
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

                    for proj in core demo_annotator vep_annotator spliceai_annotator; do
                        mkdir -p conda/$proj
                        docker run --rm \
                            -v $PWD:/workspace \
                            -w /workspace \
                            -e VCS_VERSION="$VCS_VERSION" \
                            gain-conda-builder-ci:${BUILD_NUMBER} \
                            rattler-build build \
                                --recipe $proj/conda-recipe/recipe.yaml \
                                --output-dir conda/$proj
                    done
                '''
            }
        }
    }

    post {
        always {
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
                artifacts: 'conda/**/*.conda',
                allowEmptyArchive: true,
                fingerprint: true,
            )
        }
        cleanup {
            sh '''
                for img in gain-core-ci gain-demo-annotator-ci gain-vep-annotator-ci gain-spliceai-annotator-ci gain-conda-builder-ci; do
                    docker rmi "$img:${BUILD_NUMBER}" 2>/dev/null || true
                done
            '''
        }
    }
}
