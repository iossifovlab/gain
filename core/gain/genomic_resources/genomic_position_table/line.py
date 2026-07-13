from collections import deque
from collections.abc import Generator
from typing import Any, Protocol

import pysam

from .record import (
    ALT,
    CHROM,
    PAYLOAD,
    POS_BEGIN,
    POS_END,
    REF,
    Record,
)

Key = str | int


class LineBase(Protocol):
    """Protocol for genomic position table lines."""

    chrom: str
    fchrom: str
    pos_begin: int
    pos_end: int
    ref: str | None
    alt: str | None

    def get(self, key: Key) -> Any:
        ...

    def row(self) -> tuple:
        ...


class Line:
    """Represents a line read from a genomic position table.

    Provides attribute access to a number of important columns - chromosome,
    start position, end position, reference allele and alternative allele.
    """
    __slots__ = (  # noqa: RUF023
        "_data",
        "chrom",
        "fchrom",
        "pos_begin",
        "pos_end",
        "ref",
        "alt",
    )

    def __init__(
        self,
        raw_line: tuple,
        chrom_key: int = 0,
        pos_begin_key: int = 1,
        pos_end_key: int = 2, *,
        ref_key: int | None = None,
        alt_key: int | None = None,
    ):
        self._data: tuple[str, ...] = raw_line

        self.chrom: str = self._data[chrom_key]
        self.fchrom: str = self._data[chrom_key]
        self.pos_begin: int = int(self._data[pos_begin_key])
        self.pos_end: int = int(self._data[pos_end_key])
        self.ref: str | None = \
            self._data[ref_key] if ref_key is not None else None
        self.alt: str | None = \
            self._data[alt_key] if alt_key is not None else None

    def get(self, key: Key) -> str:
        return self._data[key]  # type: ignore

    def row(self) -> tuple:
        return tuple(self._data)


