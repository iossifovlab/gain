"""What the statistics scan refuses, one rule per score kind (ADR 0027).

ADR 0008 gave the statistics scan sole ownership of validation -- reads never
validate -- and gave each score kind its own rule rather than a shared one it
did not choose.  Both halves stand; this module is where the second one is
written down.

The rules used to be ``@abstractmethod`` on ``GenomicScore`` with one body per
kind beside the read code, while their only callers were the scan's two doors
(``genomic_scores_impl/scan.py``) and ``statistics/alleles.py``.  A read class
carrying a rule only its consumer applies is what gain#1269 moved here.

Two functions, both :func:`functools.singledispatch`, both dispatching on the
score's class:

- :func:`validate_records` -- the per-record door's rule;
- :func:`validate_record_arrays` -- the same rule over a batch's columns.

Both are **transducers**: they hand back exactly what they were given, in
order, and raise at the first record their kind cannot mean.  A record the
kind's ORDERING rule refuses raises
:class:`~gain.genomic_resources.resource_errors.MalformedResourceError`; a
record whose own end precedes its own begin raises the plain ``OSError`` that
:func:`~gain.genomic_resources.resource_errors.inverted_span_error` builds,
which is a claim about one record rather than about the resource's order.
Neither re-reads and neither materialises the region -- the scan pays for one
read, and these ride it.

Both read RAW records rather than the spans a kind yields, because a kind's
normalization destroys the evidence: an allele score collapses a record to the
point it sits at, discarding its end entirely.  Raw is also the only layer at
which the per-record and the vectorized rule can say the same thing -- clipping
a record to the scanned region would tie the verdict to how the contig happened
to be partitioned.

**Three kinds, two rules.**  A position score promises one value per position,
so it refuses records that overlap *or merely touch*.  An allele score and a
fragment score both allow several records at one position and refuse only a
record that moves backwards; they differ solely in the noun their message
names.  So the backwards rule is written once per shape and registered twice,
with the noun supplied at the registration.  ADR 0008 accepted six copies to
avoid a *hidden* shared statement -- one class attribute two validators read
and interpreted differently.  A body called explicitly from two registrations
hides nothing, so that reason does not reach this arrangement (ADR 0027).

**A kind nobody wrote a rule for cannot be scanned.**  The undecorated body
of each function -- ``singledispatch``'s default, which it keys on ``object``
rather than on ``GenomicScore`` -- raises ``NotImplementedError`` naming the
class it was handed.  The three kinds are flat siblings, so a kind added later
reaches that default rather than inheriting a rule chosen for something else.

That refusal is at run time -- a missing
registration has no static analogue the way a missing ``@abstractmethod``
override had -- so ``test_every_buildable_kind_is_registered`` stands in for
the check the type checker used to make.  ADR 0001's gain#1261 Amendment
depends on this; see ADR 0027.
"""
from __future__ import annotations

from collections.abc import Generator, Iterator
from functools import partial, singledispatch

import numpy as np

from gain.genomic_resources.genomic_position_table.record import (
    ALT,
    CHROM,
    POS_BEGIN,
    POS_END,
    REF,
    Record,
)
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    FragmentScore,
    GenomicScore,
    PositionScore,
    RecordArrays,
)
from gain.genomic_resources.resource_errors import (
    backwards_records_error,
    inverted_span_error,
    overlapping_records_error,
)

#: The possessive naming the kind whose promise was broken, as
#: :func:`~gain.genomic_resources.resource_errors.backwards_records_error`
#: wants it.  Passed at the registration rather than read off the score, so
#: that a kind's rule and the words it is refused in are chosen together: an
#: attribute two validators interpret for themselves is what ADR 0008 unwound.
_ALLELE = "an allele score's"
_FRAGMENT = "a fragment score's"


def _no_rule_message(score: GenomicScore, door: str) -> str:
    """What both doors say to a kind nobody registered a rule for.

    Worded here rather than at either raise site so the two cannot drift: the
    only test on these messages matches on the class name, and would not
    notice if one door started wording the rest differently.

    The ``raise NotImplementedError(...)`` stays spelled out at each door
    rather than being built here too -- ruff reads that exact shape as a stub
    and stops asking why the door does not use its stream arguments.
    """
    return (
        f"no {door} validation rule is registered for "
        f"{type(score).__name__}; a score kind is scanned only through a "
        f"rule written for it (ADR 0027)")


@singledispatch
def validate_records(
    score: GenomicScore, records: Iterator[Record],
) -> Generator[Record, None, None]:
    """Yield a raw record stream through, refusing a malformed one.

    Dispatches on ``score``'s class to the rule registered for that kind.  A
    kind with no registration reaches this body and is refused: it would
    otherwise be validated by a rule nobody chose for it, which is the failure
    ADR 0008 exists to undo.
    """
    raise NotImplementedError(_no_rule_message(score, "record"))


