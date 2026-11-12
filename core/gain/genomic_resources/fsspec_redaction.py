"""Credential redaction and handle opening for the fsspec GRR protocols."""
from __future__ import annotations

import os
from collections.abc import Callable, Generator
from contextlib import contextmanager
from threading import Lock
from typing import IO, Any
from urllib.parse import urlparse

import pysam

from gain.genomic_resources.fsspec_errors import (
    _RETRYABLE_COPY_ERRORS,
    RetryableCopyError,
)
from gain.utils.url_redaction import (
    strip_url_credentials,
    strip_url_userinfo,
)

# Silence the spurious "[W::hts_idx_load3] The index file is older than the
# data file" warning that htslib emits when a tabix/VCF index has an older
# mtime than its data file. In our GRR workflow this is benign: both the
# caching protocol and DVC download index and data files in parallel, and
# the smaller index typically lands first. Level 1 (errors only) keeps real
# htslib errors visible while suppressing notices (3) and warnings (2).
pysam.set_verbosity(1)


def _strip_netloc_userinfo(netloc: str) -> str:
    """Return a network location with any ``user:pass@`` userinfo removed.

    The authority (``host[:port]``) is kept verbatim — case, port and IPv6
    brackets are preserved — because only the userinfo carries the secret. A
    netloc without ``@`` is returned unchanged. Splitting on the LAST ``@`` is
    correct for well-formed urls: the host part never contains an unencoded
    ``@`` (a literal ``@`` inside userinfo must be percent-encoded as ``%40``).
    """
    at_index = netloc.rfind("@")
    if at_index == -1:
        return netloc
    return netloc[at_index + 1:]


def _display_url(url: str) -> str:
    """Return the credential-free ``scheme://netloc/path`` form of a url.

    One definition of a protocol's display identity, used both to derive
    ``self.url`` and to decide whether a rebuild asking for the default
    ``public_url`` is asking for the incumbent's (#514).
    """
    parsed = urlparse(url)
    scheme = parsed.scheme or "file"
    return f"{scheme}://{_strip_netloc_userinfo(parsed.netloc)}{parsed.path}"


def _fetch_url_form(url: str) -> str:
    """Return the credential-BEARING ``scheme://netloc/path`` form of a url.

    ``_display_url``'s counterpart: identical to it for a userinfo-free url,
    and the form every remote read must derive from when the url does carry
    ``user:pass@`` (see ``FsspecReadOnlyProtocol._fetch_url``). It is also the
    form the protocol memo is keyed on, so a caller that passes a bare
    ``/abs/path`` and one that passes its ``file:///abs/path`` spelling name
    one protocol -- and a pickle, which carries the ``file://`` form, lands
    back on the instance it came from (#514).
    """
    parsed = urlparse(url)
    scheme = parsed.scheme or "file"
    return f"{scheme}://{parsed.netloc}{parsed.path}"


def _rebuild_error_without_url_credentials(
        exc: BaseException, redacted: str) -> BaseException:
    """Rebuild ``exc`` carrying the ``redacted`` message instead of its own.

    Return a fresh exception of ``exc``'s type so it can propagate/log
    without leaking the secret its own message carries; fall back for a type
    that cannot be reconstructed from a single message string.

    That fallback loses the exception's identity, and with it any decision a
    caller makes by type. The one such decision in the tree is the download
    loop's ``_RETRYABLE_COPY_ERRORS`` classification, so it is preserved
    explicitly: a transient failure stays transient across redaction.

    Before gain#1078 that was a positional guarantee -- ``copy_resource_file``
    redacted on the way out, strictly after its own ``except`` had classified
    (gain#620) -- and redacting "any earlier" was called out in that loop as
    something that would quietly cut the retry budget to a single attempt for
    exactly the authed downloads it protects. Redacting the handle itself IS
    earlier: it runs under the loop, on the read. Making retryability survive
    the rebuild is what lets it, and turns a rule about *where* redaction may
    sit into a property of the rebuild.
    """
    try:
        return type(exc)(redacted)
    except Exception:  # ruff: ignore[blind-except]  # pylint: disable=broad-exception-caught
        if isinstance(exc, _RETRYABLE_COPY_ERRORS):
            # ``RetryableCopyError`` is how this tree says "transient"
            # (gain#934): a new transient shape becomes retryable by
            # subclassing it and nothing else. A redacted rebuild of a
            # transient failure is such a shape.
            return RetryableCopyError(redacted)
        return OSError(redacted)


