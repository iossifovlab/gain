"""Copy and publish errors and constants of the fsspec GRR protocols."""
from __future__ import annotations

import asyncio
from typing import NamedTuple

import fsspec.exceptions


class FileCacheVerdict(NamedTuple):
    """The lock-free classification of a single resource file.

    ``needs_download`` is True when the local copy is missing or has drifted
    from the remote manifest and must be (re)downloaded; ``size`` is the
    manifest-recorded byte size of that pending download (0 when nothing
    needs downloading). See gain#78.
    """

    needs_download: bool
    size: int


# Per-file download retry policy for copy_resource_file. A single stalled or
# dropped read over a slow HTTP GRR link used to abort the whole cache run
# (gain#43); instead we retry the file from scratch with exponential backoff.
_COPY_MAX_ATTEMPTS = 4
_COPY_BACKOFF_BASE = 5  # seconds; delays are 5s, 15s, 45s

#: Directory inside a resource holding the protocol's own bookkeeping --
#: per-file ``.state`` documents, lockfiles, and partial downloads. Not part
#: of the resource: everything that enumerates resource files skips it.
GRR_INTERNAL_DIR = ".grr"


class RetryableCopyError(OSError):
    """A copy failure the download loop retries from scratch.

    Retryability is a property of the class, not of a list kept beside it:
    ``copy_resource_file`` catches this base, so a new transient failure
    shape becomes retryable by subclassing it and nothing else (gain#934).

    What the retry buys is a fresh remote handle and a fresh temp file, so
    only faults a second full attempt could plausibly clear belong here --
    a stalled link, a short read, corrupt bytes. A fault that would repeat
    identically is a plain ``OSError`` and surfaces on the first attempt.

    The classification is the *download* path's, and it reaches
    everything that path does -- the move included, so a corrupting
    publish inside a download is retried like any other transient fault.

    One shape reaches here from outside a download: redacting a
    credential-bearing failure whose own type cannot be reconstructed from a
    message rebuilds it as this class, so that redaction cannot silently
    reclassify a transient failure as permanent (gain#1078, ADR 0023). Such
    an error can therefore surface from a plain read -- ``get_file_content``,
    a tabix header -- where nothing will retry it. It is still true of it
    that a second attempt could plausibly have cleared it; only the
    opportunity is absent.
    """


class ChecksumMismatchError(RetryableCopyError):
    """A completed download whose md5 disagrees with the manifest.

    Almost always a truncated or corrupted transfer.
    """


class TruncatedDownloadError(RetryableCopyError):
    """A download that ended short of the manifest's recorded byte size.

    A silent short read in the fsspec range-reassembly layer (gain#292, H1)
    makes ``infile.read()`` return EOF before the whole file has been
    streamed; the copy loop stops on that empty read and writes a truncated
    file. Caught explicitly by byte count -- before the md5 check -- so the
    failure is reported as the truncation it is, with both the received and
    the expected size, rather than as an opaque checksum mismatch.
    """


class CorruptedPublishError(RetryableCopyError):
    """A published object that differs in size from the verified download.

    The download's byte-count and md5 checks both run before the move, so
    they describe the temp file, not the object the move landed. Since the
    digest is carried across rather than recomputed from the store
    (gain#865), a move that published something else -- a partial
    object-store copy, a rename that lost the tail, one that left no
    object at all -- would be recorded under the digest of bytes that are
    no longer there: size and timestamp come from the published object and
    agree with it, the md5 comes from the download and agrees with the
    manifest, so every later cache verdict passes and the file is served
    corrupt indefinitely (gain#880).

    Only size-changing corruption is caught. Catching the rest needs a
    second full read of every downloaded file -- for a remote store, a
    second transfer -- which is exactly the cost gain#865 removed.

    Raised on a second, digest-free path since gain#933: the repository's
    own artifacts publish through :meth:`_publish_file`, which compares
    the published object against the bytes it staged rather than against
    a manifest. The reasoning above is about the download, but the guard
    is the same one -- it lives on the move, so everything that moves
    gets it. The retryability inherited from
    :class:`RetryableCopyError` does not reach that second path, though:
    its callers publish once and propagate, so a corrupting publish of a
    repository artifact fails rather than retrying.
    """


# aiohttp.ClientError is folded into the retryable set when aiohttp is
# importable (it always is when an HTTP GRR is used).
try:
    import aiohttp as _aiohttp
    _aiohttp_errors: tuple[type[BaseException], ...] = (_aiohttp.ClientError,)
except ImportError:
    _aiohttp_errors = ()

# botocore's own errors reach a caller of an s3 GRR untranslated when they
# carry no HTTP response, for example an unreachable endpoint. They are
# neither ``OSError`` nor ``aiohttp`` errors.
try:
    import botocore.exceptions as _botocore_exceptions
    _botocore_errors: tuple[type[BaseException], ...] = (
        _botocore_exceptions.BotoCoreError,)
except ImportError:
    _botocore_errors = ()

# Transient errors that warrant retrying a file download from scratch. A
# stalled aiohttp read surfaces as fsspec's FSTimeoutError, and ConnectionError
# covers resets/refused connects; the tuple exists for those -- failure types
# gain does not own and so cannot give a base class. Every gain-owned shape
# enters through RetryableCopyError instead, which is why this list does not
# grow when one is added.
_RETRYABLE_COPY_ERRORS: tuple[type[BaseException], ...] = (
    fsspec.exceptions.FSTimeoutError,
    asyncio.TimeoutError,
    ConnectionError,
    RetryableCopyError,
    *_aiohttp_errors,
)
