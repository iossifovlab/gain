// Jenkins Job DSL definition for the gain-core-integration pipeline.
// Consumed by the gain-seed job on the Jenkins controller; the script path
// below loads this repo's `core/Jenkinsfile.integration` and runs it against
// the branch / commit passed as build parameters.
//
// The job is kicked off downstream from `iossifovlab/gain/<branch>`'s
// `Trigger core integration` stage on every branch, and is safe to trigger
// manually from the Jenkins UI (defaults: master).
//
// Declared at the Jenkins root (not under `iossifovlab/`): that path is a
// GitHub Organization Folder and rejects Job-DSL-managed children. Sibling of
// the `gain-seed` seed job, `gain-web-e2e`, and `gain-vep-integration`.
pipelineJob('gain-core-integration') {
    description(
        'Integration test suite for gain-core (core/tests/integration). ' +
        'Builds the gain-core CI image and runs the effect-annotation ' +
        'integration tests, which resolve the hg19 genome + refGene gene ' +
        'models from the grr-seqpipe http GRR. Triggered downstream of ' +
        'iossifovlab/gain/<branch> on every branch; safe to run manually.')

    logRotator {
        numToKeep(20)
    }

    parameters {
        stringParam(
            'BRANCH_NAME',
            'master',
            'Branch the upstream gain build was triggered from. The pipeline ' +
            'checks out this branch unless COMMIT_SHA is set.',
        )
        stringParam(
            'COMMIT_SHA',
            '',
            'Specific commit SHA to test (takes precedence over BRANCH_NAME). ' +
            'Empty = use BRANCH_NAME HEAD.',
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
                    // BRANCH_NAME build parameter declared above. A branch
                    // trigger therefore loads Jenkinsfile.integration from
                    // the same branch it tests, not from master -- without
                    // which a change to that Jenkinsfile silently no-ops on
                    // the branch introducing it and only takes effect once
                    // merged (#598; #272 fixed the same defect for
                    // gain-web-e2e).
                    //
                    // Note the COMMIT_SHA interaction: the workspace
                    // Checkout stage prefers COMMIT_SHA over BRANCH_NAME,
                    // while cpsScm here resolves ${BRANCH_NAME} to that
                    // branch's HEAD. A build triggered for an older
                    // COMMIT_SHA can thus load a newer pipeline script than
                    // the tree under test; accepted in practice, flagged
                    // here for the next reader.
                    branch('${BRANCH_NAME}')
                }
            }
            scriptPath('core/Jenkinsfile.integration')
            lightweight()
        }
    }
}
