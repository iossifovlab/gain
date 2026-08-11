"""
Provides basic classes for genomic resources and repositories.

This module defines the core architecture for managing genomic resources
through a flexible repository system. It supports different storage backends
(local files, HTTP, S3) and provides both read-only and read-write access.

Class Hierarchy:
       +---------------------+                    +-----------------+
 +-----| GenomicResourceRepo |--------------------| GenomicResource |
 |     +---------------------+                    +-----------------+
 |        ^               ^                                    |
 |        |               |                                    |
 |        |  +-----------------------------+     +----------------------------+
 |        |  | GenomicResourceProtocolRepo | ----| ReadOnlyRepositoryProtocol |
 |        |  +-----------------------------+     +----------------------------+
 |        |                                                    ^
 |        |                                                    |
 |    +--------------------------+            +-----------------------------+
 +----| GenomicResourceGroupRepo |            | ReadWriteRepositoryProtocol |
      +--------------------------+            +-----------------------------+

Key Concepts:
    - GenomicResource: Represents a single genomic resource (e.g., a reference
      genome, score set, or gene model) with metadata and file access methods.

    - GenomicResourceRepo: Abstract base for repositories that manage
      collections of genomic resources.

    - RepositoryProtocol: Defines the storage backend interface (file system,
      HTTP, S3, etc.) for accessing resource files.

    - Manifest: Tracks files and their checksums within a resource to ensure
      data integrity and enable caching.

Resource Identifiers:
    Resources are identified by an ID and optional version suffix:
    - Simple: "hg19/gene_models/refseq"
    - Versioned: "hg19/gene_models/refseq(1.2.3)"

Configuration Files:
    Each resource contains a genomic_resource.yaml configuration file with
    metadata including type, description, and resource-specific settings.

"""
# pylint: disable=too-many-lines
from __future__ import annotations

import abc
import copy
import enum
import hashlib
import ntpath
import os
import re
from collections.abc import (
    Callable,
    Generator,
    Iterator,
    Mapping,
    Sequence,
)
from dataclasses import asdict, dataclass
from typing import IO, Any, cast
from urllib.parse import unquote

import apsw
import pysam
import yaml

from gain import logging
from gain.genomic_resources.dvc import (
    DvcContentDrift,
    DvcContentDriftError,
    UnsupportedDvcDirectoryOutputError,
    dvc_sidecar_target,
    is_dvc_directory_out,
    is_dvc_sidecar,
    parse_dvc_pointer_out,
)
from gain.genomic_resources.resource_query import LabelClause, ResourceQuery
from gain.genomic_resources.resource_types import equivalent_resource_types

logger = logging.getLogger(__name__)


GR_CONF_FILE_NAME = "genomic_resource.yaml"
GR_MANIFEST_FILE_NAME = ".MANIFEST"
GR_CONTENTS_FILE_NAME = ".CONTENTS.json.gz"

# The repository index as releases before #758 also published it:
# uncompressed, beside the gzipped one. It is never written any more --
# on a large GRR the uncompressed copy dwarfs the file that matters --
# but the name stays load-bearing in three ways, so it is spelled out
# once here rather than derived from the name above. It is a historical
# fact, not that name minus its extension: deriving it would weld the
# two together, and a repository that one day publishes `.json.zst`
# would silently start probing for `.CONTENTS.json.z`.
#
#   - `load_contents`/`md5_contents` fall back to it when a repository
#     has no gzipped index, which is how GRRs published by an older
#     release, and the checked-in `fixtures/repo`, are still read;
#   - `find_directory_with_a_file` probes it to locate a repository
#     root, so such a checkout is still discoverable from a subdirectory;
#   - a publish reports one it finds, since from #758 on nothing
#     refreshes it.
GR_LEGACY_CONTENTS_FILE_NAME = ".CONTENTS.json"

GR_SQLITE_META_FILE_NAME = ".CONTENTS.sqlite3.gz"
GR_INDEX_FILE_NAME = "index.html"
GR_STATISTICS_FOLDER_NAME = "statistics"

#: What an FTS index column may be named. Every name the index build
#: creates is vetted against this before it becomes a column, because a
#: column name cannot be bound as a parameter and so has to be spliced
#: into SQL (gain#464).
INDEX_COLUMN_PATTERN = "[A-Za-z_][A-Za-z0-9_]*"
INDEX_COLUMN_RE = re.compile(f"{INDEX_COLUMN_PATTERN}\\Z")

#: The columns every resource contributes to the FTS index, in the order
#: ``ResourceImplementation.collect_index_info`` emits them.
GR_INDEX_RESOURCE_FIELDS = (
    "full_id", "id", "type", "description", "summary",
)

#: The columns a *score* implementation contributes on top of those.
GR_INDEX_SCORE_FIELDS = ("score_ids", "score_descriptions")

#: Index columns that describe the resource rather than one of its
#: ``meta.labels`` entries. A label query names a label, so a clause on one
#: of these must not be answered out of the column that shares its name --
#: and no resource can carry them as labels anyway, because the index build
#: refuses a label key repeating any of these names, whatever fields the
#: resource's own implementation contributes (gain#542).
#:
#: An implementation that contributes a further field of its own belongs
#: here too; the index cannot tell on its own which of its columns came
#: from a label. Registering it here is what both refuses it as a label key
#: and keeps a clause naming it off that column.
GR_INDEX_NON_LABEL_COLUMNS = frozenset(
    GR_INDEX_RESOURCE_FIELDS + GR_INDEX_SCORE_FIELDS,
)

#: The path `grr_manage resource-info` writes the statistics page to;
#: named here so the writer and the exclusion below cannot drift (#373).
GR_STATISTICS_INDEX_FILE_NAME = \
    f"{GR_STATISTICS_FOLDER_NAME}/{GR_INDEX_FILE_NAME}"

#: The pages `grr_manage resource-info` writes into a resource. They are
#: regenerated on every run, so they are build artefacts rather than resource
#: data and are never manifested -- whether or not DVC manages them (#373).
GR_GENERATED_INFO_PAGES = frozenset({
    GR_INDEX_FILE_NAME,
    GR_STATISTICS_INDEX_FILE_NAME,
})

GR_ENCODING = "utf-8"

_GR_ID_TOKEN_RE = re.compile(r"[a-zA-Z0-9._-]+")

#: Separators a resource path is split on before its segments are
#: scanned. A backslash is a path separator on Windows and in several
#: fsspec backends, so it counts as one here.
_RESOURCE_NAME_SEPARATOR = re.compile(r"[/\\]")

#: A drive-letter prefix (``C:``). ``ntpath.isabs`` calls ``x:y``
#: *relative*, but ``ntpath.join`` still discards the base for it, so the
#: prefix is what has to be rejected -- not absoluteness as ntpath
#: defines it.
_WINDOWS_DRIVE = re.compile(r"[a-zA-Z]:")


def is_generated_info_page(name: str) -> bool:
    """Return True if ``name`` is a page ``resource-info`` generates.

    Membership in a resource's manifest is decided by the file's PATH: the
    two pages GAIn writes itself are excluded, everything else is resource
    data. The rule it replaced -- "drop every name ending in ``html``" -- was
    a proxy for the same question and a bad one, since it silently dropped
    any html file a resource legitimately carries as data (#373).
    """
    return name in GR_GENERATED_INFO_PAGES


