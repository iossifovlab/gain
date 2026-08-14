// Jenkins Job DSL definition for the gain-nightly orchestrator.
// Consumed by the seed job on the Jenkins controller; the script
// path below loads this repo's `Jenkinsfile.nightly`.
//
// The job is cron-triggered (~02:00 UTC) and rebuilds master plus
// the integration suites unconditionally, then deploys the image it
// just validated to the gain.seqpipe.org staging host.
//
// tb-7e7: cron MUST live in this DSL, not in Jenkinsfile.nightly.
// gain-seed re-applies the DSL on every master push (pollSCM every
// 10 min) and that overwrites the job config — wiping any cron
// trigger that the Jenkinsfile registered on its previous run.
// Result was 4 manual builds and zero cron-fired builds. Putting
// the cron here means each seed run re-applies it explicitly.
// Zulip-on-failure stays in the Jenkinsfile (it's pipeline logic,
// not job config).

// Declared at the Jenkins root (not under `iossifovlab/`): that
// path is a GitHub Organization Folder and rejects Job-DSL-managed
// children. Sibling of `gain-seed`, `gain-release`, `gain-web-e2e`,
// and `gain-vep-integration`.
pipelineJob('gain-nightly') {
    description(
        'Cron-scheduled orchestrator that rebuilds master from ' +
        'scratch and re-runs gain-web-e2e, gain-vep-integration, ' +
        'gain-spliceai-integration and gain-python-matrix ' +
        'unconditionally. Catches dependency drift / silently-' +
        'stale caches on quiet days. If all of them pass, it then ' +
        'triggers gain-staging-deploy to put the image this build ' +
        'validated (pinned to the master rebuild\'s BUILD_NUMBER) ' +
        'on pooh / gain.seqpipe.org — so staging only ever moves ' +
        'to a verified master. Sends a Zulip alert on failure ' +
        '(topic: nightly).')

    logRotator {
        numToKeep(40)
    }

    triggers {
        // H hashes on the job name so the minute is stable per job
        // but spread across other nightlies on the controller.
        cron('H 2 * * *')
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
            scriptPath('Jenkinsfile.nightly')
            lightweight()
        }
    }
}
