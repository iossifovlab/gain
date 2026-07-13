"""Equivalence tests for the bulk value-extraction path on ``ScoreLine``.

``ScoreLine.get_values`` extracts the values for a whole line given a list
of already-resolved score definitions, hoisting the name->definition lookup
out of the per-line loop.  These tests pin it to the single-score
``ScoreLine.get_score`` path: for every input the bulk method must return
*exactly* what looping ``get_score`` returns -- including ``None`` for an
absent key, ``None`` for a configured NA value, and ``None`` (plus a logged
parse failure) for an unparseable value.

The value-extraction logic (``_extract_value``) is shared by both score line
classes, but each reads its raw value through a per-instance ``_get_raw``
bound to a *different* lookup: ``ScoreLine`` (the VCF/bigWig adapter backends)
binds it to ``line.get``; ``RecordScoreLine`` (the in-memory and tabix record
backends) binds it to the record payload's ``__getitem__``.

The NA and parse tests run against **both** record backends, because their
payloads are different objects: the in-memory backend's payload is a plain
``tuple`` of cells, the tabix backend's is a lazily-decoding ``pysam`` row.
``RecordScoreLine`` binds ``_get_raw`` to whichever one it is handed, so a
binding that works on one and not the other fails here.  ``ScoreLine``'s own
binding is pinned by the two adapter-backend tests at the bottom of this file.

Which class a backend is routed to is therefore load-bearing, so the routing
itself is pinned here too: the adapter backends must yield a ``ScoreLine``,
and a record-yielding table that hands the record path something that is not a
record must be rejected -- loudly, on its very first line, rather than one line
later inside the binding.  That mis-route check lives at the routing decision
(``GenomicScore.open`` installs it; it verifies the table's first record and
then disarms), NOT in ``RecordScoreLine.__init__`` -- the record contract is a
per-*table* invariant and this is the hot path, so it is not paid per line.
"""
from __future__ import annotations

import logging
from collections.abc import Generator
from typing import Any, NamedTuple

import pytest
from gain.genomic_resources.genomic_position_table import VCFLine
from gain.genomic_resources.genomic_position_table.record import Record
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    GenomicScore,
    PositionScore,
    RecordScoreLine,
    ScoreLine,
    _ScoreDef,
)
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_grr,
    a_position_score,
    a_vcf_info_score,
)

# The two tabular backends the shared _extract_value runs on, and the
# concrete score line class each one yields.  A tabular ``.txt`` resource is
# read by the in-memory backend; ``with_tabix`` realizes the same data as a
# tabix table.  Both are on the record contract, so both yield a
# RecordScoreLine -- but over different payloads (a plain tuple of cells
# vs. a lazily-decoding pysam row), which is what makes running both worth it.
_TABULAR_BACKENDS = [
    pytest.param(False, RecordScoreLine, id="inmemory"),
    pytest.param(True, RecordScoreLine, id="tabix"),
]


def _defs(score: PositionScore | AlleleScore) -> list[_ScoreDef]:
    return [
        score.score_definitions[score_id]
        for score_id in score.get_all_scores()
    ]


def _open_position(
    tmp_path, data: str, *, tabix: bool = False,
) -> PositionScore:
    builder = (
        a_position_score()
        .with_score("s_float", "float")
        .with_score("s_str", "str")
        .with_data(data)
    )
    if tabix:
        builder = builder.with_tabix()
    repo = a_grr().with_resource("pos", builder).build_repo(tmp_path)
    score = PositionScore(repo.get_resource("pos")).open()
    assert isinstance(score, PositionScore)
    return score


def test_bulk_matches_per_score_on_tabular(tmp_path) -> None:
    score = _open_position(tmp_path, """
        chrom  pos_begin  s_float  s_str
        1      10         0.5      hello
        1      11         0.25     world
    """)
    with score:
        for line in score.fetch_lines("1", 10, 11):
            per_score = [line.get_score(s) for s in score.get_all_scores()]
            bulk = line.get_values(_defs(score))
            assert bulk == per_score
        line = next(iter(score.fetch_lines("1", 10, 10)))
        assert line.get_values(_defs(score)) == [0.5, "hello"]


