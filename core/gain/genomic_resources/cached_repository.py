"""Provides caching genomic resources."""
from __future__ import annotations

import os
import posixpath
import sys
import threading
from collections.abc import Callable, Generator, Iterable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import IO, Any, cast
from urllib.parse import urlparse

import apsw
import pysam
from tqdm import tqdm

from gain import logging
from gain.genomic_resources.fsspec_protocol import (
    FileCacheVerdict,
    FsspecReadWriteProtocol,
    _strip_url_userinfo,
)
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResource,
    GenomicResourceRepo,
    Manifest,
    ReadOnlyRepositoryProtocol,
    is_safe_repo_id,
    resolve_tabix_index_filename_for_read,
)

from .fsspec_protocol import build_fsspec_protocol

logger = logging.getLogger(__name__)


class CacheResource(GenomicResource):
    """Represents resources stored in cache."""

    def __init__(self, resource: GenomicResource, protocol: CachingProtocol):
        super().__init__(
            resource.resource_id,
            resource.version,
            protocol,
            config=resource.config,
            manifest=resource.get_manifest())


class CachingProtocol(ReadOnlyRepositoryProtocol):
    """Defines caching GRR repository protocol."""

    def __init__(
        self,
        remote_protocol: ReadOnlyRepositoryProtocol,
        local_protocol: FsspecReadWriteProtocol,
        public_url: str | None = None,
    ):
        if local_protocol.scheme != "file":
            # The caching protocol serialises concurrent downloads with a
            # per-file lockfile, which only means anything on a local
            # filesystem. A cache anywhere else silently had no mutual
            # exclusion at all, so concurrent readers observed partially
            # written files. Refuse the configuration here, before any
            # caching work begins, rather than at the first acquisition.
            # See #473.
            # ``get_url()`` is already userinfo-free -- see
            # ``FsspecReadOnlyProtocol.__init__`` -- so no credential can
            # reach this message or the logs it lands in.
            raise ValueError(
                f"a GRR cache must be on a local filesystem; cache url "
                f"<{local_protocol.get_url()}> uses the unsupported scheme "
                f"<{local_protocol.scheme}>")
        self.remote_protocol = remote_protocol
        self.local_protocol = local_protocol
        super().__init__(local_protocol.proto_id, local_protocol.get_url())
        self.public_url = public_url or remote_protocol.get_public_url()
        self._all_resources: dict[str, CacheResource] | None = None
        # Mirrors FsspecReadOnlyProtocol: the memo is populated lazily, so
        # the check-then-populate has to be atomic or two threads build two
        # sets of CacheResource objects for the same resources. A plain
        # (non-reentrant) Lock is enough here -- nothing under it re-enters
        # this protocol; the reentrant one lives on the repository, which
        # does. See #446.
        self._all_resources_lock = threading.Lock()

    def __getstate__(self) -> dict[str, Any]:
        state = self.__dict__.copy()
        # A lock is unpicklable, and this class pickles today (a cache-backed
        # resource carries its protocol along). Drop it here and rebuild it
        # in __setstate__, exactly as FsspecReadOnlyProtocol does. The memo
        # itself pickles fine and is kept.
        del state["_all_resources_lock"]
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)
        self._all_resources_lock = threading.Lock()

    def get_url(self) -> str:
        return self.remote_protocol.get_url()

    def get_public_url(self) -> str:
        return self.public_url

    def invalidate(self) -> None:
        # Under the same lock as the population below: clearing the memo
        # while another thread is halfway through building it would leave
        # that thread's freshly built dict installed over the invalidation.
        with self._all_resources_lock:
            self.remote_protocol.invalidate()
            self.local_protocol.invalidate()
            self._all_resources = None

    def get_all_resources(self) -> Generator[GenomicResource, None, None]:
        yield from self.get_all_resources_dict().values()

    def get_all_resources_dict(self) -> dict[str, GenomicResource]:
        with self._all_resources_lock:
            if self._all_resources is None:
                self._all_resources = {
                    resource.get_full_id():
                        self._create_cache_resource(resource)
                    for resource in self.remote_protocol.get_all_resources()
                }
                self.local_protocol.invalidate()
            return cast(dict[str, GenomicResource], self._all_resources)

    def _create_cache_resource(
            self, remote_resource: GenomicResource) -> CacheResource:

        return CacheResource(
            remote_resource,
            self)

    def refresh_cached_resource_file(
        self, resource: GenomicResource, filename: str,
    ) -> tuple[str, str]:
        """Refresh a resource file in cache if neccessary."""
        assert resource.proto == self

        if filename.endswith(".lockfile"):
            # Ignore lockfiles
            return (resource.resource_id, filename)

        remote_resource = self.remote_protocol.get_resource(
            resource.resource_id,
            f"={resource.get_version_str()}")

        # Lock the resource file to avoid caching it simultaneously
        with self.local_protocol.obtain_resource_file_lock(resource, filename):
            self.local_protocol.update_resource_file(
                remote_resource, resource, filename)
        return (resource.resource_id, filename)

    def refresh_cached_resource(
        self, resource: GenomicResource,
    ) -> tuple[str, None]:
        """Refresh all resource files in cache if neccessary."""
        assert resource.proto == self

        for entry in resource.get_manifest():
            filename = entry.name
            if filename.endswith(".lockfile"):
                continue
            remote_resource = self.remote_protocol.get_resource(
                resource.resource_id,
                f"={resource.get_version_str()}")

            # Lock the resource file to avoid caching it simultaneously
            with self.local_protocol.obtain_resource_file_lock(
                    resource, filename):
                self.local_protocol.update_resource_file(
                    remote_resource, resource, filename)
        return (resource.resource_id, None)

    def classify_cached_resource_file(
        self, resource: GenomicResource, filename: str,
    ) -> FileCacheVerdict:
        """Classify a resource file without taking any lock or downloading.

        The lock-free decision half of :meth:`refresh_cached_resource_file`:
        it resolves the remote resource and delegates to the local
        protocol's :meth:`classify_resource_file`. See gain#78.
        """
        assert resource.proto == self

        if filename.endswith(".lockfile"):
            # Ignore lockfiles
            return FileCacheVerdict(needs_download=False, size=0)

        remote_resource = self.remote_protocol.get_resource(
            resource.resource_id,
            f"={resource.get_version_str()}")

        return self.local_protocol.classify_resource_file(
            remote_resource, resource, filename)

    def download_cached_resource_file(
        self, resource: GenomicResource, filename: str,
        *,
        on_bytes: Callable[[int], None] | None = None,
    ) -> tuple[str, str]:
        """Download a resource file into cache unconditionally.

        Takes the per-file lock and copies the file regardless of its local
        state -- the decision was already made by
        :meth:`classify_cached_resource_file`. See gain#78.
        """
        assert resource.proto == self

        remote_resource = self.remote_protocol.get_resource(
            resource.resource_id,
            f"={resource.get_version_str()}")

        # Lock the resource file to avoid caching it simultaneously
        with self.local_protocol.obtain_resource_file_lock(resource, filename):
            self.local_protocol.copy_resource_file(
                remote_resource, resource, filename, on_bytes=on_bytes)
        return (resource.resource_id, filename)

    def get_resource_url(self, resource: GenomicResource) -> str:
        """Return url of the specified resources."""
        return self.local_protocol.get_resource_url(resource)

    def get_resource_file_url(
            self, resource: GenomicResource, filename: str) -> str:
        """Return url of a file in the resource."""
        self.refresh_cached_resource_file(resource, filename)
        return self.local_protocol.get_resource_file_url(resource, filename)

    def open_raw_file(
            self, resource: GenomicResource, filename: str,
            mode: str = "rt", **kwargs: str | bool | None) -> IO:
        if "w" in mode:
            raise OSError(
                f"Read-Only caching protocol {self.get_id()} trying to open "
                f"{filename} for writing")

        self.refresh_cached_resource_file(resource, filename)
        return self.local_protocol.open_raw_file(
            resource, filename, mode, **kwargs)

    def open_tabix_file(
            self, resource: GenomicResource, filename: str,
            index_filename: str | None = None) -> pysam.TabixFile:
        self.refresh_cached_resource_file(resource, filename)
        if index_filename is None:
            # The index may be a ``.tbi`` or a ``.csi``; refresh the one the
            # manifest actually records, not an assumed name (gain#430).
            index_filename = resolve_tabix_index_filename_for_read(
                resource, filename)
        self.refresh_cached_resource_file(resource, index_filename)

        return self.local_protocol.open_tabix_file(
            resource, filename, index_filename)

    def open_vcf_file(
            self, resource: GenomicResource, filename: str,
            index_filename: str | None = None) -> pysam.VariantFile:
        self.refresh_cached_resource_file(resource, filename)
        if index_filename is None:
            index_filename = resolve_tabix_index_filename_for_read(
                resource, filename)
        self.refresh_cached_resource_file(resource, index_filename)

        return self.local_protocol.open_vcf_file(
            resource, filename, index_filename)

    def open_fasta_file(
            self, resource: GenomicResource, filename: str,
            index_filename: str | None = None,
            compressed_index_filename: str | None = None) -> pysam.FastaFile:
        self.refresh_cached_resource_file(resource, filename)
        if index_filename is None:
            index_filename = f"{filename}.fai"
        self.refresh_cached_resource_file(resource, index_filename)
        if compressed_index_filename is None:
            compressed_index_filename = f"{filename}.gzi"
        self.refresh_cached_resource_file(resource, compressed_index_filename)

        return self.local_protocol.open_fasta_file(
            resource, filename, index_filename, compressed_index_filename)

    def open_bigwig_file(
            self, resource: GenomicResource, filename: str) -> Any:
        self.refresh_cached_resource_file(resource, filename)
        return self.local_protocol.open_bigwig_file(resource, filename)

    def file_exists(self, resource: GenomicResource, filename: str) -> bool:
        self.refresh_cached_resource_file(resource, filename)

        return self.local_protocol.file_exists(resource, filename)

    def load_manifest(self, resource: GenomicResource) -> Manifest:
        self.refresh_cached_resource_file(resource, GR_CONF_FILE_NAME)
        return self.remote_protocol.load_manifest(resource)

    def open_repository_sqlite3_metadata_db(self) -> apsw.Connection:
        return self.remote_protocol.open_repository_sqlite3_metadata_db()