# Serves three consumers, so the range is drawn for the widest of them.
#
# A cache path: ``urllib.parse.urlsplit`` DELETES ASCII tab, CR and LF from
# anywhere in a url, and a repository id is joined onto a url that is then
# re-parsed to derive the cache path. An id carrying one of them therefore
# reads as a single segment while resolving to a DIFFERENT one: ``"..\\n"``
# becomes ``..`` (one level above the cache directory) and ``"a\\nb"``
# becomes ``ab``, silently sharing the cache directory of a genuinely
# different id. A NUL is not a url problem but a filesystem one -- it
# reaches the ``mkdir`` call and dies there with a message about nothing
# the operator wrote.
#
# A log line: a resource id and a manifest entry name are read verbatim out
# of remote GRR content and rendered into log messages unescaped, so a
# newline in one emits a second, fully-formed-looking record that can
# assert the opposite of what the run found (gain#642). An ANSI escape
# reaches the operator's terminal the same way.
#
# The whole C0/C1 range goes rather than the handful that bite today: none
# of them belongs in a resource name, and a list tuned to one url parser's
# or one terminal's current quirks is one change away from a hole.
#
# U+2028 and U+2029 join them because a line break is not only ``\\n``:
# ``str.splitlines`` breaks on both, so anything that post-processes a
# captured log splits there, and a UAX #14 consumer (an html log viewer)
# renders them as mandatory breaks. U+0085 NEL is already in the C1 range.
_UNSAFE_NAME_CHARACTER_RE = re.compile(
    r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def _escape_one_character(match: re.Match[str]) -> str:
    code = ord(match.group())
    if code <= 0xFF:
        return f"\\x{code:02x}"
    return f"\\u{code:04x}"


def escape_unsafe_characters(name: str) -> str:
    """Render an untrusted name safe to interpolate into ONE log line.

    Refusing a name that carries a control character is not by itself
    enough, because the refusal *names* it: the drop warning, the two
    ``validate_*`` messages and the manifest-entry warning all interpolate
    the very name they are rejecting. A newline in it then emits the second
    record the refusal exists to prevent -- and the refusal path is the one
    place a crafted name is still guaranteed to be rendered.

    So the two halves are complementary, not alternatives. Refusal keeps
    the name away from the many call sites that log a resource id or an
    entry name in passing; this keeps the handful that report the refusal
    itself on one line.

    ``\\xNN``/``\\uNNNN`` rather than ``repr``: it leaves every other
    character untouched, so an operator still reads the name they wrote,
    with only the invisible part made visible. The two widths matter --
    ``\\x2028`` for U+2028 would read as ``\\x20`` followed by the literal
    text ``28``, which is a different (and legitimate) name.
    """
    return _UNSAFE_NAME_CHARACTER_RE.sub(_escape_one_character, name)


def _escaping_path_reason(candidate: str, container: str) -> str | None:
    """Return why one already-decoded path is not usable in ``container``.

    Shared by a resource file name and a resource id. Two properties, both
    of which make the name unusable rather than merely odd:

    Containment proper -- the path must be relative and name no ``..``
    segment. Absoluteness is tested the Windows way as well as the POSIX
    way -- ``ntpath.join`` discards the base for a drive-letter path, a UNC
    share and even the drive-RELATIVE ``x:y``, exactly as ``posixpath.join``
    does for ``/x``. Ignoring that while the ``..`` scan already treats a
    backslash as a separator would be incoherent.

    No control or line-separator character -- see
    :data:`_UNSAFE_NAME_CHARACTER_RE`. This one is
    not a containment property, and it lives here anyway because this is the
    single helper both untrusted names funnel through, in both their raw and
    their percent-decoded spelling; checking it in each caller instead would
    mean writing it four times and forgetting it in the fifth.
    """
    if _UNSAFE_NAME_CHARACTER_RE.search(candidate):
        return "carries a control or line-separator character"
    if candidate.startswith(("/", "\\")):
        return "is absolute"
    if ntpath.isabs(candidate) or _WINDOWS_DRIVE.match(candidate):
        return "is absolute"
    if any(segment == ".."
           for segment in _RESOURCE_NAME_SEPARATOR.split(candidate)):
        return f"escapes the {container}"
    return None


def uncontained_resource_file_name_reason(filename: str) -> str | None:
    """Return why ``filename`` is not resource-contained, or ``None``.

    Resource file names arrive from GRR *content* -- the resource's
    ``genomic_resource.yaml`` and its ``.MANIFEST`` -- which is fetched from
    remote repositories and is therefore untrusted. A name is contained when
    it is relative and names no ``..``, ``.`` or empty segment; nested names
    such as ``statistics/histogram_score.json`` are ordinary and stay
    allowed.

    The joined location is a URL, not an os path, so the name is checked
    both as written and percent-decoded: an http(s) server decodes the path
    before resolving it, which makes ``%2e%2e`` a traversal there. A single
    decoding pass is the right depth -- ``%252e%252e`` decodes to the
    literal text ``%2e%2e``, which no server resolves any further.

    A ``..`` that would stay inside the resource (``sub/../other.txt``) is
    rejected as well, because the three backends GAIn speaks to disagree
    about what it means: `yarl`/aiohttp normalises it away client-side
    before the request is even sent, minio rejects the key outright
    (``XMinioInvalidResourceName``), and a local filesystem resolves it.
    One name, three outcomes -- so it is refused everywhere rather than left
    to mean whatever the protocol of the day decides.

    A degenerate name -- empty, blank, ``.``, or carrying an empty segment
    -- is refused too: ``open_raw_file("")`` addressed the resource
    DIRECTORY, and no resource file is legitimately spelled that way. See
    gain#467.

    A name carrying a control character is refused as well -- not a
    containment failure but a reporting one, since the name is logged
    unescaped. See gain#642.
    """
    if not filename.strip():
        return "is empty"
    for candidate in (filename, unquote(filename)):
        reason = _escaping_path_reason(candidate, "resource directory")
        if reason is not None:
            return reason
        for segment in _RESOURCE_NAME_SEPARATOR.split(candidate):
            if segment == ".":
                return "carries a <.> segment"
            if not segment.strip():
                return "carries an empty segment"
    return None


def validate_resource_file_name(resource_id: str, filename: str) -> None:
    """Raise ``ValueError`` unless ``filename`` stays inside the resource."""
    reason = uncontained_resource_file_name_reason(filename)
    if reason is not None:
        raise ValueError(
            f"resource file name <{escape_unsafe_characters(filename)}> "
            f"{reason}; "
            f"resource <{escape_unsafe_characters(resource_id)}>")


def uncontained_resource_id_reason(resource_id: str) -> str | None:
    """Return why ``resource_id`` is not repository-contained, or ``None``.

    A resource id is the *other* operand of the same join a file name goes
    through: ``get_resource_url`` joins it onto the repository url. On the
    remote path it is read verbatim out of the repository's
    ``.CONTENTS.json.gz``, so it is exactly as untrusted as a manifest
    entry name -- and containing only the file name left the escape wide
    open through its sibling (gain#467).

    ``""`` and ``"."`` are contained: both name the repository root, which
    is a supported resource in its own right -- ``proto_builder`` addresses
    it as ``""`` and ``build_local_resource`` as ``"."``. Only the escape
    itself is refused here, not the degenerate spellings a file name is
    also held to: an id is joined once, at the root, so a ``.`` segment in
    it is a no-op rather than a way to address something else.

    An id carrying a control character is refused, which is a reporting
    concern rather than a containment one -- the id is logged unescaped at
    many call sites, and a newline in it forges a log line. See gain#642.
    """
    if resource_id in {"", "."}:
        return None
    for candidate in (resource_id, unquote(resource_id)):
        reason = _escaping_path_reason(candidate, "repository")
        if reason is not None:
            return reason
    return None


def validate_resource_id(resource_id: str) -> None:
    """Raise ``ValueError`` unless ``resource_id`` stays inside the repo."""
    reason = uncontained_resource_id_reason(resource_id)
    if reason is not None:
        raise ValueError(
            f"resource id <{escape_unsafe_characters(resource_id)}> {reason}")


def report_uncontained_manifest_entries(
    resource_id: str, manifest: Manifest,
) -> None:
    """Warn about -- and never raise on -- entries that escape the resource.

    Rejecting a poisoned entry while *parsing* the manifest reads as
    defence in depth, but a manifest is parsed while ENUMERATING a
    repository: one bad entry then kills the generator before a single
    resource is yielded, and ``list``, ``repo-repair`` and even
    ``resource-repair`` on an unrelated healthy resource all die with it.
    That is the gain#464 shape -- one poisoned resource costing the whole
    repository -- and it is not worth paying, because the load-bearing
    check sits at the join in :meth:`get_resource_file_url` and fails the
    poisoned name loudly the moment anything tries to USE it.

    So this only supplies the attribution the raise used to: a warning that
    names the resource AND the entry, which the raise could not do because
    a ``ManifestEntry`` does not know which resource it belongs to.
    """
    for entry in manifest:
        reason = uncontained_resource_file_name_reason(entry.name)
        if reason is not None:
            logger.warning(
                "resource <%s> has a manifest entry <%s> that %s; "
                "any access to it will be refused",
                escape_unsafe_characters(resource_id),
                escape_unsafe_characters(entry.name), reason)


def is_gr_id_token(token: str) -> bool:
    """Check if token can be used as a genomic resource ID.

    Genomic Resource Id Token is a string with one or more letters,
    numbers, '.', '_', or '-'. The function checks if the parameter
    token is a Genomic REsource Id Token.
    """
    return bool(_GR_ID_TOKEN_RE.fullmatch(token))


# Both separators, not just ``os.sep``: a definition is portable text and
# may be written on either platform, and the id it carries must be a single
# segment under either reading.
_PATH_SEPARATORS = ("/", "\\")


def is_safe_repo_id(repo_id: str) -> bool:
    """Check if ``repo_id`` is usable as a single filesystem path segment.

    A repository id names a directory: a cached repository derives each
    repository's cache directory by joining the id onto the cache url. An id
    that is not a single path segment therefore decides where the process
    writes -- ``..`` climbs out of the configured cache directory, and an
    absolute id makes ``os.path.join`` discard the cache url altogether. Such
    an id is a configuration error and is rejected, never rewritten (#460).

    Safe means exactly one non-empty path segment that still names that
    same segment after a round trip through a url. Each check earns its
    keep separately: the separator check rules out ``sub/dir``,
    ``../../escaped`` and an absolute ``/etc/grrcache`` -- and, because a
    UNC prefix (``\\\\server\\share``, ``//server/share``, ``\\\\?\\C:\\x``)
    always carries separators, those too; :func:`ntpath.splitdrive` adds
    the one absolute-ish prefix that has no separator in it, ``C:cache``,
    which ``os.path.join`` on Windows resolves against that drive's current
    directory; the control-character check covers the id that changes shape
    when it is parsed as part of a url (see ``_UNSAFE_NAME_CHARACTER_RE``); and
    ``.`` and ``..`` are spelled out because they are ordinary segments to
    every one of the checks above.

    An empty id is not a segment either, but it is not a traversal: a falsy
    id already means "unnamed" everywhere it is read
    (``_resolve_repo_id`` synthesises one, ``find_resource`` treats it
    as "no filter"), so its callers decide what to do with it rather than
    this predicate.

    Deliberately NOT built on :func:`is_gr_id_token`. That helper enforces a
    character class (``[a-zA-Z0-9._-]``), which is both too weak and too
    strong here: ``is_gr_id_token("..")`` is True -- ``..`` matches the class
    in full, and a single-segment ``..`` still escapes one directory level --
    while ids that are perfectly safe as directory names (a space, say) would
    start failing for a reason that has nothing to do with path safety. This
    check answers only the path question.
    """
    if not repo_id or repo_id in {".", ".."}:
        return False
    if _UNSAFE_NAME_CHARACTER_RE.search(repo_id):
        return False
    if any(separator in repo_id for separator in _PATH_SEPARATORS):
        return False
    # A drive prefix is an absolute-ish path prefix without a separator:
    # ``os.path.join`` on Windows resolves ``C:cache`` against the drive's
    # current directory, discarding the cache url just as an absolute id does.
    return not ntpath.splitdrive(repo_id)[0]


_GR_ID_WITH_VERSION_TOKEN_RE = re.compile(
    r"([a-zA-Z0-9._-]+)(?:\(([0-9]\d*(?:\.\d+)*)\))?")


def parse_gr_id_version_token(token: str) -> tuple[str, tuple[int, ...]]:
    """Parse genomic resource ID with version.

    Genomic Resource Id Version Token is a Genomic Resource Id Token with
    an optional version appened. If present, the version suffix has the
    form "(3.3.2)". The default version is (0).
    Returns None if s in not a Genomic Resource Id Version. Otherwise
    returns token,version tupple
    """
    if token == "":
        return "", (0, )

    match = _RESOURCE_ID_WITH_VERSION_PATH_RE.fullmatch(token)
    if not match:
        raise ValueError(
            f"unexpected value for resource ID and version: {token}")
    token = match[1]
    version_string = match[2]
    if version_string:
        version = tuple(map(int, version_string.split(".")))
    else:
        version = (0,)
    return token, version


_RESOURCE_ID_WITH_VERSION_PATH_RE = re.compile(
    r"([a-zA-Z0-9/._-]+)(?:\(([0-9]\d*(?:\.\d+)*)\))?")


def parse_resource_id_version(
    resource_path: str,
) -> tuple[str, tuple[int, ...] | None]:
    """Parse genomic resource id and version path into Id, Version tuple.

    An optional version (0,) appened if needed. If present, the version suffix
    has the form "(3.3.2)". The default version is (0,).
    Returns tuple (None, None) if the path does not match the
    resource_id/version requirements. Otherwise returns tuple
    (resource_id, version).
    """
    if resource_path == "":
        return "", None

    match = _RESOURCE_ID_WITH_VERSION_PATH_RE.fullmatch(resource_path)
    if not match:
        raise ValueError(f"unexpeced resource path: {resource_path}")
    token = match[1]
    version_string = match[2]
    if version_string:
        version = tuple(map(int, version_string.split(".")))
    else:
        version = None
    return token, version


def version_string_to_suffix(version: str) -> str:
    """Transform version string into resource ID version suffix."""
    if version == "0":
        return ""
    return f"({version})"


def version_tuple_to_string(version: tuple[int, ...]) -> str:
    """Convert version tuple to string representation.

    Args:
        version: Version tuple like (1, 2, 3)

    Returns:
        String representation like "1.2.3"
    """
    return ".".join(map(str, version))


def version_tuple_to_suffix(version: tuple[int, ...]) -> str:
    """Transform version tuple into resource ID version suffix.

    The suffix is used to append version information to resource IDs.
    Default version (0,) produces no suffix.

    Args:
        version: Version tuple like (1, 2, 3)

    Returns:
        Empty string for version (0,), otherwise "(1.2.3)" format
    """
    if version == (0,):
        return ""
    return f"({'.'.join(map(str, version))})"


VERSION_CONSTRAINT_RE = re.compile(r"(>=|=)?(\d+(?:\.\d+)*)")


def is_version_constraint_satisfied(
        version_constraint: str | None, version: tuple[int, ...]) -> bool:
    """Check if a version matches a version constraint.

    Supports two types of constraints:
        - "=X.Y.Z": Exact match required
        - ">=X.Y.Z" or "X.Y.Z": Minimum version required (default)

    Args:
        version_constraint: Constraint string like ">=1.2.0" or "=1.2.3".
                          None or empty string matches any version.
        version: Version tuple to check like (1, 2, 3)

    Returns:
        True if the version satisfies the constraint

    Raises:
        ValueError: If constraint has invalid syntax or unknown operator
    """
    if not version_constraint:
        return True
    match = VERSION_CONSTRAINT_RE.fullmatch(version_constraint)
    if not match:
        raise ValueError(
            f"Bad syntax of version constraint {version_constraint}")
    operator = match[1] or ">="
    constraint_version = tuple(map(int, match[2].split(".")))
    if operator == "=":
        return version == constraint_version
    if operator == ">=":
        return version >= constraint_version
    raise ValueError(
        f"wrong operation {operator} in version constraint "
        f"{version_constraint}")


@dataclass(order=True)
class ManifestEntry:
    """Represents a file entry in a genomic resource manifest.

    A manifest tracks all files within a resource with their sizes and
    checksums to ensure data integrity and enable efficient caching.

    Attributes:
        name: Relative path to the file within the resource
        size: File size in bytes
        md5: MD5 checksum of file content, or None if not computed
    """

    name: str
    size: int
    md5: str | None


@dataclass(order=True)
class ResourceFileState:
    """Tracks the state of a resource file in internal repository storage.

    Used for caching and synchronization to determine if files need
    to be refreshed or re-downloaded.

    Attributes:
        filename: Relative path to the file within the resource
        size: File size in bytes
        timestamp: Last modification time as Unix timestamp
        md5: MD5 checksum of file content
    """

    filename: str
    size: int
    timestamp: float
    md5: str


class Manifest:
    """Manages file listings and checksums for a genomic resource.

    A manifest maintains a catalog of all files in a resource with their
    sizes and MD5 checksums. This enables data integrity verification,
    efficient caching, and incremental updates.

    The manifest is typically stored in a .MANIFEST file within the resource
    directory and is automatically loaded when accessing the resource.
    """

    def __init__(self) -> None:
        self.entries: dict[str, ManifestEntry] = {}

    @staticmethod
    def from_file_content(file_content: str) -> Manifest:
        """Create a manifest from raw YAML file content.

        Args:
            file_content: YAML-formatted string containing manifest entries

        Returns:
            Manifest object with entries parsed from the content
        """
        manifest_entries = yaml.safe_load(file_content)
        if manifest_entries is None:
            manifest_entries = []
        return Manifest.from_manifest_entries(manifest_entries)

    @staticmethod
    def from_manifest_entries(
            manifest_entries: list[dict[str, Any]]) -> Manifest:
        """Create a manifest from parsed manifest entry dictionaries.

        Args:
            manifest_entries: List of dicts with 'name', 'size', 'md5' keys

        Returns:
            Manifest object populated with the provided entries
        """
        result = Manifest()
        for data in manifest_entries:
            entry = ManifestEntry(
                data["name"], data["size"], data["md5"])
            result.entries[entry.name] = entry
        return result

    def get_files(self) -> list[tuple[str, int]]:
        """Get list of all files with their sizes.

        Returns:
            List of (filename, size) tuples for all files in manifest
        """
        return [
            (entry.name, entry.size)
            for entry in self.entries.values()
        ]

    def __getitem__(self, name: str) -> ManifestEntry:
        return self.entries[name]

    def __contains__(self, name: str) -> bool:
        return name in self.entries

    def __iter__(self) -> Iterator[ManifestEntry]:
        return iter(sorted(self.entries.values()))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Manifest):
            return False
        return self.entries == other.entries

    def __len__(self) -> int:
        return len(self.entries)

    def __repr__(self) -> str:
        return str(self.entries)

    def to_manifest_entries(self) -> list[dict[str, Any]]:
        """Convert manifest to list of dictionaries for serialization.

        Returns:
            List of dictionaries with 'name', 'size', 'md5' keys,
            sorted by filename
        """
        return [
            asdict(entry) for entry in sorted(self.entries.values())]

    def add(self, entry: ManifestEntry) -> None:
        """Add or update a manifest entry.

        Args:
            entry: ManifestEntry to add to the manifest
        """
        self.entries[entry.name] = entry

    def update(self, entries: dict[str, ManifestEntry]) -> None:
        """Add or update multiple manifest entries.

        Args:
            entries: Dictionary mapping filenames to ManifestEntry objects
        """
        for entry in entries.values():
            self.add(entry)

    def names(self) -> set[str]:
        """Get set of all filenames in the manifest.

        Returns:
            Set of filenames tracked by this manifest
        """
        return set(self.entries.keys())


