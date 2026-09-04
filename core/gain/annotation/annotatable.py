from __future__ import annotations

import enum
from abc import abstractmethod


class Annotatable:
    """Base class for annotatables used in annotation pipeline.

    An annotatable is the thing a pipeline annotates: a position, a
    region or an allele on one chromosome.  Every annotatable spans a
    closed, 1-based interval ``[pos, pos_end]`` -- both ends inclusive,
    so a :class:`Position` has ``pos_end == pos`` and
    ``len(annotatable)`` is ``pos_end - pos + 1``.  :class:`VCFAllele`
    says how it derives ``pos_end`` from its alleles.

    The canonical spellings are ``chrom``, ``pos`` and ``pos_end`` --
    the constructor's names and the keys :meth:`to_dict` writes.
    ``chromosome``, ``position`` and ``end_position`` are aliases kept
    for the callers that use them; each reads the same value as its
    twin.  Equality compares the type, the chromosome and both ends.
    """

    class Type(enum.Enum):
        """Defines annotatable types."""

        POSITION = 0
        REGION = 1

        SUBSTITUTION = 2
        SMALL_INSERTION = 3
        SMALL_DELETION = 4
        COMPLEX = 5

        LARGE_DUPLICATION = 6
        LARGE_DELETION = 7

        @staticmethod
        def from_string(variant: str) -> Annotatable.Type:
            """Construct annotatable type from string argument."""
            # pylint: disable=too-many-return-statements
            vtype = variant.lower()
            if vtype == "position":
                return Annotatable.Type.POSITION
            if vtype == "region":
                return Annotatable.Type.REGION
            if vtype == "substitution":
                return Annotatable.Type.SUBSTITUTION
            if vtype == "small_insertion":
                return Annotatable.Type.SMALL_INSERTION
            if vtype == "small_deletion":
                return Annotatable.Type.SMALL_DELETION
            if vtype == "complex":
                return Annotatable.Type.COMPLEX
            if vtype == "large_duplication":
                return Annotatable.Type.LARGE_DUPLICATION
            if vtype == "large_deletion":
                return Annotatable.Type.LARGE_DELETION
            raise ValueError(f"unexpected annotatable type: {variant}")

    def __init__(
        self, chrom: str, pos: int, pos_end: int,
        annotatable_type: Annotatable.Type,
    ):
        self._chrom = chrom
        self._pos = pos
        self._pos_end = pos_end
        self.type = annotatable_type

    @property
    def chrom(self) -> str:
        """The chromosome name, as given at construction."""
        return self._chrom

    @property
    def chromosome(self) -> str:
        """Alias of :attr:`chrom`."""
        return self._chrom

    @property
    def pos(self) -> int:
        """The 1-based start of the interval, inclusive."""
        return self._pos

    @property
    def position(self) -> int:
        """Alias of :attr:`pos`."""
        return self._pos

    @property
    def end_position(self) -> int:
        """Alias of :attr:`pos_end`."""
        return self._pos_end

    @property
    def pos_end(self) -> int:
        """The 1-based end of the interval, inclusive.

        Equal to :attr:`pos` for a single position; see the class
        docstring for the convention and :class:`VCFAllele` for how an
        allele's end is derived.
        """
        return self._pos_end

    def __len__(self) -> int:
        return self._pos_end - self._pos + 1

    def __repr__(self) -> str:
        raise NotImplementedError

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Annotatable):
            return False
        return self.type == other.type and self.chrom == other.chrom and \
            self.pos == other.pos and self.pos_end == other.pos_end

    @staticmethod
    def tokenize(value: str) -> tuple[str, list[str]]:
        """Split the serialized form ``TYPE(arg1, arg2, ...)`` into its parts.

        Returns the type token and the list of argument tokens, with
        whitespace stripped from the arguments.  Raises ``ValueError``
        for a value that is not exactly one call-like expression.  The
        inverse of ``__repr__``; :meth:`from_string` dispatches on the
        type token and the concrete classes parse the arguments.
        """
        # value := TYPE(arg1, arg2, ...)
        tokens = value.split("(")
        if len(tokens) != 2:
            raise ValueError("Attempted to tokenize invalid input - ", value)
        return tokens[0], tokens[1].rstrip(")").replace(" ", "").split(",")

    @staticmethod
    def from_string(value: str) -> Annotatable:
        """Deserialize an Annotatable instance from a string value."""
        a_type, _ = Annotatable.tokenize(value)
        if a_type in ("Position", "POSITION"):
            return Position.from_string(value)
        if a_type in ("Region", "REGION"):
            return Region.from_string(value)
        if a_type in ("VCFAllele", "SUBSTITUTION", "COMPLEX",
                      "SMALL_DELETION", "SMALL_INSERTION"):
            return VCFAllele.from_string(value)
        if a_type in ("CNVAllele", "LARGE_DUPLICATION", "LARGE_DELETION"):
            return CNVAllele.from_string(value)
        raise ValueError("No matching Annotatable type found for: ", value)

    @abstractmethod
    def to_dict(self) -> dict:
        """Serialize the annotatable to a dictionary."""
        raise NotImplementedError


