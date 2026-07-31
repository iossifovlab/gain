# 5. Rebuilding a memoized fsspec protocol refreshes it, and may not reconfigure it

- **Status:** accepted
- **Date:** 2026-07-30
- **Issues:** [#514](https://github.com/iossifovlab/gain/issues/514); builds on
  [#458](https://github.com/iossifovlab/gain/issues/458) (the memo lock) and
  [#488](https://github.com/iossifovlab/gain/issues/488) (mode-scoped test
  protocol ids); extended by
  [#527](https://github.com/iossifovlab/gain/issues/527), which made
  construction itself one atomic, serialised step (see the Consequences)

## Context

`FsspecReadOnlyProtocol.__new__` memoizes one instance per `(proto_id, url)` in
the module-level `_FSSPEC_PROTOCOLS` dict and never evicts. Returning an
existing instance from `__new__` does **not** stop Python from calling
`__init__` on it — so every repeat build re-ran the constructor body against an
object other callers were already using.

Nothing in the codebase builds a protocol once. `build_genomic_resource_repository`
has no repository-level memo of its own, so each of the ~10 production callers
that resolve the default GRR (`genomic_scores`, `reference_genome`,
`gene_models_factory`, `gene_scores`, `gene_sets`, `liftover_chain`, …) re-enters
`__init__` on the shared protocol; `GenomicResourceCachedRepo` does the same for
its cache-side protocol, and unpickling reaches the identical path through
`__getnewargs_ex__` → `__new__` → `__setstate__`.

Three things were being rebound on that live instance:

1. **The memo lock.** `__init__` assigned a brand-new `Lock` to
   `_all_resources_lock` and cleared `_all_resources` beside it, under no lock at
   all. A reader inside `get_all_resources_dict`'s guard holds the *old* object,
   so the two then serialise on two different locks — which made the mutual
   exclusion #458 had just established conditional on nobody rebuilding.
2. **The mode.** The memo key does not include the class, and
   `FsspecReadWriteProtocol` subclasses `FsspecReadOnlyProtocol`. A read-only
   build over a read-write key therefore satisfied `isinstance` and was answered
   with a *writable* protocol (with the read-write `__init__`, `makedirs`
   included, re-run on it), while a read-write build over a read-only key was
   answered with an instance Python skipped `__init__` on entirely — stale
   filesystem, stale kwargs, no write methods.
3. **The public url and the credentials.** A second build's `public_url` and
   `user`/`password` landed on the incumbent, so the repository every existing
   holder had was silently republished under a different url, or pointed at
   different credentials.

The test builders had already met (2) and worked around it locally: since #488
`_derive_test_proto_id` appends `-ro` so the two modes never share an id, and
`build_filesystem_test_protocol` re-checked `proto.mode()` after building. That
fixed the test helpers and left every production caller exposed.

## Decision

A rebuild of a memoized protocol is a **refresh of the resource memo, performed
under the incumbent's own lock**, and nothing else:

- `_all_resources` and `_all_resources_lock` are bound **once per instance, in
  `__new__`**, before the instance is published to `_FSSPEC_PROTOCOLS`.
- `__init__` and `__setstate__` reach the memo only through `invalidate()`,
  which takes that lock. The lock object itself is never replaced.
- `__new__` **refuses** a rebuild whose requested mode, resolved `public_url`,
  or filesystem keywords differ from the incumbent's, with a `ValueError` naming
  the protocol id and the (credential-stripped) url. Two differently configured
  protocols over one url remain available — under two ids.
- The memo key is **canonicalised** to the credential-bearing
  `scheme://netloc/path` form, so one repository cannot occupy two entries.
  Several builders (`build_local_resource`, `grr_manage`'s `_create_proto`, the
  filesystem test builder) pass a bare `/abs/path` while `__getnewargs_ex__`
  pickles the `file://` spelling: those were two keys, so a pickle round trip
  silently minted a *second* protocol over one directory — the very state whose
  halves can then disagree — and the comment on `__getnewargs_ex__` claiming
  the pickled url "matches a fresh build" was false for every one of them.
- `public_url` is compared in one spelling (`_canonical_public_url`), not as
  authored, because an incumbent's is whatever its caller passed while a
  rebuild that passes none defaults to the url's display form. A trailing
  slash otherwise read as a request to republish the repository elsewhere. The
  value reported by `get_public_url` is untouched.

"Filesystem keywords" is the set `_build_filesystem` actually reads
(`base_url`, `user`, `password`, `endpoint_url`), pinned to that function by a
drift-guard test. Keywords a caller passes that the protocol never reads are
deliberately *not* compared: the repository factory hands the builder its
`cache_dir`, which configures the cache wrapped around the protocol, and two
definitions over one directory that cache in different places are still one
protocol. A keyword given as `None` compares equal to an omitted one, because
`_build_filesystem` reads them all with `.get` — a `url`-type definition passes
neither credential keyword and an `http`-type one passes both as `None`, over
the same url.

## Why it was scoped this way

**The obvious fix — an "already initialised" flag that makes `__init__` a no-op
on a memo hit — was rejected**, and it is the one that will look attractive
again. It would have silently retired the refresh. `grr_manage` reads a
repository it has just changed by building it a second time
(`test_contents_db_rebuilt_when_contents_change` pins exactly that, and ~30
further CLI tests re-enter `cli_manage` on one repository path), so a no-op
`__init__` would have left those runs reading a stale resource list from a memo
nothing cleared. The refresh is the contract; the lock swap was the defect.

**Refusing, rather than reconfiguring properly, is the other real choice.** With
one instance per key and no eviction, honouring a divergent rebuild means either
mutating an object other callers hold — the defect — or silently ignoring what
the caller asked for. Neither is serviceable, and this repository already
treats a credential mismatch over one key as something to refuse rather than
resolve: the memo key deliberately keeps url-embedded userinfo (#467-era
comment) so a second build with other credentials cannot reuse the first
protocol. Keyword-borne credentials were the hole in that reasoning; the refusal
closes it.

**Cost, paid honestly.** The refusal found two existing collisions rather than
zero: `test_public_url_explicit_is_credential_free` shared
`("authed", "https://grr.example.com")` with the test above it while passing a
different `public_url`, and only passed *because* a rebuild rebound the
incumbent's url; it now uses an id of its own. A first attempt compared *all*
keywords and broke two `test_repository_factory` cases over `cache_dir`, which
is what narrowed the comparison to the filesystem keywords. Both are the
same shape of finding: the collisions were already there, silent.

**And the refusal itself needed a review round before it was safe.** Three
defects in the first version, none of them in the part the issue was about:
it raised `AttributeError` out of `__new__` when it met the half-initialised
incumbent of the publish-before-init race (a *worse* outcome than the
behaviour it replaced — see the Consequences below); it refused a rebuild whose
`public_url` differed from the incumbent's only by a trailing slash, with a
message that claimed the caller was repointing the repository; and the
drift guard meant to keep `_FILESYSTEM_KWARGS` honest used a pattern that
matched `kwargs.get("x")` but not `kwargs.get("x", default)` or
`kwargs["x"]` — so it would have stayed green through exactly the drift it
exists to catch. Broadening that pattern then over-matched
`client_kwargs["auth"]`, a *write* to another dict, which would have refused
every rebuild of an authed http protocol. The lesson generalises: a check that
compares a live object against a request has to be written for the states that
object can actually be in, and a guard that reads source needs a test of its
own.

## Consequences

- Two protocols over one url now require two ids, in production as in tests. A
  GRR definition tree that names one directory twice with different
  `read_only`/`public_url`/credentials fails loudly at build time instead of
  quietly serving whichever configuration was built last. The group repository's
  duplicate-child-id guard (#445) already refuses the common way to author that.
- `build_filesystem_test_protocol`'s post-build `proto.mode()` re-check is gone,
  because the protocol layer now refuses before it could fire. The `-ro` suffix
  in `_derive_test_proto_id` stays and is load-bearing: it is what gives a test
  wanting both modes over one root two ids rather than an error.
- `_FILESYSTEM_KWARGS` must be kept in step with `_build_filesystem`. A keyword
  it learns to read without joining that set is one a rebuild could go on
  changing silently, so the coupling is stated in both places and pinned by
  `test_every_filesystem_keyword_is_compared_on_a_rebuild`.
- Left open here, closed since in iossifovlab/gain#527: `__new__` reached
  `_FSSPEC_PROTOCOLS` under no mutual exclusion at all, and published the
  instance *before* `__init__` had configured it. Binding the memo and its
  lock in `__new__` narrowed that window; it did not close it, and it did not
  touch a second half this ADR never recorded.

  **The window.** Between publication and configuration the instance was
  reachable with `filesystem`, `url`, `public_url`, `kwargs` and `proto_id`
  unbound, so `get_url()` and `get_public_url()` raised `AttributeError` out
  of methods that cannot fail. The refusal had to *tolerate* that rather than
  discover it: a first attempt read `existing.public_url` unconditionally,
  which turned a race that had merely run `__init__` twice into an
  `AttributeError` raised out of `__new__` — a strictly worse outcome than
  the behaviour it replaced.

  **The half that was not reported.** `__new__` was also a plain
  check-then-set — memo read, construct on a miss, assign, with nothing
  serialising the three — so two threads that both missed for one key both
  built, and the second assignment overwrote the first. One caller then held
  an orphan protocol the memo does not know about, which breaks the
  "`(proto_id, url)` names ONE protocol instance" invariant this whole
  arrangement rests on: the orphan is never compared by
  `_refuse_a_reconfiguring_rebuild`, it carries an `_all_resources` memo and
  an `_all_resources_lock` of its own so #458's mutual exclusion is lost
  between the two halves and the refresh contract above never reaches it, and
  pickle no longer round-trips to one object.

  **What was done.** `_FSSPEC_PROTOCOLS` now holds *only configured*
  protocols. `__new__` takes a module-level guard across the memo read, the
  instance creation and the bookkeeping, and records an in-flight
  `_ProtocolConstruction` in `_FSSPEC_PROTOCOLS_UNDER_CONSTRUCTION` instead of
  publishing; a key is in one dict or the other, never both. A thread that
  finds a construction in flight waits on its `Event` — *outside* the guard,
  because `FsspecReadWriteProtocol.__init__` does filesystem I/O and holding
  the guard across it would put every protocol build in the process behind
  one remote round trip — and then rounds the loop, to be answered from the
  memo or to take an abandoned construction on.

  Publication is `_finish_construction`, and it is driven from two places
  because there are two ways to construct a protocol. `_BuiltOnceProtocolMeta`
  takes over the class call protocol so publication happens after the whole
  `__init__` chain has returned, which is what keeps a read-write protocol
  from being handed to a waiter before `makedirs` has created its root — the
  base constructor body is too early. And `__setstate__` finishes its own,
  because **unpickling never calls `__init__` at all**
  (`__getnewargs_ex__` → `__new__` → `__setstate__`): a construction lock
  taken in `__new__` and released in `__init__`, the shape that first
  suggests itself, deadlocks every deserialize into a cold memo. The
  metaclass is also the only place that sees a construction failing *before*
  `__init__` is entered — a call-signature error — which would otherwise
  strand the key and block every later builder of it forever.

  A construction that raises is **abandoned**: its record is dropped and its
  `Event` set, so the key stays buildable and the waiters take it on. That is
  the property that makes serialising construction safe at all — the failure
  mode it replaces (a duplicated `__init__`) was survivable, and a permanent
  deadlock would not be.

  What did **not** change: `__init__` still re-runs on every rebuild, so the
  refresh above is intact — the racing-build test now pins the count, at one
  construction and one refresh, so the "already initialised" no-op rejected
  above cannot creep back in as a way of making a rebuild cheap.

  The `hasattr(existing, "kwargs")` early return in the refusal is **gone**.
  It existed only to tolerate the window, the window cannot occur now, and
  tolerating it anyway would mean a regression that reopened it answered a
  divergent rebuild silently instead of being found. The mode arm still reads
  no instance state, as it always did.
- Left alone here, fixed since in iossifovlab/gain#528:
  `build_fsspec_protocol` ignored `read_only` for `http(s)` urls and always
  built a read-only protocol, so a caller asking for a writable http protocol
  was answered with a read-only one — the same silent-wrong-mode shape as this
  issue, on a scheme where read-write is not implementable at all.

  The mode arm of the refusal above cannot reach it: the mode it compares is
  the mode of the class `build_fsspec_protocol` *picked*, which on the http
  branch is read-only whatever the caller asked for. So #528 is answered in
  the builder rather than in `__new__` — an explicit `read_only=False` over an
  `http(s)` url now raises there, while an omitted `read_only` goes on
  building the read-only protocol silently. That distinction needed
  `read_only` to default to absent rather than `False`, since the two were
  otherwise indistinguishable.

  Review of that change turned up a second, wider inversion underneath it:
  `read_only` reaches the builder as a **string** from two directions —
  `grr_manage --extra-args read_only=false` parses into `dict[str, str]`, and
  a repository is built from the *raw* definition dict rather than the
  validated model, so a quoted `read_only: "false"` in yaml arrives as
  `"false"` even though `FileRepoDefinition` coerced it to `False` on the way
  in. Both were read by a bare truthiness test, so every spelling of *false*
  selected a **read-only** repository, on the local schemes too. `read_only`
  is now resolved to a boolean where it is read, and a value that spells no
  boolean at all is refused rather than guessed at.