def _error_without_url_credentials(exc: BaseException) -> BaseException:
    """Return a credential-free rebuild of ``exc``, or ``exc`` if it has none.

    "Credential" is whatever :func:`strip_url_credentials` recognises --
    ``user:pass@`` userinfo and a presigned url's query string alike -- so a
    message carrying both loses both, in one rebuild.

    Returning ``exc`` itself is what every failure carrying no credential
    wants -- the common unauthenticated case -- because a rebuild always
    costs the traceback, and costs the exception its type too whenever it
    cannot be reconstructed from a message alone. Callers can therefore
    raise the result unconditionally, and identity is what tells them
    whether anything was redacted.
    """
    message = str(exc)
    redacted = strip_url_credentials(message)
    if redacted == message:
        return exc
    return _rebuild_error_without_url_credentials(exc, redacted)


def _run_redacting_url_credentials[T](fn: Callable[[], T]) -> T:
    """Run ``fn``, re-raising any failure with its url credentials stripped.

    On a fetch failure fsspec/aiohttp embed the credential-bearing fetch url
    verbatim in the raised message (e.g. ``FileNotFoundError(url)``). Rebuild
    the error with the url's credentials stripped and raise it OUTSIDE the
    ``except`` block so no credential-bearing ``__context__``/``__cause__``
    survives a chain walk. A failure whose message carries no credential (the
    common non-authed case) is propagated unchanged.

    Both shapes are stripped here, not just userinfo: an s3 GRR is handed a
    presigned url whose signature is a query parameter, and it reaches this
    guard by exactly the same routes (gain#1339).
    """
    reraise: BaseException | None = None
    try:
        return fn()
    except Exception as exc:
        reraise = _error_without_url_credentials(exc)
        if reraise is exc:
            # Nothing was redacted. Propagate in place, so the original
            # traceback and any chain it already carries survive.
            raise
    raise reraise


def _url_carries_userinfo(url: str) -> bool:
    """Whether ``url`` embeds ``user:pass@`` userinfo.

    Defined as "the narrow redactor would change it", so that this predicate
    and ``strip_url_userinfo`` cannot come to disagree about what counts as
    userinfo. It is the userinfo half of ``_url_carries_credential``, which is
    the predicate a caller asking "is there a secret in here" wants.
    """
    return strip_url_userinfo(url) != url


#: Serialises ``_htslib_silenced`` below, because htslib's verbosity level is
#: process-global and the save/lower/restore sequence is not atomic. Two
#: threads that overlap in it strand the level: the second saves the FIRST
#: one's 0 as its "previous" level and restores that on the way out, leaving
#: htslib silent -- every ``[E::...]`` line from every later tabix, VCF and
#: fasta open discarded -- for the rest of the process's life (gain#1360).
#:
#: This is not a theoretical pool. ``web_api``'s pipeline cache opens
#: pipelines on a ``ThreadedTaskExecutor`` (8 loaders by default), and
#: opening a pipeline brackets every credentialed tabix, VCF and fasta open
#: in it.
#:
#: Not re-entrant: the only bracket is ``_open_htslib_file``'s, around a
#: bare pysam constructor that enters no bracket of its own (ADR 0023's
#: gain#1406 amendment records the second bracket this used to nest with).
#:
#: Separate from ``_STDERR_SUPPRESSION_LOCK`` on purpose: fd 2 and the
#: verbosity level are independent globals, and sharing one lock would
#: serialise credentialed bigwig opens against credentialed htslib opens for
#: nothing.
#:
#: The cost is that credentialed tabix/VCF/fasta opens serialise, each
#: holding the lock across a network open. Accepted for the same reason as
#: the fd 2 lock: it is the open, not the read, so it is not the score-scan
#: hot path, and it is paid only by a GRR whose url carries a credential.
_HTSLIB_VERBOSITY_LOCK = Lock()


@contextmanager
def _htslib_silenced() -> Generator[None]:
    """Run the body at htslib verbosity 0, restoring the previous level.

    The previous level is restored rather than assumed: this module sets 1
    at import, but the application may have set something else since.
    Every ``pysam.set_verbosity(0)`` bracket in the open path goes through
    here, so that there is exactly one place that takes the lock (see
    ``_HTSLIB_VERBOSITY_LOCK`` for why it serialises).
    """
    with _HTSLIB_VERBOSITY_LOCK:
        saved_verbosity = pysam.set_verbosity(0)
        try:
            yield
        finally:
            pysam.set_verbosity(saved_verbosity)


