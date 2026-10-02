# pylint: disable=C0116,W0621
"""The statistics build checks an allele score's ``allele_multiplicity``.

gain#1752: a ``one`` resource holding several rows for one
``(chrom, pos, ref, alt)`` key warns once per resource before the
enforcement release and fails the allele statistic from it on.  A
``many`` resource is never checked.
"""
import logging
import pathlib
from typing import Any

import pytest
from gain.genomic_resources import resource_types
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.genomic_scores import AlleleMultiplicityError
from gain.genomic_resources.histogram import NumberHistogramConfig
from gain.genomic_resources.implementations.genomic_scores_impl import scan
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.resource_types import (
    ALLELE_MULTIPLICITY_ENFORCEMENT_RELEASE,
)
from gain.genomic_resources.statistics.alleles import (
    ALLELE_STATISTICS_FILE,
    AlleleStatistics,
    RepeatedAllele,
)
from gain.genomic_resources.testing.builders import a_grr, an_allele_score

_RESOURCE_ID = "scores/repeated"

# chr1:20 A>G three times; everything else distinct.
_REPEATED_TABLE = """
    chrom  pos_begin  reference  alternative  score
    chr1   10         C          T            0.1
    chr1   20         A          G            0.2
    chr1   20         A          G            0.3
    chr1   20         A          G            0.4
    chr1   30         G          A            0.5
"""


def _allele_score(
    data: str, *, multiplicity: str | None = None, tabix: bool = True,
) -> Any:
    builder = an_allele_score().with_score("score", "float").with_data(data)
    if multiplicity is not None:
        builder = builder.with_allele_multiplicity(multiplicity)
    return builder.with_tabix() if tabix else builder


def _realize(builder: Any, root: pathlib.Path) -> GenomicResource:
    repo = a_grr().with_resource(_RESOURCE_ID, builder).build_repo(root)
    return repo.get_resource(_RESOURCE_ID)


def _build(root: pathlib.Path, *extra: str) -> None:
    cli_manage(["repo-stats", "-R", str(root), "-j", "1", *extra])


def _multiplicity_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage() for record in caplog.records
        if record.levelno == logging.WARNING
        and "allele_multiplicity" in record.getMessage()
    ]


@pytest.mark.parametrize("declared", ["one", None])
def test_a_repeated_key_warns_once_and_still_builds(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    declared: str | None,
) -> None:
    checked = _realize(
        _allele_score(_REPEATED_TABLE, multiplicity=declared),
        tmp_path / "checked")
    unchecked = _realize(
        _allele_score(_REPEATED_TABLE, multiplicity="many"),
        tmp_path / "unchecked")
    _build(tmp_path / "unchecked")
    caplog.clear()

    with caplog.at_level(logging.WARNING):
        _build(tmp_path / "checked")

    [warning] = _multiplicity_warnings(caplog)
    assert _RESOURCE_ID in warning
    assert "chr1:20:A:G" in warning
    assert "3 rows" in warning
    assert "allele_multiplicity: many" in warning
    assert ALLELE_MULTIPLICITY_ENFORCEMENT_RELEASE in warning
    assert checked.get_file_content(ALLELE_STATISTICS_FILE) \
        == unchecked.get_file_content(ALLELE_STATISTICS_FILE)


def _multiplicity_errors(
    caplog: pytest.LogCaptureFixture,
) -> list[AlleleMultiplicityError]:
    """The refusals the failed build's task reported, one per task."""
    return [
        record.exc_info[1] for record in caplog.records
        if record.exc_info is not None
        and isinstance(record.exc_info[1], AlleleMultiplicityError)
    ]


@pytest.fixture
def enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        resource_types, "allele_multiplicity_enforced", lambda: True)


