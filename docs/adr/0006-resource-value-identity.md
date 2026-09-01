# 6. A genomic resource's value identity excludes its manifest and its protocol

**Status:** accepted
**Date:** 2026-07-30
**Issues:** #524 (found reviewing the #519 fix; same attribute as #519 and #458)

## Context

`GenomicResource.__eq__` and `__hash__` were written independently and shared no
field beyond `resource_id` and `version`. `__eq__` compared `resource_id`,
`version`, `config` and the lazily-populated `_manifest` memo; `__hash__` hashed
`resource_id`, `version` and `proto.get_url()`.

That produced two distinct defects.

The manifest is built on demand and cleared by `invalidate()`, so equality was a
function of which side happened to have loaded one. Two objects denoting the
same resource compared unequal until both warmed, and `invalidate()` changed
what a resource equalled while leaving its hash alone.

Separately, and less obviously, the *hash invariant itself* was violated. Two
resources with the same id, version and config reached through **different
protocols** — what a group repository hands out when two children serve the same
resource — compared equal while hashing differently. Python requires `a == b` to
imply `hash(a) == hash(b)`; without it no hash container is correct. This is the
stricter of the two failures: unequal objects sharing a hash is a permitted
collision, but equal objects with different hashes is undefined behaviour.

Both were latent. No `GenomicResource` was a dict key or set member anywhere in
`gain` or `gpf` — every resource container was keyed by string. (One is, since
gain#1059; see the amendment under Consequences.) The one
transitive consumer is `AnnotatorInfo`, which compares its `resources`
element-wise and folds `hash(res)` into its own hash, and which *is* a dict key
and set member throughout the reannotation pipeline. There the symptom is a
spuriously re-run annotator: an unchanged annotator falls out of the previous
pipeline's set and gets recomputed.

## Decision

A resource's value identity is **what it is** — its `resource_id`, its `version`
and its `config`. `__eq__` compares exactly those three.

`__hash__` hashes `(resource_id, version)`: a **strict subset** of what `__eq__`
compares. The subset relation is what restores the invariant by construction,
and it is the property to preserve when either method is next edited. `config`
is a dict and unhashable; leaving it out only coarsens the hash, which is
always sound.

Neither method consults `proto`. Where a resource was reached is not part of
what it is.

`__eq__` returns `NotImplemented` rather than `False` for a foreign operand, so
the reflected comparison still runs — matching `AnnotatorInfo.__eq__`.

### Why it was scoped this way

The invariant can be restored from either end, and the other end was rejected.
Adding `proto` to `__eq__` would equally make the two agree, but it would make a
cache-backed resource compare unequal to the remote twin it mirrors — behaviour
the cached repository documents and depends on, and which
`test_search_resources_yields_the_memoized_resource` pins. Identity, not
equality, is what distinguishes a cached resource from its remote.

Dropping `_manifest` from `__eq__` alone — the fix as originally proposed on
#524 — was also rejected as incomplete. It resolves the visible symptom while
leaving the hash-invariant violation in place, and slightly widens the window in
which that violation is reachable.

No locking was added. `__eq__` was an unsynchronised double read of `_manifest`,
the same shape as #519; not reading the memo at all removes the concern rather
than synchronising it.

## Consequences

Two resources served by different children of a group repository, with the same
id, version and config but **drifted file content**, now compare equal and
collapse to one element in any set or dict. This is the real cost of the
decision, and the thing a later reader is most likely to want to undo. It was
accepted because the alternative breaks cached/remote equality, because the two
already compared equal whenever neither had loaded a manifest, and because
nothing in either repository de-duplicates group results through a hash
container. If content-sensitive comparison is ever needed, it belongs in an
explicit manifest comparison — `Manifest.__eq__` already exists and is what
`copy_resource` and `update_resource` use — not in resource equality.

The hash is coarser: every version of a resource id now hashes alike regardless
of where it came from. Nothing keys a hot container on resources, so this costs
nothing measurable today.

**Amended in gain#1059: something does now.** `_ConfigValidatorCache._documents`
in `resource_implementation.py` is a `WeakKeyDictionary` keyed on
`GenomicResource`, and it sits on the construction path of every score, gene
score and gene models object — the first and, at the time of writing, only
resource-keyed container in either repository. Two things above are therefore no
longer statements about a latent property:

- The dunders are load-bearing. Editing either one now changes which normalized
  config a resource is handed, not just how `AnnotatorInfo` folds its hash.
- The coarse hash costs a measured 0.13 µs per lookup, not nothing. A hit pays
  exactly one `__eq__`, comparing a config with itself, which short-circuits per
  value on identity. Resources that share a hash bucket without being equal are
  separated by the stored hash before `__eq__` is reached, and two *distinct*
  resources with the same id and version cannot occur in one repository, so the
  deep config compare the coarse hash makes possible is not reachable.

The accepted cost — two group-repository resources with drifted content
collapsing to one element — now also means they share one memo entry. Their
configs are equal by construction there, so the entry is correct for both; and
because the memo hands out copies and re-checks the config it was filled from, a
collision costs at most a re-normalization. It remains the thing a later reader
is most likely to want to undo, and this is now the second place to look when
they do.

Because neither dunder reads the protocol any more, `__eq__` is the *only* thing
separating two versions of one resource. The identity tests carry explicit
guards for `resource_id` and `version` for that reason.

### Cost of getting here

The first cut of this change was correct in production code but under-tested,
and review caught two real gaps. Nothing pinned `resource_id` or `version` as
part of value identity: mutants dropping either from `__eq__` survived the new
test file *and* all 3379 tests in the two affected packages. And the whole suite
exercised the dunders directly, never the one production consumer, so it would
have survived a refactor that kept `__eq__` intact and broke reannotation reuse.
Both gaps are now covered, the second by a test at the `ReannotationPipeline`
level that fails on the pre-fix code with a spurious rerun.

The lesson generalises: for a change to a dunder with no direct production
caller, a test against the dunder is not evidence that the behaviour anyone
actually depends on is protected.