def _open_htslib_file[T](url: str, open_: Callable[[], T]) -> T:
    """Run a pysam open that is handed the credential-bearing ``url``.

    ``open_tabix_file``, ``open_vcf_file`` and ``open_fasta_file`` hand pysam
    a url STRING rather than a handle, so ADR 0023's ``_RedactingFile`` never
    sees them -- the library owns the transport, and GAIn composes none of the
    messages, so redacting an f-string has nothing to act on either. Both
    channels the credential escapes through are closed here (gain#1314).

    The credential may be ``user:pass@`` userinfo or, for an s3 GRR whose url
    is presigned, a query-string signature. ``_url_carries_credential`` --
    gain#1333's predicate, shared rather than copied -- is what makes those
    one case rather than two, and gain#1339 is what made it safe to gate on
    here: the bracket alone would have silenced htslib for an s3 GRR while
    the rebuilt exception still carried the signature.

    The error pysam raises embeds the url verbatim, and it is an ``OSError``
    -- in ``RESOURCE_ERRORS``, so ``report_resource_failure`` writes its text
    to the log at ERROR, where it persists and is shipped.

    htslib *additionally* writes the url to fd 2 itself, which no redaction of
    the raised exception can reach, so a credential-bearing open is bracketed
    at verbosity 0. That silences htslib's own diagnostics for the duration of
    the open, which is a genuine loss of detail -- and it is spent only where
    it buys something. A GRR that hands over no credential keeps them,
    including an ANONYMOUS s3 one, whose ``sign`` returns the url with no
    query string at all. The scoping mirrors the type demotion of ADR 0023,
    likewise paid by exactly the configuration it protects.

    The bracket is ``_htslib_silenced`` -- see it for why it restores rather
    than assumes, and ``_HTSLIB_VERBOSITY_LOCK`` for why it serialises and
    which pool drives these opens (gain#1360). The equivalent hazard on fd 2
    is why ``_open_libbigwig_file`` takes ``_STDERR_SUPPRESSION_LOCK``.

    Named for htslib rather than for libraries in general because the
    silencing half is ``pysam``-specific: libBigWig has no verbosity control
    to bracket, so ``open_bigwig_file`` goes through ``_open_libbigwig_file``
    instead.

    What this does NOT cover, per ADR 0023's gain#1339 amendment: the
    returned handle's later reads. The s3 presigned shape, which the gain#1314
    amendment listed here as an open gap, is covered -- the predicate above
    recognises it and the redactor strips it. Read the gain#1339 amendment
    rather than the gain#1314 one for the current coverage claim.
    """
    if not _url_carries_credential(url):
        return _run_redacting_url_credentials(open_)
    with _htslib_silenced():
        return _run_redacting_url_credentials(open_)


def _url_carries_credential(url: str) -> bool:
    """Whether ``url`` carries credential material of any shape.

    Two doors, and a bigwig open has to close both. ``user:pass@`` userinfo
    is the one an authed http GRR comes through; an s3 GRR comes through the
    other, because ``_get_file_url`` presigns for that scheme and the bearer
    token then sits in the QUERY STRING with no userinfo at all.

    Answers True for any url carrying a ``?``, so an http GRR whose base url
    legitimately had a query string -- or a ``file`` GRR under a directory
    with a ``?`` in its name -- is treated as credentialed and loses its
    libBigWig diagnostics. Deliberate: it costs detail, never correctness.

    ADR 0023's gain#1333 amendment records why the test is the query string
    whole rather than a parameter-name list or the scheme, both of which were
    measured and rejected.
    """
    return _url_carries_userinfo(url) or bool(urlparse(url).query)


#: The descriptor libBigWig writes its diagnostics to. Spelled as the number
#: rather than ``sys.stderr.fileno()`` because it is the C-level descriptor
#: that must be replaced, and ``sys.stderr`` may be a Python object bound to
#: something else entirely -- under pytest's ``capfd``, or wherever the
#: application has rebound it.
_STDERR_FILENO = 2

