// Jenkins Job DSL definition for the gain-spliceai-integration pipeline.
// Consumed by the gain-seed job on the Jenkins controller (it globs
// **/jenkins-jobs/*.groovy from master); the script path below loads this
// repo's `spliceai_annotator/Jenkinsfile.integration` and runs it against the
// branch / commit passed as build parameters.
//
// The job is kicked off downstream from `iossifovlab/gain/master`'s
// `Trigger spliceai integration` stage when spliceai_annotator/** changes,
// and runs the slow `-m integration` harness that the fast per-PR
// spliceai_annotator step skips: the #320 frozen-fixture differential tier
// plus the #321 real tier against the node-local GRR mounted at /grr. Safe to
// trigger manually from the Jenkins UI on any branch too (defaults: master
// HEAD).
//
// No cron here: the nightly re-run -- which is what surfaces drift in the real
// hg38/GENCODE resources (the whole point of the #321 tier) -- is the
// `Run gain-spliceai-integration` stage in Jenkinsfile.nightly (wait/propagate
// true). Keeping it there rather than a second DSL cron avoids double-running
// this expensive TensorFlow + real-GRR job on the single pinned agent.

// Declared at the Jenkins root (not under `iossifovlab/`): that path is a
// GitHub Organization Folder and rejects Job-DSL-managed children. Sibling of
// the `gain-seed` seed job and of `gain-vep-integration` / `gain-web-e2e`.
pipelineJob('gain-spliceai-integration') {
    description(
        'Slow differential-harness integration tests for ' +
        'gain-spliceai-annotator (`-m integration`): pins all 16 attributes ' +
        'of today\'s TensorFlow output against a frozen hg38/GENCODE fixture ' +
        'corpus (gain#320). Triggered downstream of ' +
        'iossifovlab/gain/<branch>; safe to run manually.')

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
                    // Single-quoted Groovy string so `${BRANCH_NAME}` is stored
                    // literally in the SCM config; Jenkins's git plugin expands
                    // it at checkout time from the BRANCH_NAME build parameter,
                    // so a branch trigger loads Jenkinsfile.integration from the
                    // same branch it tests (not master).
                    branch('${BRANCH_NAME}')
                }
            }
            scriptPath('spliceai_annotator/Jenkinsfile.integration')
            lightweight()
        }
    }
}