@dataclass
class ManifestUpdate:
    """Represents a set of changes to apply to a manifest.

    Used during resource synchronization to track which files need
    to be deleted or updated.

    Attributes:
        manifest: The updated manifest with all changes applied
        entries_to_delete: Set of filenames to remove
        entries_to_update: Set of filenames that need updating
    """

    manifest: Manifest
    entries_to_delete: set[str]
    entries_to_update: set[str]

    def __bool__(self) -> bool:
        """Check if there are any changes in this update.

        Returns:
            True if there are files to delete or update
        """
        return bool(self.entries_to_delete or self.entries_to_update)


@dataclass(frozen=True)
class ResourceScan:
    """What one scan of a resource's directory found.

    ``unreadable`` maps each file the scan listed but could not stat --
    a dangling symlink, a symlink loop, a directory it may not traverse --
    to why. They are NOT in ``manifest``: there is no size to put there.

    They are carried rather than raised because the scan cannot yet know
    whether they matter. A DVC-managed file materialised as a link into a
    shared cache is unreadable exactly when that cache has been garbage
    collected, and its ``.dvc`` sidecar still describes it perfectly --
    so it is manifested from the sidecar and nothing is wrong. Only a
    name that NOTHING can describe is a broken resource, and that is not
    known until the sidecars have been merged in (gain#503).

    The reason travels with the name so that whoever DOES know the
    outcome can report it: a resource is scanned more than once per
    command, so the scan itself is the wrong place to say anything the
    user should see exactly once.
    """

    manifest: Manifest
    unreadable: Mapping[str, str]


class UnreadableResourceFilesError(ValueError):
    """Files of ONE resource that could not be read or described.

    Collected rather than raised on the first offender, so a single run
    reports every one of them. A ``ValueError`` so that
    ``cli_errors.report_resource_failure`` renders it as one line naming the
    resource and carrying the cause, with the traceback demoted to DEBUG
    (gain#364) -- and so that one broken resource fails itself instead of
    aborting the repository-wide command (gain#503).
    """

    def __init__(self, resource_id: str, names: Sequence[str]) -> None:
        self.resource_id = resource_id
        self.names = tuple(names)
        listed = ", ".join(f"<{name}>" for name in self.names)
        super().__init__(
            f"{len(self.names)} file(s) could not be read and no '.dvc' "
            f"sidecar describes them: {listed}. A file the scan lists must "
            f"end up in the manifest or fail the resource -- dropping it "
            f"would leave a '.MANIFEST' that silently omits part of the "
            f"resource. If these are symlinks into a DVC cache, restore "
            f"them with 'dvc pull' (or 'dvc checkout'); if they are stale "
            f"links, remove them",
        )


class SearchTermError(ValueError):
    """A search term that SQLite's FTS5 could not parse as a match expression.

    The term is bound, never interpolated, so this is not a failure to
    contain it -- it is that FTS5 reads a bound term as an *expression*,
    with a grammar of its own: quotes, ``AND``/``OR``/``NOT``, ``NEAR``,
    ``column : value``. A term that does not parse is the caller's
    mistake, and ``apsw.SQLError`` reports it as neither -- it names no
    term, no argument, and reads like the database broke (gain#632).

    A ``ValueError``, like ``ResourceQueryParseError``, so the two bad
    arguments this search can be given are handled the same way by the
    endpoint and the CLI that take them.

    The column filter is why the term is not simply quoted into a literal
    before it reaches ``MATCH``: label keys are index columns, and
    ``ref_genome : hg38`` is a supported search. Quoting would make that a
    search for the text.
    """

    def __init__(self, search_term: str, cause: Exception) -> None:
        # The cause is not kept: `raise ... from err` records it as
        # `__cause__` already, and the message carries what it said.
        self.search_term = search_term
        super().__init__(
            f"cannot search for <{search_term}>: it is not a valid "
            f"full-text search expression ({cause})",
        )


class SearchIndexUnavailableError(ValueError):
    """A repository that cannot apply a search filter for want of an index.

    Raised when the repository publishes no ``.CONTENTS.sqlite3.gz`` at all,
    and when the one it publishes carries no ``contents`` table because no
    resource could be indexed into it. Both are repository *health*: the
    filter is unobjectionable and a ``grr_manage repo-repair`` would let it
    be applied.

    Typed rather than left as a bare ``ValueError`` because a group
    repository absorbs this to skip the child and carry on (ADR 0012), and
    the layers it absorbs it through raise ``ValueError`` of their own that
    must keep propagating -- the cache layer resolving a resource it cannot
    place, for one.

    A ``ValueError`` still, so a caller that only ever distinguished bad
    arguments from working ones is unaffected.
    """

    def __init__(self, repo_id: str, reason: str) -> None:
        self.repo_id = repo_id
        self.reason = reason
        super().__init__(
            f"repository <{repo_id}> cannot be searched: {reason}. "
            f"Build a search index with `grr_manage repo-repair`, or "
            f"select by id and labels with a resource query, which needs "
            f"none",
        )