#: Serialises the suppression below, because fd 2 is process-global and the
#: save/redirect/restore sequence is not atomic. Two threads that overlap in
#: it strand the descriptor: the second saves the FIRST one's null device as
#: its "previous" fd 2 and restores that on the way out, leaving the process
#: with no stderr at all for the rest of its life.
#:
#: This is not a theoretical pool. ``web_api``'s pipeline cache loads
#: pipelines on a ``ThreadedTaskExecutor`` (8 loaders by default), and
#: opening a pipeline opens the bigwig tables in it.
#:
#: The cost is that concurrent credentialed bigwig opens serialise. Accepted:
#: it is the open, not the read, so it is not the score-scan hot path, and it
#: is paid only by a GRR whose url carries a credential.
_STDERR_SUPPRESSION_LOCK = Lock()


def _open_libbigwig_file[T](url: str, open_: Callable[[], T]) -> T:
    """Run a pyBigWig open that is handed the credential-bearing ``url``.

    Runs ``open_`` with fd 2 pointed at the null device, so libBigWig's
    diagnostics -- which name the url verbatim: ``[urlOpen] Couldn't open
    <url>`` from the no-curl PyPI wheel, ``[bwOpen] bwg->cl is NULL (<url>)``
    from a curl-enabled build -- go nowhere. Only a url that carries a
    credential is suppressed; anything else keeps its diagnostics. A url
    with no credential is passed to ``_run_redacting_url_credentials`` and
    nothing else, so the common path costs no syscalls.

    It has to be the file descriptor, not ``sys.stderr`` or
    ``contextlib.redirect_stderr``: libBigWig writes with its own ``fprintf``
    from C, so it holds fd 2 directly and never consults the Python object
    those rebind.

    Serialised on ``_STDERR_SUPPRESSION_LOCK``, without which two concurrent
    opens strand fd 2 at the null device for the life of the process. What
    the lock does not buy is isolation -- while it is held, another thread's
    stderr goes to the null device too -- which is why the suppression stays
    as narrow as one library call.

    ADR 0023's gain#1333 amendment records why the gate is wider than
    ``_open_htslib_file``'s, why this is a separate helper, and what the
    serialisation costs.
    """
    if not _url_carries_credential(url):
        return _run_redacting_url_credentials(open_)
    with _STDERR_SUPPRESSION_LOCK:
        try:
            saved_stderr = os.dup(_STDERR_FILENO)
        except OSError:
            # fd 2 is closed. There is no diagnostic to suppress and nothing
            # to put back, so the open runs unsuppressed rather than failing
            # -- a read that worked before this guard existed must not start
            # raising because of it.
            return _run_redacting_url_credentials(open_)
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(devnull, _STDERR_FILENO)
            finally:
                os.close(devnull)
            return _run_redacting_url_credentials(open_)
        finally:
            os.dup2(saved_stderr, _STDERR_FILENO)
            os.close(saved_stderr)


#: Redacted I/O operations that are NOT on every handle, and so must be
#: mirrored from the wrapped object rather than declared. ``readall`` and
#: ``read1`` are absent from fsspec's ``AbstractBufferedFile`` (and so from
#: ``S3File``); ``LocalFileOpener`` additionally has no ``readinto`` or
#: ``readinto1``.
#:
#: Declaring one of these as a method would make ``hasattr`` answer True for
#: a handle that does not have it, and consumers feature-detect: pandas
#: probes for ``read1`` when choosing a read path, takes it, and the
#: forwarding call then dies with ``AttributeError`` on the inner handle. A
#: wrapper must not change what the thing it wraps appears able to do.
_OPTIONAL_REDACTED_OPS = frozenset({
    "readall", "readinto", "readinto1", "read1",
})


