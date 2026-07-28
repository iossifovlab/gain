# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""What the score layer knows about bigWig, held to its promises.

The companion of ``gain.genomic_resources.bigwig_scores``: the identity value
read, the empty NA default, and the open-time config validation that is what
lets the read be an identity at all.

The governing principle, which decides which side of the line a rule falls on:
**reject what corrupts values; warn about what is merely inert.**  A score
``type:`` of ``int`` would silently truncate every value, and a column
``index:`` other than the deprecated 3 would read a column that no longer
exists -- both are refused at open.  A ``chrom:``/``pos_begin:`` block on a
bigWig table is read by nothing at all, so it is warned about and ignored.
"""
from __future__ import annotations

import pathlib
import textwrap
from typing import Any

import pytest
from gain.genomic_resources.bigwig_scores import (
    extract_bigwig_value,
    extract_bigwig_value_na,
)
from gain.genomic_resources.genomic_position_table.record import PAYLOAD
from gain.genomic_resources.genomic_scores import PositionScore
from gain.genomic_resources.repository import GR_CONF_FILE_NAME
from gain.genomic_resources.score_def import GenomicScoreDef
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    setup_bigwig,
    setup_directories,
)
from gain.genomic_resources.testing.builders import (
    BigWigScoreBuilder,
    a_bigwig_score,
    a_grr,
)

_DATA = """
    chr1  0   10  0.11
    chr1  10  20  0.22
"""


def _open_bigwig(
    tmp_path: pathlib.Path,
    builder: BigWigScoreBuilder | None = None,
) -> PositionScore:
    """Build and open a one-score bigWig position score."""
    if builder is None:
        builder = (
            a_bigwig_score()
            .with_score("bw", "float")
            .with_data(_DATA)
            .with_chrom_lens({"chr1": 1000})
        )
    repo = a_grr().with_resource("bw", builder).build_repo(tmp_path)
    return PositionScore(repo.get_resource("bw")).open()


def test_the_bigwig_value_read_is_the_identity(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bigWig record's PAYLOAD *is* the value -- nothing parses it.

    The whole point of narrowing the payload: a bigWig carries a float, the
    score's declared type is float, and ``parse_value`` on that pair is the
    identity function dressed as a dict lookup plus a call.  So the extractor
    returns the payload, and ``parse_value`` is never reached -- pinned by
    making it explode rather than by counting calls, so the read cannot be
    "mostly" identity.
    """
    def _boom(self: GenomicScoreDef, value: Any) -> Any:
        raise AssertionError(
            f"parse_value reached on the bigWig path for {value!r}")

    score = _open_bigwig(tmp_path)
    with score:
        record = next(iter(score.fetch_records("chr1", 5, 5)))
        assert record[PAYLOAD] == pytest.approx(0.11)

        monkeypatch.setattr(GenomicScoreDef, "parse_value", _boom)
        assert score.get_score_from_record(record, "bw") == \
            pytest.approx(0.11)


def test_the_identity_extractor_is_a_pure_function_of_the_payload() -> None:
    """``extract_bigwig_value`` needs no table, no file and no index."""
    score_def = _a_scoredef()
    record = ("chr1", 1, 10, None, None, 0.11)
    assert extract_bigwig_value(record, score_def) == pytest.approx(0.11)


def _a_scoredef(na_values: Any = ()) -> GenomicScoreDef:
    return GenomicScoreDef(
        score_id="bw", desc="", value_type="float",
        aggregator=None,
        small_values_desc=None, large_values_desc=None,
        hist_conf=None, col_name=None, col_index=None,
        value_parser=float, na_values=na_values,
    )


def test_the_na_extractor_is_the_identity_plus_a_sentinel_check() -> None:
    score_def = _a_scoredef(na_values="-1")
    assert extract_bigwig_value_na(
        ("chr1", 1, 10, None, None, -1.0), score_def) is None
    assert extract_bigwig_value_na(
        ("chr1", 1, 10, None, None, 0.5), score_def) == pytest.approx(0.5)


# --- the bulk column read --------------------------------------------------


