# 9. Push access to `gain` is Jenkins controller access

**Status:** accepted
**Date:** 2026-08-04
**Issues:** #643 (raised reviewing #621; the pattern itself came from #272 and #598)

## Context

Three Jenkins jobs load their pipeline *definition* from a build parameter
rather than from `master`:

| Loads definition from `${BRANCH_NAME}` | Loads definition from `master` |
| --- | --- |
| `gain-core-integration` (#598) | `gain-nightly` (cron) |
| `gain-web-e2e` (#272) | `gain-python-matrix` (cron) |
| `gain-spliceai-integration` | `gain-release` |
| | `gain-vep-integration` (master-triggered + cron) |

Jenkins does **not** evaluate a "Pipeline script from SCM" definition under the
Groovy sandbox. A `cpsScm` definition resolved from `${BRANCH_NAME}` therefore
runs whatever Groovy that branch's `Jenkinsfile` contains, with full privileges,
on the controller — not on an agent. A branch could open a `node('built-in')`
block, read `$JENKINS_HOME` and the master key, or wrap a `withCredentials`
block around an exfiltrating `sh` step. Before #598 a branch could only execute
code on an *agent*, via pytest during the suite.

Read alone, that looks like a privilege escalation introduced by #598. It is
not, because of how the repository is actually configured. Verified against the
live GitHub configuration on **2026-08-04**:

- All **12 collaborators** on `iossifovlab/gain` hold `push`. Three
  (`lchorbadjiev`, `iossifov`, `qweqq`) are admin; the other nine are
  push + triage.
- `master` **accepts direct pushes**. Classic branch protection is absent —
  `/repos/iossifovlab/gain/branches/master/protection` returns 404. The single
  active repository ruleset, `protect-master-branch`, contains exactly two
  rules: `deletion` and `non_fast_forward`, with organization admins as bypass
  actors. There is no require-pull-request rule and no required review.

So the actor the concern depends on — someone who can push a branch but cannot
land code on `master` — does not exist here. Anyone who can push branch `x` can
push straight to `master`, and the four jobs pinned to `master` will then load
*their* definitions from it, reaching the same unsandboxed controller
evaluation. The `master` pin is not a boundary; it is a pin to a ref that the
same set of people can write.

## Decision

**Push access to `iossifovlab/gain` is equivalent to Jenkins controller
access.** That is the trust model, on purpose, and the branch-triggered jobs
loading their definition from the branch under test is consistent with it rather
than an exception to it.

The split in the table above encodes **trigger cadence, not trust**. A job that
can be triggered against an arbitrary branch loads its definition from that
branch, so that a `Jenkinsfile` change is exercisable before it merges — the
defect #272 and #598 were filed to fix, where a pipeline change silently no-ops
on the branch introducing it and only takes effect once merged. A job that only
ever runs against `master` (cron, release, master-triggered) loads from
`master`, because there is no other ref for it to mean.

### Why it was scoped this way

Two narrowings were considered and rejected. Both defend a boundary that GitHub
does not currently enforce, so both cost something and buy no privilege
separation.

**Restricting `BRANCH_NAME` to a validated ref pattern** constrains which refs
the job will resolve, but every ref a contributor can create is one they can
also write arbitrary Groovy into — including `master` itself. It raises the
effort of the UI-triggered path without removing the capability, and it adds a
validation rule that will be quietly wrong the first time a legitimate branch
name falls outside the pattern.

**Keeping the definition on `master` while checking out the branch for the tree
under test** is the more attractive of the two, because it sounds like it
preserves both properties. It does not: the property #272 and #598 bought is
precisely that a change to the pipeline definition is testable on its own
branch, and this reverts exactly that. It would reintroduce the failure mode
documented on #598 — an integration job triggered with `wait: false,
propagate: false`, so the branch's own check stays green while the downstream
job is red against a pipeline the branch cannot influence.

The decision was recorded rather than merely left implicit because the reasoning
is not visible from the code. The job DSL comments explain the *testability*
motivation and say nothing about the trust model, so a reader who notices the
unsandboxed `cpsScm` re-derives the concern from scratch — which is how #643
came to be filed.

## Consequences

The controller's credentials are exposed to everyone with push access to this
repository. That is the real cost, and it is not mitigated by anything in this
decision; it is simply not *changed* by the branch-loading jobs. Limiting who
holds push, and what credentials the controller stores, are the levers that
actually move this — not the ref a pipeline definition is loaded from.

**This decision has an expiry condition.** If `master` ever gains a
require-pull-request or required-review rule — via a ruleset, classic branch
protection, or an organization-level rule — then for the first time there will
be a real privilege boundary between "can push a branch" and "can land code on
`master`". At that moment the reasoning above stops holding, and every job that
resolves its definition from a parameter-controlled ref must be reconsidered,
along with the seed job that applies the Job DSL. This is the line in this ADR
most likely to be needed later, and the one most likely to be missed: adding
branch protection would look like a pure tightening while silently leaving the
three branch-loading jobs as the way around it.

The facts in *Context* are dated deliberately. They are repository
configuration, not code, so nothing in CI fails when they change and no test
pins them. A reader arriving later should re-verify the collaborator list and
the `master` rulesets before relying on this record.

The `COMMIT_SHA` / `BRANCH_NAME` skew is a separate matter and not addressed
here: a build triggered for an older `COMMIT_SHA` resolves `${BRANCH_NAME}` to
that branch's current HEAD and can therefore load a newer pipeline script than
the tree under test. It is flagged in the affected job DSL comments and is a
correctness wrinkle, not a trust one.
