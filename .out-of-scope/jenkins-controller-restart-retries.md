# Retrying non-resumable pipeline steps across a controller restart

The `Jenkinsfile` will **not** opt into Jenkins' retry mitigations
(`retries` on an `agent` directive, or
`retry(conditions: [nonresumable()], count: 2)`) to survive a Jenkins
controller restart that lands inside a post-stage `junit` /
`recordCoverage` / `archiveArtifacts` step. When such a restart kills a
build, the answer is to press Rebuild.

## What the failure looks like

`iossifovlab/gain/master` #1040 (2026-09-03) ran 9601 tests with **zero
failures** and still finished FAILURE. The controller restarted while the
`core` parallel branch was in its post-stage `junit` step, and Jenkins
refused to resume it:

```
org.jenkinsci.plugins.workflow.steps.SynchronousResumeNotSupportedException:
The Pipeline step `junit` cannot be resumed after a controller restart.
```

`junit` is a `SynchronousNonBlockingStepExecution`; it has no resume path.
Every downstream stage (Core wheel, docs, conda, prod images, the four
integration triggers) was skipped, so nothing was published for that SHA.

## Why this is out of scope

The maintainer declined the mitigation at triage (2026-09-09). Three
reasons, in the order that decided it:

**1. Neither documented mitigation fits this pipeline's shape.** This is
the finding that matters most, because the issue was filed on the
assumption that one of them was a config-line away.

- **`retries` on the `agent` directive** requires the stage to *have* an
  `agent` directive. There is exactly one `agent` in the whole file — the
  pipeline-level `agent { label 'builder && !dory' }` — and the eight
  parallel sub-project stages declare none, so they share one workspace.
  That sharing is load-bearing: the pipeline-level `post.always` publishes
  six coverage reports from that workspace *in a deliberately controlled
  order*, precisely because parallel post blocks register in
  nondeterministic order, and it archives `dist/` from the same place.
  Giving each parallel stage its own agent splits that workspace apart. It
  is a restructure of the most carefully-reasoned block in the file, not an
  option flag.
- **`retry(conditions: [nonresumable()], count: 2)`** retries the block it
  wraps. The failing step is not in the stage's `steps` — it is in the
  stage's `post { always { script { publishReports(...) } } }`. A retry
  around the steps does not cover the post block. Covering it means moving
  `publishReports` into the retried body, which defeats the reason it lives
  in `post.always`: publishing the reports even when pytest fails.

**2. The exposure window is a hard controller crash, not a routine
restart.** The two restart paths are not alike. The routine one —
`needrestart` after an unattended upgrade — drains first: `jenkins.service`
carries an `ExecStop=` quiet-down that waits up to 600 s for executors to
go idle (`seqpipe/infra` `docs/jvm-upgrade-policy.md`), and a build that
spans it has been observed resuming clean (gain/master #950, SUCCESS 21 s
after the controller came back). What killed #1040 was an *undrained*
`Restart=on-failure` restart after a HotSpot **SIGSEGV inside G1
concurrent-mark root-region scan** on JDK 21.0.12+8 — the first `hs_err` in
that JVM's 2 d 9 h lifetime, `NRestarts=1` (`seqpipe/infra#268`). So the
mitigation is being asked to defend against JVM crashes, not against the
restart policy.

**3. The cost of doing nothing is one rebuild.** #1041 re-ran the same SHA
green in 11.4 min. The console names the reason in plain text, so it does
not cost diagnosis twice. Downstream, gpf CI floats on the last green gain
master, so a skipped publish means gpf keeps using the previous wheel
rather than breaking.

Measured base rate at the time of the decision: **one occurrence, ever**.
In the six days after #1040, gain/master completed 55 builds (#1041–#1092)
with two failures, both ordinary — #1082 a real test failure, #1048 an
`exit code 2` — and zero recurrences of the resume failure.

## The option that looks cheap and is worse

Catching `SynchronousResumeNotSupportedException` inside `publishReports`
and re-throwing everything else *is* genuinely a few lines. It was
rejected on direction, not on cost: it converts a correctly-red,
mid-flight-disrupted build into one that publishes wheels and triggers
downstream jobs with a missing test report. Trading a visible failure for a
silent one is the wrong way round.

## When to reopen this

This rejection is about an observed rate and a pipeline shape. Revisit if
any of those change:

- A **second** controller `hs_err` / crash restart, or a second build lost
  to a non-resumable step.
- The parallel sub-project stages acquire per-stage `agent` directives for
  some other reason — at that point `retries` becomes nearly free, and the
  first objection above dissolves.
- The controller moves to a posture with more frequent undrained restarts
  (worth watching across the nemo → kowalski cutover).

## Prior requests

- iossifovlab/gain#1180 — "Jenkinsfile: post-stage junit/recordCoverage
  cannot resume across a controller restart (master #1040)"