def test_the_only_bigwig_column_is_the_payload_itself(
    tmp_path: pathlib.Path,
) -> None:
    """Column 0 is the value; the bulk read agrees with the record read.

    A bigWig payload is one number, so there is exactly one column and its
    index is 0 -- the same 0 ``GenomicScore.open`` resolves every bigWig
    score's ``score_index`` to, whatever the config said.  The two reads of
    that column, per record and in bulk, must produce the same values.
    """
    builder = (
        a_bigwig_score()
        .with_score("bw", "float")
        .with_data("""
            chr1  0   2   0.5
            chr1  2   4   0.7
            chr1  4   6   0.9
        """)
        .with_chrom_lens({"chr1": 100})
    )
    score = _open_bigwig(tmp_path, builder)
    with score:
        assert score.score_definitions["bw"].score_index == 0
        batches = list(score.table.get_region_value_arrays(
            "chr1", 1, 6, [0], 100))
        bulk = [value for _, _, cols in batches for value in cols[0]]
        per_record = [
            score.get_score_from_record(record, "bw")
            for record in score.fetch_records("chr1", 1, 6)
        ]
    assert bulk == pytest.approx([0.5, 0.7, 0.9])
    assert per_record == pytest.approx(bulk)


@pytest.mark.parametrize("column", [1, 2, 3, 4, -1])
def test_a_bigwig_serves_no_column_but_zero(
    tmp_path: pathlib.Path, column: int,
) -> None:
    """Any other column is refused, not quietly served something else.

    The old four-tuple payload made ``0`` the chromosome and ``3`` the value;
    a caller that still asks for either of those is asking for a column that
    does not exist, and being handed a contig string (which is what the
    pre-narrowing reconstruction did for an unrecognised index) turns an
    aborted repair into a silently all-zero histogram.
    """
    score = _open_bigwig(tmp_path)
    with score, pytest.raises(KeyError, match="bw"):
        list(score.table.get_region_value_arrays("chr1", 1, 20, [column], 100))


# --- open-time config validation -------------------------------------------
#
# These configs cannot be authored with ``a_bigwig_score``, and deliberately so:
# the builder emits the canonical config, and what is under test here is the
# misconfiguration itself.  Hand-rolled yaml is the right answer for that, as
# it is wherever the broken config is the subject.


def _a_bigwig_resource(
    tmp_path: pathlib.Path, config: str, *, resource_id: str = "bw",
) -> PositionScore:
    """Realize a bigWig resource from a hand-written config; do not open."""
    setup_directories(tmp_path, {
        "grr.yaml": f"id: t\ntype: directory\ndirectory: {tmp_path!s}\n",
        resource_id: {GR_CONF_FILE_NAME: textwrap.dedent(config)},
    })
    setup_bigwig(
        tmp_path / resource_id / "data.bw",
        "chr1  0  10  0.11\nchr1  10  20  0.22",
        {"chr1": 1000})
    repo = build_filesystem_test_repository(tmp_path)
    return PositionScore(repo.get_resource(resource_id))


_TWO_SCORES = """
    type: position_score
    table:
        filename: data.bw
    scores:
    - id: bw
      type: float
    - id: bw2
      type: float
"""

_INT_SCORE = """
    type: position_score
    table:
        filename: data.bw
    scores:
    - id: bw
      type: int
"""

_STR_SCORE = """
    type: position_score
    table:
        filename: data.bw
    scores:
    - id: bw
      type: str
"""


def _indexed(index: int, *, key: str = "index") -> str:
    return (
        "type: position_score\n"
        "table:\n"
        "    filename: data.bw\n"
        "scores:\n"
        "- id: bw\n"
        "  type: float\n"
        f"  {key}: {index}\n"
    )


@pytest.mark.parametrize(("config", "expected"), [
    pytest.param(_TWO_SCORES, "bw2", id="more-than-one-score"),
    pytest.param(_INT_SCORE, "int", id="type-int"),
    pytest.param(_STR_SCORE, "str", id="type-str"),
    pytest.param(_indexed(0), "0", id="index-0"),
    pytest.param(_indexed(1), "1", id="index-1"),
    pytest.param(_indexed(2), "2", id="index-2"),
    pytest.param(_indexed(4), "4", id="index-4"),
    pytest.param(_indexed(4, key="column_index"), "4", id="column_index-4"),
    pytest.param(_indexed(0, key="column_index"), "0", id="column_index-0"),
])
def test_a_corrupting_bigwig_config_is_refused_at_open(
    tmp_path: pathlib.Path, config: str, expected: str,
) -> None:
    """Every refusal names the resource AND the score.

    "Which resource" is the question a ``grr_manage`` run over a few hundred
    resources leaves you with, and "which score" is the one a multi-score
    config leaves you with -- a message giving neither costs a bisect.  The
    refusal is a ``ValueError``: a resource config is data, and bad data is
    reported, not asserted away (``python -O`` strips asserts).
    """
    score = _a_bigwig_resource(tmp_path, config)
    with pytest.raises(ValueError, match=expected) as exc_info:
        score.open()
    message = str(exc_info.value)
    assert "bw" in message
    # The resource id, as the repo knows it.
    assert score.resource_id in message


