// Jenkins Job DSL definition for the gain-web-e2e (Playwright)
// pipeline. Consumed by a seed job on the Jenkins controller;
// the script path below loads this repo's
// `web_e2e/Jenkinsfile.e2e` and runs it against the branch /
// commit passed as build parameters.
//
// The job is kicked off downstream from
// `iossifovlab/gain/<branch>`'s `Trigger web_e2e` stage on
// every branch, after the parent build's `Conda packages`
// stage finishes. It can also be triggered manually from the
// Jenkins UI (defaults: master, copy wheels from the last
// successful master build).

// Declared at the Jenkins root (not under `iossifovlab/`):
// that path is a GitHub Organization Folder and rejects
// Job-DSL-managed children. Sibling of the `gain-seed` seed
// job and of `gain-vep-integration`.
pipelineJob('gain-web-e2e') {
    description(
        'End-to-end Playwright test suite for the gain ' +
        'web stack. Builds the wheel-based backend ' +
        'production image (gain-core + django-gpf-web-' +
        'annotation) and the Apache-based frontend ' +
        'production image, brings up the full e2e compose ' +
        'stack (db + mail + backend-e2e + frontend-e2e), ' +
        'and runs `npx playwright test` against it. ' +
        'Triggered downstream of iossifovlab/gain/<branch> ' +
        'after Conda packages; safe to run manually.')

    logRotator {
        numToKeep(20)
    }

    parameters {
        stringParam(
            'BRANCH_NAME',
            'master',
            'Branch the upstream gain build was triggered ' +
            'from. The pipeline checks out this branch ' +
            'unless COMMIT_SHA is set.',
        )
        stringParam(
            'COMMIT_SHA',
            '',
            'Specific commit SHA to test (takes precedence ' +
            'over BRANCH_NAME). Empty = use BRANCH_NAME HEAD.',
        )
        stringParam(
            'UPSTREAM_PROJECT',
            '',
            'Upstream Jenkins project name ' +
            '(e.g. iossifovlab/gain/master) the wheel ' +
            'artefacts are copied from. Empty = build the ' +
            'wheels from source instead.',
        )
        stringParam(
            'UPSTREAM_BUILD',
            '',
            'Upstream build number to copy wheel artefacts ' +
            'from. Empty = use the last successful build of ' +
            'UPSTREAM_PROJECT.',
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
            scriptPath('web_e2e/Jenkinsfile.e2e')
            lightweight()
        }
    }
}
