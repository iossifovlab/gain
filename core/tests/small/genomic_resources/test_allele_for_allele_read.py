# pylint: disable=redefined-outer-name,C0114,C0116
"""Interface A of the allele plane: one allele answers one row (#1753).

``get_allele_scores_for_allele`` and its singular are the reductions of
the ``_rows`` walk that answer exactly one row: the allele's only row on a
resource declaring ``allele_multiplicity: one``.  A second row on such a
resource is a data error -- warned about once per resource now, refused
from ``ALLELE_MULTIPLICITY_ENFORCEMENT_RELEASE`` on -- and a resource
declaring ``many`` is refused outright, its caller pointed at the
``_rows`` / ``_agg`` reads.

Every test runs on both the ``.mem`` and the bgzip+tabix backend: the two
walk a point through different code, and the read must not care which it
sits on.
"""

import logging
import pathlib

import pytest
from gain.genomic_resources import resource_types
from gain.genomic_resources.genomic_scores import (
    AlleleMultiplicityError,
    AlleleScore,
    build_allele_score_from_resource,
)
from gain.genomic_resources.resource_types import (
    ALLELE_MULTIPLICITY_ENFORCEMENT_RELEASE,
)
from gain.genomic_resources.score_filter import ScoreFilterError
from gain.genomic_resources.testing.builders import (
    AlleleScoreBuilder,
    a_grr,
    an_allele_score,
)


@pytest.fixture(params=[False, True], ids=["mem", "tabix"])
def tabix(request: pytest.FixtureRequest) -> bool:
    return bool(request.param)


def _builder(
    data: str, *, tabix: bool, multiplicity: str | None = None,
) -> AlleleScoreBuilder:
    builder = (
        an_allele_score()
        .with_score("freq", "float")
        .with_score("id", "str")
        .with_data(data))
    if multiplicity is not None:
        builder = builder.with_allele_multiplicity(multiplicity)
    if tabix:
        builder = builder.with_tabix()
    return builder


def _score(
    tmp_path: pathlib.Path, data: str, *, tabix: bool,
    multiplicity: str | None = None,
    resource_id: str = "scores/alleles",
) -> AlleleScore:
    """Build the score as ``resource_id`` of a GRR, so that the id a
    message is checked for is never the empty one a bare resource has."""
    repo = a_grr().with_resource(resource_id, _builder(
        data, tabix=tabix, multiplicity=multiplicity)).build_repo(tmp_path)
    return build_allele_score_from_resource(repo.get_resource(resource_id))


_UNIQUE_ROWS = """
    chrom  pos_begin  reference  alternative  freq  id
    1      10         A          C            0.2   ac
    1      10         A          G            0.1   ag
    1      16         C          T            0.3   ct
"""


@pytest.fixture
def unique_alleles(tmp_path: pathlib.Path, tabix: bool) -> AlleleScore:
    """Every allele held by one row; two alleles share position 10."""
    return _score(tmp_path, _UNIQUE_ROWS, tabix=tabix)


def test_a_unique_allele_answers_every_score_in_definition_order(
    unique_alleles: AlleleScore,
) -> None:
    with unique_alleles.open() as score:
        values = score.get_allele_scores_for_allele("1", 10, "A", "G")

    assert values == (0.1, "ag")


def test_a_unique_allele_answers_the_scores_asked_in_the_order_asked(
    unique_alleles: AlleleScore,
) -> None:
    with unique_alleles.open() as score:
        values = score.get_allele_scores_for_allele(
            "1", 10, "A", "G", scores=["id", "freq"])

    assert values == ("ag", 0.1)


@pytest.mark.parametrize(("pos", "ref", "alt"), [
    (10, "A", "T"),
    (12, "A", "C"),
    (16, "C", "A"),
])
def test_an_allele_no_row_holds_answers_none(
    unique_alleles: AlleleScore, pos: int, ref: str, alt: str,
) -> None:
    """``1:10:A:T`` shares its position with two rows, differing in alt."""
    with unique_alleles.open() as score:
        values = score.get_allele_scores_for_allele("1", pos, ref, alt)

    assert values is None


