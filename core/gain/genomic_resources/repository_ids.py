"""Resource ids, resource file names and versions.

The file names and index columns that a repository reserves, the grammar of
a resource id and its version suffix, and the checks that keep a resource
id and a resource file name inside the repository.
"""
from __future__ import annotations

import ntpath
import re
from urllib.parse import unquote

from gain.utils.log_safety import (
    UNSAFE_CHARACTER_RE,
    escape_unsafe_characters,
)

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

#: Every character a resource id may be spelled with, as the body of a
#: regex character class.  One definition, composed into the pattern
#: that accepts an id, the one that names what a malformed one carries
#: and the one that accepts a single segment, so the three cannot drift
#: apart -- they used to be written out separately, and a rule whose
#: single source of truth is a test is a rule waiting to disagree with
#: itself (gain#1352).  ``-`` stays last: anywhere else it would read as
#: a range.
RESOURCE_ID_CHARACTER_CLASS = "a-zA-Z0-9/._-"

#: One segment of a resource id: the class without its separator.
_GR_ID_TOKEN_RE = re.compile(
    f"[{RESOURCE_ID_CHARACTER_CLASS.replace('/', '')}]+")

#: Separators a resource path is split on before its segments are
#: scanned. A backslash is a path separator on Windows and in several
#: fsspec backends, so it counts as one here.
_RESOURCE_NAME_SEPARATOR = re.compile(r"[/\\]")

#: A drive-letter prefix (``C:``). ``ntpath.isabs`` calls ``x:y``
#: *relative*, but ``ntpath.join`` still discards the base for it, so the
#: prefix is what has to be rejected -- not absoluteness as ntpath
#: defines it.
_WINDOWS_DRIVE = re.compile(r"[a-zA-Z]:")


def _join_resource_path(base: str, path: str) -> str:
    """Join ``path`` under ``base``, tolerating a trailing separator.

    Both of a resource's addresses are a repository url with the resource
    id hung off it, and both bases are written by hand in a deployment's
    GRR definition -- ``public_url`` and ``directory`` alike -- so a
    trailing "/" is a spelling that turns up and must not reach the
    address as "//" (#841, and #838 where the same correction was made at
    the single-allele call site).

    The two addresses share this rather than each spelling its own join:
    when ``public_url`` is not declared the protocol reports the
    repository's own url for both, and that fallback is only worth
    anything if the two agree for EVERY spelling of the root.

    The separators a scheme owns are not stray ones, so a root that is
    nothing but a scheme keeps them: "https://" takes the id directly.

    The normalisation lives here, at the join, and not in the protocol.
    A protocol reports ``public_url`` exactly as its caller wrote it --
    that is its display identity, echoed back in the rebuild-divergence
    error. Nor could a protocol cover this: ``build_fsspec_protocol``
    takes a ``public_url`` directly, so construction does not always go
    through a GRR definition, while every address goes through this join.
    """
    trimmed = base.rstrip("/")
    if trimmed.endswith(":"):
        return f"{base}{path}"
    return f"{trimmed}/{path}"


def is_generated_info_page(name: str) -> bool:
    """Return True if ``name`` is a page ``resource-info`` generates.

    Membership in a resource's manifest is decided by the file's PATH: the
    two pages GAIn writes itself are excluded, everything else is resource
    data. The rule it replaced -- "drop every name ending in ``html``" -- was
    a proxy for the same question and a bad one, since it silently dropped
    any html file a resource legitimately carries as data (#373).
    """
    return name in GR_GENERATED_INFO_PAGES


# A resource NAME is refused if it carries any log-unsafe character (the
# range and its log-line rationale live in ``gain.utils.log_safety``). One
# consumer here is sharper than a log line and is why refusal, not just
# escaping, is needed: a cache path. ``urllib.parse.urlsplit`` DELETES
# ASCII tab, CR and LF from anywhere in a url, and a repository id is
# joined onto a url that is then re-parsed to derive the cache path. An id
# carrying one therefore reads as a single segment while resolving to a
# DIFFERENT one: ``"..\\n"`` becomes ``..`` (one level above the cache
# directory) and ``"a\\nb"`` becomes ``ab``, silently sharing the cache
# directory of a genuinely different id. A NUL is a filesystem problem
# instead -- it reaches the ``mkdir`` call and dies there with a message
# about nothing the operator wrote.
#
# Refusal and ``escape_unsafe_characters`` are complementary: the refusal
# *names* the name it rejects -- the drop warning, the two ``validate_*``
# messages and the manifest-entry warning all interpolate it -- so without
# escaping, the refusal path would emit the very record it exists to
# prevent. Refusal keeps the name away from the many sites that log an id
# in passing; escaping keeps the few that report the refusal on one line.
_UNSAFE_NAME_CHARACTER_RE = UNSAFE_CHARACTER_RE


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
    before the request is even sent, an s3 server rejects the key outright
    (RustFS answers ``InvalidArgument``, MinIO
    ``XMinioInvalidResourceName``), and a local filesystem resolves it.
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
        reason = (
            _escaping_path_reason(candidate, "resource directory")
            or _degenerate_segment_reason(candidate))
        if reason is not None:
            return reason
    return None