class GenomicResource:
    """Represents a single genomic resource with metadata and file access.

    A genomic resource is a versioned collection of data files with a
    configuration file (genomic_resource.yaml) that defines its type,
    description, and resource-specific settings.

    Common resource types include:
        - genome: Reference genome sequences
        - gene_models: Gene annotations and transcript models
        - position_score: Position-based genomic scores
        - allele_score: Variant effect scores
        - gene_score: Gene-level scores

    Attributes:
        resource_id: Unique identifier like "hg19/gene_models/refseq"
        version: Version tuple like (1, 2, 3)
        config: Configuration dictionary from genomic_resource.yaml
        proto: Repository protocol for accessing resource files
    """

    def __init__(
            self, resource_id: str, version: tuple[int, ...],
            protocol: RepositoryProtocol,
            config: dict[str, Any] | None = None,
            manifest: Manifest | None = None):

        self.resource_id = resource_id
        self.version: tuple[int, ...] = version
        self.config = config
        self.proto = protocol
        self._manifest: Manifest | None = manifest

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GenomicResource):
            return NotImplemented
        return self.resource_id == other.resource_id and \
            self.version == other.version and \
            self.config == other.config

    def __hash__(self) -> int:
        # A strict subset of what ``__eq__`` compares, which is what
        # makes ``a == b`` imply ``hash(a) == hash(b)``.  ``config`` is
        # a dict and unhashable; leaving it out only coarsens the hash.
        return hash((self.resource_id, self.version))

    def invalidate(self) -> None:
        """Clean up cached attributes like manifest, etc."""
        self._manifest = None

    def get_id(self) -> str:
        """Return genomic resource ID."""
        return self.resource_id

    def get_full_id(self) -> str:
        """Return genomic resource ID with version."""
        version = ""
        if self.get_version_str() != "0":
            version = f"({self.get_version_str()})"
        return f"{self.resource_id}{version}"

    def get_config(self) -> dict[str, Any]:
        """Return the resouce configuration."""
        if self.config is None:
            raise ValueError(
                f"use of unconfigured genomic resource: {self.resource_id}")
        return self.config

    def get_description(self) -> str:
        """Return resource description."""
        config = self.get_config()
        if config is None:
            raise ValueError(f"resource {self.resource_id} not configured")
        if config.get("meta"):
            meta = config["meta"]
            if meta.get("description"):
                return str(meta["description"])
        return ""

    def get_summary(self) -> str | None:
        """Return resource summary."""
        config = self.get_config()
        if config is None:
            raise ValueError(f"resource {self.resource_id} not configured")
        if config.get("meta"):
            meta = config["meta"]
            if meta.get("summary"):
                return str(meta["summary"])
        return self.get_description()

    def get_repo_url(self) -> str:
        """Return repository's URL."""
        return self.proto.get_url()

    def get_repo_public_url(self) -> str:
        """Return repository's URL."""
        return self.proto.get_public_url()

    def get_public_url(self) -> str:
        return f"{self.get_repo_public_url()}/{self.get_full_id()}"

    def get_url(self) -> str:
        return f"{self.get_repo_url()}/{self.get_full_id()}"

    def get_labels(self) -> dict[str, Any]:
        """Return resource labels.

        ``meta`` and ``meta.labels`` are both free-form YAML, so what is
        in either is whatever the curator wrote -- a scalar, a list and an
        int are all things a resource can declare, and only the resource
        types that run the base schema are refused for it.  Both levels
        are narrowed rather than trusted: a non-mapping reads as no labels
        and is reported, so that every caller sees a mapping whatever the
        resource says (gain#654).

        Reading never validates (ADR 0008) and never raises: this is on
        the path of every repository-wide walk -- a label search, the
        index build, ``grr_manage list`` -- and one malformed resource
        must cost that walk only itself, the way a resource the index
        cannot take does (gain#464, gain#503, ADR 0010).
        """
        config = self.get_config()
        if config is None:
            raise ValueError(f"resource {self.resource_id} not configured")
        meta = config.get("meta")
        if meta is None:
            return {}
        if not isinstance(meta, dict):
            # `meta` is as free-form as what it holds, so a non-mapping
            # here reaches this read exactly the way a non-mapping
            # `labels` does -- and used to crash it the same way.
            self._warn_not_a_mapping("meta", meta)
            return {}
        labels = meta.get("labels")
        if labels is None:
            # Absent, or declared as an explicit YAML null.  Both say
            # "no labels" and neither is a mistake, so neither is
            # reported; the production GRRs carry the null spelling.
            return {}
        if not isinstance(labels, dict):
            # Reported whatever its truthiness: `labels: []` and
            # `labels: 0` are the same curator mistake as `labels: [a]`,
            # and reading those two as no labels *silently* would leave
            # the curator with nothing to act on.
            self._warn_not_a_mapping("meta.labels", labels)
            return {}
        return labels

    def _warn_not_a_mapping(self, what: str, value: Any) -> None:
        """Report a ``meta`` level that is not the mapping it must be."""
        logger.warning(
            "resource <%s>: %s is a %s, not a mapping; reading it as no "
            "labels -- fix the resource's 'genomic_resource.yaml'",
            escape_unsafe_characters(self.resource_id),
            what, type(value).__name__)

    def get_type(self) -> str:
        """Return resource type as defined in 'genomic_resource.yaml'."""
        config = self.get_config()
        if config is None:
            raise ValueError(f"resource {self.resource_id} not configured")
        config_type = config.get("type")
        if config_type is None:
            return "basic"
        return cast(str, config_type)

    def get_version_str(self) -> str:
        """Return version string of the form '3.1'."""
        return version_tuple_to_string(self.version)

    def get_genomic_resource_id_version(self) -> str:
        """Return a string combinint resource ID and version.

        Returns a string of the form aa/bb/cc[3.2] for a genomic resource with
        id aa/bb/cc and version 3.2.
        If the version is 0 the string will be aa/bb/cc.
        """
        return f"{self.resource_id}{version_tuple_to_suffix(self.version)}"

    def file_exists(self, filename: str) -> bool:
        """Check if filename exists in this resource."""
        return self.proto.file_exists(self, filename)

    def get_manifest(self) -> Manifest:
        """Load resource manifest if it exists. Otherwise builds it."""
        # Returned through a local, never by re-reading ``_manifest``: an
        # ``invalidate`` landing between the store and a second read would
        # otherwise make this return ``None`` (#519).
        manifest = self._manifest
        if manifest is None:
            manifest = self._manifest = self.proto.get_manifest(self)
        return manifest

    def get_loaded_manifest(self) -> Manifest | None:
        """Return the resource manifest without ever building one.

        :meth:`get_manifest` falls back to *building* the manifest on a
        read-write protocol -- an md5 scan of every byte of the resource
        that also writes ``.grr/*.state`` files, and that fails outright on
        a read-only GRR mount.  A pure read path that merely wants to
        consult the manifest uses this instead and copes with ``None``.
        """
        # Through a local, as in :meth:`get_manifest` (#519).  The stakes are
        # higher here: ``None`` is a meaningful answer -- "no stored manifest"
        # -- so a re-read caught by an ``invalidate`` does more than break the
        # annotation, it makes a resource that has a manifest look like one
        # that does not.  The ``FileNotFoundError`` branch returns directly,
        # keeping that answer reachable for a resource genuinely without one.
        manifest = self._manifest
        if manifest is None:
            try:
                manifest = self._manifest = self.proto.load_manifest(self)
            except FileNotFoundError:
                return None
        return manifest

    def get_file_url(self, filename: str) -> str:
        return self.proto.get_resource_file_url(self, filename)

    def get_file_content(
        self, filename: str,
        *,
        uncompress: bool = True,
        mode: str = "t",
    ) -> Any:
        """Return the content of file in a resource."""
        return self.proto.get_file_content(
            self, filename, uncompress=uncompress, mode=mode)

    def open_raw_file(
            self, filename: str, mode: str = "rt",
            **kwargs: str | bool | None) -> IO:
        """Open a file in the resource and returns a File-like object."""
        return self.proto.open_raw_file(
            self, filename, mode, **kwargs)

    def open_tabix_file(
            self, filename: str,
            index_filename: str | None = None) -> pysam.TabixFile:
        """Open a tabix file and returns a pysam.TabixFile."""
        return self.proto.open_tabix_file(self, filename, index_filename)

    def open_vcf_file(
            self, filename: str,
            index_filename: str | None = None) -> pysam.VariantFile:
        """Open a vcf file and returns a pysam.VariantFile."""
        return self.proto.open_vcf_file(self, filename, index_filename)

    def open_fasta_file(
            self, filename: str,
            index_filename: str | None = None,
            compressed_index_filename: str | None = None) -> pysam.FastaFile:
        """Open a bgzipped fasta file and return a pysam.FastaFile."""
        return self.proto.open_fasta_file(
            self, filename, index_filename, compressed_index_filename)

    def open_bigwig_file(self, filename: str) -> Any:
        """Open a bigwig file and return it."""
        return self.proto.open_bigwig_file(self, filename)


# The tabix index flavours htslib writes next to a bgzipped table, in
# preference order.  ``.csi`` is what a contig beyond tabix's ~512 Mbp limit
# requires; ``.tbi`` is the default everywhere else.  When a resource carries
# both, ``.tbi`` wins -- the same order ``gain.utils.fs_utils`` uses for
# plain filesystem paths.
TABIX_INDEX_SUFFIXES = (".tbi", ".csi")


def resolve_tabix_index_filename(
    manifest: Manifest, filename: str,
) -> str | None:
    """Return the tabix index of ``filename`` as recorded in ``manifest``.

    Resolution is manifest-driven on purpose: the manifest is already loaded
    and is protocol-agnostic, whereas probing with ``file_exists`` costs a
    network round-trip per candidate on the http and s3 protocols.

    Returns ``None`` when the manifest records neither index -- the caller
    decides whether that is a warning (the file set of an implementation) or
    an error (an open that needs an index).  See gain#430.
    """
    for suffix in TABIX_INDEX_SUFFIXES:
        index_filename = f"{filename}{suffix}"
        if index_filename in manifest:
            return index_filename
    return None


def resolve_tabix_index_filename_for_read(
    resource: GenomicResource, filename: str,
) -> str:
    """Return the index to read ``filename`` with, never building a manifest.

    Consults the manifest only when it is already loaded or can be loaded
    from the resource: an open must stay a pure read, and
    :meth:`GenomicResource.get_manifest` would *build* -- md5-scanning the
    whole resource and writing state files -- for a resource that carries no
    ``.MANIFEST`` (gain#430).

    With no manifest at hand -- the hand-authored GRR directory shape that
    ``collect_all_resources`` tolerates -- falls back to probing the
    resource for each candidate index.  The probe is deliberately confined
    to this branch: a manifest-backed resource must never pay the
    per-candidate network round-trip the probe costs on the http and s3
    protocols.

    With a manifest that records no index at all, or when no probe
    succeeds, falls back to the historical ``.tbi`` guess so that whatever
    pysam raises still names a concrete path.
    """
    manifest = resource.get_loaded_manifest()
    if manifest is not None:
        resolved = resolve_tabix_index_filename(manifest, filename)
    else:
        resolved = _probe_tabix_index_filename(resource, filename)
    return resolved if resolved is not None else f"{filename}.tbi"


def _probe_tabix_index_filename(
    resource: GenomicResource, filename: str,
) -> str | None:
    """Return the first candidate index that exists in ``resource``."""
    for suffix in TABIX_INDEX_SUFFIXES:
        index_filename = f"{filename}{suffix}"
        try:
            if resource.file_exists(index_filename):
                return index_filename
        except OSError:
            logger.debug(
                "unable to probe %s in resource %s",
                index_filename, resource.resource_id, exc_info=True)
            return None
    return None


class Mode(enum.Enum):
    """Enumeration of repository protocol access modes.

    Attributes:
        READONLY: Protocol supports only read operations
        READWRITE: Protocol supports both read and write operations
    """

    READONLY = 1
    READWRITE = 2


# The name the resource-query push-down registers on a metadata
# connection. The connection is deserialized fresh for every search, so it
# never has to be unique across concurrent searches.
_MATCH_ID_FUNCTION = "gain_query_match_id"


def _sql_matcher(match: Callable[[str], bool]) -> Callable[..., bool]:
    """Adapt a matcher to the values an index column hands back.

    A column with no value at all reads as ``""`` rather than reaching the
    matcher as ``None``: absence and emptiness are one case for the query
    language, so an id the index left null is an empty id, not a crash.

    Typed with an open parameter list because that is the shape apsw
    accepts for a scalar function; each one registered here is declared
    with exactly one argument.
    """
    def matcher(value: Any) -> bool:
        return match("" if value is None else str(value))
    return matcher


def _resource_query_condition(
    conn: apsw.Connection, query: ResourceQuery,
) -> tuple[str, list[LabelClause]]:
    """Express ``query`` as a SQL condition over the ``contents`` table.

    Only the id glob becomes a condition, and it is not restated in SQL:
    it is registered as a scalar function backed by the very matcher the
    Python path calls, so what ``*`` means is defined once no matter which
    path evaluates the query -- rewriting it as ``GLOB`` would be a second
    definition, free to drift from the first.

    Every label clause is handed back instead. A label column records the
    value the resource carried when the index was built, and nothing forces
    a published index to be current: answering a clause out of the column
    reads an edited label as it was then, which loses the resources that
    now match and returns the ones that no longer do (gain#646). No
    post-filter can repair the first of those, because it is a row the
    statement never yields. The caller re-asks each clause of the
    resources, whose labels the indexed path already materialises in full.

    Returns the condition, and the clauses the caller must apply itself;
    leaving those out of both places would widen the search.
    """
    conn.createscalarfunction(
        _MATCH_ID_FUNCTION, _sql_matcher(query.match_id), 1,
        deterministic=True)

    # A clause that accepts an absent label is dropped rather than
    # deferred: it is a tautology, because the grammar requires at least
    # one character in a value, so ``in`` can never accept ``""`` and the
    # only ``=`` values ``fnmatch`` accepts ``""`` for are globs of ``*``
    # alone -- which accept every string. That holds for every resource
    # however stale the index is, so it is an optimisation rather than a
    # decision the index takes part in.
    deferred = [
        clause for clause in query.label_clauses
        if not clause.matches_an_absent_label()
    ]

    return f"{_MATCH_ID_FUNCTION}(id)", deferred


