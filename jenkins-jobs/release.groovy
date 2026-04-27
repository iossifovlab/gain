// Jenkins Job DSL definition for the gain-release pipeline.
// Consumed by the seed job on the Jenkins controller; the script
// path below loads this repo's `Jenkinsfile.release` and runs it
// against the tag passed as the TAG_NAME parameter.
//
// The job is kicked off downstream from the root multibranch's
// `Dispatch release` stage when a CalVer tag (^\d{4}\.\d+\.\d+$)
// lands. It can also be triggered manually from the Jenkins UI
// (e.g. to retry a release after a transient publish failure).
//
// See docs/2026-04-27-phase-10-release-pipeline.md "Addendum:
// extraction" for the rationale behind extracting Release out of
// the root Jenkinsfile.

// Declared at the Jenkins root (not under `iossifovlab/`): that
// path is a GitHub Organization Folder and rejects Job-DSL-managed
// children. Sibling of `gain-seed`, `gain-web-e2e`, and
// `gain-vep-integration`.
pipelineJob('gain-release') {
    description(
        'Tag-driven release pipeline for the gain monorepo. ' +
        'Builds wheels, sdists, conda packages, and digest-pinned ' +
        'production Docker images for a CalVer tag, then publishes ' +
        'to wheels.seqpipe.org, anaconda.org/iossifovlab, and ' +
        'registry.seqpipe.org. Triggered downstream of ' +
        'iossifovlab/gain/<tag> after the dispatcher fires; safe ' +
        'to run manually with TAG_NAME set to the tag to release.')

    logRotator {
        numToKeep(40)
    }

    parameters {
        stringParam(
            'TAG_NAME',
            '',
            'CalVer tag to release (e.g. 2026.4.27). Must match ' +
            '^\\d{4}\\.\\d+\\.\\d+$. Pre-release suffixes are ' +
            'deferred to V2.',
        )
        stringParam(
            'UPSTREAM_PROJECT',
            'iossifovlab/gain/master',
            'Multibranch path used to locate the master CI build ' +
            'whose GIT_COMMIT matches the tagged commit. Override ' +
            'only if the multibranch folder layout has changed.',
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
            // Jenkinsfile.release is loaded from master tip so
            // pipeline fixes (typos, credential renames, etc.) can
            // ship without retagging. The pipeline itself does an
            // explicit `checkout refs/tags/${TAG_NAME}` for the
            // workspace so hatch-vcs / Dockerfiles / recipes match
            // the tagged commit.
            scriptPath('Jenkinsfile.release')
            lightweight()
        }
    }
}