@pytest.mark.parametrize(("condition", "expected"), [
    ("freq < 0.15", (0.1, "ag")),
    ("freq > 0.15", None),
])
def test_the_filter_keeps_or_rejects_the_only_row(
    unique_alleles: AlleleScore,
    condition: str, expected: tuple[float, str] | None,
) -> None:
    with unique_alleles.open() as score:
        values = score.get_allele_scores_for_allele(
            "1", 10, "A", "G",
            score_filter=score.compile_filter(condition))

    assert values == expected


def test_a_row_starting_earlier_and_spanning_pos_is_not_the_allele(
    tmp_path: pathlib.Path, tabix: bool,
) -> None:
    """Agrees with ``get_allele_scores_for_allele_rows``: exact on the
    position, so the 8-12 row -- first in the file, and the legacy
    ``fetch_allele_scores`` answer -- is not the allele at 10."""
    score = _score(tmp_path, """
        chrom  pos_begin  pos_end  reference  alternative  freq  id
        1      8          12       A          C            0.7   span
        1      10         10       A          C            0.2   ac
    """, tabix=tabix)

    with score.open() as opened:
        values = opened.get_allele_scores_for_allele("1", 10, "A", "C")
        rows = opened.get_allele_scores_for_allele_rows("1", 10, "A", "C")

    assert values == (0.2, "ac")
    assert rows == [values]


def test_only_a_spanning_row_answers_none(
    tmp_path: pathlib.Path, tabix: bool,
) -> None:
    score = _score(tmp_path, """
        chrom  pos_begin  pos_end  reference  alternative  freq  id
        1      8          12       A          C            0.7   span
    """, tabix=tabix)

    with score.open() as opened:
        values = opened.get_allele_scores_for_allele("1", 10, "A", "C")

    assert values is None


# The request checks, each made on an allele no row carries, so a refusal
# cannot hide behind data: both forms of the read refuse alike.
_READS = {
    "plural": lambda opened, chrom, *, score_id, pos=200, alt="C", **kw: (
        opened.get_allele_scores_for_allele(
            chrom, pos, "A", alt, scores=[score_id], **kw)),
    "singular": lambda opened, chrom, *, score_id, pos=200, alt="C", **kw: (
        opened.get_allele_score_for_allele(
            chrom, pos, "A", alt, score=score_id, **kw)),
}


@pytest.mark.parametrize("read", sorted(_READS))
def test_an_unknown_contig_is_refused(
    unique_alleles: AlleleScore, read: str,
) -> None:
    with unique_alleles.open() as score, pytest.raises(
            ValueError, match="not among the available chromosomes"):
        _READS[read](score, "2", score_id="freq")


@pytest.mark.parametrize("read", sorted(_READS))
def test_a_foreign_filter_is_refused(
    unique_alleles: AlleleScore, tmp_path: pathlib.Path, tabix: bool,
    read: str,
) -> None:
    other = _score(tmp_path / "other", _UNIQUE_ROWS, tabix=tabix)

    with unique_alleles.open() as score, other.open() as other_score:
        foreign = other_score.compile_filter("freq > 0.15")

        with pytest.raises(ScoreFilterError, match="compiled against"):
            _READS[read](score, "1", score_id="freq", score_filter=foreign)


@pytest.mark.parametrize("read", sorted(_READS))
def test_an_unknown_score_is_refused_with_the_valid_ids_listed(
    unique_alleles: AlleleScore, read: str,
) -> None:
    with unique_alleles.open() as score, pytest.raises(
            ValueError,
            match=r"score 'nope' is not defined by resource '[^']*'; "
                  r"it has \['freq', 'id'\]"):
        _READS[read](score, "1", score_id="nope")


@pytest.mark.parametrize(("alt", "expected"), [("G", "ag"), ("T", None)])
def test_the_singular_answers_the_bare_value_or_none(
    unique_alleles: AlleleScore, alt: str, expected: str | None,
) -> None:
    with unique_alleles.open() as score:
        value = score.get_allele_score_for_allele(
            "1", 10, "A", alt, score="id")

    assert value == expected


def test_the_singular_refuses_score_none_on_two_scores(
    unique_alleles: AlleleScore,
) -> None:
    with unique_alleles.open() as score, pytest.raises(
            ValueError, match="exactly one"):
        score.get_allele_score_for_allele("1", 10, "A", "G")