def _reject_unparsable_search_term(
    conn: apsw.Connection, search_term: str,
) -> None:
    """Raise ``SearchTermError`` if FTS5 cannot read the term.

    Checked against a throwaway index carrying the same column names,
    rather than by translating whatever the repository's own index
    raises. The two are not the same test, and the difference is the
    whole point:

    * A corrupt, truncated or unreadable index fails the *same*
      ``MATCH`` statement a bad term fails -- ``vtable constructor
      failed``, ``no such tokenizer``, a ``contents`` that is not an FTS
      table. Blaming those on the term would answer 400, with "your
      search is malformed", to every caller of a repository that needs
      repairing, and would hide the breakage from anything watching for
      500s. None of them is caught here: the ones that survive being
      read far enough to name the columns go on to fail the real
      statement, and the ones that do not -- a damaged index cannot even
      be described -- come straight out of the ``pragma`` below. Either
      way the repository's failure is what the caller is told about.
    * A term is checked even when the planner never asks FTS5 to read
      it. A resource query that can match nothing folds the whole
      ``WHERE`` away, and a term validated only by being executed would
      pass unexamined -- the same request answering 400 or 200 depending
      on an unrelated parameter.

    The column names come from the index because a column filter
    (``ref_genome : hg38``) is a term that is valid only against an index
    that has the column. Every column is mirrored, including any name
    gain's own index build would have refused: this stands in for the
    index, so a name it does not carry is a column filter wrongly called
    malformed -- a working search turned into a 400, which is the one
    direction of this check that costs a caller something. The names are
    quoted rather than vetted, which is what makes that safe: a published
    index is an artefact of the repository and no more trusted than its
    manifest, and a quoted identifier carries nothing out of the
    statement it is spliced into.
    """
    columns = []
    for row in conn.execute("pragma table_info('contents')"):
        # Doubling is what keeps a name that contains a quote inside its
        # own quotes rather than ending them.
        quoted = row[1].replace('"', '""')
        columns.append(f'"{quoted}"')
    if not columns:
        # Nothing to build a probe out of. An index shaped like this
        # cannot answer a search at all; let the real statement say so.
        return

    probe = apsw.Connection(":memory:")
    try:
        try:
            probe.execute(
                "CREATE VIRTUAL TABLE contents "
                f"USING fts5({', '.join(columns)})",
            )
        except apsw.SQLError:
            # This index cannot be stood in for. Checking the term
            # against a different set of columns would refuse searches
            # the repository can answer, so it is not checked at all --
            # the real statement is left to accept or reject it.
            return
        try:
            # Stepped, not merely prepared: FTS5 parses the term when the
            # query runs. The table is empty, so this reads no rows -- it
            # only asks FTS5 whether the term is an expression.
            list(probe.execute(
                "SELECT 1 FROM contents WHERE contents MATCH ?",
                (search_term,)))
        except apsw.SQLError as err:
            raise SearchTermError(search_term, err) from err
    finally:
        probe.close()


class ReadOnlyRepositoryProtocol(abc.ABC):
    """Abstract base class for read-only repository storage protocols.

    A protocol defines how to access genomic resources from a specific
    storage backend (local filesystem, HTTP server, S3 bucket, etc.).
    Read-only protocols can retrieve resources but cannot modify them.

    Subclasses must implement methods for:
        - Listing available resources
        - Reading configuration files
        - Opening resource files
        - Loading manifests

    Attributes:
        proto_id: Unique identifier for this protocol instance
        url: Base URL or path to the repository root
        CHUNK_SIZE: Default read-buffer size for chunked file operations
            (1 MiB). This is the application-level read size for the download
            and md5 loops, not the network transfer unit -- fsspec does its
            own block-level fetching/buffering underneath. Larger chunks cut
            Python-loop and progress-callback overhead on multi-GB resources.
    """

    CHUNK_SIZE = 1024 * 1024

    def __init__(self, proto_id: str, url: str):
        self.proto_id = proto_id
        self.url = url

    def mode(self) -> Mode:
        """Return repository protocol mode.

        Returns:
            Mode.READONLY for this base class
        """
        return Mode.READONLY

    def get_id(self) -> str:
        """Return the repository protocol identifier.

        Returns:
            Protocol ID string
        """
        return self.proto_id

    @abc.abstractmethod
    def get_url(self) -> str:
        """Return the base URL of the repository.

        Returns:
            URL or path string pointing to repository root
        """

    @abc.abstractmethod
    def get_public_url(self) -> str:
        """Return the public base URL of the repository.

        Returns:
            URL or path string pointing to a public repository root
        """

    @abc.abstractmethod
    def invalidate(self) -> None:
        """Invalidate internal cache of repository protocol."""

    @abc.abstractmethod
    def get_all_resources(self) -> Generator[GenomicResource, None, None]:
        """Return generator for all resources in the repository."""

    @abc.abstractmethod
    def get_all_resources_dict(self) -> dict[str, GenomicResource]:
        """Return dictionary for all resources in the repository."""

    def find_resource(
        self, resource_id: str,
        version_constraint: str | None = None,
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

    def search_resources(
            self,
            search_term: str | None = None,
            resource_type: str | None = None,
            resource_query: str | None = None,
    ) -> Generator[GenomicResource, None, None]:
        """Search for resources using SQLite full-text search.

        The three filters conjoin: a resource must satisfy every one that is
        supplied. ``search_term`` and ``resource_type`` are matched against
        the FTS index; ``resource_query`` is the annotator wildcard language
        -- an id glob plus an optional label query -- whose id glob joins
        them in the same statement, so the index narrows once rather than
        handing rows to a filter.

        A ``resource_query`` on its own never opens the index, and is
        matched in Python instead. That is what makes it work on a
        repository with no ``.CONTENTS.sqlite3.gz`` at all, where opening
        the metadata db raises.

        Both routes evaluate the same parsed query, and every label clause
        is answered against the resource's own ``meta.labels`` rather than
        out of the value the index recorded (gain#646) -- so the two agree
        on every resource the index knows about, whatever a published index
        that has fallen behind its resources says about their labels. What
        an index too old to name a resource at all cannot do is return it,
        and that much is inherent: only the index can answer a
        ``search_term``. Rebuilding it with ``grr_manage`` is what makes a
        newly added resource findable.

        An empty ``resource_query`` is an unset one: it is what a shell
        substitutes for a variable that was never set, and the useful
        reading of ``-q "$SELECTOR"`` with no selector is the one that
        behaves like omitting the flag. A blank ``search_term`` is unset
        for the same reason (gain#633), and normalising it here -- ahead of
        the branch below that decides whether to open the index at all --
        is what keeps ``-s ""`` from demanding an index it has no filter to
        apply: ``""`` is not ``None``, so it used to reach ``MATCH`` and be
        rejected by FTS5, or, on a repository with no index, be reported as
        a search that cannot be run.

        Whitespace counts as blank: ``-s "$VAR "`` is the same accident
        as ``-s "$VAR"``. What FTS5 would otherwise make of it depends on
        which space was typed, and neither answer is worth keeping -- a
        run of ASCII spaces is the empty expression it rejects, while a
        non-breaking space is a term it accepts and nothing contains, so
        the one accident was an error or a silently empty result. A term
        that has content is passed on untouched, spaces and all --
        ``ref_genome : hg38`` is one term.

        ``resource_type`` is normalised the same way, for the same reason
        (gain#653): blank, it selected nothing where it was meant to
        select everything, and asked an index-less repository for an
        index to apply a filter nobody set.

        Raises ``ResourceQueryParseError`` for a malformed
        ``resource_query`` -- eagerly, when called, rather than on the
        first iteration, so a caller can still report it against the
        argument that caused it.
        """
        # Parsed here rather than in the generator below: this method would
        # otherwise be a generator function, whose body does not run until
        # it is first iterated.
        parsed_query = (
            ResourceQuery.parse(resource_query) if resource_query else None
        )
        # The term keeps its spaces and the type does not: a term is an
        # expression whose parts spaces separate, a type is one token.
        return self._search_resources(
            search_term if search_term and search_term.strip() else None,
            resource_type.strip() or None if resource_type else None,
            parsed_query)

    def _search_resources(
            self,
            search_term: str | None,
            resource_type: str | None,
            parsed_query: ResourceQuery | None,
    ) -> Generator[GenomicResource, None, None]:
        if search_term is None and resource_type is None:
            for res in self.get_all_resources():
                if parsed_query is None or parsed_query.match(res):
                    yield res
            return

        conn = self.open_repository_sqlite3_metadata_db()
        with conn:
            cursor = conn.cursor()
            if not cursor.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'contents'",
            ).fetchone():
                # The index has no contents table when it was built with no
                # resource in it -- an empty repository, or one whose every
                # resource the index build had to skip (gain#464).  Raised
                # rather than answered with zero rows: this repository has
                # not applied the filter, and yielding nothing would be
                # indistinguishable from having applied it and matched
                # nothing. A group needs that distinction to tell "nothing
                # matched" from "nothing was searched" (ADR 0012).
                raise SearchIndexUnavailableError(
                    self.get_id(),
                    "the search index holds no contents; no resource could "
                    "be indexed -- check the repair report for the "
                    "resources it skipped")
            query = "SELECT full_id FROM contents "
            conditions = []
            params: list[Any] = []
            if search_term is not None:
                # Ahead of the statement below, and independent of
                # whether that statement ends up asking FTS5 to read the
                # term at all.
                _reject_unparsable_search_term(conn, search_term)
                conditions.append("contents MATCH ?")
                params.append(search_term)
            if resource_type is not None:
                # Expanded, not compared: a fragment score has two accepted
                # `type:` spellings, and asking for one must find the other.
                # This has to happen HERE rather than in a caller -- the
                # predicate is applied in SQL, so no Python-side filtering
                # downstream can recover a row this query never returned.
                accepted = equivalent_resource_types(resource_type)
                placeholders = ", ".join("?" * len(accepted))
                conditions.append(f"type IN ({placeholders})")
                params.extend(accepted)
            deferred: list[LabelClause] = []
            if parsed_query is not None:
                id_condition, deferred = _resource_query_condition(
                    conn, parsed_query)
                conditions.append(id_condition)
            if conditions:
                query += " WHERE "
                query += " AND ".join(conditions)
            rows = cursor.execute(query, params)
            all_resources = self.get_all_resources_dict()
            for row in rows:
                resource = all_resources.get(row[0])
                if resource is None:
                    # The index is a separate artefact of the repository
                    # and can name a resource the ``.CONTENTS`` loader did
                    # not build -- a stale index, or one published by an
                    # untrusted GRR alongside a poisoned id that was
                    # dropped. Resolving it used to raise ``KeyError`` and
                    # take the whole search down with it (gain#467).
                    logger.warning(
                        "repo %s: index names resource <%s>, which the "
                        "repository contents do not; skipping it",
                        self.proto_id, escape_unsafe_characters(row[0]))
                    continue
                # Asked of the resource rather than of the index, whose
                # columns record what the labels were when it was built.
                if not all(
                    clause.matches_in(resource.get_labels())
                    for clause in deferred
                ):
                    continue
                yield resource

    def get_resource(
            self, resource_id: str,
            version_constraint: str | None = None) -> GenomicResource:
        """Return requested resource or raises exception if not found.

        In case resource is not found a FileNotFoundError exception
        is raised.
        """
        resource = self.find_resource(resource_id, version_constraint)
        if resource is None:
            raise FileNotFoundError(
                f"resource <{resource_id}> ({version_constraint}) not found")
        return resource

    def load_yaml(self, resource: GenomicResource, filename: str) -> Any:
        """Return parsed YAML file."""
        content = self.get_file_content(
            resource, filename, uncompress=True)
        result = yaml.safe_load(content)
        if result is None:
            return {}
        return result

    def get_file_content(
        self, resource: GenomicResource,
        filename: str,
        *,
        uncompress: bool = True,
        mode: str = "t",
    ) -> Any:
        """Return content of a file in given resource."""
        with self.open_raw_file(
                resource, filename, mode=f"r{mode}",
                uncompress=uncompress) as infile:
            return infile.read()

    def get_resource_url(self, resource: GenomicResource) -> str:
        """Return url of the specified resources.

        The resource id is the other operand of this join and is no less
        untrusted than a file name -- on the remote path it is read
        verbatim out of the repository's ``.CONTENTS.json.gz`` -- so it is
        contained here, at the join, exactly as ``get_resource_file_url``
        contains the name (gain#467).
        """
        validate_resource_id(resource.resource_id)
        return os.path.join(
            self.url,
            resource.get_genomic_resource_id_version())

    def get_resource_file_url(
            self, resource: GenomicResource, filename: str) -> str:
        """Return url of a file in the resource."""
        validate_resource_file_name(resource.resource_id, filename)
        return os.path.join(
            self.get_resource_url(resource), filename)

    @abc.abstractmethod
    def load_manifest(self, resource: GenomicResource) -> Manifest:
        """Load resource manifest."""

    @abc.abstractmethod
    def file_exists(self, resource: GenomicResource, filename: str) -> bool:
        """Check if given file exist in give resource."""

    @abc.abstractmethod
    def open_raw_file(
            self, resource: GenomicResource, filename: str,
            mode: str = "rt", **kwargs: str | bool | None) -> IO:
        """Open file in a resource and returns a file-like object."""

    @abc.abstractmethod
    def open_tabix_file(
            self, resource: GenomicResource, filename: str,
            index_filename: str | None = None) -> pysam.TabixFile:
        """Open a tabix file in a resource and return a pysam tabix file.

        Not all repositories support this method. Repositories that do
        no support this method raise and exception.
        """

    @abc.abstractmethod
    def open_vcf_file(
            self, resource: GenomicResource, filename: str,
            index_filename: str | None = None) -> pysam.VariantFile:
        """Open a vcf file in a resource and return a pysam VariantFile.

        Not all repositories support this method. Repositories that do
        no support this method raise and exception.
        """

    @abc.abstractmethod
    def open_bigwig_file(
        self, resource: GenomicResource, filename: str,
    ) -> Any:
        """Open a bigwig file in a resource and return it.

        Not all repositories support this method. Repositories that do
        no support this method raise and exception.
        """

    def open_fasta_file(
            self, resource: GenomicResource, filename: str,
            index_filename: str | None = None,
            compressed_index_filename: str | None = None) -> pysam.FastaFile:
        """Open a bgzipped fasta file in a resource and return a FastaFile.

        Not all repositories support this method. Repositories that do
        not support this method raise an exception.
        """
        raise NotImplementedError(
            f"open_fasta_file not supported by {type(self).__name__}")

    @abc.abstractmethod
    def open_repository_sqlite3_metadata_db(self) -> apsw.Connection:
        """Open the db file for repo metadata and return the connection."""

    def compute_md5_sum(self, resource: GenomicResource, filename: str) -> str:
        """Compute a md5 hash for a file in the resource."""
        logger.debug(
            "compute md5sum for %s in %s", filename, resource.resource_id)

        with self.open_raw_file(resource, filename, "rb") as infile:
            md5_hash = hashlib.md5()  # noqa S324
            while chunk := infile.read(self.CHUNK_SIZE):
                md5_hash.update(chunk)
        return md5_hash.hexdigest()

    def get_manifest(self, resource: GenomicResource) -> Manifest:
        """Load and returns a resource manifest."""
        manifest = self.load_manifest(resource)
        report_uncontained_manifest_entries(resource.resource_id, manifest)
        return manifest

    def build_genomic_resource(
            self, resource_id: str, version: tuple[int, ...],
            config: dict | None = None,
            manifest: Manifest | None = None) -> GenomicResource:
        """Build a genomic resource instance using this protocol.

        Args:
            resource_id: Resource identifier like "hg19/gene_models/refseq"
            version: Version tuple like (1, 2, 3)
            config: Optional pre-loaded configuration dict. If None, will
                   load from genomic_resource.yaml
            manifest: Optional pre-loaded manifest. If None, will load
                     when first accessed

        Returns:
            GenomicResource instance configured with this protocol
        """
        if not config:
            res = GenomicResource(resource_id, version, self)
            config = self.load_yaml(res, GR_CONF_FILE_NAME)

        if manifest is not None:
            # Both enumeration paths -- the local scan and the remote
            # ``.CONTENTS`` -- hand the manifest in already parsed, and
            # this is the first point at which it is paired with the
            # resource it belongs to (gain#467).
            report_uncontained_manifest_entries(resource_id, manifest)

        return GenomicResource(
            resource_id, version, self, config, manifest)