def test_a_bigwig_score_addressed_by_column_name_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    """A bigWig has no header, so a name has nothing to resolve against.

    This one is NOT a bigWig-specific rule and is deliberately left to the
    generic resolution: a table with no header refuses a by-name score
    whatever its backend.  What is pinned here is that the message still names
    both things.
    """
    score = _a_bigwig_resource(tmp_path, """
        type: position_score
        table:
            filename: data.bw
        scores:
        - id: bw
          type: float
          column_name: value
    """)
    with pytest.raises(ValueError, match="no header") as exc_info:
        score.open()
    message = str(exc_info.value)
    assert "bw" in message
    assert score.resource_id in message


def test_the_canonical_bigwig_config_addresses_no_column_at_all(
    tmp_path: pathlib.Path,
) -> None:
    """No ``index:``, no ``column_index:`` -- and it opens and reads.

    The canonical config, and the one the testing builder now emits.  A bigWig
    has exactly one value and no columns to choose between, so there is
    nothing for a config to address; before this change ``open()`` refused
    such a resource with "configures neither column_name nor column_index".
    """
    score = _a_bigwig_resource(tmp_path, """
        type: position_score
        table:
            filename: data.bw
        scores:
        - id: bw
          type: float
    """)
    with score.open():
        assert score.score_definitions["bw"].score_index == 0
        record = next(iter(score.fetch_records("chr1", 5, 5)))
        assert score.get_score_from_record(record, "bw") == \
            pytest.approx(0.11)


@pytest.mark.parametrize("key", ["index", "column_index"])
def test_the_deprecated_value_index_opens_and_warns_once(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    key: str,
) -> None:
    """``index: 3`` still opens -- and says, once per open, to delete it.

    16 deployed bigWig resources carry the key (one of them with the comment
    "this makes no sense and should be removed" already in its yaml), so
    refusing it would break every one of them on the day this ships.  It
    addressed the value inside the four-element payload a bigWig record used
    to carry; the payload is now the value, so the key resolves to nothing and
    is accepted as a no-op.  The warning is what gets it deleted from the GRRs.
    """
    score = _a_bigwig_resource(tmp_path, _indexed(3, key=key))
    with caplog.at_level("WARNING"), score.open():
        record = next(iter(score.fetch_records("chr1", 5, 5)))
        assert score.get_score_from_record(record, "bw") == \
            pytest.approx(0.11)

    warnings = [
        rec.getMessage() for rec in caplog.records
        if rec.levelname == "WARNING" and "deprecated" in rec.getMessage()
    ]
    assert len(warnings) == 1, warnings
    assert score.resource_id in warnings[0]
    assert "bw" in warnings[0]


def test_a_broken_bigwig_resource_can_still_be_described(
    tmp_path: pathlib.Path,
) -> None:
    """Constructing the score must NOT refuse -- only ``open()`` may.

    ``GenomicScoreImplementation.__init__`` builds the score eagerly, so a
    validation in ``GenomicScore.__init__`` would make a broken bigWig
    resource impossible to list, describe or report on -- which is exactly
    what ``grr_manage`` has to do with it.
    """
    score = _a_bigwig_resource(tmp_path, _INT_SCORE)
    assert score.get_all_scores() == ["bw"]
    with pytest.raises(ValueError, match="int"):
        score.open()


# --- the NA default --------------------------------------------------------


def test_a_bigwig_float_score_defaults_to_no_na_values(
    tmp_path: pathlib.Path,
) -> None:
    """The float default is text sentinels a float payload cannot equal.

    ``("", "nan", ".", "NA")`` exists because a tabular backend hands the score
    layer strings.  A bigWig hands it a ``float``, so not one of the four can
    ever match -- dead config, a set membership test per value, and four
    tokens of noise in the resource's statistics hash.  Emptying it is also
    what puts every deployed bigWig resource on the identity extractor rather
    than the NA one.
    """
    score = _open_bigwig(tmp_path)
    with score:
        assert score.score_definitions["bw"].na_values == set()
        assert score._extract_value is extract_bigwig_value