def test_the_singular_honours_score_none_on_one_score(
    tmp_path: pathlib.Path, tabix: bool,
) -> None:
    builder = (
        an_allele_score()
        .with_score("freq", "float")
        .with_data("""
            chrom  pos_begin  reference  alternative  freq
            1      10         A          G            0.1
        """))
    if tabix:
        builder = builder.with_tabix()
    single = build_allele_score_from_resource(
        builder.build_resource(tmp_path))

    with single.open() as score:
        value = score.get_allele_score_for_allele("1", 10, "A", "G")

    assert value == 0.1


@pytest.fixture
def declared_many(tmp_path: pathlib.Path, tabix: bool) -> AlleleScore:
    """Unique data, but declaring ``allele_multiplicity: many``."""
    return _score(tmp_path, _UNIQUE_ROWS, tabix=tabix, multiplicity="many")


@pytest.mark.parametrize("read", sorted(_READS))
@pytest.mark.parametrize("alt", ["T", "G"], ids=["absent", "unique"])
def test_a_many_resource_is_refused_at_the_call(
    declared_many: AlleleScore, read: str, alt: str,
) -> None:
    """Refused on an allele no row carries as on one a single row does:
    the refusal is the declaration's, not the data's, so nothing is read.
    The message points at the reads that answer several rows."""
    with declared_many.open() as score, pytest.raises(
            AlleleMultiplicityError) as excinfo:
        _READS[read](score, "1", score_id="freq", pos=10, alt=alt)

    assert excinfo.value.resource_id == "scores/alleles"
    assert excinfo.value.allele is None
    message = str(excinfo.value)
    assert "allele_multiplicity: many" in message
    assert "get_allele_scores_for_allele_rows" in message
    assert "_agg" in message


@pytest.mark.parametrize("read", sorted(_READS))
def test_a_many_resource_checks_the_request_first(
    declared_many: AlleleScore, read: str,
) -> None:
    """The order is pinned: an unknown contig or score id is the caller's
    error on any resource, so it is reported ahead of the declaration."""
    with declared_many.open() as score:
        with pytest.raises(ValueError, match="not among the available"):
            _READS[read](score, "2", score_id="freq")
        with pytest.raises(ValueError, match="score 'nope' is not defined"):
            _READS[read](score, "1", score_id="nope")


# ---------------------------------------------------------------------------
# An undeclared duplicate: two rows hold ``1:10:A:C`` on a resource that
# declares (by default) one row per allele.  Before
# ALLELE_MULTIPLICITY_ENFORCEMENT_RELEASE the read answers the first row and
# warns once per resource; from it on, the read refuses.
# ---------------------------------------------------------------------------

_REPEATED_ROWS = """
    chrom  pos_begin  reference  alternative  freq  id
    1      10         A          C            0.2   ac
    1      10         A          C            0.5   ac2
    1      16         C          T            0.3   ct
"""


@pytest.fixture
def repeated_alleles(tmp_path: pathlib.Path, tabix: bool) -> AlleleScore:
    """``1:10:A:C`` published twice, differing in ``freq`` and ``id``."""
    return _score(tmp_path, _REPEATED_ROWS, tabix=tabix)


def _multiplicity_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage() for record in caplog.records
        if record.levelno == logging.WARNING
        and "allele_multiplicity: many" in record.getMessage()
    ]


def test_an_undeclared_duplicate_answers_the_first_row_and_warns(
    repeated_alleles: AlleleScore, caplog: pytest.LogCaptureFixture,
) -> None:
    with repeated_alleles.open() as score:
        values = score.get_allele_scores_for_allele("1", 10, "A", "C")

    # First-wins until ALLELE_MULTIPLICITY_ENFORCEMENT_RELEASE; delete then.
    assert values == (0.2, "ac")
    [warning] = _multiplicity_warnings(caplog)
    assert "'scores/alleles'" in warning
    assert ALLELE_MULTIPLICITY_ENFORCEMENT_RELEASE in warning
    assert "allele_multiplicity: many" in warning
    assert "1:10:A:C" not in warning


def test_the_warning_is_once_per_resource_across_reads_and_instances(
    tmp_path: pathlib.Path, tabix: bool, caplog: pytest.LogCaptureFixture,
) -> None:
    """Per process, not per instance: a score is rebuilt per annotator,
    per request and per statistics task, so two builds of the one
    resource, each read twice, still warn once."""
    resource = _score(tmp_path, _REPEATED_ROWS, tabix=tabix).resource
    first = build_allele_score_from_resource(resource)
    second = build_allele_score_from_resource(resource)

    for built in (first, second):
        with built.open() as score:
            for _ in range(2):
                score.get_allele_scores_for_allele("1", 10, "A", "C")
                score.get_allele_score_for_allele(
                    "1", 10, "A", "C", score="id")

    assert len(_multiplicity_warnings(caplog)) == 1