class VCFLine(tuple):
    """Line adapter for lines derived from a VCF file.

    Implements functionality for handling multi-allelic variants
    and INFO fields.

    A VCF line is **record-shaped**: it subclasses ``tuple`` and lays its six
    slots out in record order (``CHROM``, ``POS_BEGIN``, ``POS_END``, ``REF``,
    ``ALT``, ``PAYLOAD``), so the record-indexed :class:`LineBuffer` and the
    record read cascade in ``TabixGenomicPositionTable`` -- which the VCF
    backend inherits -- can buffer and window it by slot, with no per-record
    branch on which backend produced it.  The adapter attributes
    (``chrom``, ``pos_begin``, ..., plus ``info``/``allele_index``) stay for
    the score layer, which still wraps a VCF line in a ``ScoreLine`` and reads
    INFO fields by name; #237 migrates the VCF backend proper and drops them.

    **Its PAYLOAD is not a tabular row.**  A VCF line carries the
    ``pysam.VariantRecord`` in the slot where a tabix record carries the raw
    row, so ``line[PAYLOAD][i]`` is *not* the i-th column of anything -- the
    PAYLOAD slot means whatever the backend that produced the record says it
    means.  A ``VCFLine`` is a ``tuple[Any, ...]`` like every other record, so
    the type checker cannot flag the confusion; the discriminator is the
    ``yields_records`` ClassVar on the table, which the VCF backend resets to
    False.  Only the five decoded slots (``CHROM`` ... ``ALT``) mean the same
    thing across backends.  #237 migrates the VCF backend and retires the
    split.

    The mapped (reference) contig is fixed at construction rather than written
    back onto the object afterwards -- a tuple slot cannot be rebound, and the
    buffer may already be holding the line.

    **A VCF line now has value semantics, which is a deliberate change.**  Two
    lines are equal when their six slots agree (a ``pysam.VariantRecord``
    compares structurally itself) *and* they carry the same ``allele_index``;
    :meth:`__hash__` agrees with that.  The pre-migration ``VCFLine`` had
    *identity* equality and an identity hash, so this does not restore the old
    contract -- it replaces it with the one a record-shaped tuple should have,
    the one that makes a lookup by an equal-but-not-identical line work.  Two
    further consequences of being a tuple, both intended: a line is equal to no
    other type (not even to a bare six-slot record, which has no allele index
    -- see :meth:`__eq__`), and ``sorted(lines)`` now orders them slot-wise via
    the inherited ``tuple.__lt__``, where the adapter raised ``TypeError``.
    """

    def __new__(
        cls,
        raw_line: pysam.VariantRecord,
        allele_index: int | None,
        chrom: str | None = None,
    ) -> "VCFLine":
        assert raw_line.ref is not None
        alt: str | None = None
        if allele_index is not None:
            assert raw_line.alts is not None
            alt = raw_line.alts[allele_index]
        return super().__new__(cls, (
            chrom if chrom is not None else raw_line.contig,
            raw_line.pos,
            raw_line.stop,
            raw_line.ref,
            alt,
            raw_line,
        ))

    def __init__(
        self,
        raw_line: pysam.VariantRecord,
        allele_index: int | None,
        chrom: str | None = None,  # noqa: ARG002  (consumed by __new__)
    ):
        # ``chrom`` is resolved in __new__, which is where the immutable tuple
        # slots are laid down; __init__ receives the same arguments and reads
        # the resolved contig back out of the CHROM slot.
        super().__init__()
        self.chrom: str = self[CHROM]
        self.fchrom: str = raw_line.contig
        self.pos_begin: int = self[POS_BEGIN]
        self.pos_end: int = self[POS_END]

        self.ref: str | None = self[REF]
        # Used to handle multiallelic variants in VCF files.
        # The allele index is None if the variant for this line
        # is missing its ALT, i.e. its value is '.'
        self.allele_index: int | None = allele_index
        self.alt: str | None = self[ALT]
        self.info: pysam.VariantRecordInfo = raw_line.info
        self.info_meta: pysam.VariantHeaderMetadata = raw_line.header.info

    def __getnewargs__(self) -> tuple[pysam.VariantRecord, int | None, str]:
        # A tuple subclass is reconstructed through ``__reduce_ex__`` as
        # ``cls.__new__(cls, *self.__getnewargs__())``.  The inherited
        # ``tuple.__getnewargs__`` would hand the six-slot record itself back
        # as ``raw_line`` and drop the other two arguments -- so spell the
        # construction arguments out.  The contig comes from the CHROM slot,
        # which is the *mapped* one.
        #
        # What this buys, precisely: ``copy.copy`` works, and the ``__dict__``
        # round-trip that follows ``__new__`` restores ``fchrom`` and
        # ``allele_index``.  ``copy.deepcopy`` still raises -- it deep-copies
        # the ``__getnewargs__`` arguments, and the ``pysam.VariantRecord``
        # among them cannot be pickled ("self.ptr cannot be converted to a
        # Python object").  That is not a regression (deepcopy of a VCF line
        # never worked); making it work needs a picklable VariantRecord, which
        # the VCF backend does not have.
        return self[PAYLOAD], self.allele_index, self[CHROM]

    def __eq__(self, other: object) -> bool:
        # A VCF line stands for an *allele*, and the allele index is the only
        # field that always says which one.  The ALT slot usually proxies for
        # it -- but not when a record repeats an ALT ("A -> T,T"), where the
        # two lines agree in all six slots, differ only in their allele index,
        # and carry different per-allele (Number=A) scores.  Comparing slots
        # alone would silently collapse them in a set or a dict, so the allele
        # index takes part in equality (and in ``__hash__``) as well.
        #
        # A plain record tuple is never equal to a VCF line: it has no allele
        # index to compare against, and admitting it would break transitivity
        # (both lines of a repeated ALT would equal the same bare record while
        # differing from each other).  ``False``, not ``NotImplemented``: the
        # latter would let the reflected ``tuple.__eq__`` answer instead.
        if not isinstance(other, VCFLine):
            return False
        return bool(super().__eq__(other)) \
            and self.allele_index == other.allele_index

    def __ne__(self, other: object) -> bool:
        # Python derives ``__ne__`` from ``__eq__`` only when ``__ne__`` is
        # ``object``'s.  ``tuple`` serves both from one rich-comparison slot
        # and so *provides* one, which a subclass that overrides only
        # ``__eq__`` inherits -- leaving ``a != b`` answering structurally
        # (allele index ignored) while ``a == b`` says otherwise.  Spell it
        # out.
        return not self.__eq__(other)

    def __hash__(self) -> int:
        # ``tuple.__hash__`` hashes every slot, and the PAYLOAD slot holds a
        # ``pysam.VariantRecord``, which is unhashable -- so hash the five
        # decoded slots, plus the ``allele_index`` that :meth:`__eq__` also
        # compares.  The contract holds in the direction that matters: equal
        # lines agree in all of these, hence hash equal.  The hash is coarser
        # than equality (it omits the PAYLOAD), which is allowed -- two lines
        # from different variant records that decode to the same five slots
        # and the same allele index collide and are then separated by
        # ``__eq__``.
        return hash((self[CHROM], self[POS_BEGIN], self[POS_END],
                     self[REF], self[ALT], self.allele_index))

    def get(self, key: Key) -> Any:
        """Get a value from the INFO field of the VCF line."""
        assert isinstance(key, str)

        value, meta = self.info.get(key), self.info_meta.get(key)
        if isinstance(value, tuple):
            if meta.number == "A" and self.allele_index is not None:
                value = value[self.allele_index]
            elif meta.number == "R":
                return value[
                    self.allele_index + 1
                    if self.allele_index is not None
                    else 0  # Get reference allele value if ALT is '.'
                ]
            elif meta.number == "." and meta.type == "String":
                return "|".join(value)
        return value

    def row(self) -> tuple:
        return ()


