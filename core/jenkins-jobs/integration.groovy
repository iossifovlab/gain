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
                    branch('master')
                }
            }
            scriptPath('core/Jenkinsfile.integration')
            lightweight()
        }
    }
}
