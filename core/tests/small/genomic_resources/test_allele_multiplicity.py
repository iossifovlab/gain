"""``allele_multiplicity``: how many table rows one allele key may have.

The first slice of the allele plane (gain#1749, gain#1750): the
declaration, the enum it reads as, the error later slices raise, and the
release from which an undeclared duplicate allele is an error.  Nothing
here consults any of it yet -- that is gain#1752, gain#1753, gain#1755.
"""
import pathlib

import gain
import pytest
from gain.genomic_resources import genomic_scores
from gain.genomic_resources.genomic_scores import (
    AlleleMultiplicityError,
    AlleleScore,
    allele_key,
    build_allele_score_from_resource,
)
from gain.genomic_resources.resource_errors import MalformedResourceError
from gain.genomic_resources.resource_types import (
    ALLELE_MULTIPLICITY_ENFORCEMENT_RELEASE,
    allele_multiplicity_enforced,
)
from gain.genomic_resources.testing.builders import an_allele_score


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ("many", AlleleScore.Multiplicity.MANY),
        ("one", AlleleScore.Multiplicity.ONE),
    ],
)
def test_declared_multiplicity_is_reported(
    tmp_path: pathlib.Path,
    declared: str,
    expected: AlleleScore.Multiplicity,
) -> None:
    resource = (
        an_allele_score()
        .with_allele_multiplicity(declared)
        .build_resource(tmp_path)
    )

    score = build_allele_score_from_resource(resource)

    assert score.multiplicity is expected


def test_undeclared_multiplicity_is_one(tmp_path: pathlib.Path) -> None:
    resource = an_allele_score().build_resource(tmp_path)

    score = build_allele_score_from_resource(resource)

    assert score.multiplicity is AlleleScore.Multiplicity.ONE


@pytest.mark.parametrize("declared", ["several", "ONE", "", "one\n"])
def test_unknown_multiplicity_is_refused_naming_the_valid_ones(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    declared: str,
) -> None:
    resource = (
        an_allele_score()
        .with_allele_multiplicity(declared)
        .build_resource(tmp_path)
    )

    with pytest.raises(MalformedResourceError, match="Invalid configuration"):
        build_allele_score_from_resource(resource)

    [refusal] = [
        record.getMessage() for record in caplog.records
        if "allele_multiplicity" in record.getMessage()
    ]
    assert "one" in refusal
    assert "many" in refusal


@pytest.mark.parametrize("declared", [True, 2])
def test_non_string_multiplicity_is_refused(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    declared: object,
) -> None:
    resource = (
        an_allele_score()
        .with_allele_multiplicity(declared)
        .build_resource(tmp_path)
    )

    with pytest.raises(MalformedResourceError, match="Invalid configuration"):
        build_allele_score_from_resource(resource)

    [refusal] = [
        record.getMessage() for record in caplog.records
        if "allele_multiplicity" in record.getMessage()
    ]
    assert "must be of string type" in refusal


def _reads(score: AlleleScore) -> tuple[object, ...]:
    """Everything a caller can read off the score, in one comparable value."""
    score.open()
    return (
        score.mode,
        score.get_all_scores(),
        score.get_allele_scores_for_allele_rows("1", 10, "A", "G"),
        score.get_allele_scores_for_allele_rows("1", 16, "C", "T"),
        list(score.fetch_region_segments_scores("1", 1, 20)),
        score.aggregate_region("1", 1, 20),
        score.get_allele_scores_in_region_agg("1", 1, 20),
    )


@pytest.mark.parametrize("declared", ["one", "many"])
def test_the_declaration_changes_no_read(
    tmp_path: pathlib.Path, declared: str,
) -> None:
    builder = (
        an_allele_score()
        .with_score("freq", "float")
        .with_data("""
            chrom  pos_begin  reference  alternative  freq
            1      10         A          G            0.1
            1      10         A          G            0.4
            1      10         A          C            0.2
            1      16         C          T            0.3
        """)
    )
    undeclared = build_allele_score_from_resource(
        builder.build_resource(tmp_path / "undeclared"))
    declaring = build_allele_score_from_resource(
        builder.with_allele_multiplicity(declared)
        .build_resource(tmp_path / "declaring"))

    assert _reads(declaring) == _reads(undeclared)


def test_multiplicity_error_is_a_value_error_on_the_facade() -> None:
    assert issubclass(AlleleMultiplicityError, ValueError)
    assert "AlleleMultiplicityError" in genomic_scores.__all__


def test_multiplicity_error_names_the_resource_and_the_allele() -> None:
    allele = ("chr1", 10, "A", "G")

    error = AlleleMultiplicityError(
        "scores/dup", "holds 2 rows for one allele", allele=allele)

    assert error.resource_id == "scores/dup"
    assert error.allele == allele
    assert "scores/dup" in str(error)
    assert allele_key(*allele) in str(error)
    assert "holds 2 rows for one allele" in str(error)


def test_multiplicity_error_without_an_allele() -> None:
    error = AlleleMultiplicityError(
        "scores/many", "declares allele_multiplicity: many")

    assert error.resource_id == "scores/many"
    assert error.allele is None
    assert "scores/many" in str(error)
    assert "declares allele_multiplicity: many" in str(error)


def test_enforcement_release_is_2027_1_0() -> None:
    assert ALLELE_MULTIPLICITY_ENFORCEMENT_RELEASE == "2027.1.0"


@pytest.mark.parametrize(
    ("version", "enforced"),
    [
        pytest.param("0.0.0.dev0", False, id="editable-checkout-fallback"),
        pytest.param("2026.12.3", False, id="release-before"),
        pytest.param("2027.1.0.dev4", False, id="dev-pre-release-of-it"),
        pytest.param("2027.1.0", True, id="exactly-the-release"),
        pytest.param("2027.2.1", True, id="later-release"),
        pytest.param("not a version", False, id="unparsable"),
    ],
)
def test_enforcement_follows_the_installed_version(
    version: str, enforced: bool,
) -> None:
    assert allele_multiplicity_enforced(version) is enforced


@pytest.mark.parametrize(
    ("installed", "enforced"),
    [("2027.1.0", True), ("2026.12.3", False)],
)
def test_enforcement_defaults_to_the_installed_version(
    monkeypatch: pytest.MonkeyPatch,
    installed: str,
    enforced: bool,
) -> None:
    monkeypatch.setattr(gain, "__version__", installed)

    assert allele_multiplicity_enforced() is enforced