@pytest.mark.parametrize(("tabix", "line_cls"), _TABULAR_BACKENDS)
def test_bulk_na_value_yields_none(tmp_path, tabix, line_cls) -> None:
    # The NA token must *parse successfully* so this test actually exercises
    # the na_values branch: ``"nan"`` is a configured NA value for a float
    # score AND ``float("nan")`` returns ``nan`` (it does not raise).  A
    # token like ``"."`` would be masking -- it also fails to parse, so the
    # value would come back ``None`` via the except path even if the NA
    # check were deleted, and the test could not tell the difference.
    #
    # Run on both record backends: each reads the raw "nan" through the same
    # _get_raw binding but over a different payload (plain tuple vs. lazy pysam
    # row), so a payload-specific break -- not just the shared na_values
    # branch -- fails here.
    score = _open_position(tmp_path, """
        chrom  pos_begin  s_float  s_str
        1      10         nan      hello
    """, tabix=tabix)
    with score:
        line = next(iter(score.fetch_lines("1", 10, 10)))
        assert isinstance(line, line_cls)
        # "nan" is a configured NA value for a float score, and float("nan")
        # does not raise -- so only the na_values check makes this None.
        assert "nan" in score.score_definitions["s_float"].na_values
        per_score = [line.get_score(s) for s in score.get_all_scores()]
        bulk = line.get_values(_defs(score))
        # The absolute assertions are the real guard here.  While both paths
        # share ``_extract_value``, ``bulk == per_score`` is tautological --
        # dropping the na_values check makes BOTH return ``nan``, and the
        # comparison then fails only incidentally, because ``nan != nan``.
        # Assert the absolute values first so a broken NA branch fails for
        # the right reason.  The equivalence assertion earns its keep only
        # if the single-value logic is ever forked again.
        assert bulk[0] is None
        assert per_score[0] is None
        assert bulk[1] == "hello"
        assert bulk == per_score


@pytest.mark.parametrize(("tabix", "line_cls"), _TABULAR_BACKENDS)
def test_bulk_unparseable_value_logs_and_yields_none(
    tmp_path, tabix, line_cls, caplog: pytest.LogCaptureFixture,
) -> None:
    # Runs on both record backends so the parse-failure branch (with its
    # logger.exception) is proven over both record payloads.
    score = _open_position(tmp_path, """
        chrom  pos_begin  s_float  s_str
        1      10         not_a_number  hello
    """, tabix=tabix)
    with score:
        line = next(iter(score.fetch_lines("1", 10, 10)))
        assert isinstance(line, line_cls)
        defs = _defs(score)

        with caplog.at_level(logging.ERROR):
            per_score = [line.get_score(s) for s in score.get_all_scores()]
        per_score_records = len(caplog.records)
        assert per_score_records >= 1
        assert per_score[0] is None

        caplog.clear()
        with caplog.at_level(logging.ERROR):
            bulk = line.get_values(defs)
        assert bulk == per_score
        assert bulk[0] is None
        assert len(caplog.records) == per_score_records


def test_bulk_matches_per_score_on_vcf_absent_info_key(tmp_path) -> None:
    builder = a_vcf_info_score().with_data("""
##fileformat=VCFv4.1
##INFO=<ID=scoreA,Number=1,Type=Float,Description="score A">
##INFO=<ID=scoreB,Number=1,Type=Float,Description="score B">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   10  .  A   T   .    .      scoreA=0.1
chr1   11  .  A   T   .    .      scoreA=0.2;scoreB=0.5
""")
    repo = a_grr().with_resource("vcf", builder).build_repo(tmp_path)
    score = AlleleScore(repo.get_resource("vcf")).open()
    with score:
        line = next(iter(score.fetch_lines("chr1", 10, 10)))
        per_score = [line.get_score(s) for s in score.get_all_scores()]
        bulk = line.get_values(_defs(score))
        assert bulk == per_score
        # scoreB is absent from this record's INFO -> null raw value -> None
        assert None in bulk


