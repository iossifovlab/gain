from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, cast

import apsw
from cerberus import Validator
from markdown2 import markdown

from gain import logging
from gain.task_graph.graph import TaskDesc
from gain.templates import get_template
from gain.utils.helpers import convert_size

from .repository import GenomicResource

logger = logging.getLogger(__name__)


INDEX_COLUMN_PATTERN = "[A-Za-z_][A-Za-z0-9_]*"
_INDEX_COLUMN_RE = re.compile(f"{INDEX_COLUMN_PATTERN}\\Z")

# Names FTS5 will not accept as a column of the index table: it reserves
# `rank` and `rowid`, and every FTS5 table has a hidden column named after
# the table itself -- which for the repository index is `contents`, the
# table _create_contents_db builds.  Compared case-insensitively, as SQLite
# compares identifiers.
_INDEX_RESERVED_COLUMNS = frozenset({"rank", "rowid", "contents"})


def get_base_resource_schema() -> dict[str, Any]:
    return {
        "type": {"type": "string"},
        "meta": {
            "type": "dict",
            "allow_unknown": True,
            "schema": {
                "description": {"type": "string"},
                # The keys of `labels` are constrained -- each one becomes a
                # column of the repository's search index -- but deliberately
                # NOT here: this schema is run by the three implementations
                # that validate at all, and for scores it runs inside
                # GenomicScore.__init__, on the annotation path.  A key that
                # cannot name an index column would then make an otherwise
                # sound resource unusable for annotation too.  The rule is
                # enforced where it bites, in collect_index_info() (gain#464).
                "labels": {"type": "dict", "nullable": True},
            },
        },
    }


def _index_column_problem(column: object, taken: set[str]) -> str | None:
    """Say why ``column`` cannot name a column of the FTS index, or None."""
    if not isinstance(column, str):
        # YAML mapping keys need not be strings: `2024: release` parses to
        # an int key, `true:` to a bool, `null:` to None.  Caught here so
        # that the curator is told the rule -- letting one of these reach
        # the regex raises a TypeError, which the index build can only
        # report as an internal error (gain#464, gain#364).
        return f"is not a string but {type(column).__name__}"
    if not _INDEX_COLUMN_RE.match(column):
        return "is not a valid SQL identifier"
    if column.upper() in apsw.keywords:
        # Refusing every one of `apsw.keywords` is deliberately stricter
        # than SQLite is.  Measured against SQLite 3.53: FTS5 parses its own
        # argument list, so the CREATE VIRTUAL TABLE accepts all 147 of
        # them; it is the INSERT's column list, parsed by SQLite proper,
        # that refuses the 58 reserved ones (`order`, `select`, `from`, ...)
        # while the other 89 (`key`, `match`, `filter`, ...) work.  Which
        # keyword falls in which half is a property of the SQLite build and
        # version, not of this code, so the whole list goes: the cost is a
        # handful of label keys nobody uses -- no key in the live GRR is a
        # keyword -- and the alternative is a rule that shifts under a
        # dependency bump, in the direction of an unbuildable index.
        return "is an SQL keyword"
    if column.lower() in _INDEX_RESERVED_COLUMNS:
        return "is a name FTS5 reserves"
    if column.lower() in taken:
        return "repeats a field the index already has"
    return None


def validate_index_columns(
    resource_id: str, columns: Sequence[str],
) -> None:
    """Check that ``columns`` can name the columns of the FTS index.

    Every column name is interpolated into the ``CREATE VIRTUAL TABLE`` and
    ``INSERT`` statements that build the repository index, so a name that is
    not a bare identifier -- and not one SQL or FTS5 has already taken -- is
    at best unbuildable and at worst an injection (gain#464).  Repeats are
    rejected too: the index keeps one
    column per name, so a label key that repeats a fixed field silently
    replaces that field's value for the resource, which then cannot be found
    by it.

    Raises ``ValueError`` naming the resource and the offending column.  The
    caller is the per-resource handler of the index build, so an offending
    resource is skipped and reported by id instead of taking the whole
    repository's index down with it.

    The columns are typed as strings but come from a YAML mapping's keys,
    which need not be -- a column that is not a string is refused like any
    other bad name rather than raising out of the check.
    """
    seen: set[str] = set()
    for column in columns:
        problem = _index_column_problem(column, seen)
        if problem is not None:
            raise ValueError(
                f"cannot index resource <{resource_id}>: its search index "
                f"field <{column}> {problem}; a field name -- and so every "
                f"'meta.labels' key -- must match {INDEX_COLUMN_PATTERN}, "
                f"must be neither an SQL keyword nor a name FTS5 reserves "
                f"({', '.join(sorted(_INDEX_RESERVED_COLUMNS))}), and must "
                f"not repeat another field of the index",
            )
        seen.add(column.lower())


