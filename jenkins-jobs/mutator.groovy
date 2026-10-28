// Jenkins Job DSL definition for the gain-mutator job.
// Consumed by the seed job on the Jenkins controller; the
// script path below loads this repo's `Jenkinsfile.mutator`.
//
// The job runs mutator (https://github.com/unclebob/mutator) on the
// gain.utils package, with tests/small/utils as the test command
// (#2062). It is report only: it archives the report and the mutator
// snapshot, and a survivor never fails it. Triggered nightly from the
// last stage of the gain-nightly orchestrator (no parameters, so
// BRANCH_NAME defaults to master), with `propagate: false`, so the job
// never changes the result of gain-nightly. It sends no Zulip alert.

// Declared at the Jenkins root (not under `iossifovlab/`): that
// path is a GitHub Organization Folder and rejects Job-DSL-managed
// children. Sibling of `gain-seed`, `gain-release`, `gain-nightly`
// and `gain-python-matrix`.
pipelineJob('gain-mutator') {
    description(
        'Nightly mutation report of the gain.utils package from ' +
        'mutator, with tests/small/utils as the test command. Report ' +
        'only: survivors never fail the build, and a failed baseline ' +
        'marks it UNSTABLE. Keeps the mutator snapshot as an artifact, ' +
        'so each run is differential. Triggered nightly via gain-nightly.')

    logRotator {
        numToKeep(40)
    }

    parameters {
        stringParam(
            'BRANCH_NAME',
            'master',
            'Branch to load Jenkinsfile.mutator from and test. ' +
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
                    // Jenkinsfile.mutator or scripts/mutator_nightly.sh can
                    // be exercised before it merges.
                    //
                    // Loading the definition from a branch runs that
                    // branch's Groovy unsandboxed on the controller. That
                    // is the accepted trust model, recorded with the
                    // condition that would force a revisit in
                    // docs/adr/0009-jenkins-pipeline-definition-trust.md.
                    branch('${BRANCH_NAME}')
                }
            }
            scriptPath('Jenkinsfile.mutator')
            lightweight()
        }
    }
}