class ReadWriteRepositoryProtocol(ReadOnlyRepositoryProtocol):
    """Abstract base class for read-write repository storage protocols.

    Extends ReadOnlyRepositoryProtocol with write capabilities including:
        - Creating and updating resources
        - Managing manifests
        - File upload and deletion
        - Resource versioning

    This protocol type is used for local repositories and writable remote
    storage backends where resources can be modified or created.
    """

    # pylint: disable=too-many-public-methods

    def mode(self) -> Mode:
        """Return repository protocol mode.

        Returns:
            Mode.READWRITE for this protocol type
        """
        return Mode.READWRITE

    @abc.abstractmethod
    def collect_all_resources(self) -> Generator[GenomicResource, None, None]:
        """Scan repository and yield all resources.

        Returns:
            Generator yielding GenomicResource instances for each
            resource found in the repository
        """

    @abc.abstractmethod
    def scan_resource_entries(self, resource: GenomicResource) -> ResourceScan:
        """Scan resource directory for its files.

        Args:
            resource: Resource to scan

        Returns:
            A :class:`ResourceScan` holding the entries that could be
            described and the names of the files that could not.
        """

    def collect_resource_entries(self, resource: GenomicResource) -> Manifest:
        """Scan resource directory and build manifest from files found.

        The entries-only view of :meth:`scan_resource_entries`, for callers
        that just want the names and sizes on disk. A caller that WRITES a
        manifest must use the scan itself, so that a file it could not
        describe fails the resource instead of vanishing from the manifest
        (gain#503).

        Args:
            resource: Resource to scan

        Returns:
            Manifest containing entries for all files in the resource
        """
        return self.scan_resource_entries(resource).manifest

    def _warn_dvc_size_mismatch(
            self, resource: GenomicResource, name: str,
            dvc_size: int | None, disk_size: int) -> None:
        """Warn (both modes) that a sidecar's declared size is wrong (#373)."""
        logger.warning(
            "the '.dvc' sidecar of <%s> in <%s> declares a size of "
            "%s, but the file on disk is %s bytes; taking the md5 "
            "sum and the size from the sidecar. Run 'dvc add %s' "
            "(or 'dvc commit') to make DVC describe the bytes that "
            "are there, then repair the resource again",
            name, resource.resource_id, dvc_size, disk_size, name)

    def _update_manifest_entry_and_state(
            self, resource: GenomicResource, entry: ManifestEntry,
            prebuild_entries: dict[str, ManifestEntry], *,
            verify_content: bool = False,
            save_state: bool = True) -> DvcContentDrift | None:
        """Fill in md5 and size of a *materialised* file's manifest entry.

        In the DEFAULT mode a DVC-managed file is never hashed. Three
        sources answer for a file, in this order:

        1. a recorded ``ResourceFileState`` whose size and timestamp still
           match the file. It says "GAIn hashed these bytes", and it
           outranks a sidecar that contradicts it;
        2. otherwise, the file's ``.dvc`` sidecar (its ``prebuild_entries``
           entry): it supplies BOTH the md5 sum and the size, and NO state
           is written for it. ``dvc add`` computed that md5 sum from the
           very bytes it stored, and what a client downloads is that cache
           object, so the sidecar describes the file GAIn serves. Keeping
           state to mean "GAIn hashed these bytes" is what makes rule 1
           meaningful; and re-reading the sidecar costs nothing, since
           :func:`collect_dvc_entries` parses every sidecar of the resource
           on every manifest build anyway;
        3. otherwise, the file's content, and the resulting state is saved
           -- unless ``save_state`` is off (see below).

        If the sidecar's declared size differs from the size on disk, rule 2
        logs a WARNING naming the file - the scan already stat'ed it, so the
        comparison is free - and the sidecar still wins both fields. The
        default mode reports the drift it notices for free; ``-D`` hunts for
        it.

        With ``verify_content`` (``grr_manage --without-dvc``) this is the
        VERIFIER instead: the recorded state is not consulted at all, every
        materialised file is hashed, and a file whose content disagrees with
        its sidecar is returned as a :class:`DvcContentDrift` rather than
        recorded. The caller collects those and fails the resource (#373); a
        wrong sidecar-declared size still lets the sidecar win both fields.
        Verification never writes an md5 sum that contradicts a sidecar:
        ``.MANIFEST`` is a committed artefact, and the remedy for drift is
        ``dvc add`` / ``dvc commit``, after which every machine - pointer
        only or fully materialised - produces the identical manifest.

        ``save_state=False`` (``grr_manage --dry-run``) suppresses every
        write this method would make, in BOTH modes: the md5 sums it
        derives still fill in the entry and are still compared against the
        sidecars, they are just not recorded. What the run reports is
        unchanged; what it leaves behind is nothing (#257).
        """
        dvc_entry = prebuild_entries.get(entry.name)
        if dvc_entry is not None and dvc_entry.md5 is None:
            dvc_entry = None

        if verify_content:
            state = self.build_resource_file_state(resource, entry.name)
            if dvc_entry is not None:
                if dvc_entry.md5 != state.md5:
                    return DvcContentDrift(
                        entry.name, state.md5, cast(str, dvc_entry.md5))
                if state.size != dvc_entry.size:
                    # Sidecar wins both fields; no state (#373).
                    self._warn_dvc_size_mismatch(
                        resource, entry.name, dvc_entry.size, state.size)
                    entry.md5 = dvc_entry.md5
                    entry.size = dvc_entry.size
                    return None
            if save_state:
                self.save_resource_file_state(resource, state)
            entry.md5 = state.md5
            entry.size = state.size
            return None

        pre_state = self.load_resource_file_state(resource, entry.name)
        if pre_state is not None:
            timestamp = self.get_resource_file_timestamp(
                resource, entry.name)
            size = self.get_resource_file_size(resource, entry.name)
            if abs(timestamp - pre_state.timestamp) <= 1e-2 \
                    and size == pre_state.size:
                entry.md5 = pre_state.md5
                entry.size = pre_state.size
                return None
            logger.debug(
                "timestamp (%s) or size (%s) mismatch for %s in %s; "
                "recomputing md5...",
                pre_state.timestamp - timestamp, pre_state.size - size,
                entry.name, resource.resource_id)

        if dvc_entry is not None:
            if entry.size != dvc_entry.size:
                self._warn_dvc_size_mismatch(
                    resource, entry.name, dvc_entry.size, entry.size)
            entry.md5 = dvc_entry.md5
            entry.size = dvc_entry.size
            return None

        state = self.build_resource_file_state(resource, entry.name)
        if save_state:
            self.save_resource_file_state(resource, state)

        entry.md5 = state.md5
        entry.size = state.size
        return None

    def _merge_unscanned_dvc_entries(
        self,
        resource: GenomicResource,
        manifest: Manifest,
        prebuild_entries: dict[str, ManifestEntry],
    ) -> None:
        """Merge the ``.dvc`` entries the scan did not produce itself.

        A file the scan did not yield has no scanned entry to fill in, so its
        sidecar is the only source of md5 and size there is - and it is a
        good one, whether or not the bytes happen to be on disk. The typical
        case is the pointer-only clone the ``grr`` pipeline builds from,
        where nothing has been ``dvc pull``ed; the guarantee is more general
        than that, and deliberately so: a file the scan stops yielding must
        fall back to DVC rather than silently vanish from the manifest.

        The one exception is a page ``resource-info`` generates: it is a
        build artefact regardless of who ``dvc add``ed it, and manifesting it
        would put a regenerated page under checksum control (#373).
        """
        for name, entry in prebuild_entries.items():
            if name in manifest:
                continue
            if is_generated_info_page(name):
                logger.debug(
                    "not manifesting <%s> of <%s> from its '.dvc' sidecar: "
                    "it is a page GAIn generates",
                    name, resource.resource_id)
                continue
            manifest.add(entry)

    def _check_unreadable_entries(
        self,
        resource: GenomicResource,
        manifest: Manifest,
        scan: ResourceScan,
    ) -> None:
        """Fail the resource for a file nothing could describe.

        Called AFTER the sidecars have been merged in, because that merge
        is what rescues the case this exists for: a DVC-managed file
        materialised as a symlink into a shared cache is unreadable
        exactly when that cache has been garbage collected, and its
        sidecar describes it just as well as it describes a file that was
        never pulled at all. Such a resource is not broken and must
        manifest normally.

        What is left is a file the scan listed, could not read, and no
        sidecar accounts for. Dropping it silently would write a
        '.MANIFEST' that omits part of the resource -- the failure mode
        ``_merge_unscanned_dvc_entries`` exists to prevent (#373) -- so it
        fails the resource by name instead (gain#503).
        """
        if not scan.unreadable:
            return
        names = set(scan.unreadable)
        unaccounted = names - manifest.names()
        for name in sorted(names & manifest.names()):
            # Reported HERE rather than from the scan because only here is
            # it true: the scan runs more than once per command and cannot
            # know a sidecar will answer for the file. Still a WARNING and
            # not silence -- the manifest is correct, but a resource whose
            # data cannot be read is one nobody can actually use.
            logger.warning(
                "<%s> of <%s> could not be read (%s); its '.dvc' sidecar "
                "describes it, so the manifest is correct, but the file "
                "itself is unusable until 'dvc pull' (or 'dvc checkout') "
                "restores it",
                name, resource.resource_id, scan.unreadable[name])
        if unaccounted:
            raise UnreadableResourceFilesError(
                resource.resource_id, sorted(unaccounted))

    def build_manifest(
        self, resource: GenomicResource,
        prebuild_entries: dict[str, ManifestEntry] | None = None,
        *,
        verify_content: bool = False,
    ) -> Manifest:
        """Build full manifest for the resource."""
        if prebuild_entries is None:
            prebuild_entries = {}
        scan = self.scan_resource_entries(resource)
        manifest = Manifest()
        drifts: list[DvcContentDrift] = []
        for entry in scan.manifest:
            drift = self._update_manifest_entry_and_state(
                resource, entry, prebuild_entries,
                verify_content=verify_content)
            if drift is not None:
                drifts.append(drift)
                continue
            manifest.add(entry)
        if drifts:
            raise DvcContentDriftError(resource.resource_id, drifts)
        self._merge_unscanned_dvc_entries(
            resource, manifest, prebuild_entries)
        self._check_unreadable_entries(resource, manifest, scan)
        return manifest

    def check_update_manifest(
        self, resource: GenomicResource,
        prebuild_entries: dict[str, ManifestEntry] | None = None,
        *,
        verify_content: bool = False,
        save_state: bool = True,
    ) -> ManifestUpdate:
        """Check if the resource manifest needs update.

        With ``save_state=False`` nothing it derives is recorded (#257).
        """
        if prebuild_entries is None:
            prebuild_entries = {}
        try:
            current_manifest = self.load_manifest(resource)
        except FileNotFoundError:
            current_manifest = Manifest()

        scan = self.scan_resource_entries(resource)
        manifest = Manifest()
        entries_to_update = set()
        drifts: list[DvcContentDrift] = []
        for entry in scan.manifest:
            drift = self._update_manifest_entry_and_state(
                resource, entry, prebuild_entries,
                verify_content=verify_content, save_state=save_state)
            if drift is not None:
                # Collected, not raised: one command reports every drifted
                # file of the resource, not just the first (#373).
                drifts.append(drift)
                continue
            manifest.add(entry)

            if entry.name not in current_manifest \
                    or entry.md5 != current_manifest[entry.name].md5:
                entries_to_update.add(entry.name)

        if drifts:
            raise DvcContentDriftError(resource.resource_id, drifts)

        self._merge_unscanned_dvc_entries(
            resource, manifest, prebuild_entries)
        self._check_unreadable_entries(resource, manifest, scan)

        entries_to_delete = current_manifest.names() - manifest.names()
        return ManifestUpdate(manifest, entries_to_delete, entries_to_update)

    def update_manifest(
        self, resource: GenomicResource,
        prebuild_entries: dict[str, ManifestEntry] | None = None,
        *,
        verify_content: bool = False,
    ) -> Manifest:
        """Update or create full manifest for the resource."""
        # `check_update_manifest` already fills in md5 and size of every
        # scanned entry of the manifest it returns, and merges the entries
        # the scan did not yield; what it hands back IS the updated manifest.
        manifest_update = self.check_update_manifest(
            resource, prebuild_entries, verify_content=verify_content)

        return manifest_update.manifest

    def save_manifest(
            self, resource: GenomicResource, manifest: Manifest) -> None:
        """Save manifest into genomic resource's directory."""
        logger.debug(
            "save manifest of resource %s from %s", resource.resource_id,
            self.proto_id)

        with self.open_raw_file(
                resource, GR_MANIFEST_FILE_NAME, "wt") as outfile:
            yaml.dump(manifest.to_manifest_entries(), outfile)
        resource.invalidate()

    def save_index(self, resource: GenomicResource, contents: str) -> None:
        """Save an index HTML file into the genomic resource's directory."""
        with self.open_raw_file(resource, GR_INDEX_FILE_NAME, "wt") as outfile:
            outfile.write(contents)

    def get_manifest(self, resource: GenomicResource) -> Manifest:
        """Load or build a resource manifest."""
        try:
            manifest = self.load_manifest(resource)
        except FileNotFoundError:
            # The fallback build has no CLI frame above it to collect the
            # sidecars, and building without them hashes every DVC-managed
            # byte a sidecar already describes (#721).
            manifest = self.build_manifest(
                resource, collect_dvc_entries(self, resource))
        else:
            # A BUILT manifest is a scan of the resource directory
            # and is contained by construction; only a LOADED one
            # can carry a poisoned name (gain#467).
            report_uncontained_manifest_entries(
                resource.resource_id, manifest)
        return manifest

    @abc.abstractmethod
    def get_resource_file_timestamp(
            self, resource: GenomicResource, filename: str) -> float:
        """Return the timestamp (ISO formatted) of a resource file."""

    @abc.abstractmethod
    def get_resource_file_size(
            self, resource: GenomicResource, filename: str) -> int:
        """Return the size of a resource file."""

    def build_resource_file_state(
            self, resource: GenomicResource,
            filename: str,
            **kwargs: str | float | int | None) -> ResourceFileState:
        """Build resource file state."""
        if not self.file_exists(resource, filename):
            raise ValueError(
                f"can't build resource state for not existing resource file "
                f"{resource.resource_id} > {filename}")

        md5 = kwargs.get("md5")
        timestamp = kwargs.get("timestamp")
        size = kwargs.get("size")

        if md5 is None:
            md5 = self.compute_md5_sum(resource, filename)

        if timestamp is None:
            timestamp = self.get_resource_file_timestamp(resource, filename)

        if size is None:
            size = self.get_resource_file_size(resource, filename)

        return ResourceFileState(
            filename,
            cast(int, size),
            cast(float, timestamp),
            cast(str, md5))

    @abc.abstractmethod
    def save_resource_file_state(
            self, resource: GenomicResource, state: ResourceFileState) -> None:
        """Save resource file state into internal GRR state."""

    @abc.abstractmethod
    def load_resource_file_state(
            self, resource: GenomicResource,
            filename: str) -> ResourceFileState | None:
        """Load resource file state from internal GRR state.

        If the specified resource file has no internal state returns None.
        """

    @abc.abstractmethod
    def delete_resource_file(
            self, resource: GenomicResource, filename: str) -> None:
        """Delete a resource file and it's internal state."""

    @abc.abstractmethod
    def copy_resource_file(
            self,
            remote_resource: GenomicResource,
            dest_resource: GenomicResource,
            filename: str) -> ResourceFileState | None:
        """Copy a remote resource file into local repository."""

    @abc.abstractmethod
    def update_resource_file(
            self, remote_resource: GenomicResource,
            dest_resource: GenomicResource,
            filename: str) -> ResourceFileState | None:
        """Update a resource file into repository if needed."""

    def get_or_create_resource(
            self, resource_id: str,
            version: tuple[int, ...]) -> GenomicResource:
        """Return a resource with specified ID and version.

        If the resource is not found create an empty resource.
        """
        resource = self.find_resource(
            resource_id=resource_id,
            version_constraint=f"={version_tuple_to_string(version)}")

        if resource is None:
            logger.info(
                "resource %s (%s) not found in %s; creating...",
                resource_id,
                version,
                self.get_id())
            resource = GenomicResource(
                resource_id,
                version,
                self)

        return resource

    def copy_resource(
            self,
            remote_resource: GenomicResource) -> GenomicResource:
        """Copy a remote resource into repository."""
        local_resource = self.get_or_create_resource(
            remote_resource.resource_id, remote_resource.version)

        remote_manifest = remote_resource.get_manifest()
        local_manifest = self.get_manifest(local_resource)
        filenames_to_delete = local_manifest.names() - remote_manifest.names()

        for filename in filenames_to_delete:
            self.delete_resource_file(local_resource, filename)

        for manifest_entry in remote_manifest:
            self.copy_resource_file(
                remote_resource, local_resource, manifest_entry.name)

        self.save_manifest(local_resource, remote_resource.get_manifest())
        self.invalidate()

        return self.get_resource(
            resource_id=remote_resource.resource_id,
            version_constraint=f"={remote_resource.get_version_str()}")

    def update_resource(
        self,
        remote_resource: GenomicResource,
        files_to_copy: set[str] | None = None,
    ) -> GenomicResource:
        """Copy a remote resource into repository.

        Allows copying of a subset of files from the resource via
        files_to_copy. If files_to_copy is None, copies all files.
        """
        local_resource = self.get_or_create_resource(
            remote_resource.resource_id, remote_resource.version)
        remote_manifest = remote_resource.get_manifest()
        local_manifest = self.get_manifest(local_resource)
        filenames_to_delete = local_manifest.names() - remote_manifest.names()

        if files_to_copy is None:
            files_to_copy = {entry.name for entry in remote_manifest}
        else:
            files_to_copy.add(GR_CONF_FILE_NAME)  # config is always required

        for filename in filenames_to_delete:
            self.delete_resource_file(local_resource, filename)
        for file in files_to_copy:
            self.update_resource_file(remote_resource, local_resource, file)

        if local_manifest != remote_manifest:
            self.save_manifest(local_resource, remote_resource.get_manifest())
            self.invalidate()

        return self.get_resource(
            resource_id=remote_resource.resource_id,
            version_constraint=f"={remote_resource.get_version_str()}")

    @abc.abstractmethod
    def build_content_file(self) -> list[dict[str, Any]]:
        """Build the content of the repository (i.e '.CONTENTS.json.gz')."""