def test_vcf_backend_yields_the_adapter_score_line(tmp_path) -> None:
    # The VCF backend keeps its line adapter (``yields_records`` is False), so
    # GenomicScore.open() must pick ScoreLine -- not RecordScoreLine -- for it,
    # even though it now subclasses the record-yielding tabix table.
    # _TABULAR_BACKENDS pins the record backends; this pins an adapter one.
    builder = a_vcf_info_score().with_data("""
##fileformat=VCFv4.1
##INFO=<ID=scoreA,Number=1,Type=Float,Description="score A">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   10  .  A   T   .    .      scoreA=0.1
""")
    repo = a_grr().with_resource("vcf", builder).build_repo(tmp_path)
    score = AlleleScore(repo.get_resource("vcf")).open()
    with score:
        assert score.table.yields_records is False
        line = next(iter(score.fetch_lines("chr1", 10, 10)))
        assert isinstance(line, ScoreLine)
        assert not isinstance(line, RecordScoreLine)
        assert line.get_score("scoreA") == pytest.approx(0.1)


def _a_vcf_line(tmp_path) -> VCFLine:
    """Return a real ``VCFLine`` (the adapter the VCF backend yields)."""
    builder = a_vcf_info_score().with_data("""
##fileformat=VCFv4.1
##INFO=<ID=scoreA,Number=1,Type=Float,Description="score A">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   10  .  A   T   .    .      scoreA=0.1
""")
    repo = a_grr().with_resource("vcf", builder).build_repo(tmp_path)
    score = AlleleScore(repo.get_resource("vcf")).open()
    with score:
        score_line = next(iter(score.fetch_lines("chr1", 10, 10)))
        assert isinstance(score_line, ScoreLine)
        line = score_line.line
        assert isinstance(line, VCFLine)
        return line


# --- the mis-route check --------------------------------------------------
#
# A table that claims ``yields_records`` but does not actually yield records is
# a mis-wired backend, and the score layer must say so on the spot instead of
# blowing up one line later inside the ``_get_raw`` binding with an
# ``AttributeError`` about a ``pysam...VariantRecord`` having no
# ``__getitem__`` -- an error that names nothing that is actually wrong.
#
# The check is a per-TABLE invariant (a backend yields one shape of thing, for
# every line, forever), so it is verified on the table's first record and never
# again -- see ``test_the_record_contract_is_checked_once_per_open``.  These
# tests drive it the way production reaches it: through ``fetch_lines`` on a
# score whose record-yielding table has been mis-wired to yield a non-record.


class _NotARecord(NamedTuple):
    """A six-field ``NamedTuple`` -- a tuple *subclass*, so not a record."""

    chrom: str
    pos_begin: int
    pos_end: int
    ref: str | None
    alt: str | None
    payload: tuple[str, ...]


def _mis_wire(score: GenomicScore, line: Any) -> None:
    """Make an opened record-yielding table yield ``line`` instead of records.

    The table keeps its ``yields_records = True`` claim -- which is precisely a
    mis-wired backend: it is routed to the record path (``GenomicScore.open``
    believed the claim) but yields something that is not a record.
    """
    assert score.table.yields_records is True

    def get_records_in_region(
        chrom: str | None = None,
        pos_begin: int | None = None,
        pos_end: int | None = None,
    ) -> Generator[Record, None, None]:
        yield line

    score.table.get_records_in_region = get_records_in_region  # type: ignore[method-assign]


def _fetch_one_mis_wired(
    tmp_path, line: Any,
) -> pytest.ExceptionInfo[TypeError]:
    """Fetch the first line of a mis-wired table; return its TypeError."""
    score = _open_position(tmp_path, """
        chrom  pos_begin  s_float  s_str
        1      10         0.5      hello
    """)
    with score:
        _mis_wire(score, line)
        with pytest.raises(TypeError) as exc_info:
            next(iter(score.fetch_lines("1", 10, 10)))
    return exc_info


def test_mis_routed_line_adapter_is_rejected(tmp_path) -> None:
    # A ``VCFLine`` is a ``tuple`` subclass (it rides the record-indexed
    # LineBuffer), so ``isinstance(line, tuple)`` cannot tell a record from an
    # adapter -- a VCFLine is a tuple of the right length whose PAYLOAD is a
    # ``pysam.VariantRecord``, not an indexable raw row.  The check is an exact
    # -type allowlist for exactly this reason; pin that the adapter is rejected
    # on the first line, and that the message names the offender.
    line = _a_vcf_line(tmp_path)
    assert isinstance(line, tuple)  # this is why an isinstance check fails

    exc_info = _fetch_one_mis_wired(tmp_path, line)

    message = str(exc_info.value)
    assert "VCFLine" in message
    assert "record" in message
    # It must not be the downstream payload confusion.
    assert "__getitem__" not in message


