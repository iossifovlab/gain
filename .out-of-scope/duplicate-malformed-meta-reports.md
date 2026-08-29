# A malformed `meta` block is reported once per read, not once per resource

When a resource's `meta:` block is not a mapping — the curator slip is `meta: |`
followed by prose — every reader of the block narrows it and reports it:

```
WARNING gain.genomic_resources.repository: resource <supplement_basic>: meta is
a str, not a mapping; reading it as absent -- fix the resource's
'genomic_resource.yaml'
```

Because the narrowing lives in `GenomicResource.get_meta()`, which every reader
of the block shares (gain#1004), a caller that reads two fields off it reports
the same resource twice, and a `grr_manage repo-info` run over one such resource
emits the identical line **six times**:

```
1x  _publish_repository_contents <- _create_contents_db <- collect_index_info <- get_meta
1x  _create_contents_db <- collect_index_info <- get_labels <- get_meta
1x  _run_repo_info_command <- build_index_info <- get_summary <- get_meta
1x  root <- __call__ <- get_summary     <- get_meta   # info-page template render
1x  root <- __call__ <- get_description <- get_meta
1x  root <- __call__ <- get_labels      <- get_meta
```

All six come from a single `GenomicResource` instance. This is left as it is.

## Why this is out of scope

**Nothing is wrong except the line count.** The block is narrowed correctly at
every one of those six reads, the resource is indexed as carrying absent
metadata, its page renders, and the walk costs the repository only that one
resource. The report is *accurate* six times over. What is wrong is that a
curator reading the log cannot tell six reports from six problems.

**The blast radius is five extra lines, on a narrow slice of resources.** Only
the resource types that run no schema — `basic`, i.e. supplements — reach all
six reads. A score resource carrying the same scalar `meta` emits **one** line:
the base schema refuses it before it reaches an index row or a rendered page.
So the cost is five surplus lines per mis-authored supplement resource, in a
message whose entire purpose is to get that resource fixed on sight.

**The obvious fix does not fix it.** The shape that suggests itself is to stop
`collect_index_info` from reading the block twice — it calls `get_meta()` and
then `get_labels()`, which re-enters `get_meta()`. Inlining the labels narrowing
there takes the run from six lines to five. It addresses one third of the
duplication and none of the info-page render, which is the larger contributor.
It also duplicates the logic gain#1004 consolidated, and reaching for a private
helper across classes trips SLF001.

**The fix that would work is a behaviour change that costs more than it buys.**
Since all six reads go through one instance, deduplicating inside
`_warn_not_a_mapping` on `(instance, level)` collapses six lines to one. That is
a real option, and it is refused rather than overlooked:

- It changes the contract from *report what you read* to *report what you have
  not already reported*, which makes the log a function of a process's read
  history rather than of the resource. Two runs that read a repository in
  different orders then log differently.
- It makes the `_description_in` split that gain#1004 introduced unobservable.
  That split exists so `get_summary` falls back to the description off the block
  it has already narrowed instead of re-entering `get_description`, and
  `test_get_summary_reports_a_malformed_block_once` pins it by counting log
  records. Under a per-instance dedupe that count holds whether or not the
  fallback re-enters, so the test stops pinning the decision it was written for.

The two-tier accessor pattern — a shared outer narrowing in `get_meta`, a
field-specific narrowing per accessor in `get_labels` — is worth more than the
five lines. Promoting a public `narrow_labels(meta)` to avoid the second read
would break it, which is the third fix considered and refused.

## What to do instead

Fix the resource. The message names the resource and the type the block
actually carries, and says what the read did with it. If the volume ever
becomes the problem the log-noise argument assumes it is, the measurement to
make first is how many mis-authored supplement resources a published GRR
actually holds — not how many lines one of them produces.

## Prior requests

- gain#1013 — "collect_index_info reports a malformed meta twice per resource"
  (the count in that title is 2; measured, it is 6, and only 2 of the 6 are
  `collect_index_info`)
