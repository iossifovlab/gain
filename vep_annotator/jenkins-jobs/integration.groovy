// Jenkins Job DSL definition for the gain-vep-annotator integration
// pipeline. Consumed by a seed job on the Jenkins controller; the
// script path below loads this repo's `vep_annotator/Jenkinsfile.
// integration` and runs it against master.
//
// The job is kicked off downstream from `iossifovlab/gain/master`'s
// `Trigger VEP integration` stage whenever `vep_annotator/**` changes.
// It can also be triggered manually from the Jenkins UI.

pipelineJob('iossifovlab/gain-vep-integration') {
    description(
        'Integration tests for gain-vep-annotator against a real VEP ' +
        'stack. Triggered downstream of iossifovlab/gain/master when ' +
        'vep_annotator/** changes; safe to run manually as well.')

    logRotator {
        numToKeep(20)
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