class Position(Annotatable):
    """Annotatable class representing a single position in a chromosome."""

    def __init__(self, chrom: str, pos: int):
        super().__init__(
            chrom, pos, pos, Annotatable.Type.POSITION)

    def __repr__(self) -> str:
        return f"Position({self.chrom},{self.pos})"

    def __str__(self) -> str:
        return f"{self.chrom}:{self.pos}"

    @staticmethod
    def from_string(value: str) -> Position:
        a_type, args = Annotatable.tokenize(value)
        if a_type not in ("Position", "POSITION"):
            raise ValueError
        if len(args) != 2:
            raise ValueError
        return Position(args[0], int(args[1]))

    def to_dict(self) -> dict:
        return {
            "type": self.type.name,
            "chrom": self.chrom,
            "pos": self.pos,
        }


class Region(Annotatable):
    """Annotatable class representing a region in a chromosome."""

    def __init__(self, chrom: str, pos_begin: int, pos_end: int):
        super().__init__(
            chrom, pos_begin, pos_end, Annotatable.Type.REGION)

    def __repr__(self) -> str:
        return f"Region({self.chrom},{self.pos},{self.pos_end})"

    def __str__(self) -> str:
        return f"{self.chrom}:{self.pos}-{self.pos_end}"

    @staticmethod
    def from_string(value: str) -> Region:
        a_type, args = Annotatable.tokenize(value)
        if a_type not in ("Region", "REGION"):
            raise ValueError
        if len(args) != 3:
            raise ValueError
        return Region(args[0], int(args[1]), int(args[2]))

    def to_dict(self) -> dict:
        return {
            "type": self.type.name,
            "chrom": self.chrom,
            "pos_begin": self.pos,
            "pos_end": self.pos_end,
        }


