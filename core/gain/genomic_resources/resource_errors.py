"""The exceptions more than one tier must be able to name.

`MalformedResourceError` places the blame on the *resource*: its records or
its configuration do not hold to what its kind can mean. It subclasses
`ValueError` so it falls inside `cli_errors.RESOURCE_ERRORS` by construction,
which is what makes `grr_manage` report it as one attributed line rather than
as an unexpected internal error carrying a traceback (ADR 0008).

The module is a leaf -- it imports nothing from GAIn -- so the score layer,
the table layer and the CLI tier can raise and catch the same exception
without any of them acquiring a dependency on another.

`HistogramError` is here so that `cli_errors.RESOURCE_ERRORS` can name it
without importing the histogram module; `cli_errors` says why it may not.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence


class HistogramError(Exception):
    """A histogram-specific failure of one resource.

    Raised for a categorical histogram past its cardinality limit, which
    the statistics scan catches and nullifies, and for a histogram file
    that cannot be read, which reaches ``grr_manage``'s one-line reporting
    tier.  A plain ``Exception``, not a :class:`MalformedResourceError`,
    because the second case is a fault of the resource's state rather than
    of its records or configuration.
    """


class MalformedResourceError(ValueError):
    """A resource refused because of its own records or configuration.

    Named for the state the resource is in rather than for any one rule, so
    that every refusal a reader could act on the same way -- by fixing the
    resource -- arrives under one name.
    """


def overlapping_records_error(
    resource_id: str, chrom: str, pos: int, prev_end: int,
) -> MalformedResourceError:
    """Refuse a position score whose record claims a position already taken.

    Built here rather than at either raise site because two paths detect this
    one rule -- the per-record region read and the vectorized statistics scan
    -- and a reader who meets the message from one of them must not have to
    wonder whether the other words it differently.
    """
    return MalformedResourceError(
        f"<{resource_id}> is malformed: multiple values for positions "
        f"{chrom}:{pos}, which overlaps the preceding record ending at "
        f"{chrom}:{prev_end}; a position score allows at most one record "
        f"per position")


def backwards_records_error(
    resource_id: str, chrom: str, pos: int, prev_pos: int, kind: str,
) -> MalformedResourceError:
    """Refuse a score whose records move backwards along a contig.

    The sibling of :func:`overlapping_records_error`, and here for the same
    reason.  The position rule has been built from one helper since it was
    detected on two paths; this rule is detected on two paths for each of TWO
    kinds, and was written out at all four sites -- six copies of one
    sentence, agreeing only for as long as nobody edited one of them.

    ``kind`` is the possessive naming the kind whose promise was broken
    ("a fragment score's"), so the message still says which validator fired.
    It is passed by each raise site rather than read off a shared class
    attribute: an attribute two validators interpret for themselves is what
    ADR 0008 unwound, and a *message* fragment is not a rule.
    """
    return MalformedResourceError(
        f"<{resource_id}> is malformed: the record at {chrom}:{pos} follows "
        f"the record at {chrom}:{prev_pos}; {kind} records must not move "
        f"backwards")


def score_configuration_error(
    resource_id: str, score_id: str, detail: str,
) -> MalformedResourceError:
    """Refuse a score whose DEFINITION says what the score cannot be.

    The sibling of :func:`overlapping_records_error` for the other half of
    what makes a resource malformed: not a record that breaks its kind's
    promise, but a definition claiming a value the score cannot hold.
    The rules live in three layers, and each raise site phrases its own
    ``detail``: the VCF header/config merge (the ``_refuse_*`` helpers in
    ``vcf_scores``, for an ``id``, an address or a ``type:`` the header
    contradicts, and for a field declared ``Number=G``), the construction
    convergence point (``refuse_unfoldable_histograms``, for a number
    histogram over a value type none accumulates, and
    ``refuse_unbuildable_aggregators``, for an ``aggregator:`` no
    accumulator can be built from), and open, once a tabular
    table's header is known (``validate_scoredefs``, for a column address
    the header cannot honour, and ``resolve_score_indices``, which holds
    the DEFINITIONS -- not the config -- to the same address rules as it
    resolves each one to its payload column, and refuses a VCF definition
    with no INFO key).  The ``Number=G`` rule is the one a
    ``scores:`` entry need not have caused: a header-only resource has
    none, and the claim is the header's.

    What is shared is the ADDRESS: which resource, and which score in it.
    That is the half a reader needs to find the file to edit, it is the half
    no raise site can word differently without sending someone to the
    wrong place, and it is why it is built here rather than at each site.

    Not built here, on purpose: a refusal of a REQUEST -- a score id the
    resource does not define, an aggregator a caller names that does not
    build, a ``bool`` score asked for with no aggregator named, a
    ``none_value_replacement`` of the wrong type.  Those fire at read time
    against a definition that is fine, so the resource's config is not the
    file to edit, and this prefix would send the reader there.  They carry
    the other address, ``score '<id>' … resource '<resource>'``: the
    undefined score id is worded by :func:`undefined_scores_message`, the
    aggregator refusals in
    :mod:`~gain.genomic_resources.genomic_scores.aggregation` and the
    replacement refusal on the position score.

    The prefix matches ``ResourceConfigValidationMixin`` so that a caller
    reading a config error sees one wording; the TYPE is
    :class:`MalformedResourceError`, so a caller that already catches "this
    resource's own config is bad" by type catches these too rather than
    matching on a string.
    """
    return MalformedResourceError(
        f"Invalid configuration: {resource_id}: score {score_id!r} {detail}")


def undefined_scores_message(
    resource_id: str, unknown: Iterable[str], defined: Iterable[str],
) -> str:
    """State that a caller named scores the resource does not define.

    The one sentence for this mistake, whichever surface a caller reaches it
    through: an aggregation request, a read, a histogram accessor a gene
    score shares, a filter expression.  It is a refusal of the REQUEST, so it
    carries the request address, not :func:`score_configuration_error`'s
    prefix: the resource's config is fine, and the caller's list is the
    thing to fix.

    Returned as text rather than as an exception because the surfaces raise
    different types -- ``ValueError`` from the score API,
    ``ScoreFilterError`` from a filter -- and a caller catching either by
    type must keep doing so.

    One unknown id and several read as one sentence and its plural.  Both
    lists are sorted and ``unknown`` is deduplicated, so the message does not
    depend on the order a caller listed its ids in, and it names what would
    have worked, since a typo is the usual cause.
    """
    names = sorted(set(unknown))
    subject = (
        f"score {names[0]!r} is" if len(names) == 1
        else f"scores {names} are")
    return (
        f"{subject} not defined by resource {resource_id!r}; it has "
        f"{sorted(defined)}")


def vcf_header_file_error(
    resource_id: str, header_filename: str, detail: str,
) -> MalformedResourceError:
    """Refuse a VCF table whose ``*.header.vcf.gz`` sidecar is not a header.

    The sidecar is read line by line (gain#1406), and two things can be
    wrong with it: no ``##`` line at all, or a line pysam cannot parse --
    each phrased by its raise site as ``detail``. What is shared is the
    ADDRESS, the resource and the file, built here for the reason
    :func:`score_configuration_error` gives.
    """
    return MalformedResourceError(
        f"<{resource_id}> is malformed: its header file {header_filename} "
        f"{detail}")


def inverted_span_error(
    chrom: str, pos_begin: int, pos_end: int,
    ref: str | None, alt: str | None,
) -> OSError:
    """Refuse a single record whose end precedes its begin.

    Returned rather than raised, so the raise stays at the site that read the
    slots -- and so the several sites that perform this check share one
    message.  Off the hot path by construction: a caller compares two integers
    per record and only calls this when the comparison fails.

    Not to be confused with :func:`backwards_records_error`, despite the
    neighbouring vocabulary: that one refuses a resource whose records move
    backwards *along a contig*, raises :class:`MalformedResourceError`, and
    belongs to the scan's validation.  This one is about a single record's own
    two ends, and stays an ``OSError`` -- the type the read path has always
    raised for it, and the type the tests pin.

    Takes the five decoded values rather than the record they came from, for
    two reasons.  Reading the slots here would mean importing them from
    ``genomic_position_table.record``, whose package ``__init__`` imports
    ``table_tabix``, which imports this module -- a cycle, and the end of this
    module's leaf status (see the module docstring).  And a record's last slot
    is the backend's payload, so a helper handed the whole record must be
    careful never to interpolate it: ``f"{record}"`` would print an entire
    ``pysam.VariantRecord`` -- whose repr is the whole VCF line -- or a
    ``TupleProxy``.  Named values cannot make that mistake.

    ``ref`` and ``alt`` are what tell two records at one position apart, so
    the message names them when the record carries them, and says nothing in
    their place when it does not (ADR 0027).
    """
    ref_alt = f" {ref}->{alt}" if ref is not None or alt is not None else ""
    return OSError(
        f"The resource record {chrom}:{pos_begin}-{pos_end}{ref_alt} "
        f"has a region with end {pos_end} smaller than the "
        f"beginning {pos_begin}.")


def index_column_mismatch_error(
    resource_id: str,
    index_filename: str,
    mismatches: Sequence[tuple[str, int, int]],
    *,
    end_is_implied: bool = False,
) -> MalformedResourceError:
    """Refuse a tabix table whose index was built over other columns.

    Each entry of ``mismatches`` is a field, the column its index was built
    over, and the column its table *resolves* to -- resolves, not states: a
    coordinate no configuration and no header names is read through a
    hardcoded fallback, and a fallback the index disagrees with splits the
    read from the filter exactly as a contradictory config entry does.  Both
    columns are named because the remedy is a one-line edit and a reader
    cannot write it from either number alone.  ``end_is_implied`` says the
    index records no end column at all, so its end column is its begin column
    by definition rather than by anyone's choice.

    Built here rather than at the raise site for the same reason
    :func:`overlapping_records_error` is: the rule is one rule, and it must
    read identically wherever it is reported.
    """
    fields = "; ".join(
        f"{field} is indexed on column {indexed} but the configuration "
        f"resolves it to column {configured}"
        for field, indexed, configured in mismatches
    )
    implied_note = (
        " The index records no end column, so every record ends where it "
        "begins as far as the index is concerned."
    ) if end_is_implied else ""
    return MalformedResourceError(
        f"<{resource_id}> is malformed: its table is configured over columns "
        f"its index {index_filename} was not built from: {fields}."
        f"{implied_note} A region query is filtered by the indexed columns "
        f"and read through the configured ones, so a record the index returns "
        f"is dropped without a trace, and a record the configured span "
        f"reaches over is never fetched at all. Rebuild the index over the "
        f"configured columns, or configure the table with the indexed ones.")
