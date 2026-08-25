"""Provides GRR protocols based on fsspec library."""
# pylint: disable=too-many-lines
from __future__ import annotations

import abc
import asyncio
import copy
import datetime
import gzip
import hashlib
import json
import operator
import os
import pathlib
import re
import tempfile
import time
import uuid
from collections.abc import Callable, Generator, Iterable
from contextlib import AbstractContextManager, suppress
from dataclasses import asdict
from threading import Event, Lock, get_ident
from typing import (
    IO,
    Any,
    NamedTuple,
    cast,
)
from urllib.parse import urlparse
from weakref import finalize, ref

import apsw
import fsspec
import fsspec.exceptions
import pathspec
import pyBigWig
import pysam
import yaml
from filelock import FileLock

from gain import logging
from gain.genomic_resources.dvc import (
    DVC_SUFFIX,
    UnsupportedDvcDirectoryOutputError,
    parse_dvc_pointer_out,
)
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GR_CONTENTS_FILE_NAME,
    GR_INDEX_FILE_NAME,
    GR_LEGACY_CONTENTS_FILE_NAME,
    GR_MANIFEST_FILE_NAME,
    GR_SQLITE_META_FILE_NAME,
    GenomicResource,
    Manifest,
    ManifestEntry,
    Mode,
    ReadOnlyRepositoryProtocol,
    ReadWriteRepositoryProtocol,
    ResourceFileState,
    ResourceScan,
    SearchIndexUnavailableError,
    escape_unsafe_characters,
    is_generated_info_page,
    is_gr_id_token,
    parse_gr_id_version_token,
    resolve_tabix_index_filename_for_read,
    uncontained_resource_id_reason,
    validate_resource_file_name,
    validate_resource_id,
)
from gain.templates import get_template
from gain.templates.markdown_support import render_markdown as markdown
from gain.utils.helpers import convert_size

# Silence the spurious "[W::hts_idx_load3] The index file is older than the
# data file" warning that htslib emits when a tabix/VCF index has an older
# mtime than its data file. In our GRR workflow this is benign: both the
# caching protocol and DVC download index and data files in parallel, and
# the smaller index typically lands first. Level 1 (errors only) keeps real
# htslib errors visible while suppressing notices (3) and warnings (2).
pysam.set_verbosity(1)

logger = logging.getLogger(__name__)


def _declare_index_contigs(vcf_file: pysam.VariantFile) -> None:
    """Add the index's contigs to a header that does not declare them.

    htslib does this itself -- but only for an index it found by ITSELF,
    probing next to the data file: ``vcf_hdr_read`` loads an adjacent
    ``.tbi``/``.csi`` and folds the names it lists into the header.  An
    index opened by an explicit path (every remote open, and every open of
    a table whose ``index_filename`` names a non-adjacent file) misses that
    step, and a VCF with no ``##contig`` lines then has an EMPTY header
    contig list -- so ``fetch("chr1")`` raises ``ValueError: invalid contig
    chr1`` although the index knows the contig perfectly well.

    Declaring them here is what makes an explicitly-indexed open behave
    exactly like an auto-probed one, which is the whole point of honouring a
    configured index (gain#596).  A contig the header already declares is
    left alone.
    """
    index = vcf_file.index
    if index is None:
        return
    contigs = vcf_file.header.contigs
    for indexed in index:
        contig = str(indexed)
        if contig not in contigs:
            contigs.add(contig)


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


class ChecksumMismatchError(OSError):
    """A completed download whose md5 disagrees with the manifest.

    Almost always a truncated or corrupted transfer, so it is treated as a
    retryable error by copy_resource_file rather than a hard failure.
    """


class TruncatedDownloadError(OSError):
    """A download that ended short of the manifest's recorded byte size.

    A silent short read in the fsspec range-reassembly layer (gain#292, H1)
    makes ``infile.read()`` return EOF before the whole file has been
    streamed; the copy loop stops on that empty read and writes a truncated
    file. Caught explicitly by byte count -- before the md5 check -- so the
    failure is reported as the truncation it is, with both the received and
    the expected size, rather than as an opaque checksum mismatch. Treated as
    retryable, like ChecksumMismatchError, so a transient short read is
    retried from scratch rather than aborting the file.
    """


# aiohttp.ClientError is folded into the retryable set when aiohttp is
# importable (it always is when an HTTP GRR is used).
try:
    import aiohttp as _aiohttp
    _aiohttp_errors: tuple[type[BaseException], ...] = (_aiohttp.ClientError,)
except ImportError:
    _aiohttp_errors = ()

# Transient errors that warrant retrying a file download from scratch. A
# stalled aiohttp read surfaces as fsspec's FSTimeoutError; ConnectionError
# covers resets/refused connects; TruncatedDownloadError covers a silent short
# read that ends the stream early (gain#292); ChecksumMismatchError covers a
# full-length transfer whose bytes are corrupt.
_RETRYABLE_COPY_ERRORS: tuple[type[BaseException], ...] = (
    fsspec.exceptions.FSTimeoutError,
    asyncio.TimeoutError,
    ConnectionError,
    TruncatedDownloadError,
    ChecksumMismatchError,
    *_aiohttp_errors,
)


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


# Matches the ``scheme://user:pass@`` prefix of any url embedded in a string.
# The userinfo (``[^/@\s]+``) carries the secret and is dropped, keeping the
# scheme and everything from the host onward. Works both on a bare url and on a
# longer diagnostic message that embeds one (e.g. an fsspec
# ``FileNotFoundError`` whose text IS the credential-bearing fetch url).
_URL_USERINFO_RE = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)[^/@\s]+@")


def _strip_url_userinfo(text: str) -> str:
    """Strip ``user:pass@`` userinfo from every ``scheme://user:pass@host``.

    Used to build credential-free display urls, cache-hit log lines and
    redacted fetch-error messages. The host/port/path are preserved; only the
    userinfo is removed. A string with no userinfo is returned unchanged.
    """
    return _URL_USERINFO_RE.sub(lambda match: match.group("scheme"), text)


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


def _rebuild_error_without_userinfo(
        exc: BaseException, redacted: str) -> BaseException:
    """Rebuild ``exc`` carrying the ``redacted`` message instead of its own.

    Return a fresh exception of ``exc``'s type so it can propagate/log
    without leaking the secret its own message carries; fall back to
    ``OSError`` for a type that cannot be reconstructed from a single
    message string.
    """
    try:
        return type(exc)(redacted)
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return OSError(redacted)


def _run_redacting_userinfo[T](fn: Callable[[], T]) -> T:
    """Run ``fn``, re-raising any failure with its url userinfo stripped.

    On a fetch failure fsspec/aiohttp embed the credential-bearing fetch url
    verbatim in the raised message (e.g. ``FileNotFoundError(url)``). Rebuild
    the error with the url userinfo stripped and raise it OUTSIDE the
    ``except`` block so no credential-bearing ``__context__``/``__cause__``
    survives a chain walk. A failure whose message carries no userinfo (the
    common non-authed case) is propagated unchanged.
    """
    reraise: BaseException | None = None
    try:
        return fn()
    except Exception as exc:
        message = str(exc)
        redacted = _strip_url_userinfo(message)
        if redacted == message:
            raise
        reraise = _rebuild_error_without_userinfo(exc, redacted)
    raise reraise


def _scan_for_resources(
    content_dict: dict, parent_id: list[str],
) -> Generator[tuple[str, tuple[int, ...], dict], None, None]:
    name = "/".join(parent_id)
    id_ver = parse_gr_id_version_token(name)
    if isinstance(content_dict, dict) and id_ver and \
            GR_CONF_FILE_NAME in content_dict and \
            not isinstance(content_dict[GR_CONF_FILE_NAME], dict):
        # resource found
        resource_id, version = id_ver
        yield "/".join([*parent_id, resource_id]), version, content_dict
        return

    for name, content in content_dict.items():
        id_ver = parse_gr_id_version_token(name)
        if isinstance(content, dict) and id_ver and \
                GR_CONF_FILE_NAME in content and \
                not isinstance(content[GR_CONF_FILE_NAME], dict):
            # resource found
            resource_id, version = id_ver
            yield "/".join([*parent_id, resource_id]), version, content
        else:
            curr_id = [*parent_id, name]
            curr_id_path = "/".join(curr_id)
            if not isinstance(content, dict):
                logger.warning("file <%s> is not used.", curr_id_path)
                continue
            if not is_gr_id_token(name):
                logger.warning(
                    "directory <%s> has a name <%s> that is not a "
                    "valid Genomic Resource Id Token.", curr_id_path, name)
                continue

            # scan children
            yield from _scan_for_resources(content, curr_id)


def _scan_for_resource_files(
    content_dict: dict[str, Any], parent_dirs: list[str],
) -> Generator[tuple[str, str | bytes], None, None]:

    for path, content in content_dict.items():
        if isinstance(content, dict):
            # handle subdirectory
            for fname, fcontent in _scan_for_resource_files(
                    content, [*parent_dirs, path]):
                yield fname, fcontent
        else:
            fname = "/".join([*parent_dirs, path])
            if isinstance(content, (str, bytes)):
                # handle file content
                yield fname, content
            else:
                logger.error(
                    "unexpected content at %s: %s", fname, content)
                raise TypeError(f"unexpected content at {fname}: {content}")


def build_inmemory_protocol(
        proto_id: str,
        root_path: str,
        content: dict[str, Any]) -> FsspecReadWriteProtocol:
    """Build and return an embedded fsspec protocol for testing."""
    if not os.path.isabs(root_path):
        logger.error(
            "for embedded resources repository we expects an "
            "absolute path: %s", root_path)
        raise ValueError(f"not an absolute root path: {root_path}")

    proto = build_fsspec_protocol(proto_id, f"memory://{root_path}")
    if not isinstance(proto, FsspecReadWriteProtocol):
        # Defensive, and unreachable today -- deliberately kept anyway. This
        # function's return type promises a read-write protocol, and it used
        # to make that promise true by ``cast`` alone: an assertion to the
        # type checker that nothing checked at run time (#528). The memory
        # scheme does build read-write, so the promise held; nothing kept it
        # holding.
        #
        # The one way the builder above can hand back something else is a memo
        # hit on an id already held by a read-only protocol, and that is
        # refused earlier and more informatively in ``__new__`` (#514) -- so
        # this branch has no test, because no caller can reach it. It exists
        # so that a future change to the builder's dispatch cannot quietly
        # turn the promise false.
        raise TypeError(
            f"protocol {proto_id!r} over memory://{root_path} is not "
            f"read-write, so it cannot hold an embedded repository")
    for rid, rver, rcontent in _scan_for_resources(content, []):
        resource = GenomicResource(rid, rver, proto)
        for fname, fcontent in _scan_for_resource_files(rcontent, []):
            mode = "wt"
            if isinstance(fcontent, bytes):
                mode = "wb"
            with proto.open_raw_file(resource, fname, mode) as outfile:
                outfile.write(fcontent)
            proto.save_resource_file_state(
                resource, proto.build_resource_file_state(resource, fname))

        proto.save_manifest(resource, proto.build_manifest(resource))

    return proto


#: Every keyword ``_build_filesystem`` reads, and so the whole of what a
#: keyword can configure about a protocol. **Keep in step with it** -- a
#: keyword it learns to read and this set does not is one a rebuild can go on
#: changing silently. Pinned by
#: ``test_fsspec_protocol_rebuild.py``'s drift guard.
_FILESYSTEM_KWARGS = frozenset({
    "base_url", "user", "password", "endpoint_url",
})