def test_mis_routed_tuple_subclass_is_rejected(tmp_path) -> None:
    # The exact-type check is an ALLOWLIST, not a VCFLine denylist: any tuple
    # subclass is rejected, however record-shaped it looks.  A six-field
    # NamedTuple passes both an isinstance check and a length check, so only
    # ``type(line) is not tuple`` catches it -- pin that claim rather than
    # leaving it as a comment.
    exc_info = _fetch_one_mis_wired(
        tmp_path, _NotARecord("1", 10, 10, None, None, ("1", "10", "0.5")))

    message = str(exc_info.value)
    assert "_NotARecord" in message
    assert "record" in message


def test_mis_routed_short_tuple_is_rejected(tmp_path) -> None:
    # The other half of the record contract: the slot count.  A plain tuple of
    # the wrong length is not a record.
    exc_info = _fetch_one_mis_wired(tmp_path, ("1", 10, 10))
    assert "record" in str(exc_info.value)


def test_mis_routed_long_tuple_is_rejected(tmp_path) -> None:
    # ...and the length check has two sides.  A tuple with a SEVENTH slot is
    # the drift the record contract's own RECORD_SLOTS guards against (a slot
    # appended after PAYLOAD), so it is the one the check most needs to catch,
    # and it was the untested one.
    exc_info = _fetch_one_mis_wired(
        tmp_path, ("1", 10, 10, None, None, ("1", "10", "0.5"), "seventh"))
    assert "record" in str(exc_info.value)


def test_record_backend_accepts_a_plain_record(tmp_path) -> None:
    # The check must not reject a legitimate record: the real record backend,
    # not mis-wired, reads its values through RecordScoreLine as always.
    score = _open_position(tmp_path, """
        chrom  pos_begin  s_float  s_str
        1      10         0.5      hello
    """)
    with score:
        line = next(iter(score.fetch_lines("1", 10, 10)))
        assert isinstance(line, RecordScoreLine)
        assert line.chrom == "1"
        assert line.pos_begin == 10
        assert line.pos_end == 10
        assert line.ref is None
        assert line.alt is None
        assert line.get_score("s_float") == 0.5


def test_the_record_contract_is_checked_once_per_open(tmp_path) -> None:
    # The whole point of this PR is per-line cost, and the record contract is a
    # per-TABLE invariant: whether a backend yields records is decided by its
    # class, not by its rows.  So it is checked where that decision is made --
    # ``open()`` installs a checking wrapper as the score line class -- and the
    # wrapper disarms itself once the table's first record has proved the
    # contract, rebinding the score to the bare ``RecordScoreLine``.  Every
    # subsequent line therefore constructs exactly what it did before the check
    # existed: no type call, no len call, no branch.
    #
    # This reads a private attribute on purpose: "the hot path pays nothing" is
    # the behaviour under test, and the class the score constructs per line is
    # the only place it is observable.
    score = _open_position(tmp_path, """
        chrom  pos_begin  s_float  s_str
        1      10         0.5      hello
        1      11         0.25     world
    """)
    with score:
        # armed: the score routes through the checking wrapper, not the
        # bare constructor
        assert score._score_line_class is not RecordScoreLine

        lines = list(score.fetch_lines("1", 10, 11))

        # disarmed: the contract is proved, the hot path is bare again
        assert score._score_line_class is RecordScoreLine
        assert len(lines) == 2
        assert all(isinstance(line, RecordScoreLine) for line in lines)
        assert [line.get_score("s_float") for line in lines] == [0.5, 0.25]


