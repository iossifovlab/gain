"""The manifest model of a genomic resource.

A manifest lists the files of a resource with their sizes and MD5
checksums. The other classes describe the state of a stored file, a
change to a manifest, one scan of a resource directory and the error
for resource files that cannot be read.
"""
from __future__ import annotations

import enum
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

import yaml


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


class Unread(enum.Enum):
    """A keyword that was not supplied, where ``None`` is a real value.

    An enum rather than a bare ``object()`` so that the sentinel has a
    type a signature can name and a type checker can narrow: comparing
    a parameter against the member leaves ``str | None`` in the other
    branch, which is what the field actually is. Public because it
    appears in the signature of a public method, and a subclass that
    overrides it has to be able to name it.
    """

    UNREAD = enum.auto()
    """Not supplied.

    :meth:`ReadWriteRepositoryProtocol.build_resource_file_state` and
    ``_state_describes_stored_file`` read a field off the stored file when
    the caller does not supply it, and for every other field ``None``
    says so unambiguously. A change
    token is the exception: ``None`` is the answer a store offering no
    tokens gives, so a caller that has *asked* and been told "no token"
    must be able to say that, and it must not read as "go and ask".
    """


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

        change_token: Opaque token the store supplies for the stored
            object, or None where the store has none. It changes whenever
            the object changes, and nothing else about it is defined --
            it is never parsed, and never treated as a checksum, even
            when a particular store happens to derive it from one.
    """

    filename: str
    size: int
    timestamp: float
    md5: str
    change_token: str | None = None


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
        """Create a manifest from the YAML content of a manifest file.

        Parameters
        ----------
        file_content : str
            YAML text that holds a list of entries with the keys ``name``,
            ``size`` and ``md5``. Empty YAML text gives an empty manifest.

        Returns
        -------
        Manifest
            The manifest with one entry for each item of the list.

        Raises
        ------
        KeyError
            If an item of the list has no ``name``, ``size`` or ``md5`` key.
        yaml.YAMLError
            If the text is not valid YAML.
        """
        manifest_entries = yaml.safe_load(file_content)
        if manifest_entries is None:
            manifest_entries = []
        return Manifest.from_manifest_entries(manifest_entries)

    @staticmethod
    def from_manifest_entries(
            manifest_entries: list[dict[str, Any]]) -> Manifest:
        """Create a manifest from a list of entry dictionaries.

        Parameters
        ----------
        manifest_entries : list of dict
            Dictionaries with the keys ``name``, ``size`` and ``md5``.
            An empty list gives an empty manifest.

        Returns
        -------
        Manifest
            The manifest with one entry for each dictionary.

        Raises
        ------
        KeyError
            If a dictionary has no ``name``, ``size`` or ``md5`` key.

        :meta private:
        """
        result = Manifest()
        for data in manifest_entries:
            entry = ManifestEntry(
                data["name"], data["size"], data["md5"])
            result.entries[entry.name] = entry
        return result

    def get_files(self) -> list[tuple[str, int]]:
        """Return the name and size of each file in the manifest.

        Returns
        -------
        list of tuple of (str, int)
            A ``(name, size)`` pair for each file. The list is empty when
            the manifest has no entries.
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
        """Return the entries as dictionaries, ready to serialise.

        Returns
        -------
        list of dict
            A dictionary with the keys ``name``, ``size`` and ``md5`` for
            each entry, sorted by file name. The list is empty when the
            manifest has no entries.

        :meta private:
        """
        return [
            asdict(entry) for entry in sorted(self.entries.values())]

    def add(self, entry: ManifestEntry) -> None:
        """Add an entry, or replace the entry with the same name.

        Parameters
        ----------
        entry : ManifestEntry
            The entry to store under ``entry.name``.

        :meta private:
        """
        self.entries[entry.name] = entry

    def update(self, entries: dict[str, ManifestEntry]) -> None:
        """Add or replace several entries.

        Parameters
        ----------
        entries : dict of str to ManifestEntry
            The entries to store. The method uses ``entry.name`` as the key,
            not the key of the dictionary.

        :meta private:
        """
        for entry in entries.values():
            self.add(entry)

    def names(self) -> set[str]:
        """Return the names of the files in the manifest.

        Returns
        -------
        set of str
            The file names. The set is empty when the manifest has no
            entries.
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

    ``change_tokens`` is the store's change token for each name the scan
    stat'ed, read by the same stat as its size, so a state built for that
    file needs neither read again. It is carried here rather than in the
    manifest because ``ManifestEntry`` is the serialized ``.MANIFEST`` row.
    """

    manifest: Manifest
    unreadable: Mapping[str, str]
    change_tokens: Mapping[str, str | None] = field(default_factory=dict)

    def change_token(self, name: str) -> str | Unread | None:
        """The token the scan read for ``name``, or :attr:`Unread.UNREAD`.

        UNREAD for a name the scan recorded no token for, never ``None``:
        ``None`` is what a store without tokens reports, and passing it on
        would record "this store has none" for a file nobody asked about.
        """
        return self.change_tokens.get(name, Unread.UNREAD)


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