def test_each_offending_resource_gets_its_own_warning(
    tmp_path: pathlib.Path, tabix: bool, caplog: pytest.LogCaptureFixture,
) -> None:
    repo = (
        a_grr()
        .with_resource("scores/one", _builder(_REPEATED_ROWS, tabix=tabix))
        .with_resource("scores/two", _builder(_REPEATED_ROWS, tabix=tabix))
        .build_repo(tmp_path))
    one, two = (
        build_allele_score_from_resource(repo.get_resource(resource_id))
        for resource_id in ("scores/one", "scores/two"))

    for built in (one, two):
        with built.open() as score:
            score.get_allele_scores_for_allele("1", 10, "A", "C")

    warnings = _multiplicity_warnings(caplog)
    assert len(warnings) == 2
    assert "'scores/one'" in warnings[0]
    assert "'scores/two'" in warnings[1]


@pytest.mark.parametrize(("condition", "expected"), [
    ("freq < 0.3", (0.2, "ac")),
    ("freq > 0.3", None),
], ids=["hides-the-second", "hides-the-first"])
def test_a_filter_does_not_hide_the_duplicate(
    repeated_alleles: AlleleScore, caplog: pytest.LogCaptureFixture,
    condition: str, expected: tuple[float, str] | None,
) -> None:
    """Rows are counted before the filter: hiding either row leaves a
    duplicate, which still warns.  The answer is the FIRST row, filtered
    as a unique allele's row is -- so hiding the first answers ``None``,
    never the second row."""
    with repeated_alleles.open() as score:
        values = score.get_allele_scores_for_allele(
            "1", 10, "A", "C",
            score_filter=score.compile_filter(condition))

    # First-wins until ALLELE_MULTIPLICITY_ENFORCEMENT_RELEASE; delete then.
    assert values == expected
    assert len(_multiplicity_warnings(caplog)) == 1


def test_a_unique_allele_of_a_resource_with_duplicates_does_not_warn(
    repeated_alleles: AlleleScore, caplog: pytest.LogCaptureFixture,
) -> None:
    with repeated_alleles.open() as score:
        values = score.get_allele_scores_for_allele("1", 16, "C", "T")

    assert values == (0.3, "ct")
    assert _multiplicity_warnings(caplog) == []


@pytest.fixture
def enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Act as an installed release at or past the enforcement release."""
    monkeypatch.setattr(
        resource_types, "allele_multiplicity_enforced",
        lambda *_: True)


@pytest.mark.usefixtures("enforced")
@pytest.mark.parametrize("read", sorted(_READS))
@pytest.mark.parametrize("condition", [None, "freq < 0.3", "freq > 0.3"],
                         ids=["unfiltered", "hides-second", "hides-first"])
def test_an_enforced_undeclared_duplicate_is_refused(
    repeated_alleles: AlleleScore, caplog: pytest.LogCaptureFixture,
    read: str, condition: str | None,
) -> None:
    """Naming the resource and the allele; a filter hiding either row
    still refuses, since the rows are counted before it."""
    with repeated_alleles.open() as score:
        kwargs = {} if condition is None else {
            "score_filter": score.compile_filter(condition)}
        with pytest.raises(AlleleMultiplicityError) as excinfo:
            _READS[read](
                score, "1", score_id="freq", pos=10, alt="C", **kwargs)

    assert excinfo.value.resource_id == "scores/alleles"
    assert excinfo.value.allele == ("1", 10, "A", "C")
    message = str(excinfo.value)
    assert "'scores/alleles'" in message
    assert "1:10:A:C" in message
    assert "allele_multiplicity: many" in message
    assert _multiplicity_warnings(caplog) == []


@pytest.mark.usefixtures("enforced")
def test_an_enforced_unique_allele_is_answered(
    repeated_alleles: AlleleScore,
) -> None:
    with repeated_alleles.open() as score:
        values = score.get_allele_scores_for_allele("1", 16, "C", "T")

    assert values == (0.3, "ct")
