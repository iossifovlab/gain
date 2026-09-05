from __future__ import annotations

import copy
import threading
import weakref
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, NamedTuple, cast

import apsw
from cerberus import Validator

from gain import logging
from gain.task_graph.graph import TaskDesc
from gain.templates import get_template
from gain.utils.helpers import convert_size

from .dvc import is_dvc_sidecar
from .repository import (
    GR_INDEX_NON_LABEL_COLUMNS,
    GR_INDEX_RESOURCE_FIELDS,
    INDEX_COLUMN_PATTERN,
    INDEX_COLUMN_RE,
    GenomicResource,
    _description_in,
    _summary_in,
)
from .resource_query import label_alternatives

logger = logging.getLogger(__name__)


# Names FTS5 will not accept as a column of the index table: it reserves
# `rank` and `rowid`, and every FTS5 table has a hidden column named after
# the table itself -- which for the repository index is `contents`, the
# table _create_contents_db builds.  Compared case-insensitively, as SQLite
# compares identifiers.
_INDEX_RESERVED_COLUMNS = frozenset({"rank", "rowid", "contents"})

# The index columns that describe the resource rather than one of its
# labels, lowercased for the case-insensitive comparison SQLite makes.
# A label key may never be one of these, whatever the resource's own
# implementation contributes -- see `collect_index_info` (gain#542).
_RESERVED_FIELD_NAMES = frozenset(
    name.lower() for name in GR_INDEX_NON_LABEL_COLUMNS
)


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
    if not INDEX_COLUMN_RE.match(column):
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
        if column.lower() in _RESERVED_FIELD_NAMES:
            # The name is one the index keeps for a resource field, and the
            # curator's own resource may well have no such field -- a genome
            # carrying a label named `score_ids` collides with a column only
            # score resources fill.  "Repeats a field" would leave them
            # looking for a field that is not there, so name them (gain#542).
            return (
                "is a name the index reserves for a resource field "
                f"({', '.join(sorted(_RESERVED_FIELD_NAMES))})"
            )
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
    rejected too: the index keeps one column per name.  Where the repeated
    name is a field of the resource itself, the label silently replaces that
    field's value, which then cannot be found by it; where it is a name the
    index reserves for a field some *other* implementation contributes, the
    label lands in a column that means something else for every resource
    that does contribute it (gain#542).  Both are reported against the
    offending column, the second naming the reserved set, since the curator
    has no such field of their own to look at.

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
        """The id of the resource this implementation wraps."""
        return self.resource.resource_id

    def get_config(self) -> dict:
        """The resource's configuration.

        As read from the resource at construction; an implementation
        that validates its configuration replaces it with the validated
        form, and answers that here.
        """
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

        An override that contributes further fields must call ``super()``
        and append to what it returns.  This is the only place a label key
        is checked against the names the index reserves: the build's own
        re-check sees the finished header, in which an implementation's
        fields legitimately appear, so it cannot tell a field from a label
        (gain#542).  A field added by an override belongs in
        ``GR_INDEX_NON_LABEL_COLUMNS``.
        """
        res = self.resource
        # Through the resource's own narrowing accessor, not the raw
        # config: `or {}` guards a None and an empty mapping but not a
        # scalar, and a `meta: |` block of prose then raised from the
        # `.get` below (gain#1004).
        meta = res.get_meta()
        labels: dict = res.get_labels() or {}
        header: tuple[str, ...] = (
            *GR_INDEX_RESOURCE_FIELDS,
            *labels.keys(),
        )
        # Vetted against every non-label column, not just the fields this
        # resource's own header carries.  A non-score resource carrying a
        # label named `score_ids` is the case that reaches this: its own
        # header has no score fields, so vetting the header alone lets it
        # through (gain#542).
        #
        # Unconditional on purpose -- do NOT narrow this to "only when the
        # repository also holds a score resource".  The column such a label
        # would need is already the field's, so one row cannot record both
        # whatever else the repository holds; and the index's columns are
        # the union across the repository, so whether the collision exists
        # would otherwise depend on which other resources happen to be
        # present.  Note this is about what can be *indexed*, not about
        # what a query can ask: since gain#646 no label key reaches the
        # search statement at all, so a label spelled like a field is
        # answered from the resource's own `meta.labels` on both routes.
        validate_index_columns(
            res.resource_id,
            (*sorted(GR_INDEX_NON_LABEL_COLUMNS), *labels.keys()),
        )
        # Derived from the block narrowed above, through the same helpers
        # the resource's own accessors use: the row must carry what
        # `get_description`/`get_summary` report, or a resource with a
        # description and no summary indexes an empty `summary` column
        # while displaying that description as its summary -- invisible to
        # a `summary : <term>` search (gain#1008).  Off the block rather
        # than through the accessors, which would re-narrow it and report a
        # malformed one once more per read.
        #
        # A label column holds the value's alternatives joined by a space,
        # so a list value tokenises into one FTS term per element by
        # design rather than on the punctuation of its repr.  Rendered
        # through the same helper the query matcher reads a value with,
        # so the two cannot disagree about what a list means (gain#1225).
        row: tuple[str, ...] = (
            res.get_full_id(),
            res.resource_id,
            res.get_type(),
            _description_in(meta),
            _summary_in(meta),
            *[" ".join(label_alternatives(v)) for v in labels.values()],
        )
        return header, row

    def get_statistics(self) -> ResourceStatistics | None:
        """Try and load resource statistics."""
        return None

    def reload_statistics(self) -> ResourceStatistics | None:
        """Drop the cached statistics and reload via :meth:`get_statistics`.

        For after the statistics were rebuilt on disk.  Answers what
        :meth:`get_statistics` answers: ``None`` unless the
        implementation overrides it.
        """
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
            and entry.name != "index.html"
            and not is_dvc_sidecar(entry.name)]
        template_data["resource_files"].append(
            self.FileEntry("statistics/", "", ""))
        # Each label rendered for display: a list value is its
        # alternatives, comma-separated, read through the same helper the
        # query matcher and the FTS index use (gain#1225).
        template_data["labels"] = [
            (key, ", ".join(label_alternatives(value)))
            for key, value in self.resource.get_labels().items()
        ]
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
            if entry.name.startswith("statistics")
            and not is_dvc_sidecar(entry.name)]
        return template_data

    def get_info(self, **kwargs: Any) -> str:  # ruff: ignore[unused-method-argument]
        """Construct the contents of the implementation's HTML info page."""
        template_data = self.get_template_data()
        return get_template(self.template_name).render(
            resource=self.resource,
            data=template_data,
            base="resource_template.jinja",
            styles_template=self.styles_template_name,
        )

    def get_statistics_info(self, **kwargs: Any) -> str:  # ruff: ignore[unused-method-argument]
        """Construct the contents of the implementation's HTML info page."""
        template_data = self.get_statistics_template_data()
        return get_template(self.template_name).render(
            resource=self.resource,
            data=template_data,
            base="statistics_template.jinja",
            styles_template=self.styles_template_name,
        )


class _ThreadValidators(threading.local):
    """One cerberus ``Validator`` per implementation type, per thread.

    Subclassing ``threading.local`` rather than using one directly is what
    makes ``__init__`` run once per thread, so the payload is simply there
    on first access instead of being lazily created at every use.
    """

    # pylint: disable=too-few-public-methods
    def __init__(self) -> None:
        self.by_type: weakref.WeakKeyDictionary[type, Validator] = \
            weakref.WeakKeyDictionary()


class _Memo(NamedTuple):
    """One resource's normalized config, and the config it came from.

    ``config`` is held to be compared by *identity* on the next lookup, not
    read: it is the object ``document`` was normalized from, so a caller
    handing over the same object again is asking the same question.  Held
    strongly, which normally costs nothing -- it is the resource's own
    config, and one dict is retained per (resource, type) regardless.
    """

    config: dict
    document: dict


class _ConfigValidatorCache:
    """The resource-config schemas, validators, and normalized documents.

    A resource's schema and the cerberus ``Validator`` compiled from it
    depend on the implementation TYPE, never on the config being validated,
    yet both were rebuilt on every call -- and constructing a ``Validator``
    makes cerberus validate the schema definition itself.  Together that was
    about a third of the cost of validating one resource, paid once per
    resource, and a wildcard pipeline build validates hundreds (gain#905).

    **The normalized document is kept too**, which is what remained after
    gain#905 and turned out to be the larger half (gain#1059).  It depends
    on the config and not only on the type, so it is keyed per (resource,
    type) -- and only a run whose tasks share a process collects on that: a
    resource that crosses a process boundary is unpickled into a new object,
    which is a new key and so a fresh normalization.

    That makes this the first store here whose size follows the *repository*
    rather than the handful of implementation types: one document per live
    resource something has built an implementation for, each weighing about
    what that resource's config already weighs (2.06 MiB for the 321
    resources of grr plus grr_sfari).  The keys are weak, so a resource
    nothing holds takes its entry with it -- but a protocol holds every
    resource it has enumerated, so a repo-wide walk retains one document per
    resource for as long as it runs.

    This is not the convention the four ``get_memo_key()`` memos use
    (``gene_scores``, ``gene_set``, ``gene_models_factory``,
    ``liftover_chain``), and deliberately: those memoize an object built
    *from* a resource, under a strong string key that never evicts, whereas
    what is memoized here is the normalization of a config passed as an
    argument -- which callers do supply independently of the resource.

    **The validator is deliberately not shared between threads.**  Cerberus
    keeps per-call state on the instance -- ``validate()`` assigns
    ``self.document`` and ``self._errors``, and the caller reads the
    normalized document back off the instance on the next line -- so one
    instance shared across threads can hand a caller another caller's
    normalized config.  That is not theoretical: pipeline loads run on a
    thread pool and loading a pipeline constructs resource implementations,
    and a direct reproduction bled one read in 600.  Hence per thread; a
    thread's validators die with it.

    **The schema is shared between threads, for a narrower reason than
    "cerberus treats it as read-only" -- it does not.**  ``DefinitionSchema``
    *expands* the schema in place, rebinding nested rule values, both when a
    validator is constructed and again on every ``validate()``.  What holds
    is that the expansion is value-preserving for the schemas gain actually
    has, none of which uses a rule cerberus rewrites non-trivially.  The
    exposure is not new either: ``get_schema()`` splices in the *same*
    module-level ``AGGREGATOR_SCHEMA`` object rather than a copy, so that
    fragment was already being re-expanded concurrently on every call before
    anything was cached.  Being by value is also why the guarding test
    asserts ``==`` and not ``is``.

    Nothing guards against re-entrancy: a nested validation of the same type
    on the same thread would overwrite the outer call's document before it is
    read.  No such path exists -- no gain schema carries a callable rule
    (``check_with``/``coerce``/``default_setter``), and the only code between
    ``validate()`` and the read is the failure branch's eagerly-evaluated log
    call.  Adding a callable rule to a resource schema would change that.

    Keys are held weakly so that an implementation type defined inside a test
    does not outlive it; production types are module-level and immortal
    anyway.
    """

    def __init__(self) -> None:
        self._schemas: weakref.WeakKeyDictionary[type, dict] = \
            weakref.WeakKeyDictionary()
        self._validators = _ThreadValidators()
        # Weak outer, plain inner, as `_REF_GENOME_CACHE` is: an entry's
        # implementation type is held only for as long as its resource, so
        # a type defined inside a test still dies with it.
        self._documents: weakref.WeakKeyDictionary[
            GenomicResource, dict[type, _Memo],
        ] = weakref.WeakKeyDictionary()

    def schema_for(self, implementation: type) -> dict:
        """Return ``implementation``'s schema, building it at most once.

        Two threads racing to fill an entry both call ``get_schema()`` and
        one overwrites the other, which is harmless -- the schemas are equal.
        """
        schema = self._schemas.get(implementation)
        if schema is None:
            schema = implementation.get_schema()  # type: ignore[attr-defined]
            self._schemas[implementation] = schema
        return schema

    def validator_for(self, implementation: type) -> Validator:
        """Return this thread's validator for ``implementation``."""
        validator = self._validators.by_type.get(implementation)
        if validator is None:
            # pylint: disable=not-callable
            validator = Validator(self.schema_for(implementation))
            self._validators.by_type[implementation] = validator
        return validator

    def document_for(
        self, implementation: type, resource: GenomicResource, config: dict,
    ) -> dict | None:
        """Return ``config`` normalized before, or ``None`` if it was not.

        A hit requires the *same config object*, not an equal one -- the
        precondition that goes with which is on
        ``validate_and_normalize_schema``, where a caller will read it.
        Keying on the resource alone would be wrong: the same resource is
        routinely normalized against more than one config, and every caller
        is entitled to the document for the config it actually passed.

        Switching the guard to ``==`` would not make it notice an edited
        config, which is the tempting thing to think: an entry holds the
        caller's config object itself, so equality compares that object with
        itself and says yes however it has been edited since.

        Keys are compared by value, ``GenomicResource`` having its own
        ``__eq__``, so a ``CacheResource`` and the resource it mirrors are
        one key -- and they also share the one config object, so the hit is
        correct rather than merely harmless.  A hit pays exactly one such
        ``__eq__``, comparing a config with itself, which short-circuits per
        value on identity: 0.13 us for the largest config in the GRR.
        """
        by_type = self._documents.get(resource)
        if by_type is None:
            return None
        memo = by_type.get(implementation)
        if memo is None or memo.config is not config:
            return None
        return copy.deepcopy(memo.document)

    def remember_document(
        self, implementation: type, resource: GenomicResource,
        config: dict, document: dict,
    ) -> None:
        """Memoize ``document`` as the normalization of ``config``.

        Stored as a copy, so that an entry is a value rather than a view:
        cerberus rebuilds only the mappings its schema describes, so
        ``document`` still holds the resource's own objects for fields like
        ``meta.labels``.  Callers are handed copies too, so nothing can
        observe the difference -- the copy buys the invariant, at 1.8% of a
        cold pass over a whole GRR, paid once per entry.

        As with schemas, two threads may both fill an entry and one overwrite
        the other; the documents are equal.  No lock, on either path.  A
        lost or duplicated entry is a miss and never a wrong answer, since a
        hit is rechecked against the config it was filled from -- which is
        also what makes the unguarded weakref removals this store provokes
        (its keys die, unlike the immortal types beside it) harmless on the
        3.14t lane ``Jenkinsfile.python-matrix`` runs.
        """
        by_type = self._documents.setdefault(resource, {})
        by_type[implementation] = _Memo(config, copy.deepcopy(document))

    def clear(self) -> None:
        """Forget every schema and document, and this thread's validators.

        Exists for tests.  The cache is process-wide and never invalidated,
        which is right for a running gain -- the schema of a type does not
        change -- and wrong across tests, which share one process and one
        main thread: a validator built while ``Validator`` was monkeypatched
        outlives the patch and would answer for a later test.

        Only the calling thread's validators are forgotten.  Another live
        thread's are not reachable from here, and a registry that made them
        so would put a lock on the validation path to serve a test.
        """
        self._schemas.clear()
        self._validators.by_type.clear()
        self._documents.clear()


#: The process-wide resource-config validator cache.  See the class.
CONFIG_VALIDATOR_CACHE = _ConfigValidatorCache()


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
        """Validate the resource schema and return the normalized version.

        What comes back is the caller's own document all the way down, memo
        hit or not, and detached from the config it was normalized from -- so
        an implementation may keep it and write into it, as
        ``GenomicScore.__init__`` does.

        **Do not edit a resource's config in place.**  Offering the same
        config object twice is answered from a memo rather than re-normalized
        (gain#1059), and an edit made between the two calls is not seen: the
        second caller gets the document as the config was the first time.
        Nothing in gain or gpf does this -- the implementations that validate
        all keep the normalized copy rather than the config -- and code that
        wants a config re-read should hand over a new dict, which is always
        normalized afresh.
        """
        memoized = CONFIG_VALIDATOR_CACHE.document_for(cls, resource, config)
        if memoized is not None:
            return memoized

        validator = CONFIG_VALIDATOR_CACHE.validator_for(cls)
        if not validator.validate(config):
            logger.error(
                "Resource %s of type %s has an invalid configuration. %s",
                resource.resource_id,
                resource.get_type(),
                validator.errors)
            raise ValueError(f"Invalid configuration: {resource.resource_id}")

        document = cast("dict", validator.document)
        CONFIG_VALIDATOR_CACHE.remember_document(
            cls, resource, config, document)
        # Copied even here, where the document is cerberus's own and freshly
        # made.  Cerberus copies the config shallowly and rebuilds only the
        # mappings its schema describes, so a field with no sub-schema --
        # `meta.labels`, `default_annotation` -- comes back as the very
        # object in the resource's config.  Handing that to the caller who
        # missed, and a detached copy to everyone who hits, would make
        # "whose dict is this" depend on cache state.
        return copy.deepcopy(document)