# One column of the index, as claimed by the first resource that named it:
# its spelling, and the id of that resource.
IndexColumn = tuple[str, str]

# The most columns the repository's index table can have.  SQLite's
# SQLITE_MAX_COLUMN is 2000 by default, and FTS5 spends six of those on the
# shadow table behind the virtual one; 1994 is what SQLite 3.53 accepts for
# a CREATE VIRTUAL TABLE ... USING fts5 plus an INSERT naming every column,
# which is what the index build does.  The union of a whole repository's
# label keys is what has to fit, so this is checked over the union, not per
# resource (gain#464).
MAX_INDEX_COLUMNS = 1994


def merge_index_columns(
    resource_id: str,
    columns: Sequence[str],
    claimed: Mapping[str, IndexColumn],
) -> dict[str, IndexColumn]:
    """Return ``claimed`` extended with ``columns``, keyed case-insensitively.

    The index table has one set of columns for the whole repository -- the
    union of every resource's fields -- so a field name that is fine within
    one resource can still be unusable next to another resource's.  SQLite
    compares column names case-insensitively, so ``assay`` in one resource
    and ``Assay`` in another are one column asked for twice under two
    spellings, and a ``CREATE VIRTUAL TABLE`` naming both fails -- taking
    the whole repository's index with it (gain#464).  Two resources
    spelling a field the same way share the column, which is the point of
    the index.

    Raises ``ValueError`` if ``columns`` cannot join ``claimed`` -- naming
    the resource that already holds a spelling, or, past
    ``MAX_INDEX_COLUMNS``, saying that the repository's labels no longer
    fit an FTS5 table.  The caller skips that one resource and keeps the
    rest.  ``claimed`` is never modified -- a rejected resource claims
    nothing.
    """
    additions: dict[str, IndexColumn] = {}
    for column in columns:
        key = column.lower()
        held = claimed.get(key) or additions.get(key)
        if held is None:
            additions[key] = (column, resource_id)
            continue
        spelling, holder = held
        if spelling == column:
            continue
        raise ValueError(
            f"cannot index resource <{resource_id}>: its search index "
            f"field <{column}> differs only in case from field "
            f"<{spelling}> of resource <{holder}>, which the index already "
            f"has; SQLite compares column names case-insensitively, so the "
            f"two cannot both be fields of the repository's index -- spell "
            f"them the same way. Resources join the index in resource id "
            f"order, so the spelling of the first resource by id is the "
            f"one kept",
        )
    merged = {**claimed, **additions}
    if len(merged) > MAX_INDEX_COLUMNS:
        raise ValueError(
            f"cannot index resource <{resource_id}>: its search index "
            f"fields would take the repository's index past the "
            f"{MAX_INDEX_COLUMNS} columns an FTS5 table can have -- the "
            f"repository has too many distinct 'meta.labels' keys between "
            f"all of its resources. Resources join the index in resource "
            f"id order, so the resources dropped are the ones that reach "
            f"it last by id",
        )
    return merged


class ResourceStatistics:
    """
    Base class for statistics.

    Subclasses should be created using mixins defined for each statistic type
    that the resource contains.
    """

    def __init__(self, resource_id: str):
        self.resource_id = resource_id

    @staticmethod
    def get_statistics_folder() -> str:
        return "statistics"