def _degenerate_segment_reason(name: str) -> str | None:
    """Return why a segment of ``name`` is degenerate, or ``None``.

    A ``.`` segment and an empty segment are refused by the file-name
    rule and the id rule alike, in the same words and in the same order
    -- the first offending segment, left to right -- so the rule is
    stated once and the two cannot drift.  A whole-name spelling a
    caller keeps, ``.`` as the repository root, is that caller's
    exemption to make before asking.
    """
    for segment in _RESOURCE_NAME_SEPARATOR.split(name):
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
    it is a no-op rather than a way to address something else.  (It is
    still refused -- by :func:`malformed_resource_id_reason`, on the
    different ground that no scan can ever produce it.)

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


#: The complement of :data:`RESOURCE_ID_CHARACTER_CLASS`: what a
#: well-formed id may *not* carry.  Spelled as the complement, rather
#: than reusing the pattern that accepts, so that a refusal can name the
#: single character it tripped on -- a warning that says which character
#: is worth the second regex.
_MALFORMED_ID_CHARACTER_RE = re.compile(f"[^{RESOURCE_ID_CHARACTER_CLASS}]")


def malformed_resource_id_reason(resource_id: str) -> str | None:
    """Return why ``resource_id`` is not a well-formed id, or ``None``.

    Containment is the *other* rule an id is held to, and the two are
    separate: :func:`uncontained_resource_id_reason` asks whether the id
    escapes the repository, this one whether GAIn can process it at all.
    Both run where a ``.CONTENTS`` is read, and an id refused by either
    is dropped with a warning, the rest of the ``.CONTENTS`` still
    served.  A scan runs neither: it parses each candidate path with
    :func:`parse_gr_id_version_token`, which enforces the same character
    class by construction, and a directory the parse refuses is skipped
    with a warning the same way.  The two enumeration paths agree on
    both the grammar and its consequence.

    Two segments are refused here that the scan grammar does *not*
    refuse -- both ``/`` and ``.`` are inside its character class, so
    ``a//b`` and ``a/./b`` match it -- because a scan never meets them: a
    filesystem has no empty name to offer, and every dot-named directory
    is skipped on the way down.  Only a hand-written ``.CONTENTS`` can
    carry either.  The ``.`` segment is the quieter of the two: it
    survives the join, the filesystem resolves it away, and the cached
    directory is then enumerated under a *different* id than the one it
    was served as (gain#1385).

    ``""`` and ``"."`` are exempt because both name the repository root,
    a supported resource in its own right that is published under the
    empty id and addressed as ``"."`` by ``build_local_resource``.  The
    exemption is by whole id: ``"."`` as a segment of a longer id is the
    refusal above.
    """
    if resource_id in {"", "."}:
        return None
    match = _MALFORMED_ID_CHARACTER_RE.search(resource_id)
    if match is not None:
        return (
            f"carries <{escape_unsafe_characters(match.group())}>, "
            f"which a resource id may not contain")
    return _degenerate_segment_reason(resource_id)


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


def parse_gr_id_version_token(token: str) -> tuple[str, tuple[int, ...]]:
    """Parse a genomic resource id with an optional version suffix.

    The suffix has the form ``(3.3.2)``; without one the version is
    ``(0,)``.  The empty token names the repository root.  Returns the
    ``(resource_id, version)`` tuple, and raises :class:`ValueError` on
    a token outside the resource id grammar.
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
    rf"([{RESOURCE_ID_CHARACTER_CLASS}]+)(?:\(([0-9]\d*(?:\.\d+)*)\))?")


def parse_resource_id_version(
    resource_path: str,
) -> tuple[str, tuple[int, ...] | None]:
    """Parse a resource path into an ``(resource_id, version)`` tuple.

    Like :func:`parse_gr_id_version_token`, but a path without a
    version suffix yields ``None`` for the version rather than ``(0,)``,
    so a caller can tell "unversioned" from "version 0".  Raises
    :class:`ValueError` on a path outside the resource id grammar.
    """
    if resource_path == "":
        return "", None

    match = _RESOURCE_ID_WITH_VERSION_PATH_RE.fullmatch(resource_path)
    if not match:
        raise ValueError(f"unexpected resource path: {resource_path}")
    token = match[1]
    version_string = match[2]
    if version_string:
        version = tuple(map(int, version_string.split(".")))
    else:
        version = None
    return token, version


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