def _canonical_public_url(public_url: str) -> str:
    """Return a public url in the one spelling two builds can be compared in.

    Only for comparison -- the value a protocol reports through
    ``get_public_url`` stays exactly as its caller wrote it.
    """
    return _display_url(public_url).rstrip("/")


def _protocol_config_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return the keywords that configure a protocol, ready to compare.

    Only the filesystem keywords: everything else a caller passes rides along
    without the protocol ever reading it -- the repository factory hands the
    builder its ``cache_dir``, which configures the cache wrapped *around* the
    protocol -- and so cannot make two builds over one id and url disagree.

    A keyword whose value is ``None`` is dropped rather than kept, because
    ``_build_filesystem`` reads them all with ``.get``: an omitted keyword and
    an explicit ``None`` build the identical filesystem, and the repository
    factory reaches one url both ways -- a ``url``-type definition passes
    neither credential keyword, an ``http``-type one passes both as ``None``.
    """
    return {
        key: value
        for key, value in kwargs.items()
        if key in _FILESYSTEM_KWARGS and value is not None
    }


def _refuse_a_reconfiguring_rebuild(
    cls: type[FsspecReadOnlyProtocol],
    existing: FsspecRepositoryProtocol,
    proto_id: str,
    url: str,
    kwargs: dict[str, Any],
) -> None:
    """Refuse a rebuild that asks for a differently configured protocol.

    ``__new__`` memoizes one instance per ``(proto_id, canonical url)`` and
    never evicts, so that pair names a single protocol for the whole process.
    A second build over it that asks for something *else* cannot be honoured
    and used to be answered silently and wrongly instead (#514): the memo hit
    was returned as-is, and everything ``__init__`` rebinds on the way out --
    the public url, the credential kwargs -- was applied to the instance every
    existing holder was already using.

    Mode is the sharpest case, because ``FsspecReadWriteProtocol`` subclasses
    the read-only protocol. A read-only build over a read-write key satisfied
    ``isinstance`` and got a *writable* protocol with the read-write
    ``__init__`` re-run on it, while a read-write build over a read-only key
    got an instance Python did not call ``__init__`` on at all -- stale
    filesystem, stale kwargs, and no write methods to fail on until much
    later.

    Reported here, where the wrong thing is asked for, rather than as an absent
    write method or an unexpected url somewhere downstream. Two genuinely
    different protocols over one url are still available -- under two ids.
    """
    requested = (
        Mode.READWRITE
        if issubclass(cls, ReadWriteRepositoryProtocol)
        else Mode.READONLY
    )
    if existing.mode() != requested:
        raise ValueError(
            f"protocol {proto_id!r} over {_strip_url_userinfo(url)} is "
            f"already built as {existing.mode().name}; it cannot also serve "
            f"a {requested.name} build -- give the {requested.name} protocol "
            f"an id of its own")

    # The incumbent's attributes are always there to compare against:
    # ``_FSSPEC_PROTOCOLS`` holds only protocols whose whole construction has
    # returned (#527). This used to need an early return for an incumbent
    # published before ``__init__`` had configured it -- reading
    # ``public_url`` anyway turned a race that merely ran ``__init__`` twice
    # into an ``AttributeError`` raised out of ``__new__``. That tolerance is
    # deliberately NOT kept now that the window is closed: it would let a
    # regression that reopened it answer a divergent rebuild silently instead
    # of being found. See ADR 0005.
    requested_public_url = kwargs.get("public_url")
    if requested_public_url is None:
        requested_public_url = _display_url(url)
    # Compared in one spelling, not as authored. An incumbent's ``public_url``
    # is whatever its caller passed, while a rebuild that passes none defaults
    # to the url's display form -- so a trailing slash, or a bare path against
    # its ``file://`` form, would otherwise read as a request to republish the
    # repository somewhere else.
    if _canonical_public_url(requested_public_url) != \
            _canonical_public_url(existing.public_url):
        raise ValueError(
            f"protocol {proto_id!r} over {_strip_url_userinfo(url)} is "
            f"already built with the public url "
            f"{_strip_url_userinfo(existing.public_url)}; rebuilding it "
            f"cannot repoint it at "
            f"{_strip_url_userinfo(requested_public_url)} -- give the "
            f"protocol published under that url an id of its own")

    requested_kwargs = _protocol_config_kwargs(kwargs)
    existing_kwargs = _protocol_config_kwargs(existing.kwargs)
    if requested_kwargs != existing_kwargs:
        disagreeing = sorted(
            (set(requested_kwargs) ^ set(existing_kwargs)) | {
                key
                for key in set(requested_kwargs) & set(existing_kwargs)
                if requested_kwargs[key] != existing_kwargs[key]
            })
        # The disagreeing KEYS, never their values: these keywords are how
        # http basic-auth credentials reach a protocol, and an exception
        # message is logged, echoed and reported.
        raise ValueError(
            f"protocol {proto_id!r} over {_strip_url_userinfo(url)} is "
            f"already built with a different {', '.join(disagreeing)}; "
            f"rebuilding it cannot reconfigure the protocol its holders are "
            f"using -- give the differently configured protocol an id of "
            f"its own")


class _ProtocolConstruction:
    """One in-flight construction of a memoized protocol key.

    A protocol becomes reachable through ``_FSSPEC_PROTOCOLS`` only once it is
    configured, so between ``__new__`` creating the instance and ``__init__``
    (or ``__setstate__``) finishing with it, the construction needs somewhere
    else to be recorded -- otherwise a second thread over the same key cannot
    tell "nobody has built this" from "somebody is building it", and builds a
    second instance over a key that is supposed to name one (#527).

    It is released exactly once, whether the construction was published or
    abandoned, so a waiter is never left blocked by a build that raised.

    The instance is held WEAKLY, and that is load-bearing rather than tidy:
    the two guards that release a key -- ``_BuiltOnceProtocolMeta.__call__``
    and ``__setstate__`` -- both sit *after* ``__new__`` has taken it, and the
    deserialize path can die in between, when a frame carrying a protocol is
    truncated or corrupt, or when a ``KeyboardInterrupt`` lands there. Neither
    guard is reached then, so nothing announces the abandonment. What is left
    to go on is that the half-built instance itself is dropped: a construction
    whose instance has been collected is one nobody can ever finish, and
    ``__new__`` takes such a key on rather than waiting for it.
    """

    def __init__(
        self, instance: FsspecReadOnlyProtocol, key: tuple[str, str],
    ) -> None:
        self.key = key
        self._instance = ref(instance)
        self._done = Event()
        self._owner = get_ident()
        self._finalizer = finalize(instance, self._instance_collected)
        # Process teardown is not an abandonment worth reporting, and a
        # construction still in flight then has no waiter left to wake.
        self._finalizer.atexit = False

    @property
    def instance(self) -> FsspecReadOnlyProtocol | None:
        """The instance being constructed, or ``None`` once it is dropped."""
        return self._instance()

    def _instance_collected(self) -> None:
        """Wake the waiters of a construction that can no longer finish.

        Deliberately takes no lock. A finalizer runs at whatever allocation
        point collects the instance -- including one inside
        ``_FSSPEC_PROTOCOLS_GUARD`` -- so acquiring that guard here could
        deadlock the very thread it interrupted. Waking is enough: the record
        is dropped by ``__new__``, under the guard, the next time anything
        looks at the key.
        """
        self._done.set()

    def reentered_by_this_thread(self) -> FsspecReadOnlyProtocol | None:
        """The instance, if this thread is the one constructing the key.

        Only read to keep a re-entrant construction from waiting on itself.
        Nothing in the tree builds a protocol from inside a protocol's
        constructor, so this guards against a deadlock rather than describing
        a supported shape. The answer cannot be a live construction with a
        collected instance -- the thread inside it is holding the instance.
        """
        if self._owner != get_ident():
            return None
        return self._instance()

    def wait(self) -> None:
        """Block until this construction is published, abandoned or dropped."""
        self._done.wait()

    def release(self) -> None:
        """Wake everything waiting on this construction."""
        # Detached rather than left armed: a published protocol is never
        # evicted, so its finalizer would hold this record -- and the
        # construction bookkeeping behind it -- for the life of the process.
        self._finalizer.detach()
        self._done.set()


def _finish_construction(instance: FsspecReadOnlyProtocol) -> None:
    """Publish a protocol that has finished being configured.

    A no-op unless this instance is the one under construction for its key: a
    rebuild of a memoized protocol re-runs ``__init__`` on the live instance,
    and that refresh is not a publication.
    """
    construction = instance.__dict__.pop("_construction", None)
    if construction is None:
        return
    with _FSSPEC_PROTOCOLS_GUARD:
        if _FSSPEC_PROTOCOLS_UNDER_CONSTRUCTION.get(
                construction.key) is construction:
            del _FSSPEC_PROTOCOLS_UNDER_CONSTRUCTION[construction.key]
        _FSSPEC_PROTOCOLS[construction.key] = instance
    construction.release()


def _abandon_construction(instance: FsspecReadOnlyProtocol) -> None:
    """Drop a construction that raised, leaving its key buildable again.

    The alternative -- leaving the record in place -- would make one failed
    build permanently unbuildable AND block every thread already waiting on
    it forever, which is a far worse outcome than the duplicated ``__init__``
    the serialisation replaced.
    """
    construction = instance.__dict__.pop("_construction", None)
    if construction is None:
        return
    with _FSSPEC_PROTOCOLS_GUARD:
        if _FSSPEC_PROTOCOLS_UNDER_CONSTRUCTION.get(
                construction.key) is construction:
            del _FSSPEC_PROTOCOLS_UNDER_CONSTRUCTION[construction.key]
    construction.release()


class _BuiltOnceProtocolMeta(abc.ABCMeta):
    """Publishes a protocol only once its whole construction has returned.

    ``__new__`` cannot publish the instance itself, because Python runs
    ``__init__`` only after ``__new__`` has returned -- which is exactly the
    window this class closes (#527). Nor can the base ``__init__``:
    ``FsspecReadWriteProtocol.__init__`` creates the repository root *after*
    the base constructor body, so a protocol published from there would be
    handed to a waiting thread before its root existed.

    Taking over the call protocol gives one place that is after the whole
    ``__init__`` chain and before the caller receives the object. It is also
    the only place that sees a construction failing *before* ``__init__`` is
    entered -- a call-signature error -- which would otherwise strand the key
    and block every later builder of it forever.

    Unpickling does not come through here at all: it runs
    ``__getnewargs_ex__`` -> ``__new__`` -> ``__setstate__`` and never calls
    ``__init__``, so ``__setstate__`` finishes its own construction.
    """

    def __call__(cls, *args: Any, **kwargs: Any) -> Any:
        # ``cls`` is the protocol class being instantiated, not the metaclass;
        # the cast says so, since ``ABCMeta.__new__`` is a class-creation
        # signature and this is the instance-creation one.
        target = cast("type[Any]", cls)
        instance = target.__new__(target, *args, **kwargs)
        if not isinstance(instance, cls):
            # ``type.__call__``'s own rule, kept: an instance of another class
            # is not initialised. Unreachable here -- a memo hit of another
            # class is a mode mismatch, and refused in ``__new__``.
            return instance
        try:
            # pylint: disable=unnecessary-dunder-call
            type(instance).__init__(instance, *args, **kwargs)
        except BaseException:
            _abandon_construction(instance)
            raise
        _finish_construction(instance)
        return instance


class FsspecReadOnlyProtocol(
        ReadOnlyRepositoryProtocol, metaclass=_BuiltOnceProtocolMeta):
    """Provides fsspec genomic resources repository protocol.

    ``(proto_id, url)`` names ONE protocol instance for the life of the
    process: ``__new__`` memoizes it in ``_FSSPEC_PROTOCOLS``, keyed on the
    url's canonical ``scheme://netloc/path`` form so its spelling cannot split
    one repository in two, and never evicts. A second build over that pair
    therefore reaches the object every earlier caller is already holding, and
    Python re-runs ``__init__`` on it.

    That makes a rebuild a *refresh* -- it drops the resource memo, which is
    how a caller that has just changed a repository reads it back. It is not a
    reconfiguration: a rebuild asking for a different mode, public url or
    credentials is refused rather than applied to the incumbent. See
    ``docs/adr/0005-fsspec-protocol-memo-rebuild.md`` (#514).

    Construction is one atomic step, and a protocol is reachable through the
    memo only once the whole of it has returned (#527). ``__new__`` records an
    in-flight construction instead of publishing, so a thread that arrives
    while another is building that key waits and is answered with the same,
    configured instance -- rather than either building a second protocol over
    a key that names one, or reading one whose ``filesystem``, ``url``,
    ``public_url`` and ``kwargs`` are not bound yet.
    """

    #: The repository's resources, memoized on first read, and the lock that
    #: guards it. Bound once per instance in ``__new__`` rather than in
    #: ``__init__``, because ``__init__`` re-runs on every memoized instance a
    #: rebuild hands back and must not replace the lock its readers are
    #: already holding (#514).
    _all_resources: dict[str, GenomicResource] | None
    _all_resources_lock: Lock

    #: Present only while this instance is being constructed, and removed by
    #: whichever of ``_finish_construction``/``_abandon_construction`` gets to
    #: it -- so its presence is what tells a rebuild's ``__init__`` apart from
    #: the first one (#527).
    _construction: _ProtocolConstruction

    def __getnewargs_ex__(self) -> tuple[tuple, dict]:
        # pylint: disable=invalid-getnewargs-ex-returned
        # self.kwargs may hold HTTP basic-auth credentials (user/password).
        # They are INTENTIONALLY pickled with the protocol so a dask worker
        # deserializing this protocol can rebuild an authenticated
        # filesystem and read the remote GRR. Do not strip them here — that
        # would break distributed reads of an authed http repository.
        # Pickle the credential-bearing fetch url (not the stripped display
        # url) so a dask worker rebuilds an authenticated protocol whose cache
        # key matches a fresh build, and ``__init__`` (were it called) would
        # re-derive the same stripped ``self.url``. The credential
        # re-materializes here on purpose — see the class docstring above.
        args = (self.proto_id, self._fetch_url)
        kwargs: dict[str, Any] = copy.copy(self.kwargs)
        kwargs["public_url"] = self.public_url
        return (args, kwargs)

    def __new__(cls, *args: Any, **kwargs: Any) -> FsspecReadOnlyProtocol:
        proto_id = args[0] if len(args) > 0 else kwargs["proto_id"]
        url = args[1] if len(args) > 1 else kwargs["url"]
        # The cache KEY is kept credentialed on purpose: keying on the
        # userinfo-stripped url would let a second build with DIFFERENT
        # credentials for the same host+path reuse the first protocol and
        # authenticate with the WRONG credentials. The ``_FSSPEC_PROTOCOLS``
        # dict/key is never logged, repr'd or serialized, so retaining the
        # credential in the key does not leak it. The DEBUG line below, which IS
        # a leak vector, is passed a userinfo-stripped url. For a userinfo-free
        # url the stripped url == url, so behavior is unchanged.
        #
        # Canonicalised, so the spelling of the url cannot split one repository
        # across two entries: several builders pass a bare ``/abs/path`` while
        # ``__getnewargs_ex__`` pickles the ``file://`` form, and the two used
        # to be different keys -- which is how a pickle round trip minted a
        # second protocol over one directory (#514).
        key = (proto_id, _fetch_url_form(url))
        # The memo read, the instance creation and the record of an in-flight
        # construction are one step, under one lock. They used to be a plain
        # check-then-set: two threads that both missed for one key both built,
        # and the second publication replaced the first, so the loser walked
        # away holding an orphan protocol the memo does not know about -- with
        # a resource memo and a lock of its own, outside the mutual exclusion
        # of #458 and outside the rebuild refusal of #514 (#527).
        #
        # The instance is NOT published here. ``__init__`` runs only after
        # ``__new__`` has returned, so anything published from here is
        # reachable with ``filesystem``, ``url``, ``public_url`` and
        # ``kwargs`` still unbound; publication is
        # ``_finish_construction``'s, driven by ``_BuiltOnceProtocolMeta``
        # for a normal build and by ``__setstate__`` for an unpickle.
        while True:
            with _FSSPEC_PROTOCOLS_GUARD:
                existing = _FSSPEC_PROTOCOLS.get(key)
                if existing is not None:
                    _refuse_a_reconfiguring_rebuild(
                        cls, existing, proto_id, url, kwargs)
                    logger.debug(
                        "protocol with id %s and url %s already exists, "
                        "returning the existing instance",
                        proto_id, _strip_url_userinfo(url))
                    return existing
                pending = _FSSPEC_PROTOCOLS_UNDER_CONSTRUCTION.get(key)
                if pending is not None:
                    reentered = pending.reentered_by_this_thread()
                    if reentered is not None:
                        # This thread is already constructing this key.
                        # Nothing in the tree does that, and waiting would be
                        # waiting on ourselves.
                        return reentered
                    # Asked without binding the answer to a local: a strong
                    # reference held across the wait below would pin the
                    # half-built instance, and its collection is the whole
                    # signal being read here.
                    if pending.instance is None:
                        # Nobody holds the half-built instance any more, so
                        # nobody is coming back to finish it: the deserialize
                        # that took this key died between ``__new__`` and
                        # ``__setstate__``, where neither of the guards that
                        # release a key is reached. Drop the record and take
                        # the key on -- leaving it would block every builder
                        # of it for the life of the process, in an untimed
                        # wait and with no log line (#527).
                        del _FSSPEC_PROTOCOLS_UNDER_CONSTRUCTION[key]
                        pending = None
                if pending is None:
                    instance = super().__new__(cls)
                    # Before the instance is reachable, and never again: the
                    # memo lock is the one piece of state a rebuild must not
                    # touch, so it is bound where construction happens
                    # exactly once (#514).
                    instance._all_resources_lock = Lock()
                    instance._all_resources = None
                    construction = _ProtocolConstruction(instance, key)
                    _FSSPEC_PROTOCOLS_UNDER_CONSTRUCTION[key] = construction
                    instance._construction = construction
                    return instance
            # Waited for OUTSIDE the guard: a construction runs the whole
            # ``__init__`` chain, which for a read-write protocol does
            # filesystem I/O, and holding the memo lock across that would put
            # every protocol build in the process behind one remote round
            # trip. Then round the loop: the key is either published by now,
            # or the construction was abandoned and this thread takes it on.
            pending.wait()

    def __init__(
        self, proto_id: str,
        url: str, *,
        filesystem: fsspec.AbstractFileSystem,
        public_url: str | None = None,
        **kwargs: Any,
    ):
        super().__init__(proto_id, url)
        parsed = urlparse(url)
        self.scheme = parsed.scheme
        if self.scheme == "":
            self.scheme = "file"
        # ``self.netloc``/``self.url``/``self.public_url`` are the DISPLAY /
        # IDENTITY of this protocol — returned by ``get_url``/``get_public_url``
        # and serialized into web responses, persisted docs and logs. They MUST
        # NOT carry credentials, so any ``user:pass@`` userinfo embedded in a
        # ``scheme://user:pass@host`` url is stripped from them here.
        fetch_netloc = parsed.netloc
        self.netloc = _strip_netloc_userinfo(fetch_netloc)
        self.root_path = parsed.path

        self.url = _display_url(url)
        # ``self._fetch_url`` is the credential-BEARING url used only to talk to
        # the remote filesystem. For URL-embedded userinfo, aiohttp/htslib read
        # the basic-auth credentials straight from this url string (they are not
        # in ``kwargs``), so every fetched file url must derive from it — see
        # ``get_resource_url``/``load_contents``/``md5_contents``. When the url
        # has no userinfo this is byte-identical to ``self.url``. It is also
        # what the memo is keyed on, so it and the key cannot drift.
        self._fetch_url = _fetch_url_form(url)

        if public_url is None:
            self.public_url = self.url
        else:
            self.public_url = public_url

        self.filesystem = filesystem
        # kwargs may carry HTTP basic-auth credentials (user/password). They
        # are kept so the filesystem can be rebuilt after unpickling on a
        # dask worker (see __getnewargs_ex__/__setstate__); they are never
        # logged and are masked in the definition model's repr.
        self.kwargs: dict[str, Any] = kwargs
        # This body re-runs on the LIVE instance whenever a memoized protocol
        # is rebuilt (see ``__new__``), so a rebuild is a refresh of the
        # resource memo -- ``grr_manage`` re-reading a repository it has just
        # changed depends on that. Take the incumbent's own lock to do it,
        # which is what ``invalidate`` is; rebinding a fresh ``Lock`` here and
        # clearing the memo beside it left a reader inside the guard with no
        # mutual exclusion at all (#514).
        #
        # The other assignments above are rebound on the live instance too, and
        # are safe for narrower reasons: the url fields are derived from the
        # memo key, so they cannot differ; ``public_url`` and the filesystem
        # keywords are what ``__new__`` refuses a rebuild over; ``filesystem``
        # is a freshly built but equivalent object, because every construction
        # in the tree routes through ``build_fsspec_protocol``, which derives
        # it from the key and those keywords. ``kwargs`` as a whole is NOT
        # equal -- a keyword the protocol never reads (the factory's
        # ``cache_dir``) may differ, and the second caller's value wins.
        # Nothing reads those, and rebinding a reference is atomic, so a
        # concurrent reader cannot catch any of it half-done.
        self.invalidate()

    def __getstate__(self) -> dict[str, Any]:
        state = self.__dict__.copy()
        # Remove the unpicklable entries.
        del state["filesystem"]
        del state["_all_resources"]
        del state["_all_resources_lock"]
        # Defensive: an in-flight construction is this process's bookkeeping
        # (and holds an ``Event``, which does not pickle). A configured
        # protocol has none, so this only ever fires for a protocol pickled
        # from inside its own constructor.
        state.pop("_construction", None)
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        # Unpickling is the one path that reaches ``__new__`` without
        # ``__init__`` ever running -- ``__getnewargs_ex__`` -> ``__new__`` ->
        # here -- so this is where a deserialize into a COLD memo finishes its
        # own construction. A scheme that published from ``__init__`` alone
        # would leave the key in flight forever and hang every later builder
        # of it (#527).
        try:
            self.__dict__.update(state)
            self.filesystem = _build_filesystem(
                self._fetch_url, **self.kwargs)
            # In a process that already has this protocol, ``__new__`` landed
            # on the live instance, so this is the same rebuild ``__init__``
            # performs -- and the same reason not to rebind the lock a reader
            # is holding here either (#514).
            self.invalidate()
        except BaseException:
            _abandon_construction(self)
            raise
        _finish_construction(self)

    def get_url(self) -> str:
        return self.url

    def get_resource_url(self, resource: GenomicResource) -> str:
        # Fetch path: derive resource file urls from the credential-bearing
        # ``_fetch_url`` (the base class uses ``self.url``, which is stripped of
        # userinfo for display) so URL-embedded basic-auth still reaches
        # aiohttp/htslib. Identical to the base for userinfo-free urls.
        #
        # A join of its own, so it repeats the id containment check the base
        # class does -- this override is the one that serves the REMOTE
        # repositories, whose ids come out of an untrusted ``.CONTENTS``
        # (gain#467).
        validate_resource_id(resource.resource_id)
        return os.path.join(
            self._fetch_url,
            resource.get_genomic_resource_id_version())

    def get_public_url(self) -> str:
        return self.public_url

    def invalidate(self) -> None:
        """Drop the memoized resources, leaving handed-out ones alone.

        Clears only this protocol's own cache. The resources in the memo are
        handed out by reference, so unbinding their ``proto`` on the way out
        -- as this used to do -- breaks the objects live callers are already
        holding, and they raise ``AttributeError`` on ``None`` at first use
        (#513). Their lifetime is the caller's business; dropping the memo is
        enough to make the next read reload, and enough to let a resource no
        one else holds be collected, since the memo held the only reference
        to it. Not the protocol, though -- ``_FSSPEC_PROTOCOLS`` memoizes
        every protocol for the life of the process and never evicts, so no
        amount of unbinding here ever released one.
        """
        # Under the memo lock, and over the whole body: returning the memo
        # from inside the lock is only half the guarantee, because an
        # unsynchronised ``invalidate`` can still clear the attribute in the
        # middle of a populating read (#458).
        with self._all_resources_lock:
            self._all_resources = None

    def close(self) -> None:
        """Close the genomic resource."""
        self.invalidate()

    def _read_fetch_file(
        self, filepath: str, mode: str, compression: str | None,
    ) -> str | bytes:
        """Open+read a fetch-url file, redacting any credential on failure.

        ``_run_redacting_userinfo``'s guarantee, covering both the open and
        the read.
        """
        def open_and_read() -> str | bytes:
            with self.filesystem.open(
                    filepath, mode, compression=compression) as infile:
                return cast("str | bytes", infile.read())
        return _run_redacting_userinfo(open_and_read)

    def load_contents(self) -> list[dict[str, Any]]:
        """Load the content JSON of the repository."""
        content_filename = os.path.join(
            self._fetch_url, GR_CONTENTS_FILE_NAME)
        compression: str | None = "gzip"
        if not self.filesystem.exists(content_filename):
            # Not a dead branch: nothing has written this since #758,
            # but repositories an older release published still carry
            # one and no other file describes them.
            content_filename = os.path.join(
                self._fetch_url, GR_LEGACY_CONTENTS_FILE_NAME)
            compression = None

        data = self._read_fetch_file(content_filename, "rt", compression)

        return cast(list[dict[str, Any]], json.loads(data))

    def md5_contents(self) -> str:
        """Calculate md5 hash of the repository content."""
        content_filename = os.path.join(
            self._fetch_url, GR_CONTENTS_FILE_NAME)
        if not self.filesystem.exists(content_filename):
            # See `load_contents`: the legacy index is still the only
            # description an older repository has.
            content_filename = os.path.join(
                self._fetch_url, GR_LEGACY_CONTENTS_FILE_NAME)

        data = self._read_fetch_file(content_filename, "rb", None)

        assert isinstance(data, bytes)

        return hashlib.md5(data).hexdigest()  # noqa: S324

    def get_all_resources(self) -> Generator[GenomicResource, None, None]:
        """Return generator over all resources in the repository."""
        yield from self.get_all_resources_dict().values()

    def _enumerate_resources(self) -> Iterable[GenomicResource]:
        """Enumerate this repository's resources, in any order.

        The one seam ``get_all_resources_dict`` leaves to a subclass: that
        method holds the memo, the lock, the keying and the ordering, so
        there is one implementation of the memo protocol to get right rather
        than one per protocol (#515).

        Called with ``_all_resources_lock`` HELD, and the lock is NOT
        reentrant. An implementation must therefore not take it, and must not
        re-enter ``get_all_resources_dict``, ``get_all_resources``, ``close``
        or ``invalidate`` -- on this protocol or on any protocol whose
        invalidation cascades back to this one. That is a real trap and not a
        theoretical one: ``CachingProtocol.get_all_resources_dict``
        legitimately invalidates a *sub*-protocol from inside its own memo
        population, and the same idiom here deadlocks the process with no
        traceback. Pinned by
        ``test_the_seam_runs_with_the_memo_lock_held``.

        Named apart from the public ``collect_all_resources`` on purpose:
        that one is the read-write scan itself, and this one is the memo's
        seam. Overriding the wrong one is silent.

        This implementation reads the repository's ``.CONTENTS``, the only
        enumeration available to a protocol that cannot scan for itself.
        """
        all_resources = []

        contents = self.load_contents()

        for entry in contents:
            # ``.CONTENTS`` is remote, untrusted GRR content and its ``id``
            # is joined onto the repository url, so a traversing id reads --
            # and, through the caching repository, WRITES -- outside the
            # root. Dropped with a warning rather than raised on: one
            # poisoned entry must not cost the repository its healthy
            # resources (gain#467).
            reason = uncontained_resource_id_reason(entry["id"])
            if reason is not None:
                logger.warning(
                    "repo %s: dropping resource <%s> from %s -- "
                    "its id %s",
                    self.proto_id, escape_unsafe_characters(entry["id"]),
                    GR_CONTENTS_FILE_NAME, reason)
                continue
            version = tuple(map(int, entry["version"].split(".")))
            manifest = Manifest.from_manifest_entries(entry["manifest"])
            resource = self.build_genomic_resource(
                entry["id"], version, config=entry["config"],
                manifest=manifest)
            logger.debug(
                "repo %s loaded resource %s",
                self.proto_id,
                resource.resource_id)
            all_resources.append(resource)

        return all_resources

    def get_all_resources_dict(self) -> dict[str, GenomicResource]:
        """Return the repository's resources, keyed by full id.

        The whole memo protocol -- the lock, the check-then-populate, the
        keying, the ordering and the return -- lives here and only here, for
        every fsspec protocol. A subclass enumerates the repository by
        overriding ``_enumerate_resources`` and inherits the rest (#515).

        ``FsspecReadWriteProtocol`` used to carry a second copy of all of it
        and differ only in the enumeration, which is how the release-then-read
        defect of #458 came to be in two places while the report named one.
        """
        with self._all_resources_lock:
            if self._all_resources is None:
                # Ordered here rather than in the seam: the memo's key order
                # is this method's guarantee, so an enumeration cannot cost
                # the repository its ordering by yielding as it finds.
                #
                # ``sorted`` drains the seam's iterable HERE, inside the
                # lock. A seam that returns a generator therefore still does
                # all of its work under the lock, exactly as the two
                # hand-written copies did.
                self._all_resources = {
                    res.get_full_id(): res
                    for res in sorted(
                        self._enumerate_resources(),
                        key=lambda r: r.get_full_id(),
                    )
                }
            # Returned from inside the lock: reading ``self._all_resources``
            # once the lock has been released is a second, unsynchronised
            # read of the attribute, and an ``invalidate`` landing between
            # the two hands the caller ``None`` (#458).
            return self._all_resources

    def file_exists(
            self, resource: GenomicResource, filename: str) -> bool:
        filepath = self.get_resource_file_url(resource, filename)
        return cast(bool, self.filesystem.exists(filepath))

    def load_manifest(self, resource: GenomicResource) -> Manifest:
        """Load resource manifest."""
        content = self.get_file_content(resource, GR_MANIFEST_FILE_NAME)
        return Manifest.from_file_content(content)

    def open_raw_file(
            self, resource: GenomicResource, filename: str,
            mode: str = "rt", **kwargs: str | bool | None) -> IO:
        filepath = self.get_resource_file_url(resource, filename)
        if "w" in mode:
            if self.mode() == Mode.READONLY:
                raise OSError(
                    f"Read-Only protocol {self.get_id()} trying to open "
                    f"{filepath} for writing")

            # Create the containing directory if it doesn't exists.
            parent = os.path.dirname(filepath)
            if not self.filesystem.exists(parent):
                self.filesystem.mkdir(
                    parent, create_parents=True, exist_ok=True)

        compression = None
        if kwargs.get("compression"):
            compression = "gzip"

        return self._open_fsspec_file(filepath, mode, compression)

    def _open_fsspec_file(
            self, filepath: str, mode: str,
            compression: str | None) -> IO:
        """Open ``filepath`` on the filesystem, redacting a failing url.

        ``_run_redacting_userinfo``'s guarantee, covering the open only.
        Errors raised later by reads on the RETURNED handle are out of reach
        from here — the handle escapes to arbitrary callers, and wrapping it
        would take a proxy object. A caller that opens and reads in one
        place should use ``_read_fetch_file``, whose redaction covers the
        read too.
        """
        return _run_redacting_userinfo(lambda: cast(
            IO,
            self.filesystem.open(
                filepath, mode=mode,
                compression=compression)))

    def open_repository_metadata(self) -> apsw.Connection:
        sqlite_filepath = os.path.join(
            self._fetch_url, GR_SQLITE_META_FILE_NAME)
        if not self.filesystem.exists(sqlite_filepath):
            raise SearchIndexUnavailableError(
                self.proto_id,
                "Repository contents SQLite metadata DB not found")

        connection = apsw.Connection(":memory:")
        raw_db = self._read_fetch_file(sqlite_filepath, "rb", "gzip")
        assert isinstance(raw_db, bytes)
        connection.deserialize("main", raw_db)
        return connection

    def _get_file_url(self, resource: GenomicResource, filename: str) -> str:
        def process_file_url(url: str) -> str:
            if self.scheme == "file":
                return urlparse(url).path
            if self.scheme == "s3":
                return cast(str, self.filesystem.sign(url))
            return url

        return process_file_url(self.get_resource_file_url(resource, filename))

    def open_tabix_file(
            self, resource: GenomicResource,
            filename: str,
            index_filename: str | None = None) -> pysam.TabixFile:

        if self.scheme not in {"file", "s3", "http", "https"}:
            raise OSError(
                f"tabix files are not supported on schema {self.scheme}")

        file_url = self._get_file_url(resource, filename)

        if index_filename is None:
            # The index may be a ``.tbi`` or a ``.csi``; ask the manifest
            # which one this resource actually carries (gain#430).
            index_filename = resolve_tabix_index_filename_for_read(
                resource, filename)
        index_url = self._get_file_url(resource, index_filename)

        return pysam.TabixFile(  # pylint: disable=no-member
            file_url, index=index_url, encoding="utf-8",
            parser=pysam.asTuple())

    def open_vcf_file(
            self, resource: GenomicResource,
            filename: str,
            index_filename: str | None = None) -> pysam.VariantFile:

        if self.scheme not in {"file", "s3", "http", "https"}:
            raise OSError(
                f"vcf files are not supported on schema {self.scheme}")

        file_url = self._get_file_url(resource, filename)

        if index_filename is not None:
            # Asked for BY NAME -- a table's ``index_filename``, or the
            # index the caching protocol just refreshed.  Refuse a name that
            # names nothing rather than dropping it and opening unindexed:
            # htslib would then auto-probe its way to the adjacent index and
            # read on, and a configuration that does nothing would stay
            # invisible (gain#596).
            if not resource.file_exists(index_filename):
                raise OSError(
                    f"index '{index_filename}' of '{filename}' not found in "
                    f"resource '{resource.resource_id}'")
        else:
            # The index may be a ``.tbi`` or a ``.csi``; ask the manifest
            # which one this resource actually carries, exactly as
            # ``open_tabix_file`` above does -- do NOT assume ``.tbi``
            # (gain#430, gain#596).
            index_filename = resolve_tabix_index_filename_for_read(
                resource, filename)
            if not resource.file_exists(index_filename):
                # Nothing resolved: a file that ships no index at all -- a
                # VCF header sidecar, say -- still opens, unindexed.
                return pysam.VariantFile(file_url)  # pylint: disable=no-member

        index_url = self._get_file_url(resource, index_filename)

        vcf_file = pysam.VariantFile(  # pylint: disable=no-member
            file_url, index_filename=index_url)
        _declare_index_contigs(vcf_file)
        return vcf_file

    def open_fasta_file(
            self, resource: GenomicResource,
            filename: str,
            index_filename: str | None = None,
            compressed_index_filename: str | None = None) -> pysam.FastaFile:

        if self.scheme not in {"file", "s3", "http", "https"}:
            raise OSError(
                f"fasta files are not supported on schema {self.scheme}")

        if index_filename is None:
            index_filename = f"{filename}.fai"
        if compressed_index_filename is None:
            compressed_index_filename = f"{filename}.gzi"
        if not self.file_exists(resource, compressed_index_filename):
            raise ValueError(
                f"bgzip index '{compressed_index_filename}' is required to "
                f"read bgzipped genome '{filename}' in resource "
                f"'{resource.resource_id}'; generate the .fai and .gzi "
                f"indexes with 'samtools faidx {filename}'")

        file_url = self._get_file_url(resource, filename)

        if self.scheme == "file":
            return pysam.FastaFile(  # pylint: disable=no-member
                file_url,
                filepath_index=self._get_file_url(resource, index_filename),
                filepath_index_compressed=self._get_file_url(
                    resource, compressed_index_filename))

        # Remote scheme: pysam.FastaFile requires the index arguments to be
        # local paths (it os.path.exists-checks them), but htslib can range-
        # read the data file remotely. Copy the small .fai/.gzi indexes to a
        # temporary local directory and open against those; htslib loads both
        # indexes into memory at open, so the temp files can be removed
        # immediately afterwards. The multi-GB data file stays remote.
        with tempfile.TemporaryDirectory(prefix="gain-fasta-idx-") as tmpdir:
            local_index = self._copy_resource_file_to_local(
                resource, index_filename, tmpdir)
            local_compressed_index = self._copy_resource_file_to_local(
                resource, compressed_index_filename, tmpdir)
            return pysam.FastaFile(  # pylint: disable=no-member
                file_url,
                filepath_index=local_index,
                filepath_index_compressed=local_compressed_index)

    def _copy_resource_file_to_local(
            self, resource: GenomicResource,
            filename: str, dest_dir: str) -> str:
        """Copy a (small) resource file into dest_dir; return the local path."""
        dest = os.path.join(dest_dir, os.path.basename(filename))
        with self.open_raw_file(
                resource, filename, "rb", uncompress=False) as src:
            data = src.read()
        pathlib.Path(dest).write_bytes(data)
        return dest

    def open_bigwig_file(
        self, resource: GenomicResource, filename: str,
    ) -> Any:
        if self.scheme not in {"file", "s3", "http", "https"}:
            raise OSError(
                f"bigwig files are not supported on schema {self.scheme}")
        file_url = self._get_file_url(resource, filename)
        return pyBigWig.open(file_url)  # pylint: disable=I1101


class FsspecReadWriteProtocol(
        FsspecReadOnlyProtocol, ReadWriteRepositoryProtocol):
    """Provides fsspec genomic resources repository protocol."""

    def __init__(
        self, proto_id: str,
        url: str, *,
        filesystem: fsspec.AbstractFileSystem,
        public_url: str | None = None,
        **kwargs: Any,
    ):

        super().__init__(
            proto_id, url,
            filesystem=filesystem,
            public_url=public_url,
            **kwargs,
        )

        self.filesystem.makedirs(self.url, exist_ok=True)

    def _get_resource_file_lockfile_path(
        self, resource: GenomicResource, filename: str,
    ) -> str:
        """Return path of the resource file's lockfile.

        Another join of its own, so it repeats the containment check --
        ``filelock`` creates and truncates the lockfile on acquire
        (gain#467).
        """
        if self.scheme != "file":
            raise NotImplementedError(self._non_local_lock_message())
        validate_resource_file_name(resource.resource_id, filename)
        resource_url = self.get_resource_url(resource)
        path = os.path.join(
            resource_url, GRR_INTERNAL_DIR, f"{filename}.lockfile")
        return path.removeprefix(f"{self.scheme}://")

    def _non_local_lock_message(self) -> str:
        return (
            f"resource file locking is only supported on a local "
            f"filesystem; {self.get_url()} uses the unsupported scheme "
            f"<{self.scheme}>")

    def obtain_resource_file_lock(
        self, resource: GenomicResource, filename: str,
        timeout: float = -1,
    ) -> AbstractContextManager:
        """Lock a resource's file.

        The lock is a lockfile, which only provides mutual exclusion on a
        local filesystem. Off ``file`` this used to return a no-op context
        manager -- every caller "acquired" it instantly, so the caching
        protocol serialised nothing and concurrent readers saw partially
        written files. Refuse rather than hand out a lock that does not
        lock; a GRR cache must be local. See #473.
        """
        if self.scheme != "file":
            raise NotImplementedError(self._non_local_lock_message())

        lockfile = self._get_resource_file_lockfile_path(resource, filename)
        return FileLock(lockfile, timeout=timeout)

    def _scan_path_for_resources(
        self, path_array: list[str],
    ) -> Generator[Any, None, None]:

        url = os.path.join(self.url, *path_array)
        path = os.path.join(self.root_path, *path_array)
        assert isinstance(url, str)

        if not self.filesystem.isdir(url):
            return

        content = []
        for direntry in self.filesystem.ls(url, detail=False):
            if self.netloc and direntry.startswith(self.netloc):
                direntry = direntry[len(self.netloc):]
            name = os.path.relpath(direntry, path)
            if name.startswith("."):
                continue
            content.append(name)

        if GR_CONF_FILE_NAME in content:
            res_path = "/".join(path_array)
            resource_id, version = parse_gr_id_version_token(res_path)
            if resource_id is None:
                logger.error("bad resource id/version: %s", res_path)
                return
            yield resource_id, version, res_path
        else:
            for name in content:
                yield from self._scan_path_for_resources([*path_array, name])

    def _scan_resource_for_files(
        self, resource_path: str, path_array: list[str],
        ancestor_specs: list[tuple[int, pathspec.PathSpec]] | None = None,
    ) -> Generator[tuple[str, str], None, None]:
        """Yield ``(name, url)`` for every file of a resource.

        Whether DVC manages a file is no part of the scan's answer. What a
        file IS -- resource data, or a page GAIn generated -- is read off its
        path by :meth:`collect_resource_entries`, never off the presence of a
        sidecar (#373).
        """
        url = os.path.join(self.url, resource_path, *path_array)
        if not self.filesystem.isdir(url):
            if path_array:
                yield os.path.join(*path_array), url
            return

        path = os.path.join(self.root_path, resource_path, *path_array)

        if ancestor_specs is None:
            ancestor_specs = []

        # Path of the current directory from the GRR root. Each accumulated
        # spec is anchored by the depth of the directory that holds its
        # .gitignore, so _is_gitignored can match a file against a path
        # relative to that .gitignore's own directory (git semantics).
        full_parts = [
            part for part in resource_path.split("/") if part
        ] + path_array

        # Read the .gitignore in the current directory and add its spec,
        # anchored at this directory's depth.
        current_specs = ancestor_specs
        spec = self._load_gitignore_spec(os.path.join(url, ".gitignore"))
        if spec is not None:
            current_specs = [*ancestor_specs, (len(full_parts), spec)]

        raw_names = []
        for direntry in self.filesystem.ls(url, detail=False):
            if self.netloc and direntry.startswith(self.netloc):
                direntry = direntry[len(self.netloc):]

            name = os.path.relpath(direntry, path)
            if name.startswith("."):
                continue
            raw_names.append(name)

        # `dvc add <file>` writes `/<file>` into .gitignore and drops a
        # sibling `<file>.dvc` pointer; the real data file must stay in the
        # manifest. Exempt a gitignored leaf ONLY when it has such a genuine
        # pointer in this directory (see _is_dvc_managed_leaf for the exact,
        # cli-consistent, crash-safe test). This is done per-candidate and
        # lazily: only the sibling `.dvc` of an actually-gitignored leaf is
        # ever opened, so a directory with nothing gitignored -- the common
        # case -- opens zero `.dvc` files (gain#209).
        sibling_names = set(raw_names)

        for name in raw_names:
            if self._is_gitignored(name, full_parts, current_specs):
                # A gitignored leaf named by a genuine sibling `<name>.dvc`
                # pointer is a `dvc add <file>` data file and must stay in
                # the manifest -- regardless of WHICH gitignore rule ignored
                # it. Being ignored by a coincidental ancestor pattern (e.g.
                # root `*.tmp` matching `sub/x.tmp`) instead of the `/x.tmp`
                # line dvc itself writes is still exactly the DVC situation:
                # the pointer proves the data is DVC-managed, so keeping it
                # is correct (gain#209).
                if not self._is_dvc_managed_leaf(url, name, sibling_names):
                    continue
                yield from self._scan_resource_for_files(
                    resource_path, [*path_array, name], current_specs)
                continue

            # A file that is not gitignored is already in the scan, and no
            # `.dvc` sibling is consulted for it -- `_is_dvc_managed_leaf`
            # opens the sidecar, and a directory with nothing gitignored must
            # open none (gain#209).
            yield from self._scan_resource_for_files(
                resource_path, [*path_array, name], current_specs)

    def _is_dvc_managed_leaf(
        self, url: str, name: str, sibling_names: set[str],
    ) -> bool:
        """Return True if gitignored ``name`` is a per-file `dvc add` output.

        ``name`` is a genuine DVC-managed data file -- and so must stay in the
        manifest despite being gitignored -- iff ALL hold (gain#209):

        1. ``name`` is not a directory. Only per-file ``dvc add <file>`` is
           supported: GAIn cannot verify the ``.dir`` md5 sum of a
           ``dvc add <dir>`` output against anything it can read, so it
           refuses such a resource outright rather than describe it -- see
           ``repository.collect_dvc_entries``, which fails the command on
           the sidecar (gain#255). The directory itself stays out of the
           scan.
        2. a sibling ``<name>.dvc`` exists in this directory and is a regular
           file (a *directory* literally named ``<name>.dvc`` is not a
           pointer and must not be opened).
        3. that ``<name>.dvc`` parses as a well-formed DVC pointer -- a dict
           with an ``outs`` list of dicts -- that declares ``name`` as one of
           its outputs (``out["path"] == name``). Both this test and
           ``repository.collect_dvc_entries`` delegate to
           :func:`dvc.parse_dvc_pointer_out`, so the scanner and the
           manifest builder cannot classify the same sidecar differently.

        Parsing NEVER raises: a binary/non-UTF-8 ``.dvc`` (read in binary and
        handed to ``yaml.safe_load`` as bytes, so no UnicodeDecodeError), a
        directory opened as a file, or any malformed YAML/shape is treated as
        "not a pointer" -- the scan must never abort on stray content.
        """
        # (1) per-file dvc only: a gitignored directory is never exempted.
        if self.filesystem.isdir(os.path.join(url, name)):
            return False

        # (2) a sibling pointer must exist and be a regular file.
        dvc_name = f"{name}{DVC_SUFFIX}"
        if dvc_name not in sibling_names:
            return False
        dvc_url = os.path.join(url, dvc_name)
        if self.filesystem.isdir(dvc_url):
            return False

        # (3) it must parse as a genuine pointer declaring `name` as output.
        try:
            with self.filesystem.open(dvc_url, "rb") as infile:
                content = cast(bytes, infile.read())
        except (OSError, ValueError) as error:
            logger.debug(
                "ignoring unreadable .dvc pointer %s: %s", dvc_url, error)
            return False

        return parse_dvc_pointer_out(content, name) is not None

    def _load_gitignore_spec(
        self, gitignore_url: str,
    ) -> pathspec.PathSpec | None:
        """Return the PathSpec for a .gitignore, or None if absent/empty."""
        if not self.filesystem.exists(gitignore_url):
            return None
        with self.filesystem.open(gitignore_url, "rt") as f:
            raw = cast(str, f.read())
        lines = [
            line for line in raw.splitlines()
            if line and not line.startswith("#")
        ]
        if not lines:
            return None
        return pathspec.PathSpec.from_lines("gitignore", lines)

    def _collect_ancestor_specs(
        self, resource_path: str,
    ) -> list[tuple[int, pathspec.PathSpec]]:
        """Seed gitignore specs from every directory above the resource.

        Walk the directories between the GRR root (``self.url``, inclusive)
        and the resource directory (exclusive), reading each ``.gitignore``.
        The walk never climbs above ``self.url``, so a ``.gitignore`` outside
        the GRR is never read. Each spec is anchored by the depth (from the
        GRR root) of the directory that holds it, so it matches files under
        the resource relative to its ``.gitignore`` root -- as git applies a
        repository-root rule to a nested path. The resource's own
        ``.gitignore`` is read by the descending scan, not here.

        Ancestor and descendant specs are OR-combined by :meth:`_is_gitignored`
        (any match ignores); cross-level ``!`` negation -- a deeper
        ``.gitignore`` re-including a file an ancestor ignored -- is NOT
        honored. See that method's docstring for the limitation.
        """
        parts = [part for part in resource_path.split("/") if part]
        specs: list[tuple[int, pathspec.PathSpec]] = []
        for depth in range(len(parts)):
            spec = self._load_gitignore_spec(
                os.path.join(self.url, *parts[:depth], ".gitignore"))
            if spec is not None:
                specs.append((depth, spec))
        return specs

    @staticmethod
    def _is_gitignored(
        name: str,
        full_parts: list[str],
        specs: list[tuple[int, pathspec.PathSpec]],
    ) -> bool:
        """Return True if name is excluded by any accumulated gitignore spec.

        Specs are combined by OR: ``name`` is dropped if *any* level's
        ``.gitignore`` matches it. This matches git for the common case of
        non-negated patterns, but it does NOT implement git's full
        last-match-wins-across-levels semantics: a file ignored by an ancestor
        ``.gitignore`` cannot be re-included by a ``!pattern`` in a deeper
        ``.gitignore`` (cross-level negation). Negation within a single
        ``.gitignore`` still works, since ``pathspec`` resolves last-match
        inside one spec. This limitation is characterized by
        ``test_gitignore_ancestor_negation_across_levels_is_not_honored``.
        """
        for anchor_depth, spec in specs:
            # Path of `name` relative to the directory that holds this
            # .gitignore: drop the leading `anchor_depth` components (GRR root
            # -> that directory) from the current path, then add the file name.
            rel_parts = [*full_parts[anchor_depth:], name]
            rel_path = "/".join(rel_parts)
            # Check as both a file and a directory path so that
            # trailing-/ patterns (e.g. logs/) prune whole directories.
            if spec.match_file(rel_path) or spec.match_file(rel_path + "/"):
                return True
        return False

    def _get_filepath_timestamp(self, filepath: str) -> float:
        try:
            modification = self.filesystem.modified(filepath)
            modification = modification.replace(tzinfo=datetime.UTC)
            return cast(float, round(modification.timestamp(), 2))
        except NotImplementedError:
            info = self.filesystem.info(filepath)
            modification = cast(float, info.get("created"))
            return cast(float, round(modification, 2))

    def collect_all_resources(self) -> Generator[GenomicResource, None, None]:
        """Return generator over all resources managed by this protocol."""
        for res_id, res_ver, res_path in self._scan_path_for_resources([]):
            res_fullpath = os.path.join(self.root_path, res_path)
            assert res_fullpath.startswith("/")
            res_fullpath = f"{self.scheme}://{self.netloc}{res_fullpath}"

            with self.filesystem.open(
                    os.path.join(
                        res_fullpath, GR_CONF_FILE_NAME), "rt") as infile:
                config = yaml.safe_load(infile)

            manifest: Manifest | None = None
            manifest_filename = os.path.join(
                res_fullpath, GR_MANIFEST_FILE_NAME)

            if self.filesystem.exists(manifest_filename):
                with self.filesystem.open(manifest_filename, "rt") as infile:
                    logger.debug("loading manifest from %s", manifest_filename)
                    manifest = Manifest.from_file_content(
                        cast(str, infile.read()))
            yield self.build_genomic_resource(
                res_id, res_ver, config, manifest)

    def scan_resource_entries(self, resource: GenomicResource) -> ResourceScan:
        """Scan the resource and return what was found."""
        resource_path = resource.get_genomic_resource_id_version()

        result = Manifest()
        unreadable: dict[str, str] = {}
        ancestor_specs = self._collect_ancestor_specs(resource_path)
        for name, path in self._scan_resource_for_files(
                resource_path, [], ancestor_specs):
            # The two pages `resource-info` writes are build artefacts, not
            # resource data, and stay out of the manifest -- whether or not
            # DVC manages them, since both are regenerated on every run.
            # Every OTHER file is manifested, whatever its extension: the
            # old "drop every name ending in html" rule was a proxy for "this
            # is a page GAIn generated" and silently dropped any html file a
            # resource legitimately carries as data (#373).
            if is_generated_info_page(name):
                continue

            try:
                size = self._get_filepath_size(path)
            except OSError as error:
                # The listing yielded this name and the stat could not
                # describe it. Reported rather than raised: a `.dvc` sidecar
                # may still describe it, and only the caller -- which has
                # the sidecars -- can tell a garbage-collected DVC cache
                # link from a genuinely broken resource (gain#503).
                #
                # `OSError`, not `FileNotFoundError`: a symlink into a
                # shared DVC cache fails to resolve for more reasons than a
                # collected cache object -- a loop (ELOOP), a target whose
                # parent is not a directory (ENOTDIR), a cache directory
                # this run may not traverse (EACCES). They are one situation
                # to the user, whose `exists()` is False for every one of
                # them, and were one crash each.
                #
                # Caught rather than pre-tested so that a repository with
                # nothing broken pays NOTHING: the happy path is the same
                # single stat it always was, and the link probe below runs
                # only for a name that already failed.
                if self.scheme != "file":
                    # Only a local filesystem has symlinks. A remote store
                    # that lists a key and then fails to describe it is far
                    # likelier to be a transient fault than a steady state,
                    # and letting a sidecar answer for it would publish an
                    # md5 sum for an object that is not in the bucket.
                    raise
                reason = self._unreadable_detail(path, error)
                logger.debug(
                    "cannot read <%s> of <%s>: %s",
                    name, resource.resource_id, reason)
                unreadable[name] = reason
                continue
            result.add(ManifestEntry(name, size, None))
        return ResourceScan(result, unreadable)

    def _unreadable_detail(self, path: str, error: OSError) -> str:
        """Return why ``path`` could not be read, for a human to read.

        For the case this was written for -- a link into a DVC cache that
        is no longer resolvable -- naming the link target IS the diagnosis,
        and the errno distinguishes a collected cache object from a cache
        that is merely unreachable. Only ever called for a name that
        already failed to stat, so this never touches the happy path.
        """
        reason = error.strerror or type(error).__name__
        local_path = path.removeprefix(f"{self.scheme}://")
        target: str | None = None
        with suppress(OSError):
            if os.path.islink(local_path):
                target = os.readlink(local_path)
        if target is None:
            return reason
        return f"a symlink to <{target}>: {reason}"

    def _enumerate_resources(self) -> Iterable[GenomicResource]:
        """Enumerate by scanning the repository, not by reading ``.CONTENTS``.

        The whole of this class's contribution to ``get_all_resources_dict``;
        the memo, its lock and the ordering are inherited (#515).

        Scanning is not a preference here, it is the only thing that can
        work: this class *writes* ``.CONTENTS`` -- ``build_content_file``
        enumerates through ``get_all_resources`` and serializes the result --
        so reading it back to answer that same enumeration would be circular,
        and on a repository that has never been repaired there is no
        ``.CONTENTS`` to read at all. That is the ``grr_manage repo-repair``
        path over a fresh directory.
        """
        return self.collect_all_resources()

    def _get_resource_file_state_path(
            self, resource: GenomicResource, filename: str) -> str:
        """Return filename of the resource file state path.

        This joins the resource url itself and so does NOT go through
        ``get_resource_file_url``; the containment check is repeated here on
        purpose -- see gain#467.
        """
        validate_resource_file_name(resource.resource_id, filename)
        resource_url = self.get_resource_url(resource)
        return os.path.join(
            resource_url, GRR_INTERNAL_DIR, f"{filename}.state")

    def _get_resource_file_download_path(
            self, resource: GenomicResource, filename: str) -> str:
        """Return a unique path to download a resource file into.

        Inside the resource's ``.grr`` directory, next to the file's
        ``.state`` and ``.lockfile``: protocol-internal, on the same
        filesystem as the resource file itself, and skipped by everything
        that enumerates resource files. The ``uuid`` component keeps two
        concurrent attempts on the same file from writing the same temp
        path. See gain#273.

        Another join of its own, so it repeats the containment check --
        see gain#467.
        """
        validate_resource_file_name(resource.resource_id, filename)
        resource_url = self.get_resource_url(resource)
        return os.path.join(
            resource_url, GRR_INTERNAL_DIR,
            f"{filename}.{uuid.uuid4().hex}.part")

    def get_resource_file_timestamp(
            self, resource: GenomicResource, filename: str) -> float:
        url = self.get_resource_file_url(resource, filename)
        return self._get_filepath_timestamp(url)

    def _get_filepath_size(
            self, filepath: str) -> int:
        fileinfo = self.filesystem.info(filepath)
        return int(fileinfo["size"])

    def get_resource_file_size(
            self, resource: GenomicResource, filename: str) -> int:
        path = self.get_resource_file_url(resource, filename)
        return self._get_filepath_size(path)

    def save_resource_file_state(
            self, resource: GenomicResource, state: ResourceFileState) -> None:
        """Save resource file state into internal GRR state."""
        path = self._get_resource_file_state_path(resource, state.filename)
        if not self.filesystem.exists(os.path.dirname(path)):
            self.filesystem.makedirs(
                os.path.dirname(path), exist_ok=True)

        content = asdict(state)
        with self.filesystem.open(path, "wt", encoding="utf8") as outfile:
            outfile.write(yaml.safe_dump(content))

    def load_resource_file_state(
            self, resource: GenomicResource,
            filename: str) -> ResourceFileState | None:
        """Load resource file state from internal GRR state.

        If the specified resource file has no internal state returns None.
        """
        path = self._get_resource_file_state_path(resource, filename)
        if not self.filesystem.exists(path):
            return None
        with self.filesystem.open(path, "rt", encodings="utf8") as infile:
            content = yaml.safe_load(infile.read())
            if content is None or not content:
                return None
            return ResourceFileState(
                content["filename"],
                content["size"],
                content["timestamp"],
                content["md5"],
            )

    def delete_resource_file(
            self, resource: GenomicResource, filename: str) -> None:
        """Delete a resource file and it's internal state."""
        filepath = self.get_resource_file_url(resource, filename)
        if self.filesystem.exists(filepath):
            self.filesystem.delete(filepath)

        statepath = self._get_resource_file_state_path(resource, filename)
        if self.filesystem.exists(statepath):
            self.filesystem.delete(statepath)

    def copy_resource_file(
            self,
            remote_resource: GenomicResource,
            dest_resource: GenomicResource,
            filename: str,
            on_bytes: Callable[[int], None] | None = None,
    ) -> ResourceFileState | None:
        """Copy a resource file into repository.

        A transient stall or drop mid-download (common when fetching a large
        resource over a slow HTTP GRR link) is retried from scratch with
        exponential backoff rather than aborting the file. See gain#43.

        ``on_bytes``, when given, is called with the number of bytes written
        for each chunk during the download (see gain#77). Because a retried
        attempt re-downloads the whole file from scratch, the bytes credited
        by a failed attempt are rolled back with a single compensating
        negative call before the retry, so a caller-side byte counter never
        double-counts.
        """
        assert dest_resource.resource_id == remote_resource.resource_id
        logger.debug(
            "copying resource file (%s: %s) from %s",
            remote_resource.resource_id, filename,
            remote_resource.proto.proto_id)
        remote_manifest = remote_resource.get_manifest()
        if filename not in remote_manifest:
            self.delete_resource_file(dest_resource, filename)
            return None

        manifest_entry = remote_manifest[filename]

        dest_filepath = self.get_resource_file_url(dest_resource, filename)
        dest_parent = os.path.dirname(dest_filepath)
        if not self.filesystem.exists(dest_parent):
            self.filesystem.mkdir(
                dest_parent, create_parents=True, exist_ok=True)

        # Bytes credited to on_bytes during the current attempt, so a
        # retryable failure can roll them back before the next attempt.
        attempt_bytes = 0

        def tracking_on_bytes(n: int) -> None:
            nonlocal attempt_bytes
            attempt_bytes += n
            assert on_bytes is not None
            on_bytes(n)

        wrapped_on_bytes = (
            tracking_on_bytes if on_bytes is not None else None)

        last_error: BaseException | None = None
        for attempt in range(1, _COPY_MAX_ATTEMPTS + 1):
            attempt_bytes = 0
            try:
                return self._download_resource_file(
                    remote_resource, dest_resource, filename,
                    dest_filepath, manifest_entry.md5,
                    expected_size=manifest_entry.size,
                    on_bytes=wrapped_on_bytes)
            except _RETRYABLE_COPY_ERRORS as error:
                last_error = error
                if on_bytes is not None and attempt_bytes:
                    # roll back the partially-credited bytes of this attempt
                    on_bytes(-attempt_bytes)
                if attempt >= _COPY_MAX_ATTEMPTS:
                    break
                delay = _COPY_BACKOFF_BASE * (3 ** (attempt - 1))
                logger.warning(
                    "transient failure downloading (%s: %s): %s; "
                    "retrying in %ss (attempt %s/%s)",
                    dest_resource.resource_id, filename, error,
                    delay, attempt + 1, _COPY_MAX_ATTEMPTS)
                time.sleep(delay)

        assert last_error is not None
        raise last_error

    def _download_resource_file(
            self,
            remote_resource: GenomicResource,
            dest_resource: GenomicResource,
            filename: str,
            dest_filepath: str,
            expected_md5: str | None,
            *,
            expected_size: int,
            on_bytes: Callable[[int], None] | None = None,
    ) -> ResourceFileState:
        """Download a single resource file once, verify it, then publish it.

        Opens a fresh remote handle and streams into a private temp file in
        the resource's ``.grr`` directory; the file is moved to its real
        path only once it has been verified, so the repository never holds
        an unverified resource file and a failed attempt leaves nothing
        behind at the real path (gain#273). On a local filesystem the move
        is an atomic rename; on an object store it degrades to a
        copy-and-delete, which loses the atomicity but keeps the
        verify-then-publish order -- one code path for every scheme.

        The download is verified twice: the number of bytes written must
        equal the manifest's recorded size (a silent short read in the
        fsspec range-reassembly layer ends the stream early and would
        otherwise produce a truncated file that only fails at the md5 check
        -- gain#292), and the md5 of the written bytes must match the
        manifest. The size check runs first so a truncation is reported as
        such, with both byte counts, rather than as an opaque checksum
        mismatch.

        ``on_bytes``, when given, is called with the length of each chunk
        right after it is written, to drive a byte-level progress bar
        (see gain#77).
        """
        tmp_filepath = self._get_resource_file_download_path(
            dest_resource, filename)
        tmp_parent = os.path.dirname(tmp_filepath)
        if not self.filesystem.exists(tmp_parent):
            self.filesystem.makedirs(tmp_parent, exist_ok=True)

        published = False
        try:
            bytes_written = 0
            with remote_resource.open_raw_file(
                    filename, "rb",
                    uncompress=False) as infile, \
                    self.filesystem.open(tmp_filepath, "wb") as outfile:

                md5_hash = hashlib.md5()  # noqa
                while chunk := infile.read(self.CHUNK_SIZE):
                    outfile.write(chunk)
                    bytes_written += len(chunk)
                    if on_bytes is not None:
                        on_bytes(len(chunk))
                    md5_hash.update(chunk)

            md5 = md5_hash.hexdigest()

            if not self.filesystem.exists(tmp_filepath):
                raise OSError(f"destination file not created {tmp_filepath}")

            if bytes_written != expected_size:
                raise TruncatedDownloadError(
                    f"file copy is truncated "
                    f"{dest_resource.resource_id} ({filename}); "
                    f"received {bytes_written} bytes, "
                    f"expected {expected_size}")

            if md5 != expected_md5:
                raise ChecksumMismatchError(
                    f"file copy is broken "
                    f"{dest_resource.resource_id} ({filename}); "
                    f"received {bytes_written} bytes (size ok); "
                    f"md5sum are different: "
                    f"{md5}!={expected_md5}")

            self.filesystem.mv(tmp_filepath, dest_filepath)
            published = True
        finally:
            if not published:
                self._discard_partial_download(tmp_filepath)

        state = self.build_resource_file_state(
            dest_resource,
            filename,
            md5=md5)

        self.save_resource_file_state(dest_resource, state)

        return state

    def _discard_partial_download(self, tmp_filepath: str) -> None:
        """Remove the temp file of a download that was never published.

        Called for every way out of :meth:`_download_resource_file` that
        does not publish -- a checksum mismatch, a stalled read, an
        interrupt -- so no attempt leaves a partial behind. The temp file
        may not exist at all (the remote handle can fail before the first
        write), and a removal that fails must not replace the failure that
        got us here: the retry loop classifies the error it sees, and a
        cleanup error in its place would be neither retryable nor true.
        """
        try:
            self.filesystem.rm(tmp_filepath)
        except FileNotFoundError:
            pass
        except Exception:  # noqa: BLE001  pylint: disable=broad-except
            logger.warning(
                "unable to remove the partial download %s",
                tmp_filepath, exc_info=True)

    def classify_resource_file(
            self, remote_resource: GenomicResource,
            dest_resource: GenomicResource,
            filename: str) -> FileCacheVerdict:
        """Decide whether a resource file needs (re)downloading.

        This is the lock-free decision half of :meth:`update_resource_file`:
        it performs the same checks and the same state-refresh side effect
        (rebuild + save the ``.state`` on a missing state or a timestamp/size
        drift, and delete a file no longer in the remote manifest), but it
        never copies/downloads. The verdict's ``size`` is the manifest byte
        size for files that will download (0 otherwise). See gain#78.
        """
        assert dest_resource.resource_id == remote_resource.resource_id

        remote_manifest = remote_resource.get_manifest()

        if not self.file_exists(dest_resource, filename):
            size = (
                remote_manifest[filename].size
                if filename in remote_manifest else 0)
            return FileCacheVerdict(needs_download=True, size=size)

        local_state = self.load_resource_file_state(dest_resource, filename)
        if local_state is None:
            local_state = self.build_resource_file_state(
                dest_resource, filename)
            self.save_resource_file_state(dest_resource, local_state)
        else:
            timestamp = self.get_resource_file_timestamp(
                dest_resource, filename)
            size = self.get_resource_file_size(dest_resource, filename)
            if timestamp != local_state.timestamp or \
                    size != local_state.size:
                local_state = self.build_resource_file_state(
                    dest_resource, filename)
                self.save_resource_file_state(dest_resource, local_state)

        if filename not in remote_manifest:
            self.delete_resource_file(dest_resource, filename)
            return FileCacheVerdict(needs_download=False, size=0)
        manifest_entry = remote_manifest[filename]
        if local_state.md5 != manifest_entry.md5:
            return FileCacheVerdict(
                needs_download=True, size=manifest_entry.size)

        return FileCacheVerdict(needs_download=False, size=0)

    def update_resource_file(
            self, remote_resource: GenomicResource,
            dest_resource: GenomicResource,
            filename: str) -> ResourceFileState | None:
        """Update a resource file into repository if needed."""
        verdict = self.classify_resource_file(
            remote_resource, dest_resource, filename)
        if verdict.needs_download:
            return self.copy_resource_file(
                remote_resource, dest_resource, filename)
        # No download needed: a file deleted because it left the remote
        # manifest has no state to return (load returns None); an up-to-date
        # file returns its current persisted state.
        return self.load_resource_file_state(dest_resource, filename)

    def _manifest_for_repository_index(
            self, res: GenomicResource,
            failed: frozenset[str]) -> Manifest | None:
        """The manifest to publish for ``res`` in repository-wide files.

        A resource this run FAILED to verify must not have its manifest
        rebuilt from scratch here: that fallback hashes the drifted
        bytes, writes a state and publishes an md5 the run had just
        refused to record, dropping any pointer-only entry on the way.
        Publish the manifest it already had committed, or -- if it
        never had one -- leave it out of the repository index entirely
        (#373).
        """
        if res.resource_id in failed:
            try:
                return self.load_manifest(res)
            except FileNotFoundError:
                return None
        try:
            return res.get_manifest()
        except UnsupportedDvcDirectoryOutputError as err:
            # The walk met a manifest-less `dvc add <dir>` resource the
            # command never selected, and the fallback build refused it
            # (#284, via #721's sidecar collection). That is THIS
            # resource's failure alone: it is left out of the index, with
            # a report, and the walk goes on -- one refused resource must
            # not unpublish the healthy ones (the gain#503 shape). A
            # committed manifest, had it one, would have been loaded
            # above the refusal, so there is nothing older to fall back
            # to here.
            # The refusal message is complete on its own; a traceback
            # would only bury it, hence `error`, not `exception`.
            logger.error(  # noqa: TRY400
                "not publishing <%s> in the repository index: %s",
                res.resource_id, err)
            return None

    def build_content_file(
        self, failed: frozenset[str] = frozenset(),
    ) -> list[dict[str, Any]]:
        """Build the content of the repository (i.e '.CONTENTS.json.gz').

        ``failed`` names resources this run could not verify; each is
        published from the manifest it already had, or left out if it
        never had one, so a failed run never rebuilds a manifest from
        scratch and poisons the contents with it (#373).

        Only the gzipped index is written. An uncompressed
        ``.CONTENTS.json`` left by an older release is reported rather
        than deleted (#758).
        """
        content = []
        for res in self.get_all_resources():
            manifest = self._manifest_for_repository_index(res, failed)
            if manifest is None:
                continue
            content.append({
                "full_id": res.get_full_id(),
                "id": res.resource_id,
                "version": res.get_version_str(),
                "config": res.get_config(),
                "manifest": manifest.to_manifest_entries(),
            })
        content = sorted(content, key=operator.itemgetter("id"))

        content_filepath = os.path.join(
            self.url, GR_CONTENTS_FILE_NAME)

        # gzip header OS byte (offset 9) is normalised to 0xff
        # ("unknown") so the file is byte-deterministic across
        # Python distributions. Upstream CPython hardcodes 0xff,
        # but Debian's Python patches gzip.compress to emit 0x03
        # ("Unix"), which means the same input produces different
        # bytes between a conda Python and a python:3.x-slim
        # container — enough to flag .CONTENTS.json.gz as modified
        # under `git status --porcelain` in CI even when the JSON
        # payload is identical.
        gz = gzip.compress(
            json.dumps(
                content, indent=2, sort_keys=True).encode("utf8"),
            mtime=0)
        gz = gz[:9] + b"\xff" + gz[10:]

        with self.filesystem.open(content_filepath, "wb") as outfile:
            outfile.write(gz)

        # Left where it is rather than deleted: in the GRRs that carry
        # one it is a tracked file, and a publish has no business
        # authoring that deletion in someone else's git tree.
        abandoned = os.path.join(self.url, GR_LEGACY_CONTENTS_FILE_NAME)
        if self.filesystem.exists(abandoned):
            logger.warning(
                "%s is stale: the repository index is published gzipped "
                "only, so whatever an older release left there has just "
                "been left behind by this publish. Only %s is maintained; "
                "the stale file can be deleted.",
                abandoned, GR_CONTENTS_FILE_NAME)

        return content

    def build_index_info(
        self,
        repository_template: str = "grr_index.jinja",
        about_template: str | None = "grr_about.jinja",
        failed: frozenset[str] = frozenset(),
    ) -> dict:
        """Build info dict for the repository.

        ``failed`` names resources this run could not verify; each is
        described from the manifest it already had, or left off the
        index page if it never had one, so the page never triggers a
        build-from-scratch of a failed resource's manifest (#373).
        """
        result = {}
        for res in self.get_all_resources():
            manifest = self._manifest_for_repository_index(res, failed)
            if manifest is None:
                continue
            res_size = convert_size(
                sum(f for _, f in manifest.get_files()),
            )
            assert res.config is not None
            result[res.get_full_id()] = {
                "res_full_id": res.get_full_id(),
                "res_id": res.resource_id,
                **res.config,
                "res_version": res.get_version_str(),
                "res_files": len(list(manifest.get_files())),
                "res_size": res_size,
                "res_summary": res.get_summary(),
            }

        about_md_path = os.path.join(self.url, "about.md")
        has_about = self.filesystem.exists(about_md_path)

        about_html_content = ""
        if has_about:
            with self.filesystem.open(
                    about_md_path, "rt", encoding="utf8") as infile:
                about_md_raw = infile.read()
            try:
                about_html_content = markdown(about_md_raw)
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.exception(
                    "Error occurred while converting about.md to HTML for %s",
                    self.get_url(),
                )
                raise ValueError from e

            with self.filesystem.open(
                os.path.join(self.url, "about.html"), "wt", encoding="utf8",
            ) as outfile:
                if about_template is not None:
                    outfile.write(get_template(about_template).render(
                        about_contents=about_html_content))
                else:
                    outfile.write(about_html_content)

        sqlite3_hash = ""
        gz_path = os.path.join(self.url, GR_SQLITE_META_FILE_NAME)
        if self.filesystem.exists(gz_path):
            with self.filesystem.open(gz_path, "rb") as gz_file:
                gz_bytes: bytes = cast(bytes, gz_file.read())
            sqlite3_hash = hashlib.md5(gz_bytes).hexdigest()  # noqa: S324

        content_filepath = os.path.join(self.url, GR_INDEX_FILE_NAME)
        with self.filesystem.open(
                content_filepath, "wt", encoding="utf8") as outfile:
            outfile.write(get_template(repository_template).render(
                data=result,
                has_about=has_about,
                sqlite3_hash=sqlite3_hash,
            ))

        return result


def build_local_resource(
        dirname: str, config: dict[str, Any]) -> GenomicResource:
    """Build a resource from a local filesystem directory."""
    proto = build_fsspec_protocol("d", dirname)
    return GenomicResource(".", (0, ), proto, config)


def _build_filesystem(
    url: str, **kwargs: Any,
) -> fsspec.AbstractFileSystem:
    # pylint: disable=import-outside-toplevel
    # A keyword read here is a keyword that configures a protocol, so adding
    # one means adding it to ``_FILESYSTEM_KWARGS`` too -- otherwise a rebuild
    # may silently change it under the protocol's holders (#514).
    parsed_url = urlparse(url)
    if parsed_url.scheme in {"file", ""}:
        from fsspec.implementations.local import LocalFileSystem
        return LocalFileSystem()
    if parsed_url.scheme in {"http", "https"}:
        import aiohttp
        from fsspec.implementations.http import HTTPFileSystem
        base_url = kwargs.get("base_url")
        # Relax aiohttp's default 300s total read timeout: a large GRR
        # resource (e.g. the ~15GB genome-wide gnomAD file) legitimately
        # downloads for far longer. total=None lifts the overall cap while
        # sock_read/sock_connect still turn a genuinely stalled read or hung
        # connect into a (retryable) error rather than killing the run. See
        # gain#43.
        client_kwargs: dict[str, Any] = {
            "base_url": base_url,
            "timeout": aiohttp.ClientTimeout(
                total=None, sock_read=120, sock_connect=60),
        }
        user = kwargs.get("user")
        password = kwargs.get("password")
        if user is not None and password is not None:
            client_kwargs["auth"] = aiohttp.BasicAuth(user, password)
        return HTTPFileSystem(client_kwargs=client_kwargs)
    if parsed_url.scheme == "s3":
        from s3fs.core import S3FileSystem
        endpoint_url = kwargs.get("endpoint_url")
        return S3FileSystem(
            anon=False, client_kwargs={"endpoint_url": endpoint_url})
    if parsed_url.scheme == "memory":
        from fsspec.implementations.memory import MemoryFileSystem
        return MemoryFileSystem()
    raise NotImplementedError(f"unsupported schema {parsed_url.scheme}")


FsspecRepositoryProtocol = FsspecReadOnlyProtocol | FsspecReadWriteProtocol


_FSSPEC_PROTOCOLS: dict[tuple[str, str], FsspecRepositoryProtocol] = {}

#: The constructions currently in flight, one per key at most. A key is here
#: OR in ``_FSSPEC_PROTOCOLS``, never both: an instance moves across when its
#: whole ``__init__`` chain (or ``__setstate__``) has returned, which is what
#: keeps an unconfigured protocol from ever being reachable (#527).
_FSSPEC_PROTOCOLS_UNDER_CONSTRUCTION: dict[
    tuple[str, str], _ProtocolConstruction] = {}

#: Serialises every look at the two dicts above. Held only across the memo
#: read and the bookkeeping that answers it -- never across ``__init__``,
#: which for a read-write protocol does filesystem I/O and would otherwise
#: put every protocol construction in the process behind one remote round
#: trip (#527).
_FSSPEC_PROTOCOLS_GUARD = Lock()


#: The string spellings of a boolean this module accepts for ``read_only``,
#: matching what the definition models coerce and what yaml would have
#: produced unquoted. Kept deliberately closed: a value outside it is a
#: mistake to report, not a value to guess at.
_FALSE_SPELLINGS = frozenset({"false", "no", "off", "0", ""})
_TRUE_SPELLINGS = frozenset({"true", "yes", "on", "1"})


def _resolve_read_only(*, value: str | bool | None) -> bool | None:
    """Return ``read_only`` as a boolean, or ``None`` if it was not passed.

    ``read_only`` is documented as the one boolean keyword, but it reaches
    this module as a *string* from two directions, and a bare truthiness test
    read every one of them -- ``"false"`` included -- as read-only, which is
    the inversion of what was asked for (#528):

    * ``grr_manage --extra-args read_only=false`` parses into ``dict[str,
      str]`` and is splatted in verbatim;
    * a repository definition is built from the **raw** definition dict, not
      from the validated model, so a quoted ``read_only: "false"`` in yaml
      arrives here as ``"false"`` even though ``FileRepoDefinition`` coerced
      it to ``False`` when the definition was checked.

    A value that spells no boolean at all raises rather than defaulting: it
    can only be a mistake, and every way of guessing at it silently produces
    a repository in a mode nobody asked for.
    """
    if value is None or isinstance(value, bool):
        return value
    spelling = value.strip().lower()
    if spelling in _FALSE_SPELLINGS:
        return False
    if spelling in _TRUE_SPELLINGS:
        return True
    spellings = sorted(_TRUE_SPELLINGS | (_FALSE_SPELLINGS - {""}))
    raise ValueError(
        f"read_only must be a boolean, not {value!r}; write one of "
        f"{spellings} if it has to be spelled as a string")


def build_fsspec_protocol(
    proto_id: str, root_url: str, **kwargs: str | bool | None,
) -> FsspecRepositoryProtocol:
    """Create fsspec GRR protocol based on the root url.

    ``read_only`` is the one boolean among the keyword arguments -- hence the
    widened value type; every other keyword is a url or a credential. It is
    absent by default rather than ``False`` so that *asking* for a read-write
    protocol can be told apart from not asking at all: the two mean different
    things on an http(s) url, where only one of them is serviceable (#528).
    """
    # pylint: disable=import-outside-toplevel
    public_url = cast("str | None", kwargs.pop("public_url", None))
    read_only = _resolve_read_only(value=kwargs.pop("read_only", None))
    filesystem = _build_filesystem(root_url, **kwargs)

    url = urlparse(root_url)
    if url.scheme in {"file", "", "s3", "memory"}:
        if read_only:
            return FsspecReadOnlyProtocol(
                proto_id, root_url,
                filesystem=filesystem,
                public_url=public_url,
                **kwargs)
        return FsspecReadWriteProtocol(
            proto_id, root_url,
            filesystem=filesystem,
            public_url=public_url,
            **kwargs)
    if url.scheme in {"http", "https"}:
        if read_only is not None and not read_only:
            # Asked for read-write over a scheme that cannot serve it. The
            # read-only protocol below is still the only correct object to
            # return, so this refusal costs no capability -- what it buys is
            # that the request is answered rather than dropped. It used to be
            # popped and never consulted here, so the caller learned its
            # protocol was read-only from an absent write method somewhere
            # downstream (#528) -- the same silent-wrong-mode shape #514
            # fixed for the memo rebuild.
            #
            # Note the mode arm of that rebuild refusal cannot cover this: it
            # compares the mode of the class this function *picked*, which on
            # this branch is read-only whatever the caller asked for.
            raise ValueError(
                f"protocol {proto_id!r} over {_strip_url_userinfo(root_url)} "
                f"cannot be built read-write: an http(s) repository is "
                f"read-only -- there is nothing to create over http and no "
                f"lockfile to take. Omit read_only to build the read-only "
                f"protocol, or use a file:// or s3:// url for a repository "
                f"that can be written to")
        return FsspecReadOnlyProtocol(
            proto_id, root_url,
            filesystem=filesystem,
            public_url=public_url,
            **kwargs)

    raise NotImplementedError(f"unsupported schema {url.scheme}")
