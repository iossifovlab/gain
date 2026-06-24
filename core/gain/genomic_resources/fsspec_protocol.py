"""Provides GRR protocols based on fsspec library."""
from __future__ import annotations

import asyncio
import copy
import datetime
import gzip
import hashlib
import json
import logging
import operator
import os
import pathlib
import tempfile
import time
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager
from dataclasses import asdict
from threading import Lock
from types import TracebackType
from typing import (
    IO,
    Any,
    NamedTuple,
    cast,
)
from urllib.parse import urlparse

import apsw
import fsspec
import fsspec.exceptions
import pyBigWig
import pysam
import yaml
from filelock import FileLock
from markdown2 import markdown

from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GR_CONTENTS_FILE_NAME,
    GR_INDEX_FILE_NAME,
    GR_MANIFEST_FILE_NAME,
    GR_SQLITE_META_FILE_NAME,
    GenomicResource,
    Manifest,
    ManifestEntry,
    Mode,
    ReadOnlyRepositoryProtocol,
    ReadWriteRepositoryProtocol,
    ResourceFileState,
    is_gr_id_token,
    parse_gr_id_version_token,
)
from gain.templates import get_template
from gain.utils.helpers import convert_size

# Silence the spurious "[W::hts_idx_load3] The index file is older than the
# data file" warning that htslib emits when a tabix/VCF index has an older
# mtime than its data file. In our GRR workflow this is benign: both the
# caching protocol and DVC download index and data files in parallel, and
# the smaller index typically lands first. Level 1 (errors only) keeps real
# htslib errors visible while suppressing notices (3) and warnings (2).
pysam.set_verbosity(1)

logger = logging.getLogger(__name__)


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