@singledispatch
def validate_record_arrays(
    score: GenomicScore, batches: Iterator[RecordArrays], chrom: str,
) -> Generator[RecordArrays, None, None]:
    """Yield a stream of raw column batches through, refusing a bad one.

    The vectorized counterpart of :func:`validate_records`, over the batches
    the bulk scan is already pulling.  It states the SAME ordering rule as its
    per-record twin, so a resource whose records are out of order is refused
    identically whichever path it was eligible for.  Divergence between the
    two is what ADR 0008 records as the reason the shared class attribute was
    removed.

    It also refuses a record whose end precedes its begin, as the per-record
    door does (see
    :func:`~gain.genomic_resources.resource_errors.inverted_span_error`),
    although the backends the bulk path reads keep all but one such row out
    -- a rule the backend happens to enforce is still the kind's to state
    (ADR 0008; ``test_scan_array_door.py`` pins what each backend admits).
    The refusal is the per-record door's, one suffix short of it for an
    allele record: the batches carry no ref/alt columns, so it cannot name
    them.  A resource breaking both rules is refused for the same fault on
    both doors; :func:`_refuse_first_fault` says which.

    ``chrom`` is what the batches were read for.  A bulk scan reads one
    region, which lies within one contig, so the rules carry their ordering
    state across batches but never across contigs.

    Refuses an unregistered kind, for the reason :func:`validate_records`
    gives.
    """
    raise NotImplementedError(
        _no_rule_message(score, "record-array"))


def _record_to_begin_end(record: Record) -> tuple[str, int, int]:
    """Read a record's three positional slots, checking their order.

    Private to this module, and to the two rules above: both use the chrom to
    reset their carry at a contig boundary, which is why they take the
    3-tuple rather than reading the two positions themselves.
    """
    chrom = record[CHROM]
    pos_begin = record[POS_BEGIN]
    pos_end = record[POS_END]
    if pos_end < pos_begin:
        raise inverted_span_error(
            chrom, pos_begin, pos_end, record[REF], record[ALT])
    return chrom, pos_begin, pos_end


def _position_records(
    score: PositionScore, records: Iterator[Record],
) -> Generator[Record, None, None]:
    """Refuse two records that overlap -- or merely touch.

    A position score promises one value per position, so a record beginning
    where its predecessor has not yet ended claims a position already taken.
    ``begin <= prev_end`` and not ``<``: two records sharing a single base
    pair is the same error as two overlapping by a hundred.

    Each record is compared with the one before it only, and that is
    complete -- no running maximum over the ends seen so far is needed --
    because :func:`_record_to_begin_end` has refused a record whose end
    precedes its begin before the comparison runs.  Any bound as tight as
    ``end >= begin - 1`` suffices: with ``begin > prev_end`` on top,
    ``end >= begin - 1 >= prev_end``, so the ends never decrease and the
    previous end IS the widest end seen.  Without such a bound the pairwise
    rule would be incomplete: an inverted record sits between two
    neighbours without touching either, and they may overlap each other
    behind it.
    """
    prev_chrom: str | None = None
    prev_end: int | None = None
    for record in records:
        chrom, begin, end = _record_to_begin_end(record)
        if chrom != prev_chrom:
            prev_end = None
        if prev_end is not None and begin <= prev_end:
            raise overlapping_records_error(
                score.resource_id, chrom, begin, prev_end)
        prev_chrom, prev_end = chrom, end
        yield record


def _backwards_records(
    score: GenomicScore, records: Iterator[Record], *, kind: str,
) -> Generator[Record, None, None]:
    """Refuse a record beginning before the one before it.

    The rule an allele score and a fragment score share, and the only thing
    that differs between them is ``kind`` -- the noun the refusal names.

    Several records legitimately sit at one position: one per ref/alt pair for
    an allele score, and fragments overlap freely and may share a start.  So a
    record at the SAME position as its predecessor is what these kinds ARE,
    not an error, and only a record that moves BACKWARDS is one -- no ordering
    of the alleles at a site can produce it, and no fragment interval reaching
    back over its predecessor needs it.  Only the begins take part; a record's
    own end takes none.

    The comparison restarts at every contig: where a record sits on one contig
    says nothing about the next, and without the reset every resource whose
    second contig starts before the first one ended would be refused.
    """
    prev_chrom: str | None = None
    prev_begin: int | None = None
    for record in records:
        chrom, begin, _end = _record_to_begin_end(record)
        if chrom != prev_chrom:
            prev_begin = None
        if prev_begin is not None and begin < prev_begin:
            raise backwards_records_error(
                score.resource_id, chrom, begin, prev_begin, kind)
        prev_chrom, prev_begin = chrom, begin
        yield record


#: What precedes a batch's first record before any record was seen: a value
#: no begin can sit at or before, so neither ordering rule can fire on it.
_NO_PREDECESSOR = np.iinfo(np.int64).min


