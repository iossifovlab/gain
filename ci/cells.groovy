// The CI test cells shared by the gain pipelines (#1817).
//
// Pulled in with `load` after checkout:
//
//     def cells = load 'ci/cells.groovy'
//
// Consumers:
//   - Jenkinsfile (per-branch): runProject() takes each cell's fields.
//   - Jenkinsfile.python-matrix: runCellPytest() takes each cell's fields.
//   - core/Jenkinsfile.conda-integration: withFixtures() and
//     fixtureEndpointEnv() only. Its test step runs against the
//     installed conda artefact, so it is a different cell.
//
// Each pipeline keeps its own runner. This module owns only the values
// that must not drift between the copies: the pytest target and args,
// the `docker run` extras, and the compose fixture bring-up and teardown.
// #648 was the core cell's `--ignore=tests/integration` landing in one
// copy only; #1706 and #1708 each had to edit three fixture blocks.
//
// Values that vary by run (the compose project name and the network
// name compose derives from it) stay with the caller.
//
// The free-threading probe in Jenkinsfile.python-matrix deliberately
// does NOT use these cells: it runs core's tests/small with no fixtures.

// The endpoints the `apache` and `s3` services from docker-compose.yaml
// answer on, by service name, inside the compose network. The http and
// s3 scheme parametrizations of core's suite read them.
String fixtureEndpointEnv() {
    return '-e HTTP_HOST=apache:80 -e S3_HOST=s3:9000'
}

// One map per cell:
//   pkg            importable Python package
//   tests          pytest target, relative to the project dir
//   pytestArgs     extra pytest arguments
//   dockerRunExtra extra `docker run` flags, minus `--network`
//   fixtures       true when the cell needs withFixtures(); the caller
//                  then adds `--network <compose project>_default`
Map cell(String name) {
    switch (name) {
        case 'core':
            return [
                pkg: 'gain',
                tests: 'tests',
                // --ignore=tests/integration: that tier belongs to
                // gain-core-integration (#222), which gives it a warm
                // GRR cache dir, CURL_CA_BUNDLE for pysam against
                // grr-seqpipe, a single-threaded run so workers do not
                // race to populate that cache, and the scanpy-drift
                // group its scanpy test imports.
                pytestArgs: '-n 5 --enable-http-testing ' +
                            '--enable-s3-testing ' +
                            '--ignore=tests/integration',
                dockerRunExtra:
                    fixtureEndpointEnv() + ' ' +
                    '-v $PWD/core/tests/.test_grr:/workspace/core/tests/.test_grr',
                fixtures: true,
            ]
        case 'demo_annotator':
        case 'vep_annotator':
            // These tests spawn helper containers through the Python
            // docker SDK. The host socket lets the SDK reach the daemon.
            return [
                pkg: name,
                tests: "${name}/tests".toString(),
                pytestArgs: '-n 5',
                dockerRunExtra: '-v /var/run/docker.sock:/var/run/docker.sock',
                fixtures: false,
            ]
        case 'web_api':
            // No compose services: a574c3e83 removed every mail
            // assertion from the suite, test_settings sets no email
            // backend, and pytest-django uses its in-memory one.
            return [
                pkg: 'web_annotation',
                tests: 'web_annotation/tests',
                pytestArgs: '-n 5',
                dockerRunExtra: '-e DJANGO_SETTINGS_MODULE=' +
                                'web_annotation.test_settings',
                fixtures: false,
            ]
        default:
            error("ci/cells.groovy: unknown cell '${name}'")
    }
}

// Runs body() with the http/s3 fixture stack up, under compose project
// `composeProject`. The test container reaches the services on the
// network `<composeProject>_default`.
//
//   - `-f docker-compose.yaml` skips docker-compose.override.yaml (the
//     local-dev port-publish file), so concurrent builds on one agent
//     do not collide on host ports 28080, 9000 and 9001.
//   - core/tests/.test_grr is created here, as the agent user, before
//     apache bind-mounts it. dockerd creates a MISSING bind source as
//     root:root, and that wedges every later build in the workspace
//     (gain#921).
//   - s3-setup is a one-shot bucket setup job. It runs inline, not via
//     `up --wait`, which races with short-lived services.
//   - The teardown runs on every exit path. `|| true` keeps a stack that
//     never came up from turning a test verdict into a teardown one.
def withFixtures(String composeProject, Closure body) {
    try {
        sh label: 'Start http/s3 fixtures', script: """
            mkdir -p core/tests/.test_grr
            docker compose -f docker-compose.yaml \\
                -p "${composeProject}" \\
                up -d --wait apache s3
            docker compose -f docker-compose.yaml \\
                -p "${composeProject}" \\
                run --rm s3-setup
        """
        return body()
    } finally {
        sh label: 'Stop http/s3 fixtures', script: """
            docker compose -f docker-compose.yaml \\
                -p "${composeProject}" \\
                down -v --remove-orphans || true
        """
    }
}

return this
