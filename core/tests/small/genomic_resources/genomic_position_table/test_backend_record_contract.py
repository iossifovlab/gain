"""Every backend must actually yield what its ``yields_records`` claims.

A position-table backend makes exactly one class-level claim about the shape of
the things it yields: the ``yields_records`` ClassVar.  A backend whose claim
disagrees with what it really yields is mis-wired, and the score layer would
read a raw row out of something that is not one.

Whether a backend yields records is a property of the **backend**, not of a
row: one shape of thing, for every line, forever.  So it is answerable once,
here, against all four backends -- with no runtime cost in the fetch path.
This test is what lets the score layer route on the claim and simply believe
it.

``GenomicScore.open`` makes the matching decision, also once per table, and it
turns on what a record's PAYLOAD means -- which is whatever the backend that
built it says it means:

* a record whose payload is a raw tabular row (in-memory, tabix) or the
  four-element interval of a bigWig line is wrapped in
  :class:`RecordScoreLine`, which reads score columns out of it by index;
* a **VCF** record, whose payload is a ``(variant record, allele index)`` pair,
  is wrapped in :class:`VCFScoreLine`, which looks INFO fields up by name and
  selects them by allele.

#238 migrated bigWig -- the last adapter backend -- and #239 then deleted the
line adapters, the ``LineBase`` protocol and the adapter-era ``ScoreLine``
outright.  So ``yields_records`` no longer selects between two shapes: records
are the only shape, and a backend that leaves the flag False selects no score
line at all -- ``GenomicScore.open`` refuses to open it.  That is why
test_a_backend_yields_what_its_yields_records_claim_says now *asserts* the
claim rather than branching on it, and why the fixtures below hand back an
**unopened** score: the flag is a ClassVar, so the claim can be -- and is --
checked before open() gets to reject it, which is what keeps a failing backend
pointed at this file rather than at a routing TypeError.

So each backend below declares BOTH what it yields and which score line it must
be routed to, and both are checked against the live objects.

**This is the file a backend->records migration trips** -- #237 (VCF) and #238
(bigWig) both did.  Flipping ``yields_records`` on a backend without migrating
what it yields fails here, naming the backend -- the one moment this catches it.
"""
from __future__ import annotations

import pathlib
from collections.abc import Callable

import numpy as np
import pytest
from gain.genomic_resources.genomic_position_table.record import (
    PAYLOAD,
    RECORD_SLOTS,
    sort_key,
)
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    GenomicScore,
    PositionScore,
    RecordScoreLine,
    ScoreLineBase,
    VCFScoreLine,
)
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_grr,
    a_position_score,
    a_vcf_info_score,
)

# A region each backend's fixture data answers with at least one line.
#
# The score comes back UNOPENED, and each test opens it itself.  That is what
# lets a test look at ``table.yields_records`` -- a ClassVar, known at
# construction -- *before* ``GenomicScore.open`` gets to route on it and refuse.
# Built the other way round, with the helper opening, every check of the claim
# below would sit downstream of the open() that already rejects a backend
# leaving it False, and so could never run.
Region = tuple[str, int, int]
Backend = tuple[GenomicScore, Region]


def _build_tabular(tmp_path: pathlib.Path, *, tabix: bool) -> Backend:
    builder = (
        a_position_score()
        .with_score("s_float", "float")
        .with_data("""
            chrom  pos_begin  s_float
            1      10         0.5
        """)
    )
    if tabix:
        builder = builder.with_tabix()
    repo = a_grr().with_resource("pos", builder).build_repo(tmp_path)
    return PositionScore(repo.get_resource("pos")), ("1", 10, 10)


def _build_inmemory(tmp_path: pathlib.Path) -> Backend:
    return _build_tabular(tmp_path, tabix=False)


def _build_tabix(tmp_path: pathlib.Path) -> Backend:
    return _build_tabular(tmp_path, tabix=True)


def _build_vcf(tmp_path: pathlib.Path) -> Backend:
    builder = a_vcf_info_score().with_data("""
##fileformat=VCFv4.1
##INFO=<ID=scoreA,Number=1,Type=Float,Description="score A">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   10  .  A   T   .    .      scoreA=0.1
""")
    repo = a_grr().with_resource("vcf", builder).build_repo(tmp_path)
    return AlleleScore(repo.get_resource("vcf")), ("chr1", 10, 10)


def _build_bigwig(tmp_path: pathlib.Path) -> Backend:
    builder = (
        a_bigwig_score()
        .with_score("bw", "float")
        .with_data("""
            chr1  0   10  0.11
        """)
        .with_chrom_lens({"chr1": 1000})
    )
    repo = a_grr().with_resource("bw", builder).build_repo(tmp_path)
    return PositionScore(repo.get_resource("bw")), ("chr1", 5, 5)