class BigWigLine:
    """Represents a line read from a bigWig file."""

    def __init__(self, raw_line: tuple):
        self._data: tuple[str, int, int, float] = raw_line
        self.chrom: str = self._data[0]
        self.fchrom: str = self._data[0]
        self.pos_begin: int = self._data[1]
        self.pos_end: int = self._data[2]
        self.ref: str | None = None
        self.alt: str | None = None

    def get(self, key: Key) -> str | int | int | float:
        return self._data[key]  # type: ignore

    def row(self) -> tuple:
        return tuple(self._data)


class LineBuffer:
    """Buffer of records read from a Tabix genome position table.

    Holds **records** -- the six-slot tuples the tabular parser builds -- and
    reads them by slot constant (``record[CHROM]``, ``record[POS_BEGIN]``,
    ``record[POS_END]``), never by attribute.  The slots this buffer indexes on
    are immutable -- a record's tuple cells cannot be rebound -- so a buffered
    record can be handed out and retained here at the same time without any
    risk that a later read moves it out from under the positional logic below
    (which the ``Line`` adapter it replaces did allow: the
    zero-based/chrom-mapping transforms rewrote the object in place).

    That promise covers the slots, **not the payload**: the buffer holds the
    same record object the caller got, and a tabix payload is a
    ``pysam.TupleProxy``, which defines ``__setitem__`` -- a caller that writes
    ``record[PAYLOAD][i] = ...`` mutates the row this buffer is holding.  The
    payload is shared by reference on purpose (that is what keeps it lazy); see
    ``record.py``.  Nothing here reads it, so the buffer's own behaviour is
    unaffected either way.

    The semantics are exactly those of the adapter-era buffer: it clears on a
    chromosome change, clears when it observes a non-monotonic ordering
    (:meth:`region`), prunes from the left, and locates a position by binary
    search with a linear back-scan over the equal/overlapping intervals that
    precede the hit.

    The VCF backend feeds this buffer too, via :class:`VCFLine`, which is a
    record-shaped tuple with adapter attributes bolted on; the slot reads below
    are what make that work.
    """

    def __init__(self) -> None:
        self.deque: deque[Record] = deque()

    def __len__(self) -> int:
        return len(self.deque)

    def clear(self) -> None:
        self.deque.clear()

    def append(self, record: Record) -> None:
        if len(self.deque) > 0 \
                and self.peek_first()[CHROM] != record[CHROM]:
            self.clear()
        self.deque.append(record)

    def peek_first(self) -> Record:
        return self.deque[0]

    def pop_first(self) -> Record:
        return self.deque.popleft()

    def peek_last(self) -> Record:
        return self.deque[-1]

    def region(self) -> tuple[str | None, int | None, int | None]:
        """Return region stored in the buffer."""
        if len(self.deque) == 0:
            return None, None, None

        first = self.peek_first()
        last = self.peek_last()

        if first[CHROM] != last[CHROM] \
                or first[POS_END] > last[POS_END]:
            self.clear()
            return None, None, None

        return first[CHROM], first[POS_BEGIN], last[POS_END]

    def prune(self, chrom: str, pos: int) -> None:
        """Prune the buffer if needed."""
        if len(self.deque) == 0:
            return

        first = self.peek_first()

        if chrom != first[CHROM]:
            self.clear()
            return

        while len(self.deque) > 0:
            first = self.peek_first()
            if pos <= first[POS_END]:
                break
            self.deque.popleft()

    def contains(self, chrom: str, pos: int) -> bool:
        bchrom, bbeg, bend = self.region()
        if bchrom is None or bbeg is None or bend is None:
            return False
        return chrom == bchrom and bend >= pos >= bbeg

    def find_index(self, chrom: str, pos: int) -> int:
        """Find index in line buffer that contains the passed position."""
        if len(self.deque) == 0 or not self.contains(chrom, pos):
            return -1

        if len(self.deque) == 1:
            return 0

        first_index = 0
        last_index = len(self.deque) - 1
        while True:
            mid_index = (last_index - first_index) // 2 + first_index
            if last_index <= first_index:
                break

            mid = self.deque[mid_index]
            if mid[POS_END] >= pos >= mid[POS_BEGIN]:
                break

            if pos < mid[POS_BEGIN]:
                last_index = mid_index - 1
            else:
                first_index = mid_index + 1

        while mid_index > 0:
            prev = self.deque[mid_index - 1]
            if pos > prev[POS_END]:
                break
            mid_index -= 1

        for index in range(mid_index, len(self.deque)):
            record = self.deque[index]
            if record[POS_END] >= pos >= record[POS_BEGIN]:
                mid_index = index
                break
            if record[POS_BEGIN] >= pos:
                mid_index = index
                break

        return mid_index

    def fetch(
        self, chrom: str, pos_begin: int, pos_end: int,
    ) -> Generator[Record, None, None]:
        """Return a generator of records matching the region."""
        beg_index = self.find_index(chrom, pos_begin)
        if beg_index == -1:
            return

        for index in range(beg_index, len(self.deque)):
            record = self.deque[index]
            if record[POS_END] < pos_begin:
                continue
            if pos_end is not None and record[POS_BEGIN] > pos_end:
                break
            yield record
