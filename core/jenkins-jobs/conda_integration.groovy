// Jenkins Job DSL definition for the gain-conda-integration pipeline
// (#1429). Consumed by the gain-seed job on the Jenkins controller; the
// script path below loads this repo's `core/Jenkinsfile.conda-integration`
// and runs it against the branch / commit passed as build parameters,
// with the gain-core .conda copied from the upstream build named by
// UPSTREAM_PROJECT / UPSTREAM_BUILD.
//
// The job is kicked off downstream from `iossifovlab/gain/<branch>`'s
// `Trigger conda integration` stage on every branch, and is safe to
// trigger manually from the Jenkins UI (defaults: master, artefact from
// the last successful build of iossifovlab/gain/<BRANCH_NAME>).
//
// Declared at the Jenkins root (not under `iossifovlab/`): that path is a
// GitHub Organization Folder and rejects Job-DSL-managed children. Sibling
// of `gain-core-integration`, which runs the same test tier from a
// `uv sync` of the source tree.
pipelineJob('gain-conda-integration') {
    description(
        'Integration test suite for gain-core (core/tests/integration) run ' +
        'against the CONDA PACKAGE the upstream build archived, installed ' +
        'from a local file channel over conda-forge + bioconda with strict ' +
        'channel priority and no iossifovlab channel. A red here that is ' +
        'green in gain-core-integration means packaging or channel ' +
        'resolution, not code. Triggered downstream of ' +
        'iossifovlab/gain/<branch> on every branch; safe to run manually. ' +
        'gain-release requires a SUCCESS run of this job for the commit ' +
        'it tags (#1431).')

    logRotator {
        // Matches the master multibranch. gain-release looks the
        // per-push run for a tagged commit up here (#1431), and
        // branch pushes alone got through 20 builds in half a day,
        // so 20 meant a tag cut the day after its merge found
        // nothing and had to trigger a fresh run.
        numToKeep(100)
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
        stringParam(
            'UPSTREAM_PROJECT',
            '',
            'Upstream Jenkins project name (e.g. iossifovlab/gain/master) ' +
            'the gain-core .conda is copied from. Empty = ' +
            'iossifovlab/gain/<BRANCH_NAME>.',
        )
        stringParam(
            'UPSTREAM_BUILD',
            '',
            'Upstream build number to copy the .conda from. Empty = the last ' +
            'successful build of UPSTREAM_PROJECT. On a manual run make sure ' +
            'that build is of the commit under test.',
        )
        stringParam(
            'PYTHON_VERSION',
            '',
            'Pin the solve to this Python (e.g. 3.12). Empty = unpinned, ' +
            'what a user\'s `mamba create` sees; the nightly matrix (#1430) ' +
            'sets it.',
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
                    // trigger therefore loads the pipeline script from the
                    // same branch it tests, not from master (#598; #272
                    // fixed the same defect for gain-web-e2e).
                    //
                    // Loading the definition from a branch runs that
                    // branch's Groovy unsandboxed on the controller. That
                    // is the accepted trust model, recorded with the
                    // condition that would force a revisit in
                    // docs/adr/0009-jenkins-pipeline-definition-trust.md
                    // (#643).
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
            scriptPath('core/Jenkinsfile.conda-integration')
            lightweight()
        }
    }
}