@pytest.mark.usefixtures("enforced")
@pytest.mark.parametrize("declared", ["one", None])
def test_an_enforced_repeated_key_fails_the_allele_statistic(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    declared: str | None,
) -> None:
    resource = _realize(
        _allele_score(_REPEATED_TABLE, multiplicity=declared), tmp_path)

    with pytest.raises(SystemExit):
        _build(tmp_path)

    [error] = _multiplicity_errors(caplog)
    assert error.resource_id == _RESOURCE_ID
    assert error.allele == ("chr1", 20, "A", "G")
    assert "3 rows" in str(error)
    assert "allele_multiplicity: many" in str(error)
    assert not resource.file_exists(ALLELE_STATISTICS_FILE)


@pytest.mark.usefixtures("enforced")
def test_an_enforced_repeated_key_writes_no_statistic_at_all(
    tmp_path: pathlib.Path,
) -> None:
    # Not only alleles.json: the histograms are written by the same last
    # task, after it, and must not land beside a refused allele statistic.
    # The chrom lengths are an earlier stage's, true whatever the rows.
    _realize(_allele_score(_REPEATED_TABLE), tmp_path)

    with pytest.raises(SystemExit):
        _build(tmp_path)

    statistics = tmp_path / _RESOURCE_ID / "statistics"
    written = sorted(
        str(path.relative_to(statistics))
        for path in statistics.rglob("*") if path.is_file())
    assert written == ["chrom_lengths.json"]


def _warned_allele(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    data: str,
    *,
    tabix: bool = True,
    region_size: int | None = None,
) -> list[str]:
    """Build a ``one`` resource and return its multiplicity warnings."""
    _realize(_allele_score(data, tabix=tabix), tmp_path)
    extra = () if region_size is None \
        else ("--region-size", str(region_size))
    with caplog.at_level(logging.WARNING):
        _build(tmp_path, *extra)
    return _multiplicity_warnings(caplog)


# Two repeated keys: chr1:20 A>G twice and chr1:90 C>T four times, with
# chr2:5 G>A repeated on the next contig.
_TWO_REPEATS_TABLE = """
    chrom  pos_begin  reference  alternative  score
    chr1   20         A          G            0.1
    chr1   20         A          G            0.2
    chr1   90         C          T            0.3
    chr1   90         C          T            0.4
    chr1   90         C          T            0.5
    chr1   90         C          T            0.6
    chr2   5          G          A            0.7
    chr2   5          G          A            0.8
"""


@pytest.mark.parametrize("tabix", [True, False])
@pytest.mark.parametrize("region_size", [None, 0, 1, 7, 50])
def test_the_earliest_repeated_key_is_the_one_reported(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    tabix: bool,
    region_size: int | None,
) -> None:
    [warning] = _warned_allele(
        tmp_path, caplog, _TWO_REPEATS_TABLE,
        tabix=tabix, region_size=region_size)

    assert "chr1:20:A:G" in warning
    assert "2 rows" in warning


@pytest.mark.parametrize("tabix", [True, False])
@pytest.mark.parametrize("region_size", [None, 1, 50])
def test_a_later_region_reports_its_key_when_the_first_has_none(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    tabix: bool,
    region_size: int | None,
) -> None:
    data = """
        chrom  pos_begin  reference  alternative  score
        chr1   20         A          G            0.1
        chr1   90         C          T            0.3
        chr1   90         C          T            0.4
        chr1   90         C          T            0.5
        chr2   5          G          A            0.7
        chr2   5          G          A            0.8
    """

    [warning] = _warned_allele(
        tmp_path, caplog, data, tabix=tabix, region_size=region_size)

    assert "chr1:90:C:T" in warning
    assert "3 rows" in warning


@pytest.mark.parametrize("tabix", [True, False])
def test_the_first_contig_in_genomic_order_is_reported(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    tabix: bool,
) -> None:
    # chr10 sorts before chr2 as a string, but not as a chromosome.
    data = """
        chrom  pos_begin  reference  alternative  score
        chr2   5          G          A            0.7
        chr2   5          G          A            0.8
        chr10  5          C          T            0.1
        chr10  5          C          T            0.2
    """

    [warning] = _warned_allele(tmp_path, caplog, data, tabix=tabix)

    assert "chr2:5:G:A" in warning


