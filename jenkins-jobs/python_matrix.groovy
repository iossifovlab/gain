// Jenkins Job DSL definition for the gain-python-matrix job.
// Consumed by the seed job on the Jenkins controller; the
// script path below loads this repo's `Jenkinsfile.python-matrix`.
//
// The matrix re-runs each project's pytest suite under
// Python 3.12, 3.13, and 3.14 to catch forward-compatibility
// breakage on newer interpreters before migration time.
// Triggered nightly from the gain-nightly orchestrator (no
// parameters, so BRANCH_NAME defaults to master); can also be run
// on demand against a branch. Sends a Zulip alert on failure (topic:
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

    parameters {
        stringParam(
            'BRANCH_NAME',
            'master',
            'Branch to load Jenkinsfile.python-matrix from and test. ' +
            'Default master is what the gain-nightly trigger runs.',
        )
    }

    definition {
        cpsScm {
            scm {
                git {
                    remote {
                        url('https://github.com/iossifovlab/gain.git')
                    }
                    // Single-quoted Groovy string so `${BRANCH_NAME}` is
                    // stored literally in the SCM config XML; Jenkins's git
                    // plugin expands it at checkout time from the
                    // BRANCH_NAME build parameter declared above. A manual
                    // run against a branch therefore loads the pipeline
                    // script from that branch, so a change to
                    // Jenkinsfile.python-matrix (or anything it loads) can
                    // be exercised on the matrix before it merges (#1816;
                    // same pattern as gain-conda-integration, #598, #272).
                    //
                    // Loading the definition from a branch runs that
                    // branch's Groovy unsandboxed on the controller. That
                    // is the accepted trust model, recorded with the
                    // condition that would force a revisit in
                    // docs/adr/0009-jenkins-pipeline-definition-trust.md
                    // (#643).
                    branch('${BRANCH_NAME}')
                }
            }
            scriptPath('Jenkinsfile.python-matrix')
            lightweight()
        }
    }
}