class GenomicResourceCachedRepo(GenomicResourceRepo):
    """Defines caching genomic resources repository."""

    def __init__(
            self, child: GenomicResourceRepo, cache_url: str,
            **kwargs: str | None):
        repo_id: str = f"{child.repo_id}.caching_repo"
        super().__init__(repo_id)

        cache_scheme = urlparse(cache_url).scheme
        if cache_scheme not in {"", "file"}:
            # The cache-side protocols are built lazily, one per child
            # repository, so without this the unsupported scheme is only
            # noticed at the first resource access. A GRR cache must be
            # local -- see CachingProtocol.__init__ and #473.
            #
            # The url is redacted with the same helper the cache-hit log
            # lines use: a cache url can embed ``user:pass@`` userinfo, and
            # interpolating it raw would put the secret in the exception
            # text and therefore in the logs. Both sibling messages -- the
            # one above and the repository factory's -- already redact.
            raise ValueError(
                f"a GRR cache must be on a local filesystem; cache url "
                f"<{_strip_url_userinfo(cache_url)}> uses the unsupported "
                f"scheme <{cache_scheme}>")

        logger.debug(
            "creating cached GRR with cache url: %s", cache_url)
        self._all_resources: list[GenomicResource] | None = None
        # Reentrant on purpose: ``get_all_resources`` populates its memo by
        # mapping every child resource through ``_to_cache_resource``, which
        # calls ``_get_or_create_cache_proto`` -- the same thread re-enters
        # the guarded region, and a plain Lock would deadlock on the first
        # enumeration. See #446.
        self._memo_lock = threading.RLock()
        self.child: GenomicResourceRepo = child
        self.cache_url = cache_url
        self.cache_protos: dict[str, CachingProtocol] = {}
        self.additional_kwargs = kwargs

    def __getstate__(self) -> dict[str, Any]:
        state = self.__dict__.copy()
        # A lock is unpicklable, and this repository pickles today (it
        # travels to dask workers with the resources it hands out). Drop the
        # guard here and rebuild it in __setstate__, as
        # FsspecReadOnlyProtocol does with its own. The memoized state itself
        # pickles fine and is kept.
        del state["_memo_lock"]
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)
        self._memo_lock = threading.RLock()

    def invalidate(self) -> None:
        # Same lock as the memo population: invalidating while another thread
        # is building either memo would leave that thread's result installed
        # over the invalidation, and iterating ``cache_protos`` concurrently
        # with an insertion into it is not safe either.
        with self._memo_lock:
            self.child.invalidate()
            for proto in self.cache_protos.values():
                proto.invalidate()
            self._all_resources = None

    def _to_cache_resource(
        self, remote_resource: GenomicResource,
    ) -> GenomicResource:
        """Return the cache-backed twin of a resource from the child repo.

        The child yields resources bound to the *remote* protocol; handing
        those out would let callers read files straight from the remote,
        neither consulting nor populating the cache. Every resource this
        repository produces goes through here. See #428.

        Returns the caching protocol's memoized instance, so the resource is
        the same object ``get_all_resources()`` hands out rather than a
        parallel one. ``_get_or_create_cache_proto`` guarantees the protocol
        wraps this resource's own remote, so the dict holds it.
        """
        cache_proto = self._get_or_create_cache_proto(remote_resource.proto)
        return cache_proto.get_all_resources_dict()[
            remote_resource.get_full_id()]

    def get_all_resources(self) -> Generator[GenomicResource, None, None]:
        # The memo is read into a local under the lock and yielded from
        # outside it: the generator must not hold the lock while a consumer
        # decides when to ask for the next resource.
        with self._memo_lock:
            if self._all_resources is None:
                self._all_resources = [
                    self._to_cache_resource(remote_resource)
                    for remote_resource in self.child.get_all_resources()
                ]
            all_resources = self._all_resources
        yield from all_resources

    def search_resources(
        self,
        search_term: str | None = None,
        resource_type: str | None = None,
        resource_query: str | None = None,
    ) -> Generator[GenomicResource, None, None]:
        # The child is called HERE rather than inside the generator below,
        # so that it validates the query when this call is made instead of
        # on the first iteration.
        return self._to_cache_resources(
            self.child.search_resources(
                search_term, resource_type, resource_query))

    def _to_cache_resources(
        self, remote_resources: Iterable[GenomicResource],
    ) -> Generator[GenomicResource, None, None]:
        # Resolving each hit costs no extra remote enumeration: the FTS
        # search already builds the remote's resource dict to resolve its
        # own hits.
        #
        # The query is evaluated by the child, on the remote resource,
        # before the cache wrapping: both carry the same id and the same
        # labels, and the remote is what the child already has in hand.
        for remote_resource in remote_resources:
            yield self._to_cache_resource(remote_resource)

    def _check_cache_dir_is_the_id_under_the_cache_url(
        self, proto_id: str, cache_dir_path: str,
    ) -> None:
        """Refuse a cache path that is not ``<cache_url>/<proto_id>``.

        The positive half of the id guard. ``is_safe_repo_id`` enumerates
        the shapes known to move the cache -- a blocklist, and every
        blocklist is one url-parser change behind. This states the invariant
        the blocklist exists to protect instead: whatever the id, the
        directory this repository caches into is the single directory named
        by that id, directly under the configured cache url. An id that
        climbs out (``..``) fails the parent half; one that a url parser
        rewrites on the way in (``a\\nb`` arriving as ``ab``, ``a?b``
        arriving as ``a``) fails the name half, which is the same
        wrong-bytes collision two repositories sharing a cache directory
        would cause.

        Only the path is compared. To reach a different host or bucket the
        join would have to have discarded ``cache_url`` outright -- which
        the parent comparison already catches, because what is left is not
        under it.

        A falsy id is exempt in the same way it is exempt from
        ``is_safe_repo_id``: it names no directory, so it caches into the
        cache root itself, as it always has. That is still inside the
        configured cache directory, and two of them collide on the
        duplicate-id guard rather than on this one.
        """
        # ``or "/"``: a cache url that is a bare bucket (``s3://bucket``)
        # parses to an empty path, while the child protocol built under it
        # parses to ``/<proto_id>`` -- the root, spelled out.
        cache_root = posixpath.normpath(urlparse(self.cache_url).path or "/")
        cache_dir = posixpath.normpath(cache_dir_path)
        if not proto_id:
            if cache_dir == cache_root:
                return
        elif (posixpath.dirname(cache_dir) == cache_root
                and posixpath.basename(cache_dir) == proto_id):
            return
        raise ValueError(
            f"the cache directory for repository id {proto_id!r} in "
            f"{self.repo_id} resolves to {cache_dir!r}, which is not "
            f"{proto_id!r} under the configured cache directory "
            f"{self.cache_url!r}; refusing to cache there")

    def _get_or_create_cache_proto(
            self, proto: ReadOnlyRepositoryProtocol) -> CachingProtocol:
        """Return the caching protocol wrapping ``proto``.

        Caching protocols are keyed by ``proto_id``, and the cache directory
        is derived from it, so two remote protocols sharing a ``proto_id``
        would share a caching protocol *and* a cache directory. Resources of
        the second one would then be handed out bound to the first one's
        remote and read the first one's bytes -- a resource id present in
        both repositories silently returns the wrong file's content. Refuse
        the configuration instead: it is not resolvable, and the cache
        directory collision would corrupt cached data either way.

        Defense in depth. A repository built from a GRR definition can no
        longer reach this: ``GroupRepoDefinition`` walks its whole subtree at
        validation time and rejects duplicate repository ids, and a child
        that omits ``id`` is given one synthesised from its path through the
        definition, which is distinct by construction (#445). The walk has to
        be tree-wide for that claim to hold -- a sibling-only check still let
        two id-less children at index 0 of two different groups arrive here
        sharing one id. A group assembled programmatically from protocols --
        as tests and embedding code do -- bypasses validation entirely, so
        the guard stays here too.

        The same reasoning gives the id a second requirement: the cache
        directory is ``cache_url`` joined with the ``proto_id``, so an id
        that is not a single path segment moves the cache somewhere else
        entirely -- ``..`` climbs out of the configured ``cache_dir`` and an
        absolute id makes the join discard ``cache_url`` altogether, writing
        wherever the id points. That is a configuration error, refused here
        rather than sanitised into some other directory (#460). A definition
        can no longer reach this either -- an unsafe ``id`` fails validation,
        and a synthesised id is a safe segment by construction -- but a
        programmatically assembled group can, exactly as with a duplicate id.

        That id check is a blocklist, and the join below goes through a url
        parser that quietly rewrites some ids on the way in.
        ``_check_cache_dir_is_the_id_under_the_cache_url`` states the
        invariant positively instead, and is applied to the url this method
        builds and to the protocol that comes back from building it.
        """
        proto_id = proto.proto_id
        # A falsy proto id is left alone -- it caches into the cache root, as
        # it always has, and two of them collide on the duplicate-id guard
        # below. Only a path-unsafe id is refused here.
        if proto_id and not is_safe_repo_id(proto_id):
            # ``!r``, not ``<...>``: an id refused for carrying a control
            # character prints as nothing at all otherwise.
            raise ValueError(
                f"repository id {proto_id!r} in {self.repo_id} cannot be "
                f"used as a cache directory name; a repository id must be a "
                f"single path segment -- no path separator, no absolute "
                f"path, no control character, and not '.' or '..'. Rename "
                f"this repository where it is constructed: a definition "
                f"carrying such an id is refused when it is loaded, so a "
                f"repository that got here was assembled without one")
        # get / construct / assign must be atomic: two threads racing the
        # first call for one ``proto_id`` would each build a protocol, and
        # the one that lost the assignment is an orphan -- unreachable from
        # ``cache_protos``, hence never invalidated, while its caller keeps
        # holding resources bound to it. See #446.
        with self._memo_lock:
            existing = self.cache_protos.get(proto_id)
            if existing is not None:
                if existing.remote_protocol is not proto:
                    raise ValueError(
                        f"repository id <{proto_id}> is used by more than "
                        f"one repository in {self.repo_id} "
                        f"({existing.remote_protocol.get_url()} and "
                        f"{proto.get_url()}); caching requires distinct "
                        f"repository ids -- give each child repository its "
                        f"own 'id' in the GRR definition")
                return existing

            cached_proto_url = os.path.join(self.cache_url, proto_id)
            # Before building: constructing a read-write protocol MAKES its
            # root directory, so a check that ran only on the object handed
            # back would refuse a directory it had already created.
            self._check_cache_dir_is_the_id_under_the_cache_url(
                proto_id, urlparse(cached_proto_url).path)
            logger.debug(
                "going to create cached protocol with url: %s",
                cached_proto_url)

            cache_proto = build_fsspec_protocol(
                f"{proto_id}.cached",
                cached_proto_url,
                **self.additional_kwargs)
            # And again on the protocol that was actually built. The call
            # above re-derives what the protocol does with the url; this one
            # asks the object the caching writes go through, so the guard
            # cannot drift away from the thing it is guarding.
            self._check_cache_dir_is_the_id_under_the_cache_url(
                proto_id, cache_proto.root_path)
            if not isinstance(cache_proto, FsspecReadWriteProtocol):
                # ValueError, not TypeError: this reports a bad cache_url in
                # the GRR definition, not a caller passing the wrong type.
                # (The rule only became visible here because dedenting this
                # block moved the isinstance check to the function body.)
                raise ValueError(  # noqa: TRY004
                    f"caching protocol should be RW;"
                    f"{cached_proto_url} is not RW")
            self.cache_protos[proto_id] = CachingProtocol(proto, cache_proto)

            return self.cache_protos[proto_id]

    def find_resource(
        self, resource_id: str,
        version_constraint: str | None = None,
        repository_id: str | None = None,
    ) -> GenomicResource | None:
        """Return requested resource or None if not found.

        Mirrors get_resource: the child resolves the resource (and owns
        repository_id semantics), then the hit is wrapped so the returned
        resource is cache-backed.

        This used to enumerate every resource and pick the highest version
        across protocols, which had two defects. It filtered repository_id
        against the *cache* protocol's id -- registered as
        ``f"{proto_id}.cached"`` -- so the filter never matched anything. And
        because get_resource already delegated, the two methods could return
        different versions of the same id when a group repository's children
        overlapped. Group child order is a priority list; first child wins.
        See #429.
        """
        remote_resource = self.child.find_resource(
            resource_id, version_constraint, repository_id)
        if remote_resource is None:
            return None

        return self._to_cache_resource(remote_resource)

    def get_resource(
            self, resource_id: str,
            version_constraint: str | None = None,
            repository_id: str | None = None) -> GenomicResource:

        remote_resource = self.child.get_resource(
            resource_id, version_constraint, repository_id)

        return self._to_cache_resource(remote_resource)

    def get_resource_cached_files(self, resource_id: str) -> set[str]:
        """Get a set of filenames of cached files for a given resource."""
        resource = self.child.get_resource(resource_id)
        cache_proto = self._get_or_create_cache_proto(
            resource.proto)
        cached_files = set()
        for filename in [entry.name for entry in resource.get_manifest()]:
            if filename == GR_CONF_FILE_NAME:
                continue
            if cache_proto.local_protocol.file_exists(resource, filename):
                cached_files.add(filename)
        return cached_files


