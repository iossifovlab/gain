// Jenkins Job DSL definition for the gain-nightly orchestrator.
// Consumed by the seed job on the Jenkins controller; the script
// path below loads this repo's `Jenkinsfile.nightly`.
//
// The job is cron-triggered (~02:00 UTC) and rebuilds master plus
// the integration suites (gain-web-e2e + gain-vep-integration)
// unconditionally. The cron schedule + Zulip-on-failure live in
// Jenkinsfile.nightly, not here.

// Declared at the Jenkins root (not under `iossifovlab/`): that
// path is a GitHub Organization Folder and rejects Job-DSL-managed
// children. Sibling of `gain-seed`, `gain-release`, `gain-web-e2e`,
// and `gain-vep-integration`.
pipelineJob('gain-nightly') {
    description(
        'Cron-scheduled orchestrator that rebuilds master from ' +
        'scratch and re-runs gain-web-e2e + gain-vep-integration ' +
        'unconditionally. Catches dependency drift / silently-' +
        'stale caches on quiet days. Sends a Zulip alert on ' +
        'failure (topic: nightly).')

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
            scriptPath('Jenkinsfile.nightly')
            lightweight()
        }
    }
}
