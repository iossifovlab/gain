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
    String name       = args.name                              // dir name, e.g. "demo_annotator"
    String pkg        = args.pkg                               // importable Python package, e.g. "demo_annotator"
    String tests      = args.tests                             // pytest target relative to project dir
    String mypyTarget = args.mypyTarget ?: pkg
    String mypyExtra  = args.mypyExtra ?: ''                   // e.g. "--config-file /workspace/mypy.ini"
    String pytestArgs = args.pytestArgs ?: ''                  // e.g. "-n auto"
    String distName   = name.replace('_', '-')
    String imageTag   = "gain-${distName}-ci:${env.BUILD_NUMBER}"

    sh label: "Build ${name} image", script: """
        docker build -f ${name}/Dockerfile -t ${imageTag} .
    """

    sh label: "Run ${name} CI", script: """
        mkdir -p reports/${name}
        docker run --rm \\
            -v \$PWD/reports/${name}:/reports \\
            ${imageTag} \\
            sh -c '
                set +e
                ruff check --output-format=junit --output-file=/reports/ruff.xml .
                mypy ${mypyExtra} ${mypyTarget} --junit-xml=/reports/mypy.xml
                pylint --load-plugins=pylint_junit \\
                       --output-format=pylint_junit.JUnitReporter \\
                       --exit-zero ${pkg} > /reports/pylint.xml
                pytest ${pytestArgs} \\
                    --junitxml=/reports/pytest.xml \\
                    --cov=${pkg} --cov-report=xml:/reports/coverage.xml \\
                    ${tests}
                chmod -R a+rw /reports
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
                sh 'rm -rf reports && mkdir -p reports'
            }
        }

        stage('Sub-projects') {
            parallel {
                stage('core') {
                    steps {
                        script {
                            runProject(
                                name: 'core',
                                pkg: 'gain',
                                tests: 'tests',
                                mypyTarget: 'gain',
                                mypyExtra: '--config-file /workspace/mypy.ini',
                                pytestArgs: '-n 5',
                            )
                        }
                    }
                    post { always { script { publishReports('core') } } }
                }

                stage('demo_annotator') {
                    steps {
                        script {
                            runProject(
                                name: 'demo_annotator',
                                pkg: 'demo_annotator',
                                tests: 'demo_annotator/tests',
                                pytestArgs: '-n 5',
                            )
                        }
                    }
                    post { always { script { publishReports('demo_annotator') } } }
                }

                stage('vep_annotator') {
                    steps {
                        script {
                            runProject(
                                name: 'vep_annotator',
                                pkg: 'vep_annotator',
                                tests: 'vep_annotator/tests',
                                pytestArgs: '-n 5',
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
    }

    post {
        always {
            archiveArtifacts artifacts: 'reports/**/*.xml', allowEmptyArchive: true, fingerprint: false
        }
        cleanup {
            sh '''
                for img in gain-core-ci gain-demo-annotator-ci gain-vep-annotator-ci gain-spliceai-annotator-ci; do
                    docker rmi "$img:${BUILD_NUMBER}" 2>/dev/null || true
                done
            '''
        }
    }
}