def test_bigwig_backend_yields_the_adapter_score_line(tmp_path) -> None:
    # Same for the bigWig backend, the last of the three adapter backends.
    builder = (
        a_bigwig_score()
        .with_score("bw", "float")
        .with_data("""
            chr1  0   10  0.11
            chr1  10  20  0.22
        """)
        .with_chrom_lens({"chr1": 1000})
    )
    repo = a_grr().with_resource("bw", builder).build_repo(tmp_path)
    score = PositionScore(repo.get_resource("bw")).open()
    with score:
        assert score.table.yields_records is False
        line = next(iter(score.fetch_lines("chr1", 5, 5)))
        assert isinstance(line, ScoreLine)
        assert not isinstance(line, RecordScoreLine)
        assert line.get_score("bw") == pytest.approx(0.11)


def test_record_score_line_get_score_singular(tmp_path) -> None:
    # RecordScoreLine.get_score (the singular path) is exercised directly:
    # the other tests here go through the bulk get_values.  The in-memory
    # backend yields RecordScoreLine, so this pins get_score reading through
    # the record payload's __getitem__ binding, one score id at a time.
    score = _open_position(tmp_path, """
        chrom  pos_begin  s_float  s_str
        1      10         0.5      hello
    """)
    with score:
        line = next(iter(score.fetch_lines("1", 10, 10)))
        assert isinstance(line, RecordScoreLine)
        assert line.get_score("s_float") == 0.5
        assert line.get_score("s_str") == "hello"


def test_get_values_returns_new_ordered_list(tmp_path) -> None:
    score = _open_position(tmp_path, """
        chrom  pos_begin  s_float  s_str
        1      10         0.5      hello
    """)
    with score:
        line = next(iter(score.fetch_lines("1", 10, 10)))
        reversed_defs = list(reversed(_defs(score)))
        # tabular .txt -> in-memory backend -> RecordScoreLine
        assert isinstance(line, RecordScoreLine)
        assert line.get_values(reversed_defs) == ["hello", 0.5]


# --- empty-region + unknown-score-id: the score-name resolution must not
# happen when no line is extracted.  Base only resolved a name inside the
# per-line loop (or after a `if not lines: return` guard), so an empty
# region never rejected an unknown score id.  Hoisting the resolution out
# of the loop must preserve that: an empty region with an unknown score id
# must behave exactly as base -- no KeyError.

def test_region_fetch_empty_region_unknown_score_is_not_an_error(
    tmp_path,
) -> None:
    # Regression for the eager-resolution divergence: on base an empty
    # region with an unknown score id yields no rows; it must not raise.
    score = _open_position(tmp_path, """
        chrom  pos_begin  s_float  s_str
        1      10         0.5      hello
    """)
    with score:
        assert list(score.fetch_region_values(
            "1", 5000, 5001, scores=["NOPE"])) == []


def test_region_fetch_nonempty_region_unknown_score_still_raises(
    tmp_path,
) -> None:
    # Behaviour preservation on the other side: a region that does yield a
    # line still rejects an unknown score id (base raised KeyError inside
    # the loop via score_defs[...]).
    score = _open_position(tmp_path, """
        chrom  pos_begin  s_float  s_str
        1      10         0.5      hello
    """)
    with score, pytest.raises(KeyError):
        list(score.fetch_region_values("1", 10, 10, scores=["NOPE"]))


def test_point_fetch_empty_region_unknown_score_returns_none(
    tmp_path,
) -> None:
    # PositionScore.fetch_scores resolves after `if not lines: return None`,
    # so an empty region short-circuits before touching the unknown score id.
    score = _open_position(tmp_path, """
        chrom  pos_begin  s_float  s_str
        1      10         0.5      hello
    """)
    with score:
        assert score.fetch_scores("1", 5000, scores=["NOPE"]) is None


def test_allele_point_fetch_empty_region_unknown_score_returns_none(
    tmp_path,
) -> None:
    # AlleleScore.fetch_scores resolves after its `if not lines`/
    # `if not selected_line` guards -- an empty region returns None.
    builder = a_vcf_info_score().with_data("""
##fileformat=VCFv4.1
##INFO=<ID=scoreA,Number=1,Type=Float,Description="score A">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   10  .  A   T   .    .      scoreA=0.1
""")
    repo = a_grr().with_resource("vcf", builder).build_repo(tmp_path)
    score = AlleleScore(repo.get_resource("vcf")).open()
    with score:
        assert score.fetch_scores(
            "chr1", 5000, "A", "T", scores=["NOPE"]) is None