def _human_bytes(n: int) -> str:
    """Render a byte count with a 1024 divisor, e.g. ``2.8 GB``/``712.0 MB``.

    Used for the header line and milestone byte figures so a captured log is
    readable; tqdm renders its own human units for the live bar.
    """
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if size < 1024.0 or unit == "PB":
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


class _CacheProgress:
    """Report caching progress against two metrics: bytes and files.

    Bytes are the primary metric (the visible bar / milestone driver);
    files are secondary context (a ``files=done/total`` tally). Concrete
    behaviours share this interface so the caching loop stays oblivious to
    the rendering mode:

    - off (``progress=False``): nothing is shown; the loop still logs its
      header, its DEBUG per-file lines, and the final failure summary.
    - a live :class:`tqdm` byte bar when stderr is a terminal.
    - throttled milestone log lines on byte-percentage thresholds (a ``0%``
      baseline, then each 10% crossing, then ``100%``) when stderr is not a
      terminal, so a captured CI log stays readable and greppable.

    When there are bytes to download (``byte_total > 0``) the bar/milestones
    are byte-driven via :meth:`on_bytes`. When only zero-byte files need
    downloading (``byte_total == 0`` but ``file_total > 0``) the reporter
    falls back to a file-unit bar driven by :meth:`update` so there is still
    motion. :meth:`on_bytes` may be called from multiple download threads;
    :meth:`update` is called only from the single ``as_completed`` thread.

    Failures advance the counter and are surfaced as a ``failed=N`` tally.
    """

    def __init__(self, byte_total: int, file_total: int) -> None:
        self.byte_total = byte_total
        self.file_total = file_total
        self.bytes_done = 0
        self.done = 0
        self.failed = 0

    def update(self, *, failed: bool) -> None:
        self.done += 1
        if failed:
            self.failed += 1

    def on_bytes(self, n: int) -> None:
        """Credit ``n`` downloaded bytes (signed); a no-op in off mode.

        Subclasses that render a byte-level bar override this to advance it.
        """

    def report_failure(self, message: str) -> None:
        logger.error(message)

    def close(self) -> None:
        pass


