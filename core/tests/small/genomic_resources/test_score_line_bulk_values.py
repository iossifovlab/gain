"""Equivalence tests for the bulk value-extraction path on ``ScoreLine``.

``ScoreLine.get_values`` extracts the values for a whole line given a list
of already-resolved score definitions, hoisting the name->definition lookup
out of the per-line loop.  These tests pin it to the single-score
``ScoreLine.get_score`` path: for every input the bulk method must return
*exactly* what looping ``get_score`` returns -- including ``None`` for an
absent key, ``None`` for a configured NA value, and ``None`` (plus a logged
parse failure) for an unparseable value.

The value-extraction logic (``_extract_value``) is shared by all three score
line classes, but each reaches its raw value through a ``_get_raw`` of its own,
and they do not all get there the same way.

Two of them **bind** it, in their constructor, to a callable that is reachable
*from* the line but is not the line: ``RecordScoreLine`` (the in-memory, tabix
and -- since #238 -- bigWig record backends) to the record payload's
``__getitem__``; ``ScoreLine`` (the adapter path, which now has no backend
routed to it -- bigWig was the last, and #239 removes the class) to an
adapter's ``line.get``.

``VCFScoreLine`` (the VCF record backend, whose payload is a ``(variant, allele
index)`` pair rather than a row) instead declares ``_get_raw`` as a plain
**method**, and must: its INFO lookup needs the line *itself*, and binding a
method of self onto self (``self._get_raw = self._something``) is a reference
cycle -- one per line, on the hot path, where one score line is built per line
of a fetch.  A method allocates nothing per line and refers to nothing, so the
line dies by refcount when the fetch loop drops it.  That rule -- a subclass
whose lookup needs ``self`` uses a method and does not bind -- is pinned in
test_genomic_scores.py, by
test_score_lines_are_freed_without_the_cycle_collector.

The NA and parse tests run against **both** column-payload record backends,
because their payloads are different objects: the in-memory backend's payload is
a plain ``tuple`` of cells, the tabix backend's is a lazily-decoding ``pysam``
row.  ``RecordScoreLine`` binds ``_get_raw`` to whichever one it is handed, so a
binding that works on one and not the other fails here.  Each class is exercised
over its own backend by the backend tests at the bottom of this file.

Which class a backend is routed to is therefore load-bearing, so the routing is
pinned here too, from the score's side.  That every backend's ``yields_records``
claim is *true* -- the question a runtime check used to ask, per table -- is not
a property of a line at all, and is pinned statically over all four backends by
test_backend_record_contract.py.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    PositionScore,
    RecordScoreLine,
    ScoreLine,
    VCFScoreLine,
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


def test_vcf_backend_yields_the_vcf_score_line(tmp_path) -> None:
    # The VCF backend yields records, but its scores are INFO fields rather than
    # columns, so a record payload's __getitem__ is not the lookup it needs: it
    # is routed to VCFScoreLine, the one class that performs the INFO lookup.
    # _TABULAR_BACKENDS pins the two column-payload record backends; this pins
    # the INFO-payload one.
    builder = a_vcf_info_score().with_data("""
##fileformat=VCFv4.1
##INFO=<ID=scoreA,Number=1,Type=Float,Description="score A">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   10  .  A   T   .    .      scoreA=0.1
""")
    repo = a_grr().with_resource("vcf", builder).build_repo(tmp_path)
    score = AlleleScore(repo.get_resource("vcf")).open()
    with score:
        assert score.table.yields_records is True
        # The class is chosen ONCE, at open -- before a single line is fetched.
        assert score._score_line_class is VCFScoreLine

        line = next(iter(score.fetch_lines("chr1", 10, 10)))
        assert type(line) is VCFScoreLine
        # ...and it is not the column-payload record score line: reading scoreA
        # through the record payload's __getitem__ would index the
        # (variant, allele index) pair, not the INFO field.
        assert not isinstance(line, (RecordScoreLine, ScoreLine))
        assert line.get_score("scoreA") == pytest.approx(0.1)


def test_record_backend_reads_a_record_through_the_record_score_line(
    tmp_path,
) -> None:
    # The record path, end to end: the in-memory backend yields records, so the
    # score wraps them in a RecordScoreLine, whose core fields come off the
    # record's named slots and whose scores come out of the payload.
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


def test_the_score_is_routed_before_it_reports_itself_open(tmp_path) -> None:
    # ``table_loaded = True`` is the score PUBLISHING itself: from that write
    # on, a second caller's ``open()`` takes the ``is_open()`` early return and
    # goes straight to reading ``_score_line_class``.  So the routing must
    # already be installed at that instant, or the second caller reads the
    # ``__init__`` default -- ``ScoreLine`` -- and hands it a record tuple,
    # which asserts (or under -O dies with "'tuple' object has no attribute
    # 'get'").
    #
    # Scores are shared (the in-memory CNV cache hands the same instance to
    # every caller in the process; gain-web-api serves from a thread pool), so
    # this window is reachable.  Rather than race a thread against it, stand in
    # the window itself: intercept the publishing write and look at what a
    # concurrent reader would see at exactly that moment.
    seen_at_publication: list[Any] = []

    class _ObservingPositionScore(PositionScore):
        def __setattr__(self, name: str, value: Any) -> None:
            if name == "table_loaded" and value is True:
                # what a thread taking the is_open() early return would use
                seen_at_publication.append(self._score_line_class)
            super().__setattr__(name, value)

    builder = (
        a_position_score()
        .with_score("s_float", "float")
        .with_data("""
            chrom  pos_begin  s_float
            1      10         0.5
        """)
    )
    repo = a_grr().with_resource("pos", builder).build_repo(tmp_path)
    score = _ObservingPositionScore(repo.get_resource("pos"))
    with score.open():
        assert score.table.yields_records is True
        # The score never published itself as open while still routed to the
        # adapter score line: a concurrent reader can only ever see the record
        # routing this table actually needs.
        assert seen_at_publication == [RecordScoreLine]


def test_bigwig_backend_yields_the_record_score_line(tmp_path) -> None:
    # Since #238 the bigWig backend is on the record contract too: it yields
    # records whose payload is the four-element interval, so the score is routed
    # to RecordScoreLine and read by index (``index: 3`` -> the value cell), not
    # to the retired adapter ScoreLine.  It is the third backend on that leg,
    # alongside in-memory and tabix.
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
        assert score.table.yields_records is True
        line = next(iter(score.fetch_lines("chr1", 5, 5)))
        assert isinstance(line, RecordScoreLine)
        assert not isinstance(line, VCFScoreLine)
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
