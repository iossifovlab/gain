"""Pin the offline claims of the "Allele scores" page.

The tests run the code that the page describes and assert the results
that the page states. They read no ``.rst`` file. The page is
``docs/source/development/resources/allele_scores.rst``. The examples of
the page use ``hg38/scores/CADD_v1.7``; the tests use the mini-GRR allele
score ``mini_allelescore_tabix_1`` instead, with three alleles at
``chr1:1`` (``T>A``, ``T>C``, ``T>G``). Multiplicity needs a resource that
mini-GRR does not hold, so those tests build one with ``an_allele_score``.
The tests pin these claims:

- Both builders give a closed ``AlleleScore``, and the generic builder
  gives one too. The multiplicity defaults to ``one``.
- The location arguments are positional and the rest are keyword-only.
- ``get_allele_scores_for_allele`` returns a tuple or ``None``, the
  ``_rows`` reads return lists, and the region ``_rows`` reads yield
  ``AlleleEntry`` objects or ``(pos, ref, alt, value)`` tuples.
- The ``_agg`` reads return an ``AlleleAggregate`` in query order. There
  is no singular region aggregation.
- The result table: no record, a filter that rejects every record, one
  record, several records on a ``one`` resource, any allele on a ``many``
  resource and an unknown contig.
- An empty aggregate gives ``None`` for ``max`` and ``[]`` for ``list``,
  and the region aggregation gives ``None`` when no record overlaps.
- ``ALLELE_MULTIPLICITY_ENFORCEMENT_RELEASE`` is ``2027.1.0``.
- The mode is reported by ``substitutions_mode`` and ``alleles_mode``,
  and a direct lookup of an indel is exact in both modes.
- The allele score annotator matches a deletion exactly in ``alleles``
  mode and aggregates over the bases it covers in ``substitutions`` mode.
  On a ``many`` resource it aggregates the rows of an allele.
"""
import inspect
import logging
import pathlib
import types
from typing import Any

