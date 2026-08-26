# `calc_statistics_hash` will not detect a change in the computing code

`calc_statistics_hash` hashes **inputs** — the resource config plus the md5s of
the input files — and it stays that way. It carries no version of the *code*
that produces the statistics, so a change to that code which leaves the inputs
untouched will **not** invalidate anything, and no rebuild is triggered.

Recomputing statistics after a code change is a deliberate, operator-driven
act: `grr_manage resource-stats -r <resource_id> -f`, with `resource-info -f`
when the page must re-render too (gain#774 built and pinned that path; ADR 0014
records the precedent).

## Why this is out of scope

The proposal was to give the hash a component for the computation — a code
version, a hash of the statistics-producing modules, or a hand-maintained
marker — so that deployed GRRs stop serving statistics built by superseded
code without anything reporting it.

The gap is real and the description of it is accurate. It is the remedy that is
refused, for four reasons.

**The hash is an input hash, and that is what makes it cheap to trust.** Its
job is "these bytes and this config produced these statistics". Adding a code
component changes the question it answers from *what went in* to *what ran*,
and those have different truth conditions. An input hash is verifiable from the
resource alone; a code hash is only meaningful relative to a build of gain that
the resource does not carry and cannot check.

**It would invalidate everything on releases that change nothing.** Hashing the
code means hashing the code, not its semantics. A comment, a refactor, a ruff
fix, a type annotation in any module the statistics path imports would
invalidate every resource in every GRR. The statistics scan reads every row of
every tabular score — order 10^10 records across the published repositories —
and the artifacts it writes are **committed content** in `iossifovlab/grr` and
the private `iossifovlab/grr_sfari`, not build output. So the cost of a false
invalidation is a fleet-wide rescan plus a content-repo commit, and false
invalidations would be the common case. A signal that fires mostly when nothing
changed is worse than no signal: it gets muted.

**Narrowing it to "real" changes is not decidable.** The useful predicate is
"would this code produce different statistics for this resource", which is a
question about semantics that a hash cannot answer. Every practical
approximation collapses to a marker somebody bumps by hand when they judge a
recompute is warranted — which is the same human judgement the forced rebuild
already requires, wearing a hash's clothes, with the added failure mode of
being silently forgotten in a PR that needed it.

**The judgement being automated is the wrong half.** What is hard about a
statistics correction is not *noticing* that the code changed — whoever changed
it knows. It is deciding whether the change is worth a fleet-wide rebuild, and
scheduling one. That decision wants a person, and making invalidation automatic
does not remove it; it relocates it to whoever is surprised by a rebuild they
did not ask for.

## What is still in scope

**Making the manual step visible and reliable.** The refusal is of automatic
invalidation, not of the problem's existence. Anything that helps an operator
know a rebuild is warranted, and carry one out safely, remains in scope — an
explicit sweep issue like gain#925, a note in the release process, a recorded
statistics format version that a human reads rather than a hash consumes, or a
report of which resources carry statistics at all.

**Recording what a resource's statistics were built from,** if a concrete need
arises, is a different proposal from invalidating on it. Storing a marker is
cheap and inert; *acting* on it automatically is what this document refuses.

**Per-resource forced rebuilds** are the supported mechanism and are unaffected
(gain#774). So is any future work on their scope, reporting, or the
repository-global artifacts they leave stale.

## The known consequence, accepted

The consequence is that a statistics correction reaches deployed resources only
when somebody sweeps. gain#816 is the shape to expect: fragment counts depended
on `--region-size` and were fixed in code with no input file touched, so any
GRR built before that fix keeps its wrong numbers until forced. The extended
statistics of gain#770 — coverage, segments, fragments, allele counts, the
substitution matrix, the ins/del histograms and the complex grid — all sit in
the same position, and their initial appearance is itself a manual sweep
(gain#925).

That is understood and accepted. The statistics are documentation of resource
content on an info page; they are not an annotation input and nothing computes
against them, so a stale one is a stale document rather than a wrong answer
handed to a caller.

## Prior requests

- iossifovlab/gain#706 — "`calc_statistics_hash` cannot express a change in the
  code that computes the statistics"