# All four position-table backends, each opened over a resource of its own
# format, paired with the score line class ``GenomicScore.open`` must route it
# to.  Every backend in the tree is here: a fifth one must be added, or nothing
# checks that its claim is true.
_BACKENDS: list[pytest.param] = [  # type: ignore[valid-type]
    pytest.param(_build_inmemory, RecordScoreLine, id="inmemory"),
    pytest.param(_build_tabix, RecordScoreLine, id="tabix"),
    pytest.param(_build_vcf, VCFScoreLine, id="vcf"),
    pytest.param(_build_bigwig, RecordScoreLine, id="bigwig"),
]


# Whether a record this backend yields can be HASHED -- put in a set, or used
# as a dict key.  It is a per-backend fact, not a property of the record
# contract, and that is exactly why it is declared here: a record is a plain
# tuple, so ``hash(record)`` walks the tuple -- straight into the PAYLOAD,
# whose hashability belongs to the backend that built it.  Only ONE of the
# three record backends gives it:
#
#   * in-memory -- payload is a ``tuple[str, ...]``: hashes;
#   * tabix -- payload is a ``pysam.TupleProxy``, which defines ``__eq__`` and
#     so has ``__hash__ = None``: raises ``TypeError``;
#   * VCF -- payload is a ``(pysam.VariantRecord, allele index)`` pair, and a
#     ``pysam.VariantRecord`` is unhashable for the same reason, so hashing the
#     pair -- and so the record -- raises ``TypeError``;
#   * bigWig -- payload is a plain ``(chrom, pos_begin, pos_end, value)`` tuple
#     of a str, two ints and a float, all hashable: hashes.
#
# test_every_record_backend_declares_whether_its_records_hash keeps this list
# in step with what the backends in _BACKENDS actually claim.
_HASHABILITY: list[pytest.param] = [  # type: ignore[valid-type]
    pytest.param(_build_inmemory, True, id="inmemory"),
    pytest.param(_build_tabix, False, id="tabix"),
    pytest.param(_build_vcf, False, id="vcf"),
    pytest.param(_build_bigwig, True, id="bigwig"),
]


def test_every_record_backend_declares_whether_its_records_hash(
    tmp_path: pathlib.Path,
) -> None:
    """A backend that yields records must say whether those records hash.

    The answer is its payload's, and a caller cannot read it off the record
    contract -- so a record backend that does not declare it leaves
    test_a_records_hashability_is_its_payloads with nothing to check.

    Which backends must declare is asked of the **tables themselves**:
    ``yields_records`` is the claim this whole file exists to hold backends to,
    and it is the same discriminator ``GenomicScore.open`` routes on.  Asking a
    built-but-unopened table is deliberate -- the flag is a ClassVar, so it
    needs no open handle, and a backend that leaves it False is one
    ``GenomicScore.open`` refuses outright.  Opening first would mean this loop
    could only ever see backends that already passed that gate.  It is
    deliberately NOT read off the score line class each backend is paired with
    below: a migrating backend can arrive with a score line class of its own
    (VCF did, at :class:`VCFScoreLine`) or reuse an existing one whose
    hashability differs from every backend already routed there (bigWig, #238,
    reuses :class:`RecordScoreLine` but -- unlike tabix, the other backend
    routed there -- yields hashable records), and a check written against the
    score line classes rather than the tables would miss both.  Ask the table,
    and there is nothing to add to but _HASHABILITY.
    """
    record_backends = set()
    for param in _BACKENDS:
        build_backend, _score_line_cls = param.values
        # A repo per backend: two of them build a resource under the same name.
        backend_dir = tmp_path / str(param.id)
        backend_dir.mkdir()
        score, _region = build_backend(backend_dir)
        if score.table.yields_records:
            record_backends.add(str(param.id))

    declared = {str(param.id) for param in _HASHABILITY}

    undeclared = record_backends - declared
    assert not undeclared, (
        f"{sorted(undeclared)} now set yields_records, so each yields plain "
        f"record tuples -- but none of them declares whether those records "
        f"hash. A record's hash walks the tuple straight into its PAYLOAD, so "
        f"the answer is the backend's alone to give: add it to _HASHABILITY "
        f"(and test_a_records_hashability_is_its_payloads will hold you to it)")

    stale = declared - record_backends
    assert not stale, (
        f"{sorted(stale)} declare their records' hashability in _HASHABILITY "
        f"but no longer set yields_records, so they no longer yield records "
        f"whose hashability is a fact about them. Drop them from _HASHABILITY")


