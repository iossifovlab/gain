# 36. An object built from a resource is memoised on its memo key, through one `Memo`

**Status:** accepted
**Date:** 2026-10-06
**Issues:** [gain#938](https://github.com/iossifovlab/gain/issues/938) (this
record); [gain#912](https://github.com/iossifovlab/gain/issues/912) (root
resources collided in the resource memo, which put the config into
`get_memo_key()`); [gain#857](https://github.com/iossifovlab/gain/issues/857)
and [gain#1059](https://github.com/iossifovlab/gain/issues/1059) (the two
resource-keyed caches that deliberately do not follow this rule);
[ADR 0006](0006-resource-value-identity.md)

## Context

Four factories memoise an object they build from a resource for the life of the
process: `build_gene_models_from_resource`, `build_gene_score_from_resource`,
`build_gene_set_collection_from_resource` and
`build_liftover_chain_from_resource`. Two more memoise an object built from a
local file through a synthetic resource: `build_gene_models_from_file` and
`build_gene_set_collection_from_file`.

All six were written by hand, each as a module-level `dict`, a
`threading.Lock` (shared, in the gene-models and gene-set modules, between the
resource memo and the file memo) and the same "if the key is present return it,
otherwise build, store and return" body. The four resource memos keyed on
`GenomicResource.get_memo_key()`, which canonicalised the config with
`repr(_canonical_config(config))`; the two file memos keyed on
`json.dumps(config, sort_keys=True)` instead. Those are two spellings of one
thing, and the `json` one is the weaker: it raises on a bare date, which has no
JSON spelling, and on a number used as a key beside string ones, which cannot be
sorted. Both are legal in a parsed `genomic_resource.yaml`. The file memos'
configs are synthetic and cannot hold either today, so this was a latent
divergence, not a bug.

Meanwhile the rule for *what a cache over resources keys on* had been written
down three times, each time in a docstring beside the cache that departs from
it, and in the amendment to ADR 0006 — never in one place.

## Decision

**An object built from a resource is memoised on `get_memo_key()`** — the
resource's full id, its repository url and its canonical config — under a
strong reference that is never evicted. The config is in the key because a
resource at the repository root takes its whole meaning from it (gain#912); the
repository url is in it so that one resource reached through two repositories
is built once per repository. The key is finer than value identity
(ADR 0006), never coarser, so a miss costs a rebuild, not a wrong answer.

**An object built from a local file is memoised on the absolute file name plus
`config_memo_key()` of the synthetic config the factory builds.** That is the
same function `get_memo_key()` uses, so there is one spelling of "config → memo
key" for both. It lives in `gain.genomic_resources.memo`, beside the private
canonicalisation it wraps.

**The check → build → store sequence and its lock exist once**, in
`gain.genomic_resources.memo.Memo`. All six memos are `Memo` instances. The
build runs under the memo's lock, so two callers racing on one key build it once
and receive the same object; a hit returns the stored object itself (`is`).
Each memo now owns its own lock — the resource and file memos of one module no
longer share one — which serialises less and changes nothing observable, since
no builder calls back into either memo.

### Two deliberate exceptions

- `GenomicScoreImplementation._REF_GENOME_CACHE` (gain#857) is keyed **weakly
  on the repository object**, then on genome id, and is not a `Memo`: a shared
  `ReferenceGenome` owns a backend whose `close()` any caller could trigger. Its
  reasoning is in its comment in `genomic_scores_impl/base.py`.
- `_ConfigValidatorCache` (gain#1059) is keyed **weakly on `GenomicResource`**
  by value: what it memoises is the normalisation of a config passed as an
  argument, not an object built from the resource. Its docstring in
  `resource_implementation.py` contrasts it with the four memos here, and
  ADR 0006's amendment records what it makes the resource dunders carry.

The annotation-config hashes (`_hash_params` in `annotation_config.py`) are not
memo keys at all — they hash annotator parameters for identity — and are out of
this rule's scope.

### Why it was scoped this way

The rule existed already; this records it, and consolidates the code that
follows it, without changing what any memo holds or for how long. Eviction, a
size bound and weak references were each left out on purpose: the memos have
always been strong and unbounded, callers rely on a repeat call returning the
same object, and changing that is a behavioural decision of its own. `Memo`
makes any of them a one-place change when someone makes that decision.

The tests keep reaching the memos by their module names (`_FILE_CACHE`,
`_RESOURCE_CACHE`) through `Memo.clear()` and `len()`, and the gene-models test
conftest patches a fresh `Memo` over each. A memo that captured its storage at
construction would have let a patched-in attribute go unnoticed while the tests
still passed; the factories therefore look the module attribute up at every
call. Each of the three isolation fixtures was checked by disabling it and
watching a test go red.

## Consequences

- There is one place to change how resource-built objects are cached, and one
  function to change how a config spells into a key. A change to
  `config_memo_key` now moves the keys of all six memos at once.
- A builder passed to `Memo.get_or_build` must not call back into the same memo:
  the lock is not reentrant, exactly as the hand-written blocks were not.
- A builder that raises stores nothing, as before.
- The memos still never evict. A long-lived process that builds from many
  distinct resources or files holds every result; that is unchanged, and is now
  visible in one class rather than six blocks.