def dvc_directory_output_message(
    resource_id: str, entry_name: str, filename: str,
) -> str:
    """Say why a ``dvc add <dir>`` output is refused, and what to do.

    One text for both gates -- ``cli_dvc``'s pre-flight and the manifest
    builder -- so what the user is told cannot depend on which of them saw
    the sidecar first (#284).

    Every name here is untrusted GRR content and this message is a refusal
    report, so the names are escaped for the same reason the sibling
    warnings in :func:`collect_dvc_entries` are (gain#642).
    """
    resource_id = escape_unsafe_characters(resource_id)
    entry_name = escape_unsafe_characters(entry_name)
    filename = escape_unsafe_characters(filename)
    return (
        f"resource <{resource_id}> has a 'dvc add <dir>' output: "
        f"the '.dvc' file <{entry_name}> describes the directory "
        f"<{filename}>. 'dvc add <dir>' outputs are not supported by "
        f"GAIn: the '.dir' md5 sum such a sidecar declares is the "
        f"hash of a DVC cache object, not of any file in the "
        f"resource, so GAIn can never verify it against the bytes it "
        f"serves. DVC-manage the individual files instead: run 'dvc "
        f"add <file>' on each file of <{filename}> (and remove "
        f"<{entry_name}>)."
    )


def collect_dvc_entries(
        proto: ReadWriteRepositoryProtocol,
        res: GenomicResource) -> dict[str, ManifestEntry]:
    """Collect manifest entries defined by .dvc files.

    A ``.dvc`` file that cannot be read, does not parse as a pointer for the
    data file it sits next to, or declares no usable md5 sum and size is
    skipped with a warning - never propagated into the manifest, and never
    allowed to abort the command. `.dvc` sidecars are read on every
    ``grr_manage`` run, and the repository scan that produced this entry has
    already tolerated the very same content (see
    ``FsspecReadWriteProtocol._is_dvc_managed_leaf``); the two classify
    identically because both delegate to
    :func:`dvc.parse_dvc_pointer_out`.

    A *well-formed* sidecar for a ``dvc add <dir>`` output is a different
    matter: it is not ignored, it is REFUSED. GAIn cannot verify a ``.dir``
    md5 sum - it hashes a DVC cache object, not any file GAIn can read - so
    writing it into the manifest would be a false clean bill of health, and
    quietly skipping the directory would leave its data unmanifested and
    unverified. Either way the resource would be certified without its
    content ever being checked, so the command fails instead (#255). This is
    the gate every ``grr_manage`` subcommand that builds or checks a
    manifest passes through -- and, since #721, the fallback build a
    repository walk triggers -- and it applies whether or not the
    directory is materialised. It is kept even though
    ``cli_dvc.refuse_dvc_directory_outputs`` refuses such a resource
    before any command reaches this function: a manifest must never be
    built from a sidecar GAIn cannot verify, whoever asks for it (#284).

    An entry is produced for every readable sidecar. Every materialised
    file's entry is consulted by
    :meth:`ReadWriteRepositoryProtocol._update_manifest_entry_and_state` -
    the sidecar IS the md5 sum of the file it describes - and the entries
    for files the scan did not yield are merged by
    :meth:`ReadWriteRepositoryProtocol._merge_unscanned_dvc_entries` (#373).

    Lives beside the manifest builder, not in the CLI, because the builder
    itself must reach it: a manifest built as a FALLBACK - a repository
    walk meeting a resource that never had a ``.MANIFEST`` - has no CLI
    frame above it to collect the sidecars, and building without them
    hashes every DVC-managed byte the sidecar already describes (#721).

    Raises:
        UnsupportedDvcDirectoryOutputError: the resource has a ``dvc add
            <dir>`` output.
    """
    result = {}
    manifest = proto.collect_resource_entries(res)
    for entry in manifest:
        if not is_dvc_sidecar(entry.name):
            continue
        filename = dvc_sidecar_target(entry.name)
        basename = os.path.basename(filename)

        try:
            with proto.open_raw_file(res, entry.name, "rb") as infile:
                content = cast(bytes, infile.read())
        except (OSError, ValueError):
            logger.warning(
                "unable to read the '.dvc' file <%s> of <%s>; ignoring it",
                escape_unsafe_characters(entry.name),
                escape_unsafe_characters(res.resource_id))
            continue

        out = parse_dvc_pointer_out(content, basename)
        if out is None:
            logger.warning(
                "the '.dvc' file <%s> of <%s> is not a dvc pointer for <%s>; "
                "ignoring it",
                escape_unsafe_characters(entry.name),
                escape_unsafe_characters(res.resource_id),
                escape_unsafe_characters(filename))
            continue

        if is_dvc_directory_out(out):
            raise UnsupportedDvcDirectoryOutputError(
                dvc_directory_output_message(
                    res.resource_id, entry.name, filename))

        md5 = out.get("md5")
        size = out.get("size")
        if not isinstance(md5, str) or not isinstance(size, int):
            logger.warning(
                "the '.dvc' file <%s> of <%s> declares no usable md5 sum and "
                "size for <%s>; ignoring it",
                escape_unsafe_characters(entry.name),
                escape_unsafe_characters(res.resource_id),
                escape_unsafe_characters(filename))
            continue

        if filename not in manifest:
            logger.info(
                "filling manifest of <%s> with entry for <%s> based on "
                "dvc data only",
                res.resource_id, filename)

        result[filename] = ManifestEntry(filename, size, md5)

    return result