class VCFAllele(Annotatable):
    """A small variant in VCF terms: chrom, pos, ref and alt.

    The alleles decide both the :class:`Annotatable.Type` and the
    interval:

    - one base to one base is a ``SUBSTITUTION``, spanning ``pos``
      alone;
    - a one-base reference that the alternative extends (same first
      base) is a ``SMALL_INSERTION``, spanning ``pos`` to ``pos + 1``
      -- the two bases the insertion falls between;
    - a reference longer than one base collapsed to its first base is
      a ``SMALL_DELETION``, and any other pair is ``COMPLEX``; both
      span ``pos`` to ``pos + len(ref)``.

    So for a deletion or a complex allele ``pos_end`` reaches one base
    past the last reference base, which sits at ``pos + len(ref) - 1``.
    Annotators query exactly this span.

    The canonical spellings are ``ref`` and ``alt``; ``reference`` and
    ``alternative`` are aliases.  Equality also compares both alleles.
    """

    def __init__(self, chrom: str, pos: int, ref: str, alt: str):
        assert ref is not None
        assert alt is not None

        self._ref = ref
        self._alt = alt

        allele_type = None
        if len(ref) == 1 and len(alt) == 1:
            allele_type = Annotatable.Type.SUBSTITUTION
            pos_end = pos
        elif len(ref) == 1 and len(alt) > 1 and ref[0] == alt[0]:
            allele_type = Annotatable.Type.SMALL_INSERTION
            pos_end = pos + 1
        elif len(ref) > 1 and len(alt) == 1 and ref[0] == alt[0]:
            allele_type = Annotatable.Type.SMALL_DELETION
            pos_end = pos + len(ref)
        else:
            allele_type = Annotatable.Type.COMPLEX
            pos_end = pos + len(ref)

        super().__init__(chrom, pos, pos_end, allele_type)

    @property
    def ref(self) -> str:
        """The reference allele as written in VCF, anchor base included."""
        return self._ref

    @property
    def reference(self) -> str:
        """Alias of :attr:`ref`."""
        return self._ref

    @property
    def alt(self) -> str:
        """The alternative allele as written in VCF, anchor base included."""
        return self._alt

    @property
    def alternative(self) -> str:
        """Alias of :attr:`alt`."""
        return self._alt

    def __repr__(self) -> str:
        return (
            f"VCFAllele({self.chrom},{self.pos}"
            f",{self.ref},{self.alt})"
        )

    def __str__(self) -> str:
        return f"{self.chrom}:{self.pos} {self.ref}>{self.alt}"

    def __eq__(self, other: object) -> bool:
        if not super().__eq__(other):
            return False
        if not isinstance(other, VCFAllele):
            return False
        return self.ref == other.ref and self.alt == other.alt

    @staticmethod
    def from_string(value: str) -> VCFAllele:
        """Deserialize a ``VCFAllele`` from its ``__repr__`` form.

        Accepts ``VCFAllele(chrom, pos, ref, alt)``, and the same four
        arguments under any small-variant type name (``SUBSTITUTION``,
        ``SMALL_INSERTION``, ``SMALL_DELETION``, ``COMPLEX``).  Raises
        ``ValueError`` for another type token or argument count.  The
        type is re-derived from the alleles, not taken from the token.
        """
        a_type, args = Annotatable.tokenize(value)
        if a_type not in ("VCFAllele", "SUBSTITUTION", "COMPLEX",
                          "SMALL_DELETION", "SMALL_INSERTION"):
            raise ValueError
        if len(args) != 4:
            raise ValueError
        return VCFAllele(args[0], int(args[1]), args[2], args[3])

    def to_dict(self) -> dict:
        """Serialize to ``type``, ``chrom``, ``pos``, ``ref`` and ``alt``.

        ``type`` is the type's name.  ``pos_end`` is not written: it is
        re-derived from the alleles.
        """
        return {
            "type": self.type.name,
            "chrom": self.chrom,
            "pos": self.pos,
            "ref": self.ref,
            "alt": self.alt,
        }


class CNVAllele(Annotatable):
    """Defines copy number variants annotatable."""

    def __init__(
        self, chrom: str, pos_begin: int, pos_end: int,
        cnv_type: Annotatable.Type,
    ):
        assert cnv_type in {
            Annotatable.Type.LARGE_DELETION,
            Annotatable.Type.LARGE_DUPLICATION}, cnv_type

        super().__init__(chrom, pos_begin, pos_end, cnv_type)

    def __repr__(self) -> str:
        return f"CNVAllele({self.chrom},{self.pos},{self.pos_end},{self.type})"

    @staticmethod
    def from_string(value: str) -> CNVAllele:
        a_type, args = Annotatable.tokenize(value)
        if a_type == "CNVAllele":
            if len(args) != 4:
                raise ValueError
            cnv_type = Annotatable.Type.from_string(args[3])
        elif a_type in ("LARGE_DUPLICATION", "LARGE_DELETION"):
            if len(args) != 3:
                raise ValueError
            cnv_type = Annotatable.Type.from_string(a_type)
        else:
            raise ValueError
        return CNVAllele(args[0], int(args[1]), int(args[2]), cnv_type)

    def to_dict(self) -> dict:
        return {
            "type": self.type.name,
            "chrom": self.chrom,
            "pos_begin": self.pos,
            "pos_end": self.pos_end,
        }