import pytest
from gain import grr
from gain.annotation.annotatable import VCFAllele
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.genomic_resources import resource_types
from gain.genomic_resources.aggregators import ScoreAggregationQuery
from gain.genomic_resources.genomic_scores import (
    AlleleAggregate,
    AlleleEntry,
    AlleleMultiplicityError,
    AlleleScore,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import a_grr, an_allele_score

ALLELE_SCORE = "mini_allelescore_tabix_1"
NUM = "allele_tsv_1_num"
CAT = "allele_tsv_1_cat"

_DUPLICATED = """
    chrom  pos_begin  reference  alternative  score
    chr1   10         C          T            0.1
    chr1   20         A          G            0.2
    chr1   20         A          G            0.3
    chr1   30         G          ATT          0.5
"""


def _open_mini(repo: GenomicResourceRepo) -> AlleleScore:
    return grr.build_allele_score_from_resource_id(ALLELE_SCORE, repo).open()


_FOR_ANNOTATION = """
    chrom  pos_begin  reference  alternative  score
    chr1   20         A          G            0.2
    chr1   20         A          G            0.4
    chr1   21         C          T            0.6
    chr1   22         G          A            0.8
"""


def _built_repo(
    root: pathlib.Path, data: str, *, multiplicity: str | None = None,
    mode: str | None = None,
) -> GenomicResourceRepo:
    builder = an_allele_score().with_score("score", "float") \
        .with_data(data).with_tabix()
    if multiplicity is not None:
        builder = builder.with_allele_multiplicity(multiplicity)
    repo: GenomicResourceRepo = a_grr().with_resource(
        "score", builder).build_repo(root)
    if mode is not None:
        config = root / "score" / "genomic_resource.yaml"
        config.write_text(
            config.read_text() + f"allele_score_mode: {mode}\n")
        repo = grr.build_genomic_resource_repository({
            "id": "built", "type": "directory", "directory": str(root),
        })
    return repo


def _built_score(
    root: pathlib.Path, *, multiplicity: str | None = None,
    mode: str | None = None,
) -> AlleleScore:
    repo = _built_repo(
        root, _DUPLICATED, multiplicity=multiplicity, mode=mode)
    return grr.build_allele_score_from_resource_id("score", repo).open()


def test_builders_give_a_closed_allele_score(
    repo: GenomicResourceRepo,
) -> None:
    """The specific and the generic builders give an ``AlleleScore``."""
    from_id = grr.build_allele_score_from_resource_id(ALLELE_SCORE, repo)
    from_resource = grr.build_allele_score_from_resource(
        repo.get_resource(ALLELE_SCORE))
    generic = grr.build_score_from_resource_id(ALLELE_SCORE, repo)

    for score in (from_id, from_resource, generic):
        assert isinstance(score, AlleleScore)
        assert not score.is_open()


def test_multiplicity_defaults_to_one(repo: GenomicResourceRepo) -> None:
    """A resource that does not set ``allele_multiplicity`` is ``one``."""
    score = grr.build_allele_score_from_resource_id(ALLELE_SCORE, repo)

    assert score.multiplicity is AlleleScore.Multiplicity.ONE


@pytest.mark.parametrize(("method", "location"), [
    ("get_allele_scores_for_allele", ["chrom", "pos", "ref", "alt"]),
    ("get_allele_scores_for_allele_rows", ["chrom", "pos", "ref", "alt"]),
    ("get_allele_scores_for_allele_agg", ["chrom", "pos", "ref", "alt"]),
    ("get_allele_scores_in_region_rows", ["chrom", "start", "end"]),
    ("get_allele_scores_in_region_agg", ["chrom", "start", "end"]),
])
def test_location_is_positional_and_the_rest_keyword_only(
    method: str, location: list[str],
) -> None:
    """Each read takes ``score_filter``, and only by keyword."""
    parameters = list(
        inspect.signature(getattr(AlleleScore, method)).parameters.values())

    positional = [p.name for p in parameters[1:]
                  if p.kind is p.POSITIONAL_OR_KEYWORD]
    keyword_only = [p.name for p in parameters if p.kind is p.KEYWORD_ONLY]
    assert positional == location
    assert "score_filter" in keyword_only


def test_single_record_read_returns_a_tuple(
    repo: GenomicResourceRepo,
) -> None:
    """The plural read returns a tuple, the singular one a value."""
    with _open_mini(repo) as score:
        assert score.get_allele_scores_for_allele(
            "chr1", 1, "T", "A") == (1.0, "good")
        assert score.get_allele_score_for_allele(
            "chr1", 1, "T", "A", score=NUM) == 1.0


def test_rows_read_of_one_allele_returns_lists(
    repo: GenomicResourceRepo,
) -> None:
    """``_rows`` gives a list of tuples, or a list of values."""
    with _open_mini(repo) as score:
        assert score.get_allele_scores_for_allele_rows(
            "chr1", 1, "T", "A") == [(1.0, "good")]
        assert score.get_allele_score_for_allele_rows(
            "chr1", 1, "T", "A", score=CAT) == ["good"]


def test_region_rows_yield_allele_entries(
    repo: GenomicResourceRepo,
) -> None:
    """The region read reports which alleles it found."""
    with _open_mini(repo) as score:
        entries = score.get_allele_scores_in_region_rows("chr1", 1, 1)

        assert isinstance(entries, types.GeneratorType)
        result = list(entries)

    assert all(isinstance(e, AlleleEntry) for e in result)
    assert [(e.pos, e.ref, e.alt, e.values) for e in result] == [
        (1, "T", "A", (1.0, "good")),
        (1, "T", "C", (1.1, "bad")),
        (1, "T", "G", (1.2, "bad")),
    ]


def test_singular_region_rows_yield_tuples(
    repo: GenomicResourceRepo,
) -> None:
    """The singular region read yields ``(pos, ref, alt, value)``."""
    with _open_mini(repo) as score:
        result = list(score.get_allele_score_in_region_rows(
            "chr1", 1, 1, score=NUM))

    assert result == [(1, "T", "A", 1.0), (1, "T", "C", 1.1),
                      (1, "T", "G", 1.2)]


def test_aggregation_of_one_allele(repo: GenomicResourceRepo) -> None:
    """The ``_agg`` read returns an ``AlleleAggregate`` in query order."""
    with _open_mini(repo) as score:
        plural = score.get_allele_scores_for_allele_agg(
            "chr1", 1, "T", "A", queries=[
                ScoreAggregationQuery(NUM, "max"),
                ScoreAggregationQuery(CAT, "list"),
            ])
        singular = score.get_allele_score_for_allele_agg(
            "chr1", 1, "T", "A", score=NUM, aggregator="max")

    assert isinstance(plural, AlleleAggregate)
    assert list(plural.values) == [1.0, ["good"]]
    assert singular is not None
    assert singular.values == (1.0,)


def test_region_aggregation_has_no_singular_form() -> None:
    """There is no ``get_allele_score_in_region_agg``."""
    assert hasattr(AlleleScore, "get_allele_scores_in_region_agg")
    assert not hasattr(AlleleScore, "get_allele_score_in_region_agg")


def test_no_record_for_the_allele(repo: GenomicResourceRepo) -> None:
    """The first row of the result table."""
    with _open_mini(repo) as score:
        assert score.get_allele_scores_for_allele(
            "chr1", 1, "T", "T") is None
        assert score.get_allele_scores_for_allele_rows(
            "chr1", 1, "T", "T") == []
        assert score.get_allele_scores_for_allele_agg(
            "chr1", 1, "T", "T") is None


def test_filter_rejects_every_record(repo: GenomicResourceRepo) -> None:
    """The second row: an aggregate over an empty selection."""
    with _open_mini(repo) as score:
        reject = score.compile_filter(f"{NUM} > 5")

        single = score.get_allele_scores_for_allele(
            "chr1", 1, "T", "A", score_filter=reject)
        rows = score.get_allele_scores_for_allele_rows(
            "chr1", 1, "T", "A", score_filter=reject)
        aggregate = score.get_allele_scores_for_allele_agg(
            "chr1", 1, "T", "A", score_filter=reject, queries=[
                ScoreAggregationQuery(NUM, "max"),
                ScoreAggregationQuery(CAT, "list"),
            ])

    assert single is None
    assert rows == []
    assert aggregate is not None
    values: tuple[Any, ...] = aggregate.values
    assert values == (None, [])


def test_region_aggregation_without_an_overlapping_record(
    repo: GenomicResourceRepo,
) -> None:
    """No record in the region gives ``None``, a rejecting filter does not."""
    with _open_mini(repo) as score:
        reject = score.compile_filter(f"{NUM} > 5")
        queries = [ScoreAggregationQuery(NUM, "max"),
                   ScoreAggregationQuery(CAT, "list")]

        empty_region = score.get_allele_scores_in_region_agg(
            "chr1", 3, 4, queries=queries)
        all_rejected = score.get_allele_scores_in_region_agg(
            "chr1", 1, 1, queries=queries, score_filter=reject)
        no_rows = list(score.get_allele_scores_in_region_rows("chr1", 3, 4))

    assert empty_region is None
    assert all_rejected is not None
    values: tuple[Any, ...] = all_rejected.values
    assert values == (None, [])
    assert not no_rows


def test_several_records_on_a_one_resource_warn_and_give_the_first(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """Before the enforcement release, the read warns and reads row one."""
    assert not resource_types.allele_multiplicity_enforced()
    score = _built_score(tmp_path)

    with caplog.at_level(logging.WARNING):
        values = score.get_allele_scores_for_allele("chr1", 20, "A", "G")

    assert values == (0.2,)
    assert "holds several rows for one allele" in caplog.text
    assert score.get_allele_scores_for_allele_rows(
        "chr1", 20, "A", "G") == [(0.2,), (0.3,)]


def test_filter_applies_only_to_the_first_record(
    tmp_path: pathlib.Path,
) -> None:
    """The second row passes the filter, but the read answers ``None``."""
    score = _built_score(tmp_path)
    keep_second = score.compile_filter("score > 0.25")

    assert score.get_allele_scores_for_allele(
        "chr1", 20, "A", "G", score_filter=keep_second) is None


def test_many_resource_refuses_every_single_record_read(
    tmp_path: pathlib.Path,
) -> None:
    """A ``many`` resource refuses even an allele with one row."""
    score = _built_score(tmp_path, multiplicity="many")

    assert score.multiplicity is AlleleScore.Multiplicity.MANY
    with pytest.raises(AlleleMultiplicityError):
        score.get_allele_scores_for_allele("chr1", 10, "C", "T")
    assert score.get_allele_scores_for_allele_rows(
        "chr1", 20, "A", "G") == [(0.2,), (0.3,)]
    aggregate = score.get_allele_scores_for_allele_agg(
        "chr1", 20, "A", "G", queries=[ScoreAggregationQuery("score", "max")])
    assert aggregate is not None
    assert aggregate.values == (0.3,)


@pytest.mark.parametrize(("method", "args"), [
    ("get_allele_scores_for_allele", ("chrZ", 1, "T", "A")),
    ("get_allele_scores_for_allele_rows", ("chrZ", 1, "T", "A")),
    ("get_allele_scores_for_allele_agg", ("chrZ", 1, "T", "A")),
    ("get_allele_scores_in_region_rows", ("chrZ", 1, 2)),
    ("get_allele_scores_in_region_agg", ("chrZ", 1, 2)),
])
def test_unknown_contig_raises(
    repo: GenomicResourceRepo, method: str, args: tuple,
) -> None:
    """The last row of the result table: ``ValueError`` from every read."""
    def read(score: AlleleScore) -> object:
        result = getattr(score, method)(*args)
        if isinstance(result, types.GeneratorType):
            return list(result)
        return result

    with _open_mini(repo) as score, pytest.raises(ValueError):
        read(score)


def test_enforcement_release() -> None:
    """The release that enforces ``one`` is ``2027.1.0``."""
    assert resource_types.ALLELE_MULTIPLICITY_ENFORCEMENT_RELEASE == \
        "2027.1.0"
    assert isinstance(resource_types.allele_multiplicity_enforced(), bool)


@pytest.mark.parametrize("mode", ["substitutions", "alleles"])
def test_mode_is_reported_and_does_not_change_a_lookup(
    tmp_path: pathlib.Path, mode: str,
) -> None:
    """A direct lookup of an insertion is exact in either mode."""
    score = _built_score(tmp_path, mode=mode)

    assert score.substitutions_mode() is (mode == "substitutions")
    assert score.alleles_mode() is (mode == "alleles")
    assert score.get_allele_scores_for_allele(
        "chr1", 30, "G", "ATT") == (0.5,)
    assert score.get_allele_scores_for_allele(
        "chr1", 30, "G", "AT") is None


@pytest.mark.parametrize(("multiplicity", "mode", "duplicated", "deletion"), [
    ("many", "alleles", 0.4, None),
    ("many", "substitutions", 0.4, 0.8),
])
def test_annotator_follows_the_mode_and_the_multiplicity(
    tmp_path: pathlib.Path, multiplicity: str, mode: str,
    duplicated: float, deletion: float | None,
) -> None:
    """A ``many`` allele is aggregated, and the mode decides the deletion."""
    repo = _built_repo(
        tmp_path, _FOR_ANNOTATION, multiplicity=multiplicity, mode=mode)
    pipeline = load_pipeline_from_yaml(
        "- allele_score_annotator:\n    resource_id: score\n",
        repo, work_dir=tmp_path / "work")

    assert pipeline.annotate(
        VCFAllele("chr1", 20, "A", "G")) == {"score": duplicated}
    assert pipeline.annotate(
        VCFAllele("chr1", 21, "CG", "C")) == {"score": deletion}
