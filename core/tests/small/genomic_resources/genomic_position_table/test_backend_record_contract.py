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
is a **three-way** one -- because a record's PAYLOAD means whatever the backend
that built it says it means:

* a record whose payload is a raw tabular row (in-memory, tabix) is wrapped in
  :class:`RecordScoreLine`, which reads score columns out of it by index;
* a **VCF** record, whose payload is a ``(variant record, allele index)`` pair,
  is wrapped in :class:`VCFScoreLine`, which looks INFO fields up by name and
  selects them by allele;
* an adapter-yielding table's lines (bigWig) in :class:`ScoreLine`, which reads
  them through ``line.get``.

So each backend below declares BOTH what it yields and which score line it must
be routed to, and both are checked against the live objects.

**This is the file #238 (bigWig -> records) will trip** (as #237, VCF, tripped
it).  Flipping ``yields_records`` on a backend without migrating what it yields
fails here, naming the backend -- which is the one moment this catches anything.
"""
from __future__ import annotations

import pathlib
from collections.abc import Callable

import pytest
from gain.genomic_resources.genomic_position_table.line import LineBase
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
    ScoreLine,
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
Region = tuple[str, int, int]
OpenedBackend = tuple[GenomicScore, Region]


def _open_tabular(tmp_path: pathlib.Path, *, tabix: bool) -> OpenedBackend:
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
    return PositionScore(repo.get_resource("pos")).open(), ("1", 10, 10)


def _open_inmemory(tmp_path: pathlib.Path) -> OpenedBackend:
    return _open_tabular(tmp_path, tabix=False)


def _open_tabix(tmp_path: pathlib.Path) -> OpenedBackend:
    return _open_tabular(tmp_path, tabix=True)


def _open_vcf(tmp_path: pathlib.Path) -> OpenedBackend:
    builder = a_vcf_info_score().with_data("""
##fileformat=VCFv4.1
##INFO=<ID=scoreA,Number=1,Type=Float,Description="score A">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   10  .  A   T   .    .      scoreA=0.1
""")
    repo = a_grr().with_resource("vcf", builder).build_repo(tmp_path)
    return AlleleScore(repo.get_resource("vcf")).open(), ("chr1", 10, 10)


def _open_bigwig(tmp_path: pathlib.Path) -> OpenedBackend:
    builder = (
        a_bigwig_score()
        .with_score("bw", "float")
        .with_data("""
            chr1  0   10  0.11
        """)
        .with_chrom_lens({"chr1": 1000})
    )
    repo = a_grr().with_resource("bw", builder).build_repo(tmp_path)
    return PositionScore(repo.get_resource("bw")).open(), ("chr1", 5, 5)


# All four position-table backends, each opened over a resource of its own
# format, paired with the score line class ``GenomicScore.open`` must route it
# to.  Every backend in the tree is here: a fifth one must be added, or nothing
# checks that its claim is true.
_BACKENDS: list[pytest.param] = [  # type: ignore[valid-type]
    pytest.param(_open_inmemory, RecordScoreLine, id="inmemory"),
    pytest.param(_open_tabix, RecordScoreLine, id="tabix"),
    pytest.param(_open_vcf, VCFScoreLine, id="vcf"),
    pytest.param(_open_bigwig, ScoreLine, id="bigwig"),
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
#     pair -- and so the record -- raises ``TypeError``.
#
# Only record-yielding backends are listed (bigWig still yields adapters);
# test_every_record_backend_declares_whether_its_records_hash keeps this list
# in step with _BACKENDS.
_HASHABILITY: list[pytest.param] = [  # type: ignore[valid-type]
    pytest.param(_open_inmemory, True, id="inmemory"),
    pytest.param(_open_tabix, False, id="tabix"),
    pytest.param(_open_vcf, False, id="vcf"),
]


def test_every_record_backend_declares_whether_its_records_hash() -> None:
    # A new record backend must say whether its records hash -- the answer is
    # its payload's, and a caller cannot read it off the record contract.
    record_backends = {
        param.id for param in _BACKENDS
        if param.values[1] in (RecordScoreLine, VCFScoreLine)
    }
    assert {param.id for param in _HASHABILITY} == record_backends


@pytest.mark.parametrize(("open_backend", "records_hash"), _HASHABILITY)
def test_a_records_hashability_is_its_payloads(
    tmp_path: pathlib.Path,
    open_backend: Callable[[pathlib.Path], OpenedBackend],
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
    score, region = open_backend(tmp_path)
    with score:
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


@pytest.mark.parametrize(("open_backend", "score_line_cls"), _BACKENDS)
def test_a_backend_yields_what_its_yields_records_claim_says(
    tmp_path: pathlib.Path,
    open_backend: Callable[[pathlib.Path], OpenedBackend],
    score_line_cls: type[ScoreLineBase],
) -> None:
    # The claim, against the first thing the backend actually produces --
    # which is the earliest moment a claim about records can be contradicted.
    score, region = open_backend(tmp_path)
    with score:
        table = score.table
        backend = type(table).__name__
        first = next(iter(table.get_records_in_region(*region)))

        if table.yields_records:
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
        else:
            # An adapter backend yields a ``LineBase``: ScoreLine reads its
            # values through ``line.get`` and its coordinates off the named
            # attributes.  ``LineBase`` is a bare ``Protocol`` (not
            # runtime-checkable), so it is checked the way a protocol is
            # satisfied -- structurally, member by member, which is also
            # exactly the surface ScoreLine uses.
            assert type(first) is not tuple, (
                f"{backend} leaves yields_records False, so GenomicScore.open "
                f"routes it to ScoreLine -- but it yields a plain record "
                f"tuple, which has no .get and no named attributes. A "
                f"backend that yields records must set yields_records")
            missing = [
                member for member in LineBase.__protocol_attrs__
                if not hasattr(first, member)
            ]
            assert not missing, (
                f"{backend} leaves yields_records False, so GenomicScore.open "
                f"routes it to ScoreLine -- but it yields a "
                f"{type(first).__name__}, which is not a line adapter: it is "
                f"missing {missing}")


@pytest.mark.parametrize(("open_backend", "score_line_cls"), _BACKENDS)
def test_open_routes_a_backend_to_the_score_line_its_payload_needs(
    tmp_path: pathlib.Path,
    open_backend: Callable[[pathlib.Path], OpenedBackend],
    score_line_cls: type[ScoreLineBase],
) -> None:
    # The other half: ``GenomicScore.open`` must route each backend to the score
    # line that can actually read ITS payload.  Together with the test above --
    # the claim about what is yielded is true -- this is what makes the routing
    # correct for every backend, without any per-line check.
    score, region = open_backend(tmp_path)
    with score:
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
