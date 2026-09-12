# 23. Url credentials are redacted at the handle, and redaction preserves retryability

**Status:** accepted
**Date:** 2026-09-01
**Issues:** [#620](https://github.com/iossifovlab/gain/issues/620),
[#629](https://github.com/iossifovlab/gain/issues/629),
[#1017](https://github.com/iossifovlab/gain/issues/1017),
[#1058](https://github.com/iossifovlab/gain/issues/1058),
[#1078](https://github.com/iossifovlab/gain/issues/1078),
[#1106](https://github.com/iossifovlab/gain/issues/1106),
[#1314](https://github.com/iossifovlab/gain/issues/1314),
[#1333](https://github.com/iossifovlab/gain/issues/1333),
[#1339](https://github.com/iossifovlab/gain/issues/1339),
[#1398](https://github.com/iossifovlab/gain/issues/1398)

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
`core/gain`. (*Read the gain#1314 amendment with this paragraph.* It is
correct that those four need no handle wrapper, and that is exactly why the
credential they hand over went unexamined until gain#1314.) For the same reason the wrapper is not in the score-scan hot
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

## Amendment — gain#1106: the handle is not the only escape

**Date:** 2026-09-09

"Redaction is a property of the handle" is a statement about *reads and
writes*, and it was read more broadly than it can carry. A credential can
also escape from a message GAIn composes **before any handle exists**, where
there is nothing for `_RedactingFile` to wrap.

`FsspecReadOnlyProtocol.open_raw_file` refused a write against a read-only
protocol by interpolating the resource file url — which resolves through this
class's `get_resource_url` override to the credential-bearing `_fetch_url` —
into an `OSError` message. The refusal fires on the mode alone, before the
open, so the wrapper never saw it. `OSError` is in `RESOURCE_ERRORS`, so
`report_resource_failure` logged that text at ERROR.

Redacted by hand with `_strip_url_userinfo`, the way `_download_resource_file`
already redacts its "destination file not created" path, and for the same
reason. The Decision is unchanged; the boundary is narrower than it read.

**The rule this leaves.** Redaction is automatic for anything that travels
*through* a handle. Anything GAIn interpolates into a message itself is the
call site's own responsibility, and the discriminator is the url's
provenance: `_fetch_url`-derived (`get_resource_url`,
`get_resource_file_url`, `get_file_url`, `_get_file_url`) carries the
credential; `self.url`-derived (`get_url`, `get_public_url`, the resource
scan) is credential-free by construction and needs nothing.

An audit against that rule at the time of gain#1106 found the write refusal
to be the only unredacted site that is *reachable* with a credential. It is
not the only one that exists: `FsspecReadWriteProtocol` subclasses this class,
inherits the tainted `get_resource_url`, and interpolates a
`get_resource_file_url`-derived path unredacted in its publish-mode refusal
and its corrupt-publish report. Those are safe for the same reason the
Consequences above give — `build_fsspec_protocol` builds that class only for
`file://`, `s3://` and `memory://` — which is a property of protocol
selection, not of the sites themselves.

**Left open.** The paragraph above arguing that `open_tabix_file`,
`open_vcf_file`, `open_fasta_file` and `open_bigwig_file` need no wrapper
because they hand their library a url string is correct about the *handle*,
and is exactly why nobody looked at what those libraries then do with it.
pysam embeds the full credentialed url in its own `OSError`, and htslib and
pyBigWig write it to stderr. Tracked as gain#1314; addressed in the amendment
below.

Nor is the hand-remembering itself. Message interpolation is the one mechanism
in this family with no structural guard — twelve `_strip_url_userinfo` calls
that a thirteenth site can silently forget, which is how gain#1106 arose. A
type cannot carry the taint (`os.path.join` drops it, and `str()` on the way
to `yarl`/fsspec would strip the credential before it reached the wire), so
the remedy is an AST fence over the provenance rule above. Tracked as
gain#1318.

## Amendment — gain#1314: a url handed to a library is the third escape

**Date:** 2026-09-09

The two escapes named so far are a handle (wrapped) and a message GAIn
composes (redacted by hand). There is a third: a credential-bearing url
handed to a **third-party library**, which then puts it in its own error text
and on its own stderr. GAIn wraps nothing and composes nothing there, so
neither existing remedy reaches it.

All four opens leak, `open_fasta_file` included — the one the issue left
unmeasured, because its "bgzip index is required" guard raises before any url
is built and so hides the leak from any test that arranges a resource with no
files. Three of them raise an `OSError` subclass, which is in
`RESOURCE_ERRORS`, so the credential lands in the log at ERROR.

**Decided.** The three pysam opens route their *remote* library call through
`_open_htslib_file`, which closes both channels:

- the raised error goes through `_run_redacting_userinfo`, as everything else
  in this family does; and
- an authed open is bracketed at `pysam.set_verbosity(0)`, which is what
  silences htslib's own `[E::…]` line on fd 2. The module already sets level
  1 at import — "errors only", which is precisely why that line still
  printed.

**The silencing is scoped to urls that carry userinfo**, and that scoping is
the load-bearing part. Silencing htslib costs its account of *why* an open
failed, so it is spent only where it buys something: an unauthed GRR — every
deployment today — keeps its diagnostics. This is the same bargain the
Consequences above strike for the type demotion, and for the same reason.

**`open_bigwig_file` gets the redaction and not the bracket.** libBigWig is
not htslib: `set_verbosity` does not reach it, and it has no verbosity control
of its own — its `[urlOpen]` line goes to fd 2 through its own `fprintf`. Its
exception, unlike pysam's, carries no url and is a `RuntimeError`, which is
not in `RESOURCE_ERRORS` and so never reaches the ERROR log. So its remaining
leak is stderr-only and strictly less severe, and closing it needs an
fd-level capture with no precedent in this tree. Split as gain#1333, and
closed there by the amendment below. The
redaction wrapper is still applied there, because "the message is clean" is a
property of that library's current wording rather than a guarantee, and it
costs nothing: a message with no userinfo is propagated untouched.

`open_fasta_file` wraps only its remote branch: the `file` scheme reduces the
url to a bare filesystem path before pysam sees it, so no userinfo can reach
that call.

**What this does NOT cover, and the scope of the word "credential" here.**
Everything above is about `user:pass@` **userinfo**, which is what
`_strip_url_userinfo` and therefore `_url_carries_userinfo` recognise. An
**s3** GRR is a different shape: `_get_file_url` hands pysam a *presigned*
url, whose signature lives in the query string. The predicate is False for it,
so there is no verbosity bracket, and the redactor would not strip those
parameters even if there were. A failing s3 open can therefore still put a
time-limited signature into the ERROR log. Closing that needs a second notion
of what a credential in a url looks like, not a wider application of this one.
Tracked as gain#1339 — **and closed by the amendment below.**

(*The gain#1333 amendment defines that second notion and applies it on the
bigwig path only — the paragraph above still describes the three pysam opens
exactly. Note also that `X-Amz-Credential`/`X-Amz-Signature` names the SigV4
spelling; the default s3fs produces is SigV2.*)

**The guard is scoped to the open, and the returned object is not.** The
pysam object keeps the credential-bearing url — `TabixFile.filename` returns
it verbatim — and does its real data I/O later, outside all three remedies.
That is the shape that cost this tree #1017 and then #1058, so it deserves an
explicit answer rather than silence.

It is accepted, on a measurement: htslib's *read*-path errors do not carry the
url. Probed two ways against a real authed server — killing the server after
the open, and answering every later request with a 500 — a far-region `fetch`
gives `ValueError: iteration failed (error code -2)` and an
`[E::hts_itr_next] Failed to seek` on stderr, neither carrying the url. No
site in `core/gain` interpolates `pysam_file.filename` either. So the exposure
is latent rather than live, and it rests on that measurement: an htslib
upgrade that starts naming the file in a read error would reopen it.

The wrapper that would close it structurally is rejected for the reason this
ADR already gives for keeping `_RedactingFile` off this path. A redacting
pysam proxy would break five load-bearing `isinstance` checks (in
`table_tabix`, `table_vcf`, and a refusal in `genomic_scores.base` documented
around one of them) and would put a Python wrapper on `fetch` — the
score-scan hot path this ADR deliberately keeps wrappers out of.

**The rule this leaves.** Two questions decide the remedy, in order. *Is
there a handle GAIn holds?* If so the handle redacts, and the call site needs
to know nothing. If not, *where did the url come from?* — the same
`_fetch_url`-provenance test the gain#1106 amendment states: a
`_fetch_url`-derived url must not reach a message unredacted, whether GAIn
interpolates it (redact at the site) or hands it to a library (wrap the
call).

What the third case adds is that wrapping the call is not sufficient by
itself, because the library also owns diagnostic channels GAIn does not
raise: htslib's stderr is silenced, libBigWig's is silenced by a different
mechanism (the gain#1333 amendment), and the returned handle's later I/O is
neither (above). So a new url-taking backend must be checked channel by
channel, and this ADR is where the answer for each is recorded — the coverage
claim lives in that list, not in the rule.

## Amendment — gain#1333: the descriptor, and a second shape of credential

**Date:** 2026-09-10

The gain#1314 amendment closed `open_bigwig_file`'s exception channel and
left its stderr channel open, on the reasoning that the remedy had no
precedent here. This closes it, and in doing so has to answer a question the
htslib half never faced: *what counts as a credential in a url.*

**The descriptor, because nothing above it works.** libBigWig writes with its
own `fprintf` from C, so it holds fd 2 directly. Rebinding `sys.stderr` or
using `contextlib.redirect_stderr` swaps a Python object the C code never
consults: they suppress nothing while appearing to work, which makes a test
written against either of them pass identically on unfixed code. So
`_open_libbigwig_file` points fd 2 at the null device for the duration of the
open and restores it in a `finally`. It is the first fd-level suppression in
this tree.

It is a separate function from `_open_htslib_file` because *neither* half of
that one transfers. The redaction has nothing to act on — pyBigWig's
exception carries no url — and `pysam.set_verbosity` is htslib's knob, which
reaches another library not at all. Sharing the name would suggest a
generality that does not exist.

**The gate is wider than the htslib one, deliberately.**
`_url_carries_credential` is userinfo **or a query string**, where
`_open_htslib_file` tests userinfo alone. The query string is what an s3
GRR's presigned url carries, and both alternatives to testing it are wrong:

- **A parameter-name list is wrong.** s3fs signs with **SigV2** unless told
  otherwise — `AWSAccessKeyId`, `Signature`, `Expires` — and yields the
  `X-Amz-*` set only under an explicit `signature_version` of `s3v4`, which
  GAIn does not pass. A gate keyed on `X-Amz-*` names would therefore miss
  the shape GAIn produces by default. Measured against live MinIO with
  `s3fs==2026.3.0`.
- **The scheme is wrong the other way.** `sign()` on an anonymous filesystem
  answers a **bare** url, with no query string and no credential in it. A
  "suppress whenever the scheme is s3" gate would spend that GRR's
  diagnostics for nothing.

The cost of testing the query string whole is that an http GRR whose base url
legitimately carried one would be read as credentialed and lose its libBigWig
diagnostics. Accepted: it costs detail, never correctness, and no GRR in this
tree is shaped that way.

**This does not close gain#1339, and must not be made to.** The wider notion
of "credential" is applied *only* on this path. The three pysam opens still
gate on userinfo alone, so a failing s3 open still puts a time-limited
signature into the ERROR log through them — the more severe channel of the
two, since the bigwig leak was never more than stderr.

Handing `_url_carries_credential` to `_open_htslib_file` as well would look
like finishing the job and would in fact make things worse. gain#1339 is two
separable pieces: this predicate, and a *redactor* that can strip a
query-string signature out of a message where pysam and htslib delimit the
embedded url differently. Widening only the gate would silence htslib's
diagnostics for every failing s3 open while leaving the exception text — the
part that reaches the log at ERROR — still carrying the signature. That
trades away the diagnosis and buys nothing. The predicate is deliberately
left as a module-level function so gain#1339 can reuse it once it has the
redactor to go with it.

**The bargain is gain#1314's; the caveat is NOT.** The suppression is spent
only where it buys something, so an uncredentialed url — every deployment
today — keeps libBigWig's account of why the open failed. That half carries
over unchanged.

The concurrency caveat does not, and the difference is why this suppression
needs a lock where the verbosity bracket does not have one. Both touch
process-global state, but the consequences are not comparable: a lost
verbosity restore strands htslib at "silent" and costs detail, while a lost
fd 2 restore leaves the process writing every subsequent log line, traceback
and library diagnostic into the null device, permanently, with nothing raised
to say so. Two overlapping suppressions do exactly that — the second thread
saves the first one's null device as its "previous" fd 2 and restores *that*.

And it is reachable. The claim that no bigwig open is driven from a thread
pool is true only of `core`: `web_api`'s pipeline cache loads pipelines on a
`ThreadedTaskExecutor` (8 loaders by default, alongside 16 annotation workers
under its gunicorn settings), and opening a pipeline opens the bigwig tables
in it. So `_STDERR_SUPPRESSION_LOCK` serialises the save/redirect/restore
sequence. The cost is that concurrent credentialed bigwig opens serialise;
accepted, because it is the open rather than the read, so it is not the
score-scan hot path this ADR keeps wrappers out of.

Serialising buys correctness, not isolation: while one thread has fd 2
pointed at the null device, another thread's stderr goes there too. That is
inherent to a process-global descriptor, and it is the reason the suppression
is scoped to a single library call on a credential-bearing url rather than to
anything wider.

Failures on **reads** through an already-open bigwig handle are out of scope
here, as the pysam equivalent is above, and for a weaker reason: unlike the
htslib read path, libBigWig's was not measured. If it names urls too, that is
a new finding rather than a widening of this one.

## Amendment — gain#1339: the redactor the wider predicate was waiting for

**Date:** 2026-09-10

The gain#1314 amendment named the shape it did not cover: an **s3** GRR is
handed a *presigned* url, whose credential is a query parameter rather than
`user:pass@` userinfo. The gain#1333 amendment then built the predicate that
recognises it, `_url_carries_credential`, and deliberately applied it on the
libBigWig path only — saying in terms that handing it to `_open_htslib_file`
without a redactor to go with it would trade away htslib's diagnostics and
buy nothing, because the exception text, which is the channel that reaches
the ERROR log, would still carry the signature.

This is that redactor, and the gate widening it makes safe.

It was **live, not latent**: htslib reads a presigned s3 url perfectly well
(measured — a presigned tabix open fetches rows), and a failing open is
precisely when the url gets printed.

**Decided.**

- `_strip_url_query` drops the `?query` of any url embedded in a string.
- `_strip_url_credentials` composes it with `_strip_url_userinfo` and is what
  every redaction path now runs, so a url carrying both shapes loses both.
- `_open_htslib_file` now gates on `_url_carries_credential` — the same
  predicate `_open_libbigwig_file` uses, reused rather than copied, which is
  why gain#1333 left it a module-level function.

The **order** inside `_strip_url_credentials` is load-bearing and is not
symmetry: userinfo goes first, because a password may itself contain `?`, and
stripping the query first cuts `https://alice:p?w@host/f.gz` down to
`https://alice:p` — half the password kept and the host, which is what says
*which* GRR failed, gone.

`_strip_url_userinfo` itself is unchanged and stays narrower, because the
*display*-url callers want exactly it: a display url keeps its query string,
which on a stored (unsigned) url is part of the address rather than a secret.

Three helpers were **renamed** with the concept, so the sections above name
functions that no longer exist under those names:
`_run_redacting_userinfo` → `_run_redacting_url_credentials`,
`_error_without_userinfo` → `_error_without_url_credentials`, and
`_rebuild_error_without_userinfo` → `_rebuild_error_without_url_credentials`.
Their behaviour is otherwise unchanged — in particular the rebuild still
preserves retryability, which is half of what this ADR decided.

**The whole query string goes, not a list of parameter names.** botocore emits
two presigned spellings — `AWSAccessKeyId`/`Signature`/`Expires` and
`X-Amz-Credential`/`X-Amz-Signature`/friends — and which one a deployment gets
is a property of its endpoint and region, not of anything GAIn configures.
GAIn passes no `signature_version`, and the default against a custom endpoint
is the *older* of the two. A redactor written from the newer spelling alone —
which is the one the issue text quoted — passes the common shape straight
through. Both are pinned in the tests for that reason. Nothing downstream
reads a query string off a GRR file url, so there is no diagnostic value to
weigh against dropping it. This is the same argument the gain#1333 amendment
makes for testing the query whole rather than by name, reached independently
from the redactor's side.

**Where the presigned shape can reach, channel by channel.** The issue asked
whether it reaches any interpolation site beyond the four library opens. It
does not. A presigned url exists only as the return of `_get_file_url`, and
that method has exactly four callers — `open_tabix_file`, `open_vcf_file`,
`open_fasta_file`, `open_bigwig_file`. Every other url in the tree derives
from `get_resource_file_url`/`_fetch_url`, and `_fetch_url_form` rebuilds
`scheme://netloc/path`, which cannot carry a query string at all.

That is what makes the handful of log lines still redacting an error message
with the narrow `_strip_url_userinfo` — two in `cached_repository` and two in
`fsspec_protocol` — safe today. They are safe by *reachability*, not because
narrower is what they want, so a future path that carries a presigned url to
one of them has to widen it. Tracked as gain#1370, and recorded here rather
than left to inference, because this section is where a coverage claim of
this ADR belongs.

**What this does NOT cover.** The returned handle's later reads are unchanged
(above). The htslib bracket is still **not serialised** while the verbosity
level is process-global (superseded by the gain#1360 amendment below, which
serialises it), and this amendment makes gain#1360 materially more
reachable rather than less: every s3 GRR is presigned, where before only a
url-authed GRR entered that bracket at all. The fd 2 equivalent is already
serialised on `_STDERR_SUPPRESSION_LOCK` (gain#1333); the verbosity one is
not, and a lock around a library open should be decided on its own evidence
rather than folded into a redaction fix.

**A deeper alternative, recorded because it was measured rather than
imagined.** htslib has S3 support compiled in and resolves credentials from
the same ambient chain botocore already uses, so handing it `s3://bucket/key`
instead of a signed url would mean no presigned url existed for the three
pysam opens at all — nothing to redact, and gain#1360 unreachable for s3. It
is not done here: it cannot help `open_bigwig_file` (libBigWig has no s3), it
splits authentication across two configuration surfaces that must be kept in
agreement, and it is currently proved only as far as the TLS handshake on
this host. gain#1371 took it further — it works in the `core` image once a CA
bundle sits at libcurl's compiled-in path — and declined it; the reasons are in
`.out-of-scope/htslib-native-s3.md`.

## Amendment — gain#1360: the verbosity bracket is serialised

**Date:** 2026-09-11

The gain#1339 amendment left the htslib verbosity bracket **not serialised**
and said the lock should be decided on its own evidence. This is that
evidence, and the lock.

**Two brackets, not one, and the second was never gated.** gain#1360 was
filed against `_open_htslib_file` alone, and rated latent rather than live
on the grounds that its bracket was gated on userinfo and no deployment
configures a url-authed GRR. That was a snapshot of gain#1333's knowledge.
`VCFGenomicPositionTable._load_vcf_header` has
bracketed its header open at verbosity 0 since long before any of this —
on every url, credentialed or not, because a header-only sidecar makes
htslib log a spurious `[E::idx_find_and_load]` while probing for an index
it does not ship — and it runs from the table's *constructor*, which `GenomicScore`
reaches while building, which `load_pipeline_from_yaml` reaches while
building the pipeline, which `web_api`'s pipeline cache runs on its
`ThreadedTaskExecutor` (8 loaders by default). Two pipelines that each carry
a VCF-backed score, built concurrently on a plain public GRR, are enough:
the second constructor saves the first one's 0 as its "previous" level and
restores it last, and htslib is silent for the life of the process. That is
reachable on gainweb as deployed, not latent. Reproduced at triage by
forcing the interleaving, for all three shapes: a userinfo url, a presigned
url, and the ungated header load.

**One bracket, one lock, re-entrant.** Both sites now go through a single
`_htslib_silenced()` context manager in the fsspec protocol module, which
takes `_HTSLIB_VERBOSITY_LOCK` around the save/lower/restore. It is an
`RLock`, and it has to be: `_load_vcf_header`'s bracket encloses
`open_vcf_file`, which for a credential-bearing url enters
`_open_htslib_file`'s bracket on the same thread. A plain `Lock` deadlocks
there — on every credentialed VCF score, which after gain#1339 is every VCF
score on a credentialed s3 GRR (an anonymous one signs nothing and never
enters the bracket) — and the nesting test pins that: it runs the
construction on a daemon thread with a bounded join and asserts the table
was actually built, so the regression reports as *that test's* failure
rather than a hang. (The stuck thread still owns the lock afterwards, so
later brackets in the same process block behind it — a test-run cost of
the regression, not something the test can release on another thread's
behalf.) With re-entry the inner bracket saves and restores 0, which is
harmless.

**The nesting could have been removed instead, and was not.** The
`[E::idx_find_and_load]` line that `_load_vcf_header` exists to silence is
written on the *unindexed* branch of `open_vcf_file` — the one a sidecar
that ships no index takes — so bracketing there would leave the header load
with no bracket of its own, and a plain `Lock` would do. It is not done
here because it re-scopes the silencing: every unindexed VCF open would
lose its diagnostics, not just the header load, and this amendment
serialises rather than re-scopes. It remains the cheaper shape if a future
change wants the lock non-reentrant.

**Separate from `_STDERR_SUPPRESSION_LOCK`.** fd 2 and the verbosity level
are independent globals; one lock for both would serialise credentialed
bigwig opens against VCF header loads and buy nothing for it.

**What it costs.** Credentialed tabix/VCF/fasta opens and *all* VCF header
loads serialise, each holding the lock across a network open — and, for a
header load on the caching protocol, across the sidecar's refresh and the
index resolution that `open_vcf_file` performs before the open, since the
bracket encloses the whole call. Accepted on the same grounds as the fd 2
lock: it is table construction and open, not the read, so the score-scan
hot path is untouched. The gates are unchanged — this amendment serialises,
it does not re-scope.

**Tests.** Forced interleavings, not races, in the shape gain#1333 set:
thread two is admitted only once thread one is known to be inside the
bracket and is made to leave last, and the assertion is on the consequence —
a later credential-free open still writes `[E::hts_open_format]` to fd 2 —
never on `set_verbosity` calls. One through `open_tabix_file` on a
credentialed url, one through `build_genomic_position_table` on a plain
filesystem VCF resource, one same-thread nesting on a credentialed url.
Each was mutation-proved: with the lock swapped for a plain `Lock` only the
nesting test goes red; with the lock removed only the two interleaving tests
do; and with the constructor made to raise before the inner bracket, the
nesting test goes red rather than passing on a thread that merely ended.

## Amendment — gain#1406: the header sidecar is read through a handle

**Date:** 2026-09-11

The gain#1360 amendment named "the cheaper shape if a future change wants
the lock non-reentrant" and did not take it. This is that change — by a
different route than the one named there, and a strictly better one.

**The second bracket had no reason to exist.** `_load_vcf_header` opened the
`*.header.vcf.gz` sidecar by name — `pysam.VariantFile(url)` — purely to
read `header.info`. The `[E::idx_find_and_load]` it silenced was htslib's
index probe on that by-name open, which a header-only sidecar cannot
satisfy. Read the sidecar's `##` lines through a **handle** instead —
`open_raw_file(…, compression="gzip")`, the route `TabixGenomicPositionTable`
already takes for its own header — and build the metadata with
`pysam.VariantHeader()` + `add_line(line)`, and no filename reaches htslib
at all: no probe, no line on fd 2, nothing to silence. Verified at triage
and pinned by test: the resulting `info` map is equal to the one the by-name
open produced across every `Number` shape `parse_vcf_scoredefs`
distinguishes (`1`, `R`, `.`, `0`/Flag), and fd 2 stays clean *without* a
bracket — with the by-name route restored unbracketed and the test's
"never reached `open_vcf_file`" assertion removed, the `capfd` assertion
alone goes red on exactly that line.

By this ADR's own rule (the gain#1314 amendment: *is there a handle GAIn
holds?*) that moves the header load from the third escape — a url handed to
a library, needing a bracket and a lock — to the first, which
`_RedactingFile` closes structurally with no help from the call site.

**What falls out.** One bracket left, `_open_htslib_file`'s, gated on
`_url_carries_credential` and wrapped around a bare pysam constructor that
enters no bracket of its own. Nothing nests, so `_HTSLIB_VERBOSITY_LOCK` is
a plain `Lock`. The lock is now paid **only by credentialed opens** — the
bargain `_STDERR_SUPPRESSION_LOCK` already makes — where the gain#1360
amendment charged it to every VCF header load in the process, on a plain
public GRR, each holding it across the sidecar's refresh and index
resolution. The table module no longer imports a private name from the
protocol module. The same-thread nesting test and the forced overlap of two
header loads are deleted with the mechanism they pinned; the forced overlap
through `open_tabix_file` on a credentialed url stays, for the bracket that
survives.

**The narrower alternative, and why not.** Keeping `VariantFile` and
bracketing only `open_vcf_file`'s unindexed branch — the shape the
gain#1360 amendment named — would also have un-nested the brackets, at the
price of silencing every unindexed VCF open. The handle read silences
nothing anywhere: what htslib's header parser writes at the verbosity this
module sets — `[E::bcf_hdr_parse_line]` for a line it cannot parse — now
reaches fd 2, where the old bracket discarded it. (Its
`[W::bcf_hdr_register_hrec]` warnings for a declaration missing `Number` or
`Type` are level-3 lines; neither route ever wrote them at level 1.)

**Behavioural delta.** The sidecar's bytes come through fsspec rather than
htslib's `hFILE` on the direct `http` and `s3` protocols — as the tabix base
class's header bytes already do; on the caching protocol both routes refresh
the file first. Two refusals, both `MalformedResourceError` (so the
statistics scan attributes them to the resource by type, as it does every
other refusal of a resource's own content) naming the resource, the sidecar
and the line. One is **stricter than the by-name open**: a line before
`#CHROM` that pysam cannot parse — a `##` line with no `=`, or a blank line
— was logged by htslib (silenced) and *skipped*; `add_line` raises a bare
`ValueError("Invalid header line")` and the constructor re-raises it named.
The other reproduces what the by-name open refused as `ValueError: invalid
file`: an empty sidecar, or one with no `##` line at all — without the
guard, the handle read would return an empty header from an empty file. (A
sidecar with `##fileformat` but no `##INFO` passes both routes with no
scores; neither refuses it.) Neither shape occurs in a published sidecar
(the ClinVar and dbSNP ones under the seqpipe GRRs were checked; both
routes produce equal `info` maps on each).

**Cost.** `VariantHeader.add_line` re-syncs the header on every call, ~27 µs
a line against ~0.7 µs a line for htslib's one-shot parse: ~+2 ms per VCF
table construction on the 84-line dbSNP sidecar, once per pipeline build,
on the loader pool, not on the read path. On the caching protocol the net
is smaller still, since the handle read refreshes the sidecar twice where
the by-name open refreshed it four times (its own, plus the probe for the
index it never has). Feeding htslib the redacted bytes through a pipe fd
would keep both invariants (no filename, no bracket) at a tenth of the cost,
at the price of the named per-line refusal; not taken at this size.

## Amendment — gain#1398: the presigned url has a lifetime, and it is the handle's

**Date:** 2026-09-12

The gain#1339 amendment describes the presigned url an s3 GRR hands pysam
without saying how long it is good for. It was good for **100 seconds** —
s3fs's default `expiration`, which `_get_file_url` never overrode — and
that number is the handle's lifetime, not the open's: pysam holds the
signed string and htslib re-requests it with a `Range:` header on every
seek, so the first fetch after the signature lapsed that touched an unread
byte range failed. Measured against the MinIO fixture: a `TabixFile`
presigned for 2 s fetches, waits, and then raises
`ValueError: iteration failed (error code -2)` on a far region; the same
open presigned for 600 s fetches it. Nothing in the suite held an s3-backed
handle for two minutes, and the production definitions in this stack are
cached — the caching protocol downloads the file and opens it by local
path, so no signed url reaches pysam there — which is why an uncached s3
GRR could break this way unnoticed.

**The lifetime is now seven days**, `S3_PRESIGN_EXPIRATION_SECONDS`, passed
explicitly on every presign `_get_file_url` performs — the data file and
each index, on every pysam open and on the bigwig open. Seven days is the
most a SigV4 signature allows; S3 refuses a longer `X-Amz-Expires` at
request time (botocore does not validate it). The older SigV2 spelling —
the one botocore emits against a custom endpoint such as MinIO, per the
gain#1339 amendment — has no such ceiling, and gets the same seven days
anyway, so an s3 GRR's handles live equally long whichever signature its
endpoint negotiates. The cap is the widest SigV4 allows rather than a
choice an operator tunes, and no configuration surface is added.

**What that buys and what it costs.** A handle on an uncached s3 GRR is
good for a week after its open; every open re-signs, so a fresh handle
starts a fresh week. A handle older than that still fails on its next
unread range, and the remedy is to reopen — the cap cannot be removed while
pysam is handed a signed string, because there is no hook to re-sign an
open handle. Removing it would mean handing htslib `s3://` and letting it
sign per request, which gain#1371 proved workable and declined
(`.out-of-scope/htslib-native-s3.md`). The cost is a longer-lived bearer
credential in the url; the redactors and the brackets this ADR describes
are what make that acceptable, and none of them changes — the signed url's
shape is the same, only its `Expires` / `X-Amz-Expires` moves.
