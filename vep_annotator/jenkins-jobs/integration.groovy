// Jenkins Job DSL definition for the gain-vep-annotator integration
// pipeline. Consumed by a seed job on the Jenkins controller; the
// script path below loads this repo's `vep_annotator/Jenkinsfile.
// integration` and runs it against master.
//
// The job is kicked off downstream from `iossifovlab/gain/master`'s
// `Trigger VEP integration` stage whenever `vep_annotator/**` changes,
// runs nightly via the cron below, and is safe to trigger manually.
//
// tb-7e7: cron MUST live in this DSL, not in Jenkinsfile.integration.
// gain-seed re-applies the DSL on every master push and overwrites
// the job config — wiping any cron trigger that the Jenkinsfile
// registered on its previous run. Putting the cron here means each
// seed run re-applies it explicitly.

// Declared at the Jenkins root (not under `iossifovlab/`): that path
// is a GitHub Organization Folder and rejects Job-DSL-managed
// children. Sibling of the `gain-seed` seed job.
pipelineJob('gain-vep-integration') {
    description(
        'Integration tests for gain-vep-annotator against a real VEP ' +
        'stack. Triggered downstream of iossifovlab/gain/master when ' +
        'vep_annotator/** changes; runs nightly; safe to trigger ' +
        'manually as well.')

    logRotator {
        numToKeep(20)
    }

    triggers {
        // Nightly hash-spread in the 00:00–05:59 UTC window so
        // regressions against a live VEP + GRR surface even when
        // vep_annotator/ itself hasn't changed on master.
        cron('H H(0-5) * * *')
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
            scriptPath('vep_annotator/Jenkinsfile.integration')
            lightweight()
        }
    }
}
