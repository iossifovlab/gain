// Jenkins Job DSL definition for the gain-python-matrix job.
// Consumed by the seed job on the Jenkins controller; the
// script path below loads this repo's `Jenkinsfile.python-matrix`.
//
// The matrix re-runs each project's pytest suite under
// Python 3.12, 3.13, and 3.14 to catch forward-compatibility
// breakage on newer interpreters before migration time.
// Triggered nightly from the gain-nightly orchestrator; can also
// be run on demand. Sends a Zulip alert on failure (topic:
// nightly) — same topic as the orchestrator so all nightly
// breakage lands in one thread.
//
// The job also carries the free-threaded (3.14t) readiness probe
// added in #151. That stage is informational: it colours its own
// stage UNSTABLE and publishes junit, but never changes the build
// result, so it can never trigger the Zulip alert above.

// Declared at the Jenkins root (not under `iossifovlab/`): that
// path is a GitHub Organization Folder and rejects Job-DSL-managed
// children. Sibling of `gain-seed`, `gain-release`, `gain-nightly`,
// `gain-web-e2e`, and `gain-vep-integration`.
pipelineJob('gain-python-matrix') {
    description(
        'Pytest matrix across Python 3.12/3.13/3.14 for ' +
        'gain-core, gain-demo-annotator, gain-vep-annotator, ' +
        'and gain-web-api. Forward-compatibility canary for the ' +
        'dependency closure (numpy/pandas/pysam/aiohttp/Django/' +
        'psycopg). Also runs an informational free-threaded ' +
        '(3.14t) GIL-readiness probe over gain-core and ' +
        'gain-web-api. Triggered nightly via gain-nightly. Sends ' +
        'a Zulip alert on failure (topic: nightly).')

    logRotator {
        numToKeep(40)
    }

    definition {
        cpsScm {
            scm {
                git {
                    remote {
                        url('https://github.com/iossifovlab/gain.git')
                    }
                    branch('master')
                }
            }
            scriptPath('Jenkinsfile.python-matrix')
            lightweight()
        }
    }
}
