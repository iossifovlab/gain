# 22. A resource file state is judged by the store's change token

**Status:** accepted
**Date:** 2026-08-28
**Issues:** [#881](https://github.com/iossifovlab/gain/issues/881), measured while reviewing [#865](https://github.com/iossifovlab/gain/issues/865)

## Context

Every materialised resource file has a `.grr/<name>.state` document recording
the size, the modification time and the md5 sum of the bytes GAIn hashed. Two
places ask whether that record still describes the file: `classify_resource_file`,
which decides whether a cached file must be downloaded again, and
`_update_manifest_entry_and_state`, which decides whether a manifest build may
copy the md5 sum out of the state instead of re-reading the file. Both answered
by comparing the recorded modification time and size against the store.

On s3 the modification time is not a single value. MinIO reports `LastModified`
truncated to whole seconds on `head_object` and with milliseconds on
`list_objects_v2`, and fsspec's `modified()` is served from whichever call last
filled the s3fs listing cache. Measured on a freshly published three-file
resource: 0 of 3 files read as drifted with the listing cache empty, and 3 of 3
after anything listed the bucket — `state_ts=…629.0` against `live_ts=…629.11`.
Each one then cost a full re-read to re-hash and a rewrite of a state that was
already correct. The same fixture on a `file://` destination showed no drift at
all.

The cost is bounded rather than unbounded: repeated sweeps over an unchanged
repository re-hashed 1, 1, 1, 0, 0, 0 files, because each state write invalidates
the listing cache and the remainder of that sweep is answered by HEADs. The
ceiling is one re-hash per file per flip of access pattern.

The obvious fix — the one the issue itself proposed — is to normalise the
recorded timestamp to whole seconds so the two sources agree. **It is unsafe, and
measurably so.** Overwriting one object twelve times with same-size, different
bytes put 11 of 11 consecutive pairs in the same whole second. A comparison that
sees only whole seconds therefore cannot tell a same-size rewrite from the
version it replaced, and `_update_manifest_entry_and_state` would copy the
superseded md5 sum into a `.MANIFEST` — a committed artefact every client
trusts. Reading consistently from `head_object` fails for the same reason: HEAD
*is* the whole-second source.

The store already offers something better. Measured on the same objects, the
`ETag` is **identical from `head_object` and from `list_objects_v2`** where
`LastModified` differs, and it took 6 distinct values across 6 same-size rewrites
where the whole-second timestamp took 2. For single-part uploads it is the md5
sum itself; above roughly 100 MB s3fs uploads in parts and it becomes
`"<hash>-N"` — verified at 120 MiB, where it is no longer the md5 but still
changes on a same-size rewrite and still agrees across HEAD and LIST.

## Decision

`ResourceFileState` records an opaque **change token** supplied by the store, and
where both the store and the state have one, the token alone decides whether the
state still describes the file. The size is not consulted alongside it: an object
whose token has not moved has not been written.

The token is whatever the store offers as "this is the version you are looking
at". On fsspec it is read out of `info()` as the `ETag`; a filesystem that
reports none — the local filesystem — yields `None`, and those stores keep the
modification-time comparison they have always had. The value is stored verbatim
and is **never parsed**, never compared against an md5 sum, and never assumed to
be one, even where a particular store derives it from one.

Both callers share the token rule and keep their own fallback tolerance: the
cache decision compares timestamps for equality, the manifest scan forgives a
hundredth of a second.

## Why it was scoped this way

**Why not normalise the timestamp.** Covered above: it trades a bounded cost
nobody is currently paying for a silent stale-md5 defect in a committed artefact.
This is the alternative that will look attractive again — it is a two-line change
that makes the reported symptom disappear — so it is written down here rather
than left in a commit message.

**Why the fallback tolerances were not unified.** The first attempt made both
callers use the manifest scan's hundredth-of-a-second tolerance, on the grounds
that two sites answering one question should answer it the same way. That is a
regression: on `file://`, where there is no token, it makes the cache decision
stop noticing a rewrite that lands within the same hundredth of a second. It was
caught by running the new same-size-rewrite test against unmodified `master` and
finding the `file` arm green there and red on the branch — the reverse of the s3
arm. Only the token rule is shared.

**Why an empty token is treated as no token.** s3fs fills a missing ETag on the
`head_object` path with `""` rather than `None`. An empty token recorded against
an empty token compares equal for ever, which is precisely the blindness this
decision exists to avoid, so a falsy token is read as absent.

**What this does not fix.** A store with no change token and a second-granular
modification time cannot distinguish a same-size rewrite inside one second at
all. That is inherent, not an implementation gap. Real AWS S3 is believed to
report `LastModified` at whole-second granularity on both HEAD and LIST — this
was not verified, no real S3 endpoint being available — which would mean the
*drift* reported in #881 is a MinIO artefact while the *blind spot* is real
everywhere, and the change token is the only thing on offer that closes it.

## Consequences

- A same-size rewrite inside one second is now detected on s3. It was not
  before: both reads returned the same whole-second HEAD value. This decision
  closes a correctness gap, not only a cost.
- The `.state` document gains a `change_token` key. A state written before this
  carries none and must keep loading, so the loader reads the key with `.get()`
  and such a state falls back to the modification time until it is next rebuilt.
  An older GAIn reading a newer document indexes only the four keys it knows and
  ignores the extra one, so the format moved compatibly in both directions.
- `get_resource_file_change_token` is a new abstract method on
  `ReadWriteRepositoryProtocol`, alongside the timestamp and size accessors it
  sits with. `FsspecReadWriteProtocol` is its only implementer today.
- The soundness of the token rests on `info()` being fresh. s3fs answers it
  cache-first, and an in-place overwrite behind a warm listing can leave the
  pre-write ETag in the cache. This is not new — the previous comparison read
  `LastModified` and size out of that same stale entry — but the accidental
  second signal it used to have (the HEAD-versus-LIST disagreement, which often
  forced a rebuild for the wrong reason) is gone.
- `ResourceFileState` is an `order=True` dataclass and the new field is
  nullable, so ordering two states that differ only in whether a token is
  present would raise `TypeError`. No caller sorts them today.