@pytest.fixture(params=[1, 2, 3, 100_000], ids=lambda size: f"batch{size}")
def batch_size(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
) -> int:
    monkeypatch.setattr(scan, "_SCAN_BATCH_SIZE", request.param)
    return int(request.param)


@pytest.mark.usefixtures("batch_size")
@pytest.mark.parametrize("tabix", [True, False])
def test_a_key_interleaved_with_others_at_its_position_is_repeated(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    tabix: bool,
) -> None:
    data = """
        chrom  pos_begin  reference  alternative  score
        chr1   10         C          T            0.1
        chr1   20         A          G            0.2
        chr1   20         A          T            0.3
        chr1   20         A          G            0.4
        chr1   20         A          C            0.5
        chr1   20         A          G            0.6
        chr1   30         G          A            0.7
    """

    [warning] = _warned_allele(tmp_path, caplog, data, tabix=tabix)

    assert "chr1:20:A:G" in warning
    assert "3 rows" in warning


@pytest.mark.usefixtures("batch_size")
@pytest.mark.parametrize("tabix", [True, False])
def test_distinct_alleles_at_one_position_are_not_repeated(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    tabix: bool,
) -> None:
    data = """
        chrom  pos_begin  reference  alternative  score
        chr1   20         A          G            0.2
        chr1   20         A          T            0.3
        chr1   20         AC         A            0.4
        chr1   21         A          G            0.5
        chr1   21         A          T            0.6
    """

    assert _warned_allele(tmp_path, caplog, data, tabix=tabix) == []