def test_a_tabix_float_score_keeps_the_text_na_default(
    tmp_path: pathlib.Path,
) -> None:
    """The emptying is bigWig's alone -- a text backend still needs the four.

    The other half of the previous test: this is a change to what ONE backend
    defaults to, not to ``normalize_na_values``.
    """
    from gain.genomic_resources.testing.builders import a_position_score
    builder = (
        a_position_score()
        .with_score("s", "float")
        .with_data("""
            chrom  pos_begin  s
            1      10         0.5
        """)
        .with_tabix()
    )
    repo = a_grr().with_resource("pos", builder).build_repo(tmp_path)
    score = PositionScore(repo.get_resource("pos")).open()
    with score:
        assert {"", "nan", ".", "NA"} <= score.score_definitions["s"].na_values


def test_a_configured_bigwig_na_value_survives_the_emptied_default(
    tmp_path: pathlib.Path,
) -> None:
    """A resource that states ``na_values`` means it, and keeps it.

    The default is emptied only where the raw config OMITS the key -- read off
    the ``scores:`` entries, because by the time a parsed definition exists its
    ``na_values`` is already the default and no longer says whether the config
    asked for it.
    """
    builder = (
        a_bigwig_score()
        .with_score("bw", "float")
        .with_na_values("-1")
        .with_data("""
            chr1  0   10  -1
            chr1  10  20  1
        """)
        .with_chrom_lens({"chr1": 1000})
    )
    score = _open_bigwig(tmp_path, builder)
    with score:
        assert score.score_definitions["bw"].na_values == {"-1", -1.0}
        assert score._extract_value is extract_bigwig_value_na
        na_record = next(iter(score.fetch_records("chr1", 1, 1)))
        real_record = next(iter(score.fetch_records("chr1", 11, 11)))
        assert score.get_score_from_record(na_record, "bw") is None
        assert score.get_score_from_record(real_record, "bw") == \
            pytest.approx(1.0)


# --- inert table config: warned about, ignored ------------------------------

# The shape the deployed ``hg19/scores/Linsight`` resource carries: a bigWig
# table describing itself with the three positional column blocks of a tabular
# one.  ``BigWigTable.open`` does call ``_set_core_column_keys()``, so
# ``chrom_key``/``pos_begin_key``/``pos_end_key`` are set from these -- and
# nothing in ``table_bigwig`` ever reads any of the three.  So the keys change
# no value, and the resource must keep opening and annotating correctly.
_LINSIGHT_SHAPED = """
    type: position_score
    table:
        filename: data.bw
        chrom:
            index: 0
        pos_begin:
            index: 1
        pos_end:
            index: 2
    scores:
    - id: bw
      type: float
      index: 3
"""


def test_a_linsight_shaped_bigwig_table_warns_opens_and_reads(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Inert config is warned about and ignored -- never refused.

    The other side of the principle from the refusals above: these keys
    corrupt nothing, so refusing them would break a deployed resource to no
    purpose.  The warning names each ignored key so the config can be cleaned
    up; the read is unaffected.
    """
    with caplog.at_level("WARNING"):
        score = _a_bigwig_resource(tmp_path, _LINSIGHT_SHAPED)
    warned = " ".join(
        rec.getMessage() for rec in caplog.records
        if rec.levelname == "WARNING")
    for key in ("chrom", "pos_begin", "pos_end"):
        assert key in warned, (key, warned)

    with score.open():
        record = next(iter(score.fetch_records("chr1", 5, 5)))
        assert score.get_score_from_record(record, "bw") == \
            pytest.approx(0.11)
        record = next(iter(score.fetch_records("chr1", 15, 15)))
        assert score.get_score_from_record(record, "bw") == \
            pytest.approx(0.22)


def test_a_header_on_a_bigwig_table_is_warned_about_and_ignored(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A bigWig is binary and has no header; a configured one names nothing."""
    config = """
        type: position_score
        table:
            filename: data.bw
            header_mode: list
            header: [chrom, pos_begin, pos_end, value]
        scores:
        - id: bw
          type: float
    """
    with caplog.at_level("WARNING"):
        score = _a_bigwig_resource(tmp_path, config)
    warned = " ".join(
        rec.getMessage() for rec in caplog.records
        if rec.levelname == "WARNING")
    assert "header" in warned

    with score.open():
        record = next(iter(score.fetch_records("chr1", 5, 5)))
        assert score.get_score_from_record(record, "bw") == \
            pytest.approx(0.11)