@pytest.mark.parametrize(("build_backend", "records_hash"), _HASHABILITY)
def test_a_records_hashability_is_its_payloads(
    tmp_path: pathlib.Path,
    build_backend: Callable[[pathlib.Path], Backend],
    records_hash: bool,
) -> None:
    """A record hashes exactly when its backend's payload does.

    The record contract (record.py) says so, and this is what pins it.  The
    decoded half of a record always hashes -- ``sort_key`` projects it, and
    that projection is a key a caller can always take -- but the record as a
    whole is a tuple with the payload inside it, so its hash is the payload's
    to give or to withhold.  A caller that wants a set of records, or a dict
    keyed by one, must key it on ``sort_key(record)`` and not on the record --
    on two of the three record backends the record itself raises.
    """
    score, region = build_backend(tmp_path)
    with score.open():
        records = list(score.table.get_records_in_region(*region))
        first = records[0]
        backend = type(score.table).__name__

        # The decoded half always hashes, whatever the backend.
        assert hash(sort_key(first)) == hash(sort_key(first))

        if records_hash:
            by_record = {record: i for i, record in enumerate(records)}
            assert by_record[first] == 0, (
                f"{backend} records are declared hashable but do not work as "
                f"dict keys")
        else:
            with pytest.raises(TypeError, match="unhashable"):
                hash(first)
            with pytest.raises(TypeError, match="unhashable"):
                _ = {first: 0}


@pytest.mark.parametrize(("build_backend", "score_line_cls"), _BACKENDS)
def test_a_backend_yields_what_its_yields_records_claim_says(
    tmp_path: pathlib.Path,
    build_backend: Callable[[pathlib.Path], Backend],
    score_line_cls: type[ScoreLineBase],
) -> None:
    score, region = build_backend(tmp_path)
    table = score.table
    backend = type(table).__name__

    # Records are the only shape there is.  #239 deleted the line adapters and
    # the ``ScoreLine`` that read them, so a backend leaving ``yields_records``
    # False no longer selects an alternative implementation -- it selects none,
    # and ``GenomicScore.open`` refuses to open it (TypeError).
    #
    # This runs BEFORE the open() below, which is the whole point: it is the
    # requirement stated where a new backend meets it, rather than an open()
    # failure it has to work backwards from.  The score arrives unopened for
    # exactly this reason -- ``yields_records`` is a ClassVar, so the claim is
    # answerable with no handle at all, and asserting it here means a backend
    # that leaves it False fails naming itself and saying what to do, instead of
    # tripping open()'s routing TypeError first.
    assert table.yields_records, (
        f"{backend} leaves yields_records False. Since #239 there is no "
        f"line-adapter score line to be routed to, so a backend must set "
        f"yields_records and yield records; GenomicScore.open raises on "
        f"one that does not")

    # The claim, against the first thing the backend actually produces --
    # which is the earliest moment a claim about records can be contradicted.
    with score.open():
        first = next(iter(table.get_records_in_region(*region)))

        # A record is a PLAIN tuple -- exact type, not isinstance: a tuple
        # *subclass* with attributes bolted on (which is what the retired
        # VCFLine bridge was) is an adapter wearing a record's shape, and an
        # isinstance check would wave it through.
        assert type(first) is tuple, (
            f"{backend} sets yields_records, so GenomicScore.open routes "
            f"it to a record score line -- but it yields a "
            f"{type(first).__name__}, not a plain record tuple")
        assert len(first) == RECORD_SLOTS, (
            f"{backend} sets yields_records but yields a "
            f"{len(first)}-slot tuple; a record has {RECORD_SLOTS} slots")
        payload = first[PAYLOAD]
        # Both record score lines index the payload -- RecordScoreLine binds
        # _get_raw to payload.__getitem__ for a score column, VCFScoreLine
        # reads the (variant, allele index) pair out of it -- so a payload
        # must be indexable...
        assert hasattr(payload, "__getitem__"), (
            f"{backend} sets yields_records but its record's PAYLOAD is a "
            f"{type(payload).__name__}, which is not indexable")
        # ...and must not be a str/bytes: those are indexable, but index
        # to *characters*, so every score would silently parse to None
        # rather than raise.
        assert not isinstance(payload, (str, bytes)), (
            f"{backend} sets yields_records but its record's PAYLOAD is a "
            f"{type(payload).__name__} -- indexing it yields characters, "
            f"not cells")