class ChecksumMismatchError(OSError):
    """A completed download whose md5 disagrees with the manifest.

    Almost always a truncated or corrupted transfer, so it is treated as a
    retryable error by copy_resource_file rather than a hard failure.
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
# covers resets/refused connects; ChecksumMismatchError covers truncated
# transfers.
_RETRYABLE_COPY_ERRORS: tuple[type[BaseException], ...] = (
    fsspec.exceptions.FSTimeoutError,
    asyncio.TimeoutError,
    ConnectionError,
    ChecksumMismatchError,
    *_aiohttp_errors,
)


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

    proto = cast(
        FsspecReadWriteProtocol,
        build_fsspec_protocol(proto_id, f"memory://{root_path}"))
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


class FsspecReadOnlyProtocol(ReadOnlyRepositoryProtocol):
    """Provides fsspec genomic resources repository protocol."""

    def __getnewargs_ex__(self) -> tuple[tuple, dict]:
        # pylint: disable=invalid-getnewargs-ex-returned
        args = (self.proto_id, self.url)
        kwargs: dict[str, Any] = copy.copy(self.kwargs)
        kwargs["public_url"] = self.public_url
        return (args, kwargs)

    def __new__(cls, *args: Any, **kwargs: Any) -> FsspecReadOnlyProtocol:
        proto_id = args[0] if len(args) > 0 else kwargs["proto_id"]
        url = args[1] if len(args) > 1 else kwargs["url"]
        key = (proto_id, url)
        if key in _FSSPEC_PROTOCOLS:
            logger.debug(
                "protocol with id %s and url %s already exists, "
                "returning the existing instance",
                proto_id, url)
            return _FSSPEC_PROTOCOLS[key]
        instance = super().__new__(cls)
        _FSSPEC_PROTOCOLS[key] = instance
        return instance

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
        self.netloc = parsed.netloc
        self.root_path = parsed.path

        self.url = f"{self.scheme}://{self.netloc}{self.root_path}"

        if public_url is None:
            self.public_url = self.url
        else:
            self.public_url = public_url

        self.filesystem = filesystem
        self.kwargs: dict[str, Any] = kwargs
        self._all_resources_lock = Lock()
        self._all_resources: dict[str, GenomicResource] | None = None

    def __getstate__(self) -> dict[str, Any]:
        state = self.__dict__.copy()
        # Remove the unpicklable entries.
        del state["filesystem"]
        del state["_all_resources"]
        del state["_all_resources_lock"]
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)
        self.filesystem = _build_filesystem(
            self.url, **self.kwargs)
        self._all_resources = None
        self._all_resources_lock = Lock()

    def get_url(self) -> str:
        return self.url

    def get_public_url(self) -> str:
        return self.public_url

    def invalidate(self) -> None:
        if self._all_resources is not None:
            for resource in self._all_resources.values():
                resource.proto = None  # type: ignore
        self._all_resources = None

    def close(self) -> None:
        """Close the genomic resource."""
        self.invalidate()

    def load_contents(self) -> list[dict[str, Any]]:
        """Load the content JSON of the repository."""
        content_filename = os.path.join(
            self.url, GR_CONTENTS_FILE_NAME)
        compression: str | None = "gzip"
        if not self.filesystem.exists(content_filename):
            content_filename = content_filename[:-3]
            compression = None

        with self.filesystem.open(
                content_filename, "rt", compression=compression) as infile:
            data = infile.read()

        return cast(list[dict[str, Any]], json.loads(data))

    def md5_contents(self) -> str:
        """Calculate md5 hash of the repository content."""
        content_filename = os.path.join(
            self.url, GR_CONTENTS_FILE_NAME)
        if not self.filesystem.exists(content_filename):
            content_filename = content_filename[:-3]

        with self.filesystem.open(content_filename, "rb") as infile:
            data = infile.read()

        assert isinstance(data, bytes)

        return hashlib.md5(data).hexdigest()  # noqa: S324

    def get_all_resources(self) -> Generator[GenomicResource, None, None]:
        """Return generator over all resources in the repository."""
        yield from self.get_all_resources_dict().values()

    def get_all_resources_dict(self) -> dict[str, GenomicResource]:
        with self._all_resources_lock:
            if self._all_resources is None:
                all_resources = []

                contents = self.load_contents()

                for entry in contents:
                    version = tuple(map(int, entry["version"].split(".")))
                    manifest = Manifest.from_manifest_entries(
                        entry["manifest"])
                    resource = self.build_genomic_resource(
                        entry["id"], version, config=entry["config"],
                        manifest=manifest)
                    logger.debug(
                        "repo %s loaded resource %s",
                        self.proto_id,
                        resource.resource_id)
                    all_resources.append(resource)

                self._all_resources = {
                    res.get_full_id(): res
                    for res in sorted(
                        all_resources,
                        key=lambda r: r.get_full_id(),
                    )
                }

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

        return cast(
            IO,
            self.filesystem.open(
                filepath, mode=mode,
                compression=compression))

    def open_repository_sqlite3_metadata_db(self) -> apsw.Connection:
        sqlite_filepath = os.path.join(
            self.url, GR_SQLITE_META_FILE_NAME)
        if not self.filesystem.exists(sqlite_filepath):
            raise ValueError(
                "Repository contents SQLite metadata DB not found!")

        connection = apsw.Connection(":memory:")
        with self.filesystem.open(
                sqlite_filepath, "rb", compression="gzip") as decompressed_db:
            raw_db = decompressed_db.read()
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
            index_filename = f"{filename}.tbi"
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

        if index_filename is None:
            index_filename = f"{filename}.tbi"
        index_url = self._get_file_url(resource, index_filename)

        return pysam.VariantFile(  # pylint: disable=no-member
            file_url, index_filename=index_url)

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
        """Return path of the resource file's lockfile."""
        if self.scheme != "file":
            raise NotImplementedError
        resource_url = self.get_resource_url(resource)
        path = os.path.join(resource_url, ".grr", f"{filename}.lockfile")
        return path.removeprefix(f"{self.scheme}://")

    def obtain_resource_file_lock(
        self, resource: GenomicResource, filename: str,
        timeout: float = -1,
    ) -> AbstractContextManager:
        """Lock a resource's file."""

        class NoLock:
            """Lock representation."""

            def __enter__(self) -> None:
                pass

            def __exit__(
                    self,
                    exc_type: type[BaseException] | None,
                    exc_value: BaseException | None,
                    exc_tb: TracebackType | None) -> bool:
                return exc_type is None

        if self.scheme != "file":
            return NoLock()

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
    ) -> Generator[Any, None, None]:

        url = os.path.join(self.url, resource_path, *path_array)
        if not self.filesystem.isdir(url):
            if path_array:
                yield os.path.join(*path_array), url
            return

        path = os.path.join(self.root_path, resource_path, *path_array)
        content = []
        for direntry in self.filesystem.ls(url, detail=False):
            if self.netloc and direntry.startswith(self.netloc):
                direntry = direntry[len(self.netloc):]

            name = os.path.relpath(direntry, path)
            if name.startswith("."):
                continue
            content.append(name)

        for name in content:
            yield from self._scan_resource_for_files(
                resource_path, [*path_array, name])

    def _get_filepath_timestamp(self, filepath: str) -> float:
        try:
            modification = self.filesystem.modified(filepath)
            modification = modification.replace(tzinfo=datetime.timezone.utc)
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

    def collect_resource_entries(self, resource: GenomicResource) -> Manifest:
        """Scan the resource and resturn a manifest."""
        resource_path = resource.get_genomic_resource_id_version()

        result = Manifest()
        for name, path in self._scan_resource_for_files(resource_path, []):
            if name.endswith("html"):  # Ignore generated info files
                continue

            size = self._get_filepath_size(path)
            result.add(ManifestEntry(name, size, None))
        return result

    def get_all_resources(self) -> Generator[GenomicResource, None, None]:
        """Return generator over all resources in the repository."""

        yield from self.get_all_resources_dict().values()

    def get_all_resources_dict(self) -> dict[str, GenomicResource]:
        with self._all_resources_lock:
            if self._all_resources is None:
                self._all_resources = {
                    res.get_full_id(): res
                    for res in sorted(
                        self.collect_all_resources(),
                        key=lambda r: r.get_full_id(),
                    )
                }
        return self._all_resources

    def _get_resource_file_state_path(
            self, resource: GenomicResource, filename: str) -> str:
        """Return filename of the resource file state path."""
        resource_url = self.get_resource_url(resource)
        return os.path.join(resource_url, ".grr", f"{filename}.state")

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
            on_bytes: Callable[[int], None] | None = None,
    ) -> ResourceFileState:
        """Download a single resource file once and verify its checksum.

        Opens a fresh remote handle and truncates the destination, so a
        retried call recovers cleanly from a partially-written file.

        ``on_bytes``, when given, is called with the length of each chunk
        right after it is written, to drive a byte-level progress bar
        (see gain#77).
        """
        with remote_resource.open_raw_file(
                filename, "rb",
                uncompress=False) as infile, \
                self.open_raw_file(
                    dest_resource,
                    filename, "wb",
                    uncompress=False) as outfile:

            md5_hash = hashlib.md5()  # noqa
            while chunk := infile.read(self.CHUNK_SIZE):
                outfile.write(chunk)
                if on_bytes is not None:
                    on_bytes(len(chunk))
                md5_hash.update(chunk)

        md5 = md5_hash.hexdigest()

        if not self.filesystem.exists(dest_filepath):
            raise OSError(f"destination file not created {dest_filepath}")

        if md5 != expected_md5:
            raise ChecksumMismatchError(
                f"file copy is broken "
                f"{dest_resource.resource_id} ({filename}); "
                f"md5sum are different: "
                f"{md5}!={expected_md5}")

        state = self.build_resource_file_state(
            dest_resource,
            filename,
            md5sum=md5)

        self.save_resource_file_state(dest_resource, state)

        return state

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

    def build_content_file(self) -> list[dict[str, Any]]:
        """Build the content of the repository (i.e '.CONTENTS.json' file)."""
        content = [
            {
                "full_id": res.get_full_id(),
                "id": res.resource_id,
                "version": res.get_version_str(),
                "config": res.get_config(),
                "manifest": res.get_manifest().to_manifest_entries(),
            }
            for res in self.get_all_resources()]
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

        with self.filesystem.open(
                content_filepath[:-3],
                "wt", encoding="utf8") as outfile:
            json.dump(content, outfile, indent=2, sort_keys=True)

        return content

    def get_content_file_path(self) -> str:
        return os.path.join(self.url, GR_CONTENTS_FILE_NAME[:-3])

    def build_index_info(
        self,
        repository_template: str = "grr_index.jinja",
        about_template: str | None = "grr_about.jinja",
    ) -> dict:
        """Build info dict for the repository."""
        result = {}
        for res in self.get_all_resources():
            res_size = convert_size(
                sum(f for _, f in res.get_manifest().get_files()),
            )
            assert res.config is not None
            result[res.get_full_id()] = {
                "res_full_id": res.get_full_id(),
                "res_id": res.resource_id,
                **res.config,
                "res_version": res.get_version_str(),
                "res_files": len(list(res.get_manifest().get_files())),
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


def build_fsspec_protocol(
    proto_id: str, root_url: str, **kwargs: str | None,
) -> FsspecRepositoryProtocol:
    """Create fsspec GRR protocol based on the root url."""
    # pylint: disable=import-outside-toplevel
    public_url = kwargs.pop("public_url", None)
    read_only = kwargs.pop("read_only", False)
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
        return FsspecReadOnlyProtocol(
            proto_id, root_url,
            filesystem=filesystem,
            public_url=public_url,
            **kwargs)

    raise NotImplementedError(f"unsupported schema {url.scheme}")
