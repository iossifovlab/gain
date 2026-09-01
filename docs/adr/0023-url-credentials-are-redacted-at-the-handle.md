# 23. Url credentials are redacted at the handle, and redaction preserves retryability

**Status:** accepted
**Date:** 2026-09-01
**Issues:** [#620](https://github.com/iossifovlab/gain/issues/620),
[#629](https://github.com/iossifovlab/gain/issues/629),
[#1017](https://github.com/iossifovlab/gain/issues/1017),
[#1058](https://github.com/iossifovlab/gain/issues/1058),
[#1078](https://github.com/iossifovlab/gain/issues/1078)

## Context

A GRR can be configured as `https://user:pass@host/repo`. The password is a
real secret, and the url that carries it is the url every remote read must
derive from — `FsspecReadOnlyProtocol._fetch_url`. When a fetch fails,
fsspec and aiohttp put that url verbatim into the message of the error they
raise, so any code that propagates or logs such an error publishes the
credential.

The rule that answers this — *strip url userinfo from anything that
escapes* — has been rediscovered four times. #620 redacted the download
loop's retry warnings and its terminal error. #629 followed with the
definition and repr surfaces. #1017 found that
`_copy_resource_file_to_local` opened through `open_raw_file` and then read
on the returned handle, so the open was redacted and the read was not.
#1058 found the identical shape one layer up, in `get_file_content`, which
sits in front of the path #1017 had just fixed.

Each was closed at its own call site, by routing that caller through
`_read_fetch_file` — open and read in one place, both under
`_run_redacting_userinfo`. #1078 then established that this was
approximately half a fix: **33** call sites read on a handle that
`open_raw_file` returned, and the remedy could not reach most of them.
`_read_fetch_file` reads the whole file, so it cannot serve a caller that
holds the handle for byte-offset random access (`_RawSeekSequence`),
iterates it lazily (`InmemoryGenomicPositionTable`), or reads it in bounded
chunks on purpose (`compute_md5_sum`, `_download_resource_file`) — slurping
a multi-GB file to redact it trades a leak for a memory regression.

One of those sites is materially worse than the others.
`TabixGenomicPositionTable._validate_index_columns` reads the index header
inside a deliberately broad `except Exception` (#628: a transient fault must
not refuse a resource htslib has just read) and reports the decline with
`str(error)` in a `logger.warning`. Every other site propagates an
exception, which may or may not be rendered; that one *writes the
credential into the log*, where it persists and is shipped.

The rule also lived nowhere durable. It was stated in the docstring of a
private method, `_open_fsspec_file`, which said reads on the returned handle
were out of its reach — an accurate description of a gap, read only by
someone already standing in that file.

## Decision

**Redaction is a property of the handle, not of the call site.**
`_open_fsspec_file` returns a `_RedactingFile` wrapping the fsspec handle.
Every operation on it that can perform I/O against the store — `read`,
`readall`, `readinto`, `readline`, `readlines`, `seek`, `tell`, `write`,
`flush`, `truncate`, `close`, iteration, and leaving a `with` block — runs
under `_run_redacting_userinfo`. In every other respect the wrapper is
transparent, delegating unknown attributes to the handle it wraps, because
that handle escapes to `gzip.open`, pandas, `json.load`, `LiftOver` and the
gene-set and gene-model readers.

`readall` earns its place by a route worth recording, because wrapping the
obvious methods does not cover it. `io.BufferedReader(handle).read()`
resolves to `readall`; a *delegated* `readall` executes on the inner handle
and drives the inner `readinto` from there, so the wrapper is bypassed end
to end and the buffering consumer receives an unredacted failure. Wrapping
`readinto` alone does not help — the entry point has to be wrapped too.

This closes all 33 sites at one choke point and closes future ones by
default: `open_raw_file` on both the protocol and `GenomicResource` already
routes through it, so no call site changed. `_read_fetch_file` remains, as
the way for a caller that genuinely slurps to say so in one call.

Two properties of the wrapper are load-bearing and easy to lose:

- **`__iter__`, `__next__`, `__enter__` and `__exit__` are defined, not
  delegated.** Special methods are looked up on the type, so `__getattr__`
  never sees them: without them `for line in handle` — how the tabular,
  chrom-mapping and gene-set readers consume a file — raises `TypeError`
  rather than delegating. The test-only `_FaultyFile` (ADR 0021) carries the
  same note for the same reason.
- **`__enter__` answers the wrapper, not the handle it wraps.** A file
  object's own `__enter__` answers itself; handing that through would give
  the `with` body the unwrapped handle and every read in it would run
  unredacted, which is a wrapper that buys nothing while appearing to work.

**Redaction preserves retryability.** Rebuilding an error whose type cannot
be reconstructed from a single message string used to fall back to a bare
`OSError`, which matches nothing in `_RETRYABLE_COPY_ERRORS`. That was safe
only because of *where* redaction sat: `copy_resource_file` redacted strictly
on the way out, after its own `except` had classified, and #620 recorded in
that loop that redacting "any earlier" would quietly cut the retry budget to
a single attempt for exactly the authed downloads it protects.

Redacting the handle *is* earlier — it runs under that loop, on the read. So
the positional rule is replaced by a property of the rebuild:
`_rebuild_error_without_userinfo` rebuilds a transient failure it cannot
reconstruct as `RetryableCopyError`, which is how this tree says "transient"
(#934). Redaction can now happen anywhere without changing a retry decision.

## Why scoped this way

The alternative considered in #1078 was to keep routing call sites through
`_read_fetch_file` one at a time. It was rejected on arithmetic: it cannot
express the held-handle, lazy or chunked sites at all, so it would have left
the log-writing tabix site leaking permanently while appearing to make
progress.

The objection to the wrapper — that the handle escapes to pysam and
pyBigWig, so the wrapper would have to forward `fileno`/`readinto`
faithfully or break backends that work today — was measured and found not to
hold. `open_tabix_file`, `open_vcf_file`, `open_fasta_file` and
`open_bigwig_file` each hand those libraries a **url string** and never touch
`open_raw_file`; there is no `fileno()` or `readinto` call anywhere in
`core/gain`. For the same reason the wrapper is not in the score-scan hot
path, which reaches its data through pysam and pyBigWig by url. The one
genuine per-call path is `_RawSeekSequence.fetch`'s seek-and-read on its held
handle.

The type demotion is the accepted cost, and it is bounded in a way worth
stating: `_error_without_userinfo` rebuilds **only** when the message
actually carries userinfo, returning the original object otherwise. Every
unauthenticated deployment — which is all of them today — keeps its
exception types, its tracebacks and its exception chains untouched. The
demotion is paid exactly by the configuration it protects.

## Consequences

- A new caller of `open_raw_file` is redacted without knowing this rule
  exists. That is the point: the rule stopped being something each call site
  has to remember.
- An error surfacing from an authed GRR read may be an `OSError` or a
  `RetryableCopyError` rather than the transport's own type. Code that
  branches on a transport exception type must not assume it survives an
  authed read.

  The type tests this has to clear fall into two groups. The larger one is
  `except FileNotFoundError` — sixteen sites, reading a missing file as an
  answer rather than a fault (`get_loaded_manifest`, the score and
  reference-genome implementations, the gene-set statistics readers). All of
  them are safe *by construction rather than by luck*: `FileNotFoundError`
  can be rebuilt from a single message string, so it round-trips through
  `type(exc)(redacted)` and never reaches the fallback at all. Only errors
  that cannot be so rebuilt are demoted, and the two tests such an error can
  meet — `RESOURCE_ERRORS` in `report_resource_failure`, and
  `_RETRYABLE_COPY_ERRORS` — are both satisfied by the rebuild.
- The wrapper sits between every raw-file consumer and fsspec. A consumer
  needing an attribute the wrapper neither names nor can delegate would
  break; none does today, and the delegation is deliberately total.
- Write-mode opens through `open_raw_file` go through the same wrapper, as
  does the staged publish sink, so a write that fails on release is redacted
  like a read.

  That is *not* the same as saying every write in the protocol is covered.
  `FsspecReadWriteProtocol` calls `self.filesystem.open(...)` directly in
  around nine internal places — the DVC and gitignore probes, the resource
  scan, the `.state` documents, the download's local temp file. None is
  wrapped, and none is reachable with a credential today: only that class
  makes those calls, and `build_fsspec_protocol` builds it solely for
  `file://`, `s3://` and `memory://`.

  So the choke-point property is structural at the *protocol-selection*
  layer, not at the filesystem layer. A future write-capable scheme that
  admits url userinfo would reopen every one of those sites silently, and no
  test here would catch it. That this is a live rather than theoretical
  distinction is already recorded in the tree: `_download_resource_file`
  redacts its "destination file not created" path by hand precisely because
  `tmp_filepath` "derives from the credential-bearing fetch url on a write
  protocol over an authed store" (gain#620). Closing those sites means
  wrapping at the filesystem rather than the protocol, which is a larger
  change than this one and was not made.