@pytest.mark.parametrize(("build_backend", "score_line_cls"), _BACKENDS)
def test_open_routes_a_backend_to_the_score_line_its_payload_needs(
    tmp_path: pathlib.Path,
    build_backend: Callable[[pathlib.Path], Backend],
    score_line_cls: type[ScoreLineBase],
) -> None:
    # The other half: ``GenomicScore.open`` must route each backend to the score
    # line that can actually read ITS payload.  Together with the test above --
    # the claim about what is yielded is true -- this is what makes the routing
    # correct for every backend, without any per-line check.
    score, region = build_backend(tmp_path)
    with score.open():
        # The choice is made once, at open: it is already installed before a
        # single line is fetched.
        assert score._score_line_class is score_line_cls, (
            f"{type(score.table).__name__} (yields_records="
            f"{score.table.yields_records}) was routed at open to "
            f"{score._score_line_class.__name__}, "
            f"not {score_line_cls.__name__}")

        line = next(iter(score.fetch_lines(*region)))
        assert type(line) is score_line_cls, (
            f"{type(score.table).__name__} yields_records="
            f"{score.table.yields_records} was routed to "
            f"{type(line).__name__}")
        # ...and the routed score line can actually read a score through it --
        # which is what fails if a backend is routed to a score line whose raw
        # lookup does not fit its payload.
        score_id = next(iter(score.get_all_scores()))
        assert line.get_score(score_id) is not None


# Whether this backend serves ``get_region_value_arrays`` -- the OPTIONAL bulk
# column-array read (gain#398).  Unlike ``yields_records`` this has a genuine
# False state: a backend that does not implement the fast path is in no way
# broken, it simply keeps the record read.
#
#   * tabix -- reads raw rows straight from pysam and serves columns by
#     integer payload index: True;
#   * bigWig -- turns each adaptive-window interval chunk into arrays: True;
#   * in-memory -- no implementation of its own: False;
#   * VCF -- INHERITS tabix's implementation but sets the flag back to False,
#     because its PAYLOAD is (variant, allele index) rather than a raw row and
#     its scores are INFO fields addressed by name, not by column index. This
#     is the case the flag exists for: the capability is NOT derivable from the
#     class hierarchy, so it has to be declared.
#
# test_a_backend_serves_value_arrays_exactly_when_it_claims_to holds every
# backend to its entry in BOTH directions.
_VALUE_ARRAYS: list[pytest.param] = [  # type: ignore[valid-type]
    pytest.param(_build_inmemory, False, id="inmemory"),
    pytest.param(_build_tabix, True, id="tabix"),
    pytest.param(_build_vcf, False, id="vcf"),
    pytest.param(_build_bigwig, True, id="bigwig"),
]


def test_every_backend_declares_whether_it_serves_value_arrays() -> None:
    """_VALUE_ARRAYS must cover every backend, so a fifth cannot slip in.

    Same guard as the hashability list above: a new backend that is added to
    _BACKENDS but not here would never have its claim checked against its
    behaviour, and the flag's whole job is to be checkable.
    """
    assert {str(param.id) for param in _VALUE_ARRAYS} == \
        {str(param.id) for param in _BACKENDS}


@pytest.mark.parametrize(("build_backend", "serves_arrays"), _VALUE_ARRAYS)
def test_a_backend_serves_value_arrays_exactly_when_it_claims_to(
    build_backend: Callable[[pathlib.Path], Backend],
    serves_arrays: bool,
    tmp_path: pathlib.Path,
) -> None:
    """The claim and the behaviour, held together in both directions.

    A backend that claims support must produce arrays that agree with its own
    record read; one that does not claim it must refuse cleanly, with a
    ``TypeError`` -- and not, say, trip an assert deep in an inherited fetch.
    """
    score, (chrom, beg, end) = build_backend(tmp_path)

    # The claim is read off the UNOPENED table: it is a ClassVar, and the
    # score-level query is answerable without opening the file.
    assert score.table.supports_value_arrays is serves_arrays
    # Every fixture score here is float, so the query's value-type half
    # is satisfied and the backend is what decides.
    assert score.supports_region_value_arrays(
        list(score.score_definitions)) is serves_arrays

    with score.open() as opened:
        score_id = opened.get_all_scores()[0]
        if not serves_arrays:
            with pytest.raises(TypeError, match="supports_region_value_arrays"):
                list(opened.fetch_region_value_arrays(
                    chrom, beg, end, [score_id]))
            return

        batches = list(
            opened.fetch_region_value_arrays(chrom, beg, end, [score_id]))
        lines = list(opened.fetch_lines(chrom, beg, end))

    spans = [
        (int(begin), int(stop))
        for pos_begin, pos_end, _ in batches
        for begin, stop in zip(pos_begin, pos_end, strict=True)
    ]
    assert spans == [(line.pos_begin, line.pos_end) for line in lines]

    # The VALUES too, not just the spans -- this test used to promise
    # agreement with the record read and check only the coordinates, so a
    # backend returning the right rows with the wrong numbers passed it.
    values = [
        value for _, _, cols in batches for value in cols[score_id]
    ]
    expected = [line.get_score(score_id) for line in lines]
    assert np.array_equal(
        np.array(values, dtype=np.float64),
        np.array([np.nan if v is None else v for v in expected],
                 dtype=np.float64),
        equal_nan=True), (values, expected)
