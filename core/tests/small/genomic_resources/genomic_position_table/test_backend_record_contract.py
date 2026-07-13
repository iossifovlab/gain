"""Every backend must actually yield what its ``yields_records`` claims.

A position-table backend makes exactly one class-level claim about the shape of
the things it yields: the ``yields_records`` ClassVar.  ``GenomicScore.open``
routes on that claim alone -- a record-yielding table's lines are wrapped in
:class:`RecordScoreLine`, which reads score columns out of the record's PAYLOAD
by index; an adapter-yielding table's in :class:`ScoreLine`, which reads them
through ``line.get``.  A backend whose claim disagrees with what it really
yields is mis-wired, and the score layer would read a raw row out of something
that is not one.

Whether a backend yields records is a property of the **backend**, not of a
row: one shape of thing, for every line, forever.  So it is answerable once,
here, against all four backends -- with no runtime cost in the fetch path.
This test is what lets the score layer route on the claim and simply believe
it.

**This is the file #237 (VCF -> records) and #238 (bigWig -> records) will
trip.**  Flipping ``yields_records`` on a backend without migrating what it
yields fails here, naming the backend -- which is the one moment this catches
anything.
"""
from __future__ import annotations

import pathlib
from collections.abc import Callable

import pytest
from gain.genomic_resources.genomic_position_table.line import LineBase
from gain.genomic_resources.genomic_position_table.record import (
    PAYLOAD,
    RECORD_SLOTS,
)
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    GenomicScore,
    PositionScore,
    RecordScoreLine,
    ScoreLine,
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
# format.  Every backend in the tree is here: a fifth one must be added, or
# nothing checks that its claim is true.
_BACKENDS: list[pytest.param] = [  # type: ignore[valid-type]
    pytest.param(_open_inmemory, id="inmemory"),
    pytest.param(_open_tabix, id="tabix"),
    pytest.param(_open_vcf, id="vcf"),
    pytest.param(_open_bigwig, id="bigwig"),
]


@pytest.mark.parametrize("open_backend", _BACKENDS)
def test_a_backend_yields_what_its_yields_records_claim_says(
    tmp_path: pathlib.Path,
    open_backend: Callable[[pathlib.Path], OpenedBackend],
) -> None:
    # The claim, against the first thing the backend actually produces --
    # which is the earliest moment a claim about records can be contradicted.
    score, region = open_backend(tmp_path)
    with score:
        table = score.table
        backend = type(table).__name__
        first = next(iter(table.get_records_in_region(*region)))

        if table.yields_records:
            # A record is a PLAIN tuple.  Exact type, not isinstance: a
            # ``VCFLine`` is a tuple *subclass* of exactly RECORD_SLOTS slots,
            # so an isinstance check would call an adapter a record.
            assert type(first) is tuple, (
                f"{backend} sets yields_records, so GenomicScore.open routes "
                f"it to RecordScoreLine -- but it yields a "
                f"{type(first).__name__}, not a plain record tuple")
            assert len(first) == RECORD_SLOTS, (
                f"{backend} sets yields_records but yields a "
                f"{len(first)}-slot tuple; a record has {RECORD_SLOTS} slots")
            payload = first[PAYLOAD]
            # RecordScoreLine binds _get_raw to payload.__getitem__ and reads
            # score columns by index, so the payload must be an indexable raw
            # row...
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


@pytest.mark.parametrize("open_backend", _BACKENDS)
def test_open_routes_a_backend_to_the_score_line_its_claim_implies(
    tmp_path: pathlib.Path,
    open_backend: Callable[[pathlib.Path], OpenedBackend],
) -> None:
    # The other half: ``GenomicScore.open`` must route on the claim.  Together
    # with the test above -- the claim is true -- this is what makes the
    # routing correct for every backend, without any per-line check.
    score, region = open_backend(tmp_path)
    with score:
        expected = (
            RecordScoreLine if score.table.yields_records else ScoreLine)
        line = next(iter(score.fetch_lines(*region)))
        assert type(line) is expected, (
            f"{type(score.table).__name__} yields_records="
            f"{score.table.yields_records} was routed to "
            f"{type(line).__name__}")
        # ...and the routed score line can actually read a score through it.
        score_id = next(iter(score.get_all_scores()))
        assert line.get_score(score_id) is not None
