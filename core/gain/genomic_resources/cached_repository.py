"""Provides caching genomic resources."""
from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable, Generator, Iterable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import IO, Any, cast

import apsw
import pysam
from tqdm import tqdm

from gain.genomic_resources.fsspec_protocol import (
    FileCacheVerdict,
    FsspecReadWriteProtocol,
)
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResource,
    GenomicResourceRepo,
    Manifest,
    ReadOnlyRepositoryProtocol,
    is_version_constraint_satisfied,
    parse_resource_id_version,
    version_tuple_to_string,
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
        self.remote_protocol = remote_protocol
        self.local_protocol = local_protocol
        super().__init__(local_protocol.proto_id, local_protocol.get_url())
        self.public_url = public_url or remote_protocol.get_public_url()
        self._all_resources: dict[str, CacheResource] | None = None

    def get_url(self) -> str:
        return self.remote_protocol.get_url()

    def get_public_url(self) -> str:
        return self.public_url

    def invalidate(self) -> None:
        self.remote_protocol.invalidate()
        self.local_protocol.invalidate()
        self._all_resources = None

    def get_all_resources(self) -> Generator[GenomicResource, None, None]:
        yield from self.get_all_resources_dict().values()

    def get_all_resources_dict(self) -> dict[str, GenomicResource]:
        if self._all_resources is None:
            self._all_resources = {
                resource.get_full_id(): self._create_cache_resource(resource)
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
            index_filename = f"{filename}.tbi"
        self.refresh_cached_resource_file(resource, index_filename)

        return self.local_protocol.open_tabix_file(
            resource, filename, index_filename)

    def open_vcf_file(
            self, resource: GenomicResource, filename: str,
            index_filename: str | None = None) -> pysam.VariantFile:
        self.refresh_cached_resource_file(resource, filename)
        if index_filename is None:
            index_filename = f"{filename}.tbi"
        self.refresh_cached_resource_file(resource, index_filename)

        return self.local_protocol.open_vcf_file(
            resource, filename, index_filename)

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

        logger.debug(
            "creating cached GRR with cache url: %s", cache_url)
        self._all_resources: list[GenomicResource] | None = None
        self.child: GenomicResourceRepo = child
        self.cache_url = cache_url
        self.cache_protos: dict[str, CachingProtocol] = {}
        self.additional_kwargs = kwargs

    def invalidate(self) -> None:
        self.child.invalidate()
        for proto in self.cache_protos.values():
            proto.invalidate()
        self._all_resources = None

    def get_all_resources(self) -> Generator[GenomicResource, None, None]:
        if self._all_resources is None:
            self._all_resources = []
            for remote_resource in self.child.get_all_resources():
                cache_proto = self._get_or_create_cache_proto(
                    remote_resource.proto)
                version_constraint = f"={remote_resource.get_version_str()}"
                self._all_resources.append(
                    cache_proto.get_resource(
                        remote_resource.resource_id, version_constraint))
        yield from self._all_resources

    def search_resources(
        self,
        search_term: str | None = None,
        resource_type: str | None = None,
    ) -> Generator[GenomicResource, None, None]:
        yield from self.child.search_resources(search_term, resource_type)

    def _get_or_create_cache_proto(
            self, proto: ReadOnlyRepositoryProtocol) -> CachingProtocol:
        proto_id = proto.proto_id
        if proto_id not in self.cache_protos:
            cached_proto_url = os.path.join(self.cache_url, proto_id)
            logger.debug(
                "going to create cached protocol with url: %s",
                cached_proto_url)

            cache_proto = build_fsspec_protocol(
                f"{proto_id}.cached",
                cached_proto_url,
                **self.additional_kwargs)
            if not isinstance(cache_proto, FsspecReadWriteProtocol):
                raise ValueError(
                    f"caching protocol should be RW;"
                    f"{cached_proto_url} is not RW")
            self.cache_protos[proto_id] = \
                CachingProtocol(
                    proto,
                    cache_proto)

        return self.cache_protos[proto_id]

    def find_resource(
        self, resource_id: str,
        version_constraint: str | None = None,
        repository_id: str | None = None,
    ) -> GenomicResource | None:
        """Return requested resource or None if not found."""
        if version_constraint is None:
            resource_id, version = parse_resource_id_version(resource_id)
            if version is not None:
                version_constraint = f"={version_tuple_to_string(version)}"
        matching_resources: list[GenomicResource] = []
        for res in self.get_all_resources():
            if res.resource_id != resource_id:
                continue
            if repository_id is not None and \
                    res.proto.proto_id != repository_id:
                continue
            if is_version_constraint_satisfied(
                    version_constraint, res.version):
                matching_resources.append(res)
        if not matching_resources:
            return None

        def get_resource_version(res: GenomicResource) -> tuple[int, ...]:
            return res.version

        return max(
            matching_resources,
            key=get_resource_version)

    def get_resource(
            self, resource_id: str,
            version_constraint: str | None = None,
            repository_id: str | None = None) -> GenomicResource:

        if version_constraint is None:
            resource_id, version = parse_resource_id_version(resource_id)
            if version is not None:
                version_constraint = f"={version_tuple_to_string(version)}"

        remote_resource = self.child.get_resource(
            resource_id, version_constraint, repository_id)

        cache_proto = self._get_or_create_cache_proto(
            remote_resource.proto)
        version_constraint = f"={remote_resource.get_version_str()}"
        return cache_proto.get_resource(resource_id, version_constraint)

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


class _CacheProgress:
    """Report caching progress as files complete.

    Three concrete behaviours share this interface so the caching loop
    stays oblivious to the rendering mode:

    - off (``progress=False``): nothing is shown; the loop still logs its
      header, its DEBUG per-file lines, and the final failure summary.
    - a live :class:`tqdm` bar when stderr is a terminal.
    - throttled milestone log lines (a ``0%`` baseline, then each 10%
      crossing, then ``100%``) when stderr is not a terminal, so a captured
      CI log stays readable and greppable.

    Failures advance the counter like any other completed file and are
    surfaced as a running ``failed=N`` tally.
    """

    def __init__(self, total: int) -> None:
        self.total = total
        self.done = 0
        self.failed = 0

    def update(self, *, failed: bool) -> None:
        self.done += 1
        if failed:
            self.failed += 1

    def on_bytes(self, n: int) -> None:
        """Credit ``n`` downloaded bytes; a no-op at the file granularity.

        Subclasses that render a byte-level bar override this to advance it.
        """

    def report_failure(self, message: str) -> None:
        logger.error(message)

    def close(self) -> None:
        pass


class _MilestoneProgress(_CacheProgress):
    """Log a progress line on the ``0% / every 10% / 100%`` schedule.

    A genuine ``0%`` baseline line is emitted at construction, before any
    file completes (matching spec #59's non-TTY acceptance criteria). After
    that, one line is emitted on each 10% bucket crossing, and a final
    ``100%`` line when the last file completes. The construction baseline
    seeds ``_last_bucket = 0`` so the existing bucket-dedup suppresses a
    duplicate ``0%`` line as the first files complete.

    When ``total == 0`` there is nothing to cache, so the baseline is
    skipped rather than emitting a misleading ``0/0 (100%)`` line.
    """

    def __init__(self, total: int) -> None:
        super().__init__(total)
        if self.total:
            self._last_bucket = 0
            self._log_progress()
        else:
            self._last_bucket = -1

    def _log_progress(self) -> None:
        pct = self.done * 100 // self.total if self.total else 100
        failed_suffix = f", failed={self.failed}" if self.failed else ""
        logger.info(
            "caching progress: %s/%s files (%s%%)%s",
            self.done, self.total, pct, failed_suffix)

    def update(self, *, failed: bool) -> None:
        super().update(failed=failed)
        pct = self.done * 100 // self.total if self.total else 100
        bucket = pct // 10
        if bucket == self._last_bucket and self.done != self.total:
            return
        self._last_bucket = bucket
        self._log_progress()


class _TqdmProgress(_CacheProgress):
    """Drive a live tqdm bar, writing failures above it via tqdm.write."""

    def __init__(self, total: int) -> None:
        super().__init__(total)
        self._bar = tqdm(
            total=total, desc="caching", unit="file", leave=True)

    def update(self, *, failed: bool) -> None:
        super().update(failed=failed)
        if self.failed:
            self._bar.set_postfix(failed=self.failed, refresh=False)
        self._bar.update(1)

    def report_failure(self, message: str) -> None:
        self._bar.write(message)

    def close(self) -> None:
        self._bar.close()


def _make_cache_progress(total: int, *, progress: bool) -> _CacheProgress:
    if not progress:
        return _CacheProgress(total)
    if sys.stderr.isatty():
        return _TqdmProgress(total)
    return _MilestoneProgress(total)


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
                failures.append(
                    f"{resource.resource_id}: {filename} ({error})")
                # One concise line per failure; a stack trace per failed file
                # would swamp a large run (see the gain#43 rationale in the
                # download loop), so logger.error not logger.exception.
                logger.error(  # noqa: TRY400
                    "failed to classify (%s: %s): %s",
                    resource.resource_id, filename, error)
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
        "caching %s file(s), %s bytes to download; %s already cached",
        len(worklist), total_bytes, already_cached)

    # Phase B: download (with per-file locks) only the work-list entries.
    executor = ThreadPoolExecutor(max_workers=workers)
    futures: dict[Future, str] = {}
    for resource, filename, _size in worklist:
        cached_proto = cast(CachingProtocol, resource.proto)
        logger.debug(
            "request to cache resource file: (%s, %s) from %s",
            resource.resource_id, filename,
            cached_proto.remote_protocol.proto_id)
        futures[executor.submit(
            cached_proto.download_cached_resource_file,
            resource,
            filename,
        )] = f"{resource.resource_id}: {filename}"

    total_files = len(futures)
    reporter = _make_cache_progress(total_files, progress=progress)
    failures: list[str] = list(classify_failures)
    try:
        for count, future in enumerate(as_completed(futures)):
            label = futures[future]
            try:
                resource_id, filename = future.result()
            except Exception as error:  # noqa: BLE001 - report, don't abort
                # A single file failing (e.g. a download that stalled past
                # its retries) must not discard the progress of every other
                # file in the run. Collect the failure and keep caching; we
                # raise a summary at the end so the run still fails loudly.
                # See gain#43.
                failures.append(f"{label} ({error})")
                # One concise line per failure; the full summary is raised at
                # the end. A stack trace per failed file would swamp a large
                # run.
                reporter.report_failure(
                    f"failed {count}/{total_files} ({label}): {error}")
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
