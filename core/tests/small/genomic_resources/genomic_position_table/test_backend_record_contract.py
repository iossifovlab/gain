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

* a record whose payload is a raw tabular row (in-memory, tabix) is read by
  :func:`extract_column_value`, which takes score columns out of it by index;
* a **VCF** record, whose payload carries the variant, its allele index and
  the two pysam INFO proxies, is read by :func:`extract_vcf_value`, which
  looks INFO fields up by name and selects them by allele;
* a **bigWig** record, whose payload IS the interval's value, is read by
  :func:`extract_bigwig_value` -- an identity, with no index and no parse.

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
from collections.abc import Callable, Iterable

import numpy as np
import pytest
from gain.genomic_resources.bigwig_scores import (
    extract_bigwig_value,
)
from gain.genomic_resources.genomic_position_table.record import (
    PAYLOAD,
    POS_BEGIN,
    POS_END,
    RECORD_SLOTS,
    Record,
    sort_key,
)
from gain.genomic_resources.genomic_position_table.table import (
    GenomicPositionTable,
)
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    GenomicScore,
    PositionScore,
)
from gain.genomic_resources.score_def import (
    extract_column_value,
)
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_grr,
    a_position_score,
    a_vcf_info_score,
)
from gain.genomic_resources.vcf_scores import (
    extract_vcf_value,
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
#
# That "every backend in the tree" is no longer a promise this comment makes on
# its own -- test_every_backend_in_the_tree_is_in_the_backend_list, over in
# test_table_lifetime.py, sweeps the real ``GenomicPositionTable`` subclasses
# and fails a concrete backend that is missing from this list.  It lives there
# because the release-policy tests are what a missing entry silently robs of a
# subject (gain#359), but what it holds to reality is this list.
_BACKENDS: list[pytest.param] = [  # type: ignore[valid-type]
    pytest.param(_build_inmemory, extract_column_value, id="inmemory"),
    pytest.param(_build_tabix, extract_column_value, id="tabix"),
    pytest.param(_build_vcf, extract_vcf_value, id="vcf"),
    pytest.param(_build_bigwig, extract_bigwig_value, id="bigwig"),
]


def build_every_backend(
    tmp_path: pathlib.Path,
) -> dict[str, GenomicScore]:
    """One UNOPENED score per ``_BACKENDS`` entry, keyed by its param id.

    A repo per backend: two of them build a resource under the same name, so
    each gets a directory of its own.

    Shared with test_table_lifetime.py, whose exhaustiveness sweep wants the
    same four scores for the opposite reason -- it reads ``type(score.table)``
    off each to learn which backend the entry really builds, where the checks
    in this file read the table's class-level claims.  Both are "hand-written
    list against reality", and neither may open a score first: the claims are
    ClassVars, readable before ``GenomicScore.open`` gets to route on them.
    """
    built = {}
    for param in _BACKENDS:
        build_backend, _extractor = param.values
        backend_dir = tmp_path / str(param.id)
        backend_dir.mkdir()
        score, _region = build_backend(backend_dir)
        built[str(param.id)] = score
    return built


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
#   * bigWig -- payload is the interval's value, a bare ``float``: hashes.
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
    below: a migrating backend can arrive with an extractor of its own (VCF
    did, at :func:`extract_vcf_value`; bigWig has since acquired
    :func:`extract_bigwig_value`) or reuse an existing one whose hashability
    differs from every backend already routed there, and a check written
    against the extractors rather than the tables would miss both.  Ask the
    table, and there is nothing to add to but _HASHABILITY.
    """
    record_backends = {
        backend_id
        for backend_id, score in build_every_backend(tmp_path).items()
        if score.table.yields_records
    }

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


@pytest.mark.parametrize(("build_backend", "extractor"), _BACKENDS)
def test_a_backend_yields_what_its_yields_records_claim_says(
    tmp_path: pathlib.Path,
    build_backend: Callable[[pathlib.Path], Backend],
    extractor: object,
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
        # A payload must be readable by the extractor this backend is routed
        # to, and there are two shapes of that.  An INDEXED payload -- a raw
        # tabular row, or the VCF (variant, allele index, info, info_meta)
        # tuple -- must be indexable, and must not be a str/bytes: those are
        # indexable but index to *characters*, so every score would silently
        # parse to None rather than raise.  A WHOLE-payload extractor
        # (bigWig's identity) needs neither, and asking for indexability there
        # would be asking for the repetition the narrowing removed.
        if extractor is extract_bigwig_value:
            assert isinstance(payload, float), (
                f"{backend} is routed to the identity extractor, so its "
                f"record's PAYLOAD must be the value itself; it is a "
                f"{type(payload).__name__}")
        else:
            assert hasattr(payload, "__getitem__"), (
                f"{backend} sets yields_records but its record's PAYLOAD is a "
                f"{type(payload).__name__}, which is not indexable")
            assert not isinstance(payload, (str, bytes)), (
                f"{backend} sets yields_records but its record's PAYLOAD is a "
                f"{type(payload).__name__} -- indexing it yields characters, "
                f"not cells")


@pytest.mark.parametrize(("build_backend", "extractor"), _BACKENDS)
def test_open_routes_a_backend_to_the_extractor_its_payload_needs(
    tmp_path: pathlib.Path,
    build_backend: Callable[[pathlib.Path], Backend],
    extractor: object,
) -> None:
    # The other half: ``GenomicScore.open`` must route each backend to the
    # value extractor that can actually read ITS payload.  Together with the
    # test above -- the claim about what is yielded is true -- this is what
    # makes the routing correct for every backend, without any per-record
    # check.
    score, region = build_backend(tmp_path)
    with score.open():
        # The choice is made once, at open: it is already installed before a
        # single record is fetched.
        assert score._extract_value is extractor, (
            f"{type(score.table).__name__} (yields_records="
            f"{score.table.yields_records}) was routed at open to "
            f"{score._extract_value.__name__}, "
            f"not {extractor.__name__}")

        record = next(iter(score.fetch_records(*region)))
        assert type(record) is tuple, (
            f"{type(score.table).__name__} yields_records="
            f"{score.table.yields_records} was routed to "
            f"{type(record).__name__}")
        # ...and the routed extractor can actually read a score off it --
        # which is what fails if a backend is routed to an extractor whose raw
        # lookup does not fit its payload.
        score_id = next(iter(score.get_all_scores()))
        assert score.get_score_value_from_record(record, score_id) is not None


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
        records = list(opened.fetch_records(chrom, beg, end))

    spans = [
        (int(begin), int(stop))
        for pos_begin, pos_end, _ in batches
        for begin, stop in zip(pos_begin, pos_end, strict=True)
    ]
    assert spans == [
        (rec[POS_BEGIN], rec[POS_END]) for rec in records]

    # The VALUES too, not just the spans -- this test used to promise
    # agreement with the record read and check only the coordinates, so a
    # backend returning the right rows with the wrong numbers passed it.
    values = [
        value for _, _, cols in batches for value in cols[score_id]
    ]
    expected = [
        opened.get_score_value_from_record(rec, score_id)
        for rec in records]
    assert np.array_equal(
        np.array(values, dtype=np.float64),
        np.array([np.nan if v is None else v for v in expected],
                 dtype=np.float64),
        equal_nan=True), (values, expected)


# ---------------------------------------------------------------------------
# What a backend owes a region read that is ABANDONED part-way (gain#1120).
# ---------------------------------------------------------------------------
#
# A consumer is under no obligation to finish iterating.  So a backend must
# survive being walked away from: the read it abandons may cost memory that is
# not yet reclaimed, but it may not accumulate that cost across queries, and
# it may not leave the table answering the next query differently.
#
# The scan fixtures below are separate from _BACKENDS on purpose.  Those build
# a ONE-record table, which is the right subject for a claim about the shape of
# a record and the wrong one for a claim about accumulation: a single record
# cannot grow into a leak, so every backend would pass the boundedness half by
# having nothing to retain.  These build a long forward scan instead, which is
# the shape that told the two apart (the tabix backend ended it holding one
# record per query).
_SCAN_POSITIONS = list(range(10, 610, 3))


def _abandon_every_query(
    table: GenomicPositionTable, positions: list[int], backend: str,
) -> int:
    """Take one record from each query, drop it, and report what is retained.

    Every position must hold a record.  A query that yields nothing runs its
    generator to exhaustion, which reaches whatever cleanup sits after the
    yield loop -- so a scan over empty positions is bounded whatever the
    backend does with ``GeneratorExit``, and would measure nothing.
    """
    for pos in positions:
        records = table.get_records_in_region("1", pos, pos)
        assert next(records, None) is not None, (
            f"{backend} yields nothing at {pos}: the query would run to "
            f"exhaustion and the read would never be abandoned")
        records.close()
    return table.buffered_record_count()


def _build_tabular_scan(
    tmp_path: pathlib.Path, *, tabix: bool,
) -> GenomicScore:
    lines = ["chrom  pos_begin  s_float"]
    lines.extend(
        f"1  {pos}  {pos / 10}" for pos in _SCAN_POSITIONS)
    builder = (
        a_position_score()
        .with_score("s_float", "float")
        .with_data("\n".join(lines))
    )
    if tabix:
        builder = builder.with_tabix()
    repo = a_grr().with_resource("pos", builder).build_repo(tmp_path)
    return PositionScore(repo.get_resource("pos"))


def _build_inmemory_scan(tmp_path: pathlib.Path) -> GenomicScore:
    return _build_tabular_scan(tmp_path, tabix=False)


def _build_tabix_scan(tmp_path: pathlib.Path) -> GenomicScore:
    return _build_tabular_scan(tmp_path, tabix=True)


def _build_vcf_scan(tmp_path: pathlib.Path) -> GenomicScore:
    lines = [
        "##fileformat=VCFv4.1",
        '##INFO=<ID=scoreA,Number=1,Type=Float,Description="score A">',
        "#CHROM POS ID REF ALT QUAL FILTER INFO",
    ]
    lines.extend(
        f"1   {pos}  .  A   T   .    .      scoreA={pos / 10}"
        for pos in _SCAN_POSITIONS
    )
    builder = a_vcf_info_score().with_data("\n".join(lines))
    repo = a_grr().with_resource("vcf", builder).build_repo(tmp_path)
    return AlleleScore(repo.get_resource("vcf"))


def _build_bigwig_scan(tmp_path: pathlib.Path) -> GenomicScore:
    # bigWig intervals are half-open and 0-based, so a row covering the 1-based
    # position ``pos`` is ``[pos - 1, pos)``.
    lines = [
        f"1  {pos - 1}  {pos}  {pos / 10}" for pos in _SCAN_POSITIONS]
    builder = (
        a_bigwig_score()
        .with_score("bw", "float")
        .with_data("\n".join(lines))
        .with_chrom_lens({"1": _SCAN_POSITIONS[-1] + 100})
    )
    repo = a_grr().with_resource("bw", builder).build_repo(tmp_path)
    return PositionScore(repo.get_resource("bw"))


# Every backend again -- the same four, over data long enough for the
# boundedness half to be able to fail.
_SCAN_BACKENDS: list[pytest.param] = [  # type: ignore[valid-type]
    pytest.param(_build_inmemory_scan, id="inmemory"),
    pytest.param(_build_tabix_scan, id="tabix"),
    pytest.param(_build_vcf_scan, id="vcf"),
    pytest.param(_build_bigwig_scan, id="bigwig"),
]


def test_every_backend_is_covered_by_the_abandonment_contract() -> None:
    """_SCAN_BACKENDS must cover every backend, so a fifth cannot slip in."""
    assert {str(param.id) for param in _SCAN_BACKENDS} == \
        {str(param.id) for param in _BACKENDS}


@pytest.mark.parametrize("build_backend", _SCAN_BACKENDS)
def test_abandoning_a_region_read_leaves_the_table_bounded_and_correct(
    tmp_path: pathlib.Path,
    build_backend: Callable[[pathlib.Path], GenomicScore],
) -> None:
    """Abandoning a region read must cost memory only, and only once.

    Two halves, both over a forward scan whose every query yields a record and
    is then dropped holding it:

    * **bounded** -- what the table retains between queries must not grow with
      the length of the scan.  ``buffered_record_count`` is how a backend says
      what it is holding; a backend that buffers nothing inherits the base
      class's zero and passes by construction, which is the honest answer for
      three of the four.
    * **correct** -- the answers after the scan must be the answers a table
      that was never abandoned gives.

    Trivially true for the in-memory and bigWig backends, which retain
    nothing between queries.  Load-bearing for **two**: tabix, which serves a
    query from a warm ``LineBuffer`` and evicts the dead records only once the
    query has been served -- and VCF, which subclasses it and inherits the
    buffer, the read and the leak along with them.  (Reverting the fix fails
    both params, at 200 retained records each.)  That asymmetry is the point:
    the guarantee is stated once, here, so that
    gain#834 can stream an allele region on the strength of it rather than
    re-verifying it, and so that a NEW backend introducing cross-query
    retention has to meet it.

    What this does NOT do is pin the tabix backend's two buffered paths
    separately.  A scan alternates between them, so either one's prune keeps
    the buffer bounded on behalf of the other.
    ``test_the_buffer_hit_path_prunes_when_abandoned`` and
    ``test_the_sequential_seek_path_prunes_when_abandoned``, over in
    test_overlapping_intervals.py, take one path each with a single query --
    as does the tabix-level statement of this same boundedness,
    ``test_abandoned_queries_keep_the_buffer_bounded``, which measures the
    growth in records where this measures it in queries.
    """
    half = _SCAN_POSITIONS[:len(_SCAN_POSITIONS) // 2]

    score = build_backend(tmp_path)
    with score.open() as opened:
        table = opened.table
        backend = type(table).__name__

        # The scan is run at two lengths and the GROWTH between them is what
        # is asserted, rather than a cap: the contract's claim is that
        # retention does not grow with the length of the scan, and doubling
        # the scan asks exactly that without borrowing a number from one
        # backend's internals.  (An earlier draft capped this at
        # ``LineBuffer.COMPACT_FLOOR`` -- a tabix performance knob.  Retuning
        # it would have changed what a cross-backend correctness test
        # asserts, and a new backend retaining 30 records per query would
        # have passed.)
        #
        # The longer scan restarts at the first position, which is BACKWARD
        # for every backend that tracks a cursor, so the second measurement
        # does not inherit the first's retention.
        retained_half = _abandon_every_query(table, half, backend)
        retained_full = _abandon_every_query(table, _SCAN_POSITIONS, backend)

        added = len(_SCAN_POSITIONS) - len(half)
        growth = retained_full - retained_half
        assert growth < added // 2, (
            f"{backend} retained {retained_half} records over {len(half)} "
            f"abandoned queries and {retained_full} over "
            f"{len(_SCAN_POSITIONS)}: {added} more queries grew retention by "
            f"{growth}, which is not sublinear")

        abandoned_answers = [
            _positions_of(table.get_records_in_region("1", pos, pos))
            for pos in _SCAN_POSITIONS
        ]

    # The oracle: the same scan on a table no one walked away from.
    fresh = build_backend(tmp_path / "fresh")
    with fresh.open() as opened_fresh:
        expected = [
            _positions_of(
                opened_fresh.table.get_records_in_region("1", pos, pos))
            for pos in _SCAN_POSITIONS
        ]

    assert abandoned_answers == expected


def _positions_of(records: Iterable[Record]) -> list[tuple[int, int]]:
    """Project records onto their spans -- comparable across backends."""
    return [(record[POS_BEGIN], record[POS_END]) for record in records]