class _MilestoneProgress(_CacheProgress):
    """Log a progress line on the ``0% / every 10% / 100%`` schedule.

    In byte mode (``byte_total > 0``) milestones fire on byte-percentage
    crossings driven by :meth:`on_bytes`; :meth:`update` only bumps the file
    figures shown on the next byte-milestone line. In the zero-byte fallback
    (``byte_total == 0``, ``file_total > 0``) milestones are file-driven via
    :meth:`update`, preserving the pre-gain#79 behaviour exactly.

    A genuine ``0%`` baseline line is emitted at construction (gain#67),
    seeding ``_last_bucket = 0`` so the bucket-dedup suppresses a duplicate
    ``0%`` line on the first crossing. When the driving total is 0 there is
    nothing to cache, so the baseline is skipped rather than emitting a
    misleading ``0/0 (100%)`` line.

    ``on_bytes`` is called from multiple download threads, so the byte
    accumulator, the bucket cursor and the logging are guarded by a lock.
    """

    def __init__(self, byte_total: int, file_total: int) -> None:
        super().__init__(byte_total, file_total)
        self._byte_mode = byte_total > 0
        self._lock = threading.Lock()
        driving_total = byte_total if self._byte_mode else file_total
        if driving_total:
            self._last_bucket = 0
            self._log_progress()
        else:
            self._last_bucket = -1

    def _pct(self) -> int:
        if self._byte_mode:
            if not self.byte_total:
                return 100
            return min(100, self.bytes_done * 100 // self.byte_total)
        return self.done * 100 // self.file_total if self.file_total else 100

    def _log_progress(self) -> None:
        failed_suffix = f", failed={self.failed}" if self.failed else ""
        if self._byte_mode:
            logger.info(
                "caching progress: %s/%s (%s%%), %s/%s files%s",
                _human_bytes(self.bytes_done), _human_bytes(self.byte_total),
                self._pct(), self.done, self.file_total, failed_suffix)
        else:
            logger.info(
                "caching progress: %s/%s files (%s%%)%s",
                self.done, self.file_total, self._pct(), failed_suffix)

    def on_bytes(self, n: int) -> None:
        if not self._byte_mode:
            return
        with self._lock:
            self.bytes_done += n
            pct = self._pct()
            bucket = pct // 10
            # ``_last_bucket`` is a high-water mark: a rollback (negative
            # delta from a retryable failure, slice 1) lowers ``bytes_done``
            # but does NOT lower the cursor, so re-crossing an
            # already-reported bucket logs no duplicate line. A line is
            # emitted only on a forward crossing into a new bucket. Bucket 10
            # occurs only at 100%, so this still logs the final line exactly
            # once; once full, further positive deltas (e.g. a concurrent
            # file's chunks after a terminal-failure top-up overshot the
            # total) are deduped rather than re-logging 100%.
            if bucket <= self._last_bucket:
                return
            self._last_bucket = bucket
            self._log_progress()

    def update(self, *, failed: bool) -> None:
        super().update(failed=failed)
        if self._byte_mode:
            # File figures normally ride the next byte-milestone line. But a
            # terminal failure tops up the bytes (reaching 100%) and is
            # marked failed straight after -- there is no later byte line to
            # carry the tally, so re-log the 100% line once the bar is full
            # so the failed=N tally is visible. See gain#79 / gain#43.
            if failed and self._pct() == 100:
                with self._lock:
                    self._log_progress()
            return
        bucket = self._pct() // 10
        if bucket == self._last_bucket and self.done != self.file_total:
            return
        self._last_bucket = bucket
        self._log_progress()


class _TqdmProgress(_CacheProgress):
    """Drive a live tqdm bar, writing failures above it via tqdm.write.

    In byte mode the bar counts bytes (human units + throughput + ETA),
    advanced by :meth:`on_bytes`; :meth:`update` only refreshes the
    ``files=done/total`` postfix. In the zero-byte fallback the bar counts
    files, advanced by :meth:`update`. ``on_bytes`` runs on multiple
    download threads, so bar mutations are guarded by a lock.
    """

    def __init__(self, byte_total: int, file_total: int) -> None:
        super().__init__(byte_total, file_total)
        self._byte_mode = byte_total > 0
        self._lock = threading.Lock()
        if self._byte_mode:
            self._bar = tqdm(
                total=byte_total, desc="caching", unit="B",
                unit_scale=True, unit_divisor=1024, leave=True)
        else:
            self._bar = tqdm(
                total=file_total, desc="caching", unit="file", leave=True)

    def _postfix(self) -> None:
        files = f"{self.done}/{self.file_total}"
        if self.failed:
            self._bar.set_postfix_str(
                f"files={files}, failed={self.failed}", refresh=False)
        else:
            self._bar.set_postfix_str(f"files={files}", refresh=False)

    def on_bytes(self, n: int) -> None:
        if not self._byte_mode:
            return
        with self._lock:
            self._bar.update(n)

    def update(self, *, failed: bool) -> None:
        super().update(failed=failed)
        with self._lock:
            self._postfix()
            if not self._byte_mode:
                self._bar.update(1)

    def report_failure(self, message: str) -> None:
        self._bar.write(message)

    def close(self) -> None:
        self._bar.close()


def _make_cache_progress(
    byte_total: int, file_total: int, *, progress: bool,
) -> _CacheProgress:
    if not progress:
        return _CacheProgress(byte_total, file_total)
    if sys.stderr.isatty():
        return _TqdmProgress(byte_total, file_total)
    return _MilestoneProgress(byte_total, file_total)


def _resolve_resources(
    repository: GenomicResourceRepo,
    resource_ids: Iterable[str] | None,
) -> list[GenomicResource]:
    """Resolve the remote resources to cache, either all or a given list."""
    if resource_ids is None:
        return list(repository.get_all_resources())
    resources: list[GenomicResource] = []
    for resource_id in resource_ids:
        remote_res = repository.get_resource(resource_id)
        assert remote_res is not None, resource_id
        resources.append(remote_res)
    return resources


def _enumerate_resource_files(
    resource: GenomicResource,
) -> list[str]:
    """Return the file set to consider for caching a single resource.

    Mirrors the pre-refactor selection exactly: a resource of a known
    implementation type contributes ``genomic_resource.yaml`` plus the
    implementation's ``files``; a resource of an unknown type contributes
    every manifest entry except ``.lockfile`` files (the coarse
    ``refresh_cached_resource`` set). See gain#78.
    """
    # pylint: disable=import-outside-toplevel
    from gain.genomic_resources import get_resource_implementation_builder

    impl_builder = get_resource_implementation_builder(resource.get_type())
    if impl_builder is None:
        logger.info(
            "unexpected resource type <%s> for resource %s; "
            "updating resource", resource.get_type(), resource.resource_id)
        return [
            entry.name
            for entry in resource.get_manifest()
            if not entry.name.endswith(".lockfile")
        ]

    impl = impl_builder(resource)
    return ["genomic_resource.yaml", *impl.files]


def _build_cache_worklist(
    cached_proto: CachingProtocol,
    resource: GenomicResource,
    filenames: Iterable[str],
    workers: int | None = None,
) -> tuple[list[tuple[GenomicResource, str, int]], int, int, list[str]]:
    """Classify ``filenames`` of ``resource`` (lock-free) into a work-list.

    Returns ``(worklist, total_bytes, already_cached, failures)`` where
    ``worklist`` is the list of ``(resource, filename, size)`` entries that
    need downloading, ``total_bytes`` is the summed manifest size of those
    entries, ``already_cached`` counts the files that need no download, and
    ``failures`` collects per-file classify errors (a classify failure must
    not abort the whole run). Classification is lock-free, so it is fanned
    out across a thread pool. See gain#43, gain#78.
    """
    filenames = list(filenames)
    worklist: list[tuple[GenomicResource, str, int]] = []
    total_bytes = 0
    already_cached = 0
    failures: list[str] = []

    with ThreadPoolExecutor(max_workers=workers) as classify_executor:
        future_to_name = {
            classify_executor.submit(
                cached_proto.classify_cached_resource_file,
                resource, filename): filename
            for filename in filenames
        }
        for future, filename in future_to_name.items():
            try:
                verdict = future.result()
            except Exception as error:  # noqa: BLE001 - report, don't abort
                # A classify failure (e.g. a corrupt .state, or a resource
                # gone from the remote) must not discard the whole run; it is
                # collected and surfaced in the end-of-run summary like a
                # download failure. See gain#43.
                # ``error`` may be an fsspec/aiohttp fetch failure whose message
                # embeds the credential-bearing fetch url; strip any url
                # userinfo before it reaches the failure summary or the logs.
                redacted = _strip_url_userinfo(str(error))
                failures.append(
                    f"{resource.resource_id}: {filename} ({redacted})")
                # One concise line per failure; a stack trace per failed file
                # would swamp a large run (see the gain#43 rationale in the
                # download loop), so logger.error not logger.exception.
                logger.error(  # noqa: TRY400
                    "failed to classify (%s: %s): %s",
                    resource.resource_id, filename, redacted)
                continue
            if verdict.needs_download:
                worklist.append((resource, filename, verdict.size))
                total_bytes += verdict.size
            else:
                already_cached += 1

    return worklist, total_bytes, already_cached, failures


def _classify_resources(
    resources: list[GenomicResource],
    workers: int | None,
) -> tuple[list[tuple[GenomicResource, str, int]], int, int, list[str]]:
    """Phase A: classify every resource's files (lock-free) into a work-list.

    Returns ``(worklist, total_bytes, already_cached, failures)``. A classify
    failure for one file is collected (not raised) so the run continues and
    surfaces it in the end-of-run summary, preserving the gain#43 contract
    that one file failing must not discard the whole run. See gain#78.
    """
    worklist: list[tuple[GenomicResource, str, int]] = []
    total_bytes = 0
    already_cached = 0
    failures: list[str] = []
    for resource in resources:
        if not isinstance(resource.proto, CachingProtocol):
            continue
        filenames = _enumerate_resource_files(resource)
        res_worklist, res_bytes, res_cached, res_failures = \
            _build_cache_worklist(
                resource.proto, resource, filenames, workers)
        worklist.extend(res_worklist)
        total_bytes += res_bytes
        already_cached += res_cached
        failures.extend(res_failures)
    return worklist, total_bytes, already_cached, failures


def cache_resources(
    repository: GenomicResourceRepo,
    resource_ids: Iterable[str] | None,
    workers: int | None = None,
    *,
    progress: bool = True,
) -> None:
    """Cache resources from a list of remote resource IDs."""
    resources = _resolve_resources(repository, resource_ids)

    # Phase A: classify (lock-free) the same file set as before into an
    # authoritative work-list of files that actually need downloading. A
    # classify failure is collected, not raised, so the run still proceeds
    # and surfaces it in the end-of-run summary (gain#43).
    worklist, total_bytes, already_cached, classify_failures = \
        _classify_resources(resources, workers)

    logger.info(
        "caching %s file(s), %s to download; %s already cached",
        len(worklist), _human_bytes(total_bytes), already_cached)

    # Nothing to download: the header already reported it; skip the executor,
    # the reporter and the bar entirely (gain#67 nothing-to-do behaviour).
    if not worklist:
        if classify_failures:
            summary = "\n".join(f"  - {f}" for f in classify_failures)
            raise RuntimeError(
                f"failed to cache {len(classify_failures)}/"
                f"{len(classify_failures)} resource file(s):\n{summary}")
        return

    # Phase B: download (with per-file locks) only the work-list entries.
    executor = ThreadPoolExecutor(max_workers=workers)
    # Each future maps to its label and manifest size; the size feeds the
    # terminal-failure byte top-up below.
    futures: dict[Future, tuple[str, int]] = {}
    total_files = len(worklist)
    reporter = _make_cache_progress(
        byte_total=total_bytes, file_total=total_files, progress=progress)
    for resource, filename, size in worklist:
        cached_proto = cast(CachingProtocol, resource.proto)
        logger.debug(
            "request to cache resource file: (%s, %s) from %s",
            resource.resource_id, filename,
            cached_proto.remote_protocol.proto_id)
        futures[executor.submit(
            cached_proto.download_cached_resource_file,
            resource,
            filename,
            on_bytes=reporter.on_bytes,
        )] = (f"{resource.resource_id}: {filename}", size)

    failures: list[str] = list(classify_failures)
    try:
        for count, future in enumerate(as_completed(futures)):
            label, size = futures[future]
            try:
                resource_id, filename = future.result()
            except Exception as error:  # noqa: BLE001 - report, don't abort
                # A single file failing (e.g. a download that stalled past
                # its retries) must not discard the progress of every other
                # file in the run. Collect the failure and keep caching; we
                # raise a summary at the end so the run still fails loudly.
                # See gain#43.
                # ``error`` may embed the credential-bearing fetch url (see the
                # classify path above); redact any url userinfo before it
                # reaches the summary or the reporter's ERROR log.
                redacted = _strip_url_userinfo(str(error))
                failures.append(f"{label} ({redacted})")
                # One concise line per failure; the full summary is raised at
                # the end. A stack trace per failed file would swamp a large
                # run.
                reporter.report_failure(
                    f"failed {count}/{total_files} ({label}): {redacted}")
                # Slice 1 rolls a retryable terminal failure's bytes back to
                # net ~0, so credit the file's full size to land the byte bar
                # at 100%, then mark the failure so the tally shows. See
                # gain#79 / gain#43. Must precede update(failed=True).
                reporter.on_bytes(size)
                reporter.update(failed=True)
                continue
            logger.debug(
                "finished %s/%s (%s: %s)", count, total_files,
                resource_id, filename)
            reporter.update(failed=False)
    finally:
        # Cleanup must run on every exit path -- normal completion, an
        # unexpected exception, or a KeyboardInterrupt escaping the loop --
        # so a live tqdm bar is always finalized rather than left dangling.
        # See gain#68.
        reporter.close()
        executor.shutdown()

    if failures:
        summary = "\n".join(f"  - {failure}" for failure in failures)
        # Files acted on that could fail: downloads attempted (total_files)
        # plus files that failed classification before reaching the
        # work-list. (Already-cached files cannot fail, so are excluded.)
        attempted = total_files + len(classify_failures)
        raise RuntimeError(
            f"failed to cache {len(failures)}/{attempted} resource "
            f"file(s):\n{summary}")