@pytest.mark.parametrize("tabix", [True, False])
def test_a_key_straddling_a_batch_boundary_is_repeated(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    tabix: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Batches of two: [chr1:10, chr1:20 A>G], [chr1:20 A>G, chr1:30] --
    # neither batch holds the key twice.
    monkeypatch.setattr(scan, "_SCAN_BATCH_SIZE", 2)
    data = """
        chrom  pos_begin  reference  alternative  score
        chr1   10         C          T            0.1
        chr1   20         A          G            0.2
        chr1   20         A          G            0.3
        chr1   30         G          A            0.4
    """

    [warning] = _warned_allele(tmp_path, caplog, data, tabix=tabix)

    assert "chr1:20:A:G" in warning
    assert "2 rows" in warning


@pytest.mark.usefixtures("batch_size")
@pytest.mark.parametrize("tabix", [True, False])
def test_the_reported_key_keeps_its_own_count_when_its_pair_recurs(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    tabix: bool,
) -> None:
    # A>G again at chr1:30, more often, in the same region: a separate key
    # whose rows must not be counted as the chr1:20 key's.
    data = """
        chrom  pos_begin  reference  alternative  score
        chr1   20         A          G            0.1
        chr1   20         A          G            0.2
        chr1   30         A          G            0.3
        chr1   30         A          G            0.4
        chr1   30         A          G            0.5
        chr1   30         A          G            0.6
    """

    [warning] = _warned_allele(tmp_path, caplog, data, tabix=tabix)

    assert "chr1:20:A:G" in warning
    assert "2 rows" in warning


def _long_run_table(*, repeated: bool) -> str:
    # Eighteen distinct insertions at chr1:20 -- a run longer than the
    # shifted comparison takes -- and, when ``repeated``, the ninth again.
    alts = [f"A{'C' * length}" for length in range(1, 19)]
    if repeated:
        alts.append(alts[8])
    rows = "\n".join(
        f"chr1 20 A {alt} 0.{index}" for index, alt in enumerate(alts))
    return f"""
        chrom  pos_begin  reference  alternative  score
        chr1   10         C          T            0.1
        {rows}
        chr1   30         G          A            0.5
    """


@pytest.mark.parametrize("tabix", [True, False])
def test_a_repeat_in_a_long_run_at_one_position_is_found(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    tabix: bool,
) -> None:
    [warning] = _warned_allele(
        tmp_path, caplog, _long_run_table(repeated=True), tabix=tabix)

    assert "chr1:20:A:ACCCCCCCCC" in warning
    assert "2 rows" in warning


@pytest.mark.parametrize("tabix", [True, False])
def test_a_long_run_of_distinct_alleles_is_not_repeated(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    tabix: bool,
) -> None:
    assert _warned_allele(
        tmp_path, caplog, _long_run_table(repeated=False),
        tabix=tabix) == []


@pytest.mark.parametrize("tabix", [True, False])
def test_a_region_reports_no_key_it_does_not_own(
    tmp_path: pathlib.Path,
    tabix: bool,
) -> None:
    # The repeated rows sit at chr1:10 but reach to 40, so the query for
    # chr1:21-40 is handed them too; the region owning chr1:10 is the
    # one that reports them.
    resource = _realize(_allele_score(
        """
        chrom  pos_begin  pos_end  reference  alternative  score
        chr1   10         40       A          G            0.1
        chr1   10         40       A          G            0.2
        chr1   30         30       C          T            0.3
        """,
        tabix=tabix), tmp_path)
    confs: dict = {"score": NumberHistogramConfig.from_dict({
        "type": "number",
        "view_range": {"min": 0, "max": 1},
        "number_of_bins": 10,
    })}

    owner = scan.do_histogram_task(resource, confs, "chr1", 1, 20)
    neighbour = scan.do_histogram_task(resource, confs, "chr1", 21, 40)

    assert owner.alleles is not None
    assert neighbour.alleles is not None
    assert owner.alleles.repeated_allele() == RepeatedAllele(
        "chr1", 10, "A", "G", 2)
    assert neighbour.alleles.repeated_allele() is None


@pytest.mark.parametrize("is_enforced", [True, False])
@pytest.mark.parametrize("tabix", [True, False])
def test_a_many_resource_is_not_checked(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    is_enforced: bool,
    tabix: bool,
) -> None:
    monkeypatch.setattr(
        resource_types, "allele_multiplicity_enforced", lambda: is_enforced)
    resource = _realize(
        _allele_score(_TWO_REPEATS_TABLE, multiplicity="many", tabix=tabix),
        tmp_path)

    with caplog.at_level(logging.WARNING):
        _build(tmp_path)

    assert _multiplicity_warnings(caplog) == []
    assert resource.file_exists(ALLELE_STATISTICS_FILE)


_DISTINCT_TABLE = """
    chrom  pos_begin  reference  alternative  score
    chr1   10         C          T            0.1
    chr1   20         A          G            0.2
    chr1   20         A          T            0.3
    chr1   30         G          A            0.5
    chr2   30         G          A            0.5
"""


@pytest.mark.usefixtures("enforced")
@pytest.mark.parametrize("tabix", [True, False])
def test_a_one_resource_without_repeats_builds_unchanged(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    tabix: bool,
) -> None:
    checked = _realize(
        _allele_score(_DISTINCT_TABLE, tabix=tabix), tmp_path / "checked")
    unchecked = _realize(
        _allele_score(_DISTINCT_TABLE, multiplicity="many", tabix=tabix),
        tmp_path / "unchecked")
    _build(tmp_path / "unchecked")

    with caplog.at_level(logging.WARNING):
        _build(tmp_path / "checked")

    assert _multiplicity_warnings(caplog) == []
    assert checked.get_file_content(ALLELE_STATISTICS_FILE) \
        == unchecked.get_file_content(ALLELE_STATISTICS_FILE)


def test_a_restored_statistic_carries_no_repeated_key(
    tmp_path: pathlib.Path,
) -> None:
    resource = _realize(_allele_score(_REPEATED_TABLE), tmp_path)
    _build(tmp_path)

    restored = AlleleStatistics.deserialize(
        resource.get_file_content(ALLELE_STATISTICS_FILE))

    assert restored.repeated_allele() is None