def _redacting_call(bound: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap one bound method of an inner handle in the redaction guard."""
    def call(*args: Any, **kwargs: Any) -> Any:
        return _run_redacting_url_credentials(lambda: bound(*args, **kwargs))
    return call


class _RedactingFile:
    """A file handle whose failures carry no url credentials.

    ``_open_fsspec_file`` redacts the open. Everything the caller then does
    with the handle -- ``read``, a bounded ``read(n)``, ``readline``,
    iteration, ``seek`` -- used to run unwrapped, so on an authed GRR a
    failure mid-stream surfaced the credential-bearing fetch url verbatim
    (gain#1078). Wrapping the handle is what reaches those: the alternative,
    routing each caller through ``_read_fetch_file``, reads the whole file
    and so cannot serve the callers that hold the handle for random access,
    iterate it lazily, or read it in chunks on purpose.

    Transparent by construction: every attribute this class does not name is
    delegated, because the handle escapes to ``gzip.open``, ``pandas``,
    ``json.load`` and the gene-set and gene-model readers, which reach for
    ``mode``, ``name``, ``closed`` and friends.

    Transparency includes what the handle appears *unable* to do. The
    operations in ``_OPTIONAL_REDACTED_OPS`` are redacted through
    ``__getattr__`` rather than declared here, so a handle without them still
    answers ``hasattr`` False -- see that constant for what goes wrong
    otherwise.

    See ADR 0023, which records why redaction belongs on the handle rather
    than at each of the 33 call sites that read one.
    """

    def __init__(self, inner: IO) -> None:
        self._inner = inner

    def read(self, *args: Any, **kwargs: Any) -> Any:
        return _run_redacting_url_credentials(
            lambda: self._inner.read(*args, **kwargs))

    def readline(self, *args: Any, **kwargs: Any) -> Any:
        return _run_redacting_url_credentials(
            lambda: self._inner.readline(*args, **kwargs))

    def readlines(self, *args: Any, **kwargs: Any) -> Any:
        return _run_redacting_url_credentials(
            lambda: self._inner.readlines(*args, **kwargs))

    def seek(self, *args: Any, **kwargs: Any) -> Any:
        # Seeking a remote file is I/O: fsspec's cached readers fetch the
        # block the new offset lands in.
        return _run_redacting_url_credentials(
            lambda: self._inner.seek(*args, **kwargs))

    def tell(self, *args: Any, **kwargs: Any) -> Any:
        return _run_redacting_url_credentials(
            lambda: self._inner.tell(*args, **kwargs))

    def write(self, *args: Any, **kwargs: Any) -> Any:
        return _run_redacting_url_credentials(
            lambda: self._inner.write(*args, **kwargs))

    def flush(self, *args: Any, **kwargs: Any) -> Any:
        return _run_redacting_url_credentials(
            lambda: self._inner.flush(*args, **kwargs))

    def writelines(self, *args: Any, **kwargs: Any) -> Any:
        return _run_redacting_url_credentials(
            lambda: self._inner.writelines(*args, **kwargs))

    def truncate(self, *args: Any, **kwargs: Any) -> Any:
        return _run_redacting_url_credentials(
            lambda: self._inner.truncate(*args, **kwargs))

    def close(self, *args: Any, **kwargs: Any) -> Any:
        # A write handle does its store round trip on the way out, so a close
        # is as able to surface the fetch url as any read. Every write site in
        # this tree reaches that through ``__exit__`` rather than here, which
        # is why ``__exit__`` is wrapped too and not merely delegated.
        return _run_redacting_url_credentials(
            lambda: self._inner.close(*args, **kwargs))

    def __iter__(self) -> _RedactingFile:
        # Special methods are looked up on the TYPE, never through
        # ``__getattr__``, so without these two ``for line in handle`` --
        # how the tabular and gene-set readers consume a resource file --
        # raises ``TypeError: not iterable`` instead of delegating.
        return self

    def __next__(self) -> Any:
        return _run_redacting_url_credentials(lambda: next(self._inner))

    def __enter__(self) -> _RedactingFile:
        # The inner handle's own ``__enter__`` answers itself -- that is what
        # every file object does -- and the ``with`` body must be handed THIS
        # object rather than that one, or every read in the body would run
        # unredacted and the wrapper would buy nothing.
        _run_redacting_url_credentials(self._inner.__enter__)
        return self

    def __exit__(self, *exc_info: Any) -> Any:
        # Wrapped, not delegated: this is where a ``with`` block releases the
        # handle, and it is the path every write site in this tree actually
        # takes -- ``with ... as outfile:``, never a bare ``close()``. The
        # return value decides whether the block's own exception is
        # suppressed, so it must be passed through untouched.
        return _run_redacting_url_credentials(
            lambda: self._inner.__exit__(*exc_info))

    def __getattr__(self, name: str) -> Any:
        # ``_inner`` is bound in ``__init__`` and so never reaches here in
        # normal use. It is named explicitly anyway: without this, an
        # instance that has not run ``__init__`` -- one built by
        # ``__new__``, or mid-unpickle -- turns every attribute access into
        # unbounded recursion instead of an ``AttributeError``.
        if name == "_inner":
            raise AttributeError(name)
        attr = getattr(self._inner, name)
        if name in _OPTIONAL_REDACTED_OPS and callable(attr):
            return _redacting_call(attr)
        return attr