class GenomicResourceImplementation(ABC):
    """
    Base class used by resource implementations.

    Resources are just a folder on a repository. Resource implementations
    are classes that know how to use the contents of the resource.
    """

    def __init__(self, genomic_resource: GenomicResource):
        self.resource = genomic_resource
        self.config: dict = self.resource.get_config()
        self._statistics: ResourceStatistics | None = None

    @property
    def resource_id(self) -> str:
        return self.resource.resource_id

    def get_config(self) -> dict:
        return self.config

    @property
    def files(self) -> set[str]:
        """Return a list of resource files the implementation utilises."""
        return set()

    @abstractmethod
    def calc_statistics_hash(self) -> bytes:
        """
        Compute the statistics hash.

        This hash is used to decide whether the resource statistics should be
        recomputed.
        """
        raise NotImplementedError

    @abstractmethod
    def create_statistics_build_tasks(
        self, **kwargs: Any,
    ) -> list[TaskDesc]:
        """Create tasks for calculating resource statistics for task graph."""
        raise NotImplementedError

    @abstractmethod
    def calc_info_hash(self) -> bytes:
        """Compute and return the info hash."""
        raise NotImplementedError

    @abstractmethod
    def get_info(self, **kwargs: Any) -> str:
        """Construct the contents of the implementation's HTML info page."""
        raise NotImplementedError

    @abstractmethod
    def get_statistics_info(self, **kwargs: Any) -> str:
        """Construct the contents of the implementation's HTML
        statistics info page.
        """
        raise NotImplementedError

    def collect_index_info(
        self,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Collect resource info for FTS index building.

        Returns a (header, row) pair where header contains field names and
        row contains the corresponding values for this resource.
        Label keys/values are appended after the fixed fields.

        Raises ``ValueError`` if a label key cannot name an index field --
        every implementation reaches the index through here, and the index
        build reports a raise from here against this one resource (gain#464).
        """
        res = self.resource
        meta = res.get_config().get("meta", {}) or {}
        labels: dict = res.get_labels() or {}
        header: tuple[str, ...] = (
            "full_id", "id", "type", "description", "summary",
            *labels.keys(),
        )
        validate_index_columns(res.resource_id, header)
        row: tuple[str, ...] = (
            res.get_full_id(),
            res.resource_id,
            res.get_type(),
            meta.get("description", "") or "",
            meta.get("summary", "") or "",
            *[str(v) for v in labels.values()],
        )
        return header, row

    def get_statistics(self) -> ResourceStatistics | None:
        """Try and load resource statistics."""
        return None

    def reload_statistics(self) -> ResourceStatistics | None:
        self._statistics = None
        return self.get_statistics()


class InfoImplementationMixin:
    """Mixin that provides generic template info page generation interface."""

    @dataclass
    class FileEntry:
        """Provides an entry into manifest object."""

        name: str
        size: str
        md5: str | None

    resource: GenomicResource
    template_name: ClassVar[str] = "base_implementation.jinja"
    styles_template_name: ClassVar[str] = "base_implementation_styles.jinja"

    def _get_template_data(self) -> dict:
        return {}

    def get_template_data(self) -> dict:
        """
        Return a data dictionary to be used by the template.

        Will transform the description in the meta section using markdown.
        """
        template_data = self._get_template_data()

        template_data["resource_files"] = [
            self.FileEntry(entry.name, convert_size(entry.size), entry.md5)
            for entry in self.resource.get_manifest().entries.values()
            if not entry.name.startswith("statistics")
            and entry.name != "index.html"]
        template_data["resource_files"].append(
            self.FileEntry("statistics/", "", ""))
        return template_data

    def get_statistics_template_data(self) -> dict:
        """
        Return a data dictionary to be used by the statistics template.

        Will transform the description in the meta section using markdown.
        """
        template_data = self._get_template_data()

        template_data["statistic_files"] = [
            self.FileEntry(
                entry.name.removeprefix("statistics/"),
                convert_size(entry.size),
                entry.md5,
            )
            for entry in self.resource.get_manifest().entries.values()
            if entry.name.startswith("statistics")]
        return template_data

    def get_info(self, **kwargs: Any) -> str:  # noqa: ARG002
        """Construct the contents of the implementation's HTML info page."""
        template_data = self.get_template_data()
        return get_template(self.template_name).render(
            resource=self.resource,
            markdown=markdown,
            data=template_data,
            base="resource_template.jinja",
            styles_template=self.styles_template_name,
        )

    def get_statistics_info(self, **kwargs: Any) -> str:  # noqa: ARG002
        """Construct the contents of the implementation's HTML info page."""
        template_data = self.get_statistics_template_data()
        return get_template(self.template_name).render(
            resource=self.resource,
            markdown=markdown,
            data=template_data,
            base="statistics_template.jinja",
            styles_template=self.styles_template_name,
        )


class ResourceConfigValidationMixin:
    """Mixin that provides validation of resource configuration."""

    @staticmethod
    @abstractmethod
    def get_schema() -> dict:
        """Return schema to be used for config validation."""
        raise NotImplementedError

    @classmethod
    def validate_and_normalize_schema(
            cls, config: dict, resource: GenomicResource) -> dict:
        """Validate the resource schema and return the normalized version."""
        # pylint: disable=not-callable
        validator = Validator(cls.get_schema())
        if not validator.validate(config):
            logger.error(
                "Resource %s of type %s has an invalid configuration. %s",
                resource.resource_id,
                resource.get_type(),
                validator.errors)
            raise ValueError(f"Invalid configuration: {resource.resource_id}")
        return cast(dict, validator.document)