def _map_relaying_skips[
    HitT, MappedT, SkipsT: list[tuple[str, str]] | None,
](
    hits: Generator[HitT, None, SkipsT],
    transform: Callable[[HitT], MappedT],
) -> Generator[MappedT, None, SkipsT]:
    """Yield ``transform(hit)`` per hit, relaying the skips return value.

    ``yield from`` relays a generator's return value natively but cannot
    transform the hits on the way; a ``for`` loop transforms and relays
    nothing. Every search wrapper that maps its hits threads through
    here, so the skips promise (gain#686) is kept in one place.
    """
    while True:
        try:
            hit = next(hits)
        except StopIteration as stop:
            # Deliberately PEP 380: the return value is how a search
            # reports the children a group skipped while answering
            # (gain#686), so B901's accidental shape this is not.
            skips: SkipsT = stop.value
            return skips  # noqa: B901
        yield transform(hit)


def drain_search[HitT](
    hits: Generator[HitT, None, list[tuple[str, str]] | None],
) -> tuple[list[HitT], list[tuple[str, str]]]:
    """Exhaust a search, answering its rows and its skips.

    The consumption idiom for a caller that presents totals: a ``for``
    loop silently discards the skips a group reports on its generator's
    return value (gain#686). ``None`` and ``[]`` both arrive as ``[]``,
    so the dual spelling ends here.
    """
    rows: list[HitT] = []
    while True:
        try:
            rows.append(next(hits))
        except StopIteration as stop:
            # `list(...)` rather than an Optional-annotated local:
            # astroid does not narrow `x or []` past the annotation and
            # would call every consumer's unpacking non-iterable (E1133).
            return rows, list(stop.value or [])


class GenomicResourceRepo(abc.ABC):
    """Abstract base class for genomic resource repositories.

    A repository manages a collection of genomic resources, providing
    methods to discover, retrieve, and (for writable repos) create resources.

    Repositories can be:
        - Protocol-based: Direct access to a single storage backend
        - Group: Aggregates multiple child repositories
        - Cached: Wraps another repository with local caching

    All repositories support resource lookup with optional version constraints:
        repo.get_resource("hg19/genome")           # Latest version
        repo.get_resource("hg19/genome", ">=2.0")  # Version 2.0 or higher
        repo.get_resource("hg19/genome", "=2.1")   # Exact version 2.1

    Attributes:
        repo_id: Unique identifier for this repository
        definition: Configuration dict used to create this repository
    """

    def __init__(self, repo_id: str):
        self._repo_id: str = repo_id
        self._definition: dict[str, Any] | None = None

    def close(self) -> None:
        """Release any resources held by this repository."""
        self._definition = None

    @property
    def definition(self) -> dict[str, Any] | None:
        """Get a copy of the repository configuration definition.

        Returns:
            Deep copy of definition dict, or None if not set
        """
        if self._definition:
            return copy.deepcopy(self._definition)
        return self._definition

    @definition.setter
    def definition(self, value: dict[str, Any]) -> None:
        """Set the repository configuration definition.

        Args:
            value: Configuration dict to store (will be deep copied)
        """
        self._definition = copy.deepcopy(value)

    @abc.abstractmethod
    def invalidate(self) -> None:
        """Clear cached state and force reload on next access.

        Implementations should clear any cached resource lists, metadata,
        or file contents to ensure fresh data is loaded.
        """

    @property
    def repo_id(self) -> str:
        """Get the repository identifier.

        Returns:
            Repository ID string
        """
        return self._repo_id

    @abc.abstractmethod
    def get_resource(
            self, resource_id: str, version_constraint: str | None = None,
            repository_id: str | None = None) -> GenomicResource:
        """Return one resource with id qual to resource_id.

        If resource is not found, exception is raised.

        ``repository_id`` restricts the lookup to the repository carrying
        that id, anywhere in this repository's tree -- including this
        repository itself: every repository answers to its own id, so
        passing ``repo.repo_id`` is equivalent to passing nothing (#447). A
        falsy ``repository_id`` is no filter at all.
        """

    @abc.abstractmethod
    def find_resource(
            self, resource_id: str, version_constraint: str | None = None,
            repository_id: str | None = None) -> GenomicResource | None:
        """Return one resource with id qual to resource_id.

        If resource is not found, None is returned.

        ``repository_id`` selects a repository by id under the same rule as
        :meth:`get_resource` -- a repository answers to its own id, and a
        falsy id is no filter.
        """

    @abc.abstractmethod
    def search_resources(
        self,
        search_term: str | None = None,
        resource_type: str | None = None,
        resource_query: str | None = None,
    ) -> Generator[GenomicResource, None, list[tuple[str, str]] | None]:
        """Search resources by FTS term, type and/or wildcard query.

        All supplied filters conjoin.

        The generator's return value carries the ``(repository id, reason)``
        pairs of the children a group skipped while still answering (ADR
        0012, gain#686); ``None`` and ``[]`` both mean nothing was skipped.
        A ``for`` loop discards it, which is exactly right for a caller
        that does not present totals.
        """

    def search_resources_by_child(
        self,
        search_term: str | None = None,
        resource_type: str | None = None,
        resource_query: str | None = None,
    ) -> Generator[
        tuple[GenomicResourceRepo, GenomicResource], None,
        list[tuple[str, str]] | None,
    ]:
        """Search, pairing each hit with the repository that serves it.

        For a repository that serves resources itself the answer is always
        this one, which is what this implementation says. A group overrides
        it to name the child the resource actually came from, so a caller
        that has to label a hit -- ``grr_manage list`` prints the id beside
        every row -- does not have to take a group apart to find out.

        The filters mean exactly what they mean for
        :meth:`search_resources`, which is the projection of this -- and
        the return value carries the same skips.
        """
        return _map_relaying_skips(
            self.search_resources(search_term, resource_type, resource_query),
            lambda res: (self, res))

    @abc.abstractmethod
    def get_all_resources(self) -> Generator[GenomicResource, None, None]:
        """Return a generator over all resource in the repository."""


class GenomicResourceProtocolRepo(GenomicResourceRepo):
    """Base class for real genomic resources repositories."""

    def __init__(
            self,
            proto: ReadOnlyRepositoryProtocol | ReadWriteRepositoryProtocol):
        super().__init__(proto.get_id())
        self.proto = proto

    def close(self) -> None:
        self.invalidate()
        super().close()

    def invalidate(self) -> None:
        self.proto.invalidate()

    def get_resource(
            self, resource_id: str, version_constraint: str | None = None,
            repository_id: str | None = None) -> GenomicResource:

        if repository_id and self.repo_id != repository_id:
            raise ValueError(
                f"can't find resource ({resource_id}, {version_constraint}: "
                f"repository {repository_id} in repository {self.repo_id}")

        return self.proto.get_resource(resource_id, version_constraint)

    def find_resource(
            self, resource_id: str, version_constraint: str | None = None,
            repository_id: str | None = None) -> GenomicResource | None:

        if repository_id and self.repo_id != repository_id:
            return None

        return self.proto.find_resource(resource_id, version_constraint)

    def search_resources(
        self,
        search_term: str | None = None,
        resource_type: str | None = None,
        resource_query: str | None = None,
    ) -> Generator[GenomicResource, None, None]:
        # `return`, not `yield from`: the protocol validates the query when
        # the call is made, and a generator function here would defer that
        # to the first iteration.
        return self.proto.search_resources(
            search_term, resource_type, resource_query)

    def get_all_resources(self) -> Generator[GenomicResource, None, None]:
        return self.proto.get_all_resources()


RepositoryProtocol = ReadOnlyRepositoryProtocol | ReadWriteRepositoryProtocol