def _predecessors(column: np.ndarray, carried: int | None) -> np.ndarray:
    """Each record's predecessor's value of ``column``, as a column.

    The first record's predecessor is the one ``carried`` in from the batch
    before -- or, when there is none, :data:`_NO_PREDECESSOR`.  A rule
    compares ``pos_begin`` against this column in one vectorized step, so a
    violation straddling a batch boundary is found at index 0 like any other:
    batches are a read-granularity artefact, and no rule may depend on where
    one happens to break.
    """
    first = _NO_PREDECESSOR if carried is None else carried
    return np.concatenate(([first], column[:-1]))


def _refuse_first_fault(
    chrom: str, pos_begin: np.ndarray, pos_end: np.ndarray,
    disorder: np.ndarray,
) -> int | None:
    """Name a batch's first faulty record: raise for its span, or return it.

    ``disorder`` marks the records that break the kind's ordering rule.  A
    record whose end precedes its begin breaks the rule every kind shares
    (:func:`~gain.genomic_resources.resource_errors.inverted_span_error`), so
    this is the array-side twin of :func:`_record_to_begin_end`, and shared
    by both array rules as that reader is by both per-record rules.  The
    batches carry no ref/alt columns, so the refusal names none.

    The precedence is the per-record door's: a record's own span is read
    before the record is compared with its predecessor, so of a batch's
    faults the earliest in record order is named, and a record breaking both
    rules is named for its span.  Returns the index of the first record
    breaking the ordering rule when that comes first -- the caller raises,
    since it alone knows the words -- and ``None`` when no record breaks
    either.
    """
    inverted = pos_end < pos_begin
    faults = disorder | inverted
    if not bool(faults.any()):
        return None
    at = int(np.argmax(faults))
    if inverted[at]:
        raise inverted_span_error(
            chrom, int(pos_begin[at]), int(pos_end[at]), None, None)
    return at


def _position_record_arrays(
    score: PositionScore, batches: Iterator[RecordArrays], chrom: str,
) -> Generator[RecordArrays, None, None]:
    """Refuse two records that overlap -- or merely touch, vectorized.

    The same rule as :func:`_position_records`, stated over a batch's columns
    instead of over records.  Both read the RAW begin and end, which is the
    only layer at which the two can say the same thing.

    Adjacent pairs only, exactly as :func:`_position_records` compares them,
    and complete for the reason given there: :func:`_refuse_first_fault`
    names a record whose end precedes its begin unless a touching record
    comes first, so up to the first touching record the ends never decrease.
    """
    prev_end: int | None = None
    for batch in batches:
        pos_begin, pos_end, _cells = batch
        if pos_begin.size:
            taken = _predecessors(pos_end, prev_end)
            at = _refuse_first_fault(
                chrom, pos_begin, pos_end, pos_begin <= taken)
            if at is not None:
                raise overlapping_records_error(
                    score.resource_id, chrom, int(pos_begin[at]),
                    int(taken[at]))
            prev_end = int(pos_end[-1])
        yield batch


def _backwards_record_arrays(
    score: GenomicScore, batches: Iterator[RecordArrays], chrom: str,
    *, kind: str,
) -> Generator[RecordArrays, None, None]:
    """Refuse a record beginning before the one before it, vectorized.

    The same rule as :func:`_backwards_records`, over a batch's columns, and
    shared by the same two kinds for the same reason.  The comparison is
    strict: several records at ONE position are what these kinds are made of.

    Only the begins take part in the ordering, and only the RAW ones -- the
    ends an optional ``pos_end`` column carries are not what an allele
    record means, and a fragment's own end is not what its ordering is
    about.  The ends are read for one thing only: :func:`_refuse_first_fault`
    refuses a record whose end precedes its begin, as
    :func:`_record_to_begin_end` does for the other path.
    """
    prev_begin: int | None = None
    for batch in batches:
        pos_begin, pos_end, _cells = batch
        if pos_begin.size:
            before = _predecessors(pos_begin, prev_begin)
            at = _refuse_first_fault(
                chrom, pos_begin, pos_end, pos_begin < before)
            if at is not None:
                raise backwards_records_error(
                    score.resource_id, chrom, int(pos_begin[at]),
                    int(before[at]), kind)
            prev_begin = int(pos_begin[-1])
        yield batch


# -- The kind-to-rule map ----------------------------------------------------
#
# Six lines, one per (kind, door).  This is the whole of what each kind was
# saying by having its own method body, and it is meant to be read as a table:
# a position score is the one kind whose records may not touch, and the other
# two share one rule under two nouns.  A kind absent from here is refused by
# the base registrations above.

validate_records.register(PositionScore, _position_records)
validate_records.register(
    AlleleScore, partial(_backwards_records, kind=_ALLELE))
validate_records.register(
    FragmentScore, partial(_backwards_records, kind=_FRAGMENT))

validate_record_arrays.register(PositionScore, _position_record_arrays)
validate_record_arrays.register(
    AlleleScore, partial(_backwards_record_arrays, kind=_ALLELE))
validate_record_arrays.register(
    FragmentScore, partial(_backwards_record_arrays, kind=_FRAGMENT))
