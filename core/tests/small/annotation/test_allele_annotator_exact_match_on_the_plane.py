# pylint: disable=redefined-outer-name,C0114,C0116
"""The allele annotator's exact match reads through the plane (gain#1755).

A ``VCFAllele`` the annotator sends to the exact match is read by the
resource's ``allele_multiplicity``:

- on a ``one`` resource, ``get_allele_scores_for_allele`` answers the
  allele's only row;
- on a ``many`` resource, ``get_allele_scores_for_allele_agg`` folds the
  allele's rows with each attribute's aggregator -- the attribute's own,
  else the resource's per-score default, else the class default -- so a
  ``many`` resource needs no pipeline-config change.

The plane matches the position exactly, so a row starting before the
allele's position whose span reaches it is no longer the allele.

Every ``many`` allele below holds rows whose values differ, so a fold and
a first-row answer cannot be confused.
"""

import pathlib
import textwrap

import pytest
from gain.annotation.annotatable import VCFAllele
from gain.annotation.annotation_config import AnnotationConfigurationError
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import (
    AlleleScoreBuilder,
    a_grr,
    an_allele_score,
)

_DATA = """
    chrom  pos_begin  reference  alternative  freq  other  id  flag
    chr1   10         A          C            0.2   0.7    ac  True
    chr1   10         A          C            0.4   0.5    ac  False
    chr1   10         A          G            0.1   0.9    ag  True
    chr1   16         C          T            0.3   0.3    ct  False
"""

TWO_ROWS = VCFAllele("chr1", 10, "A", "C")
ONE_ROW = VCFAllele("chr1", 16, "C", "T")


def _score(multiplicity: str | None) -> AlleleScoreBuilder:
    builder = (
        an_allele_score()
        .with_score("freq", "float", desc="a float score")
        .with_score("other", "float", desc="a float with a default")
        .with_aggregator("min", score_id="other")
        .with_score("id", "str", desc="a string score")
        .with_score("flag", "bool", desc="a bool score")
        .with_data(_DATA)
    )
    if multiplicity is not None:
        builder = builder.with_allele_multiplicity(multiplicity)
    return builder


@pytest.fixture
def many_repo(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """``chr1:10 A>C`` in two rows on a resource declaring ``many``."""
    return (
        a_grr()
        .with_resource("alleles", _score("many"))
        .build_repo(tmp_path)
    )


def _pipeline(
    repo: GenomicResourceRepo, attributes: str,
) -> AnnotationPipeline:
    return load_pipeline_from_yaml(textwrap.dedent(f"""
        - allele_score:
            resource_id: alleles
            attributes:
{textwrap.indent(textwrap.dedent(attributes), " " * 12)}
        """), repo)


def test_many_folds_the_rows_with_the_float_default(
    many_repo: GenomicResourceRepo,
) -> None:
    """``max`` is the float class default: 0.4 of the two rows' 0.2, 0.4."""
    with _pipeline(many_repo, """
        - source: freq
    """) as pipeline:
        result = pipeline.annotate(TWO_ROWS)

    assert result == {"freq": 0.4}


def test_many_folds_with_the_resource_per_score_default(
    many_repo: GenomicResourceRepo,
) -> None:
    """``other`` declares ``min`` in the resource: 0.5 of 0.7, 0.5."""
    with _pipeline(many_repo, """
        - source: other
    """) as pipeline:
        result = pipeline.annotate(TWO_ROWS)

    assert result == {"other": 0.5}


def test_many_folds_with_the_attribute_aggregator_over_the_default(
    many_repo: GenomicResourceRepo,
) -> None:
    """An attribute's own aggregator wins over the resource's ``min``."""
    with _pipeline(many_repo, """
        - source: other
          aggregator: max
        - source: freq
          name: freq_min
          aggregator: min
    """) as pipeline:
        result = pipeline.annotate(TWO_ROWS)

    assert result == {"other": 0.7, "freq_min": 0.2}


def test_many_answers_a_one_row_allele_and_an_absent_one(
    many_repo: GenomicResourceRepo,
) -> None:
    """One row is folded too; an allele no row holds is ``None``.

    The ``str`` default aggregator keeps every value, so ``id`` is a list
    even of one row -- the aggregator's property, as on a region fold.
    """
    with _pipeline(many_repo, """
        - source: freq
        - source: id
    """) as pipeline:
        one_row = pipeline.annotate(ONE_ROW)
        absent = pipeline.annotate(VCFAllele("chr1", 10, "A", "T"))

    assert one_row == {"freq": 0.3, "id": ["ct"]}
    assert absent == {"freq": None, "id": None}


def test_many_with_every_row_filtered_out_is_an_empty_selection(
    many_repo: GenomicResourceRepo,
) -> None:
    """Each aggregator answers for no rows; not the ``None`` of absence.

    ``list`` gives ``[]`` where an absent allele gives ``None``, and the
    keys are empty -- the region fold's answer for an all-filtered region.
    """
    with load_pipeline_from_yaml(textwrap.dedent("""
        - allele_score:
            resource_id: alleles
            allele_filter: freq > 0.5
            attributes:
            - source: freq
            - source: freq
              name: freqs
              aggregator: list
            - source: allele
        """), many_repo) as pipeline:
        filtered = pipeline.annotate(TWO_ROWS)
        absent = pipeline.annotate(VCFAllele("chr1", 10, "A", "T"))

    assert filtered == {"freq": None, "freqs": [], "allele": []}
    assert absent == {"freq": None, "freqs": None, "allele": None}


def test_many_answers_one_allele_key_for_rows_that_agree(
    many_repo: GenomicResourceRepo,
) -> None:
    """Two rows, one key: the suffixed ``id`` is the same on both."""
    with _pipeline(many_repo, """
        - source: allele
          include_attributes: id
    """) as pipeline:
        result = pipeline.annotate(TWO_ROWS)

    assert result == {"allele": ["chr1:10:A:C:ac"]}


def test_many_answers_a_key_per_differing_suffix(
    many_repo: GenomicResourceRepo,
) -> None:
    """``freq`` differs across the two rows, so each row is its own key."""
    with _pipeline(many_repo, """
        - source: allele
          include_attributes: freq
    """) as pipeline:
        result = pipeline.annotate(TWO_ROWS)

    assert result == {"allele": ["chr1:10:A:C:0.2", "chr1:10:A:C:0.4"]}


def test_many_refuses_a_bool_attribute_naming_no_aggregator_at_load(
    many_repo: GenomicResourceRepo,
) -> None:
    """The exact match folds on ``many``, so the refusal names the attribute.
    """
    with pytest.raises(AnnotationConfigurationError) as excinfo:
        _pipeline(many_repo, """
            - source: flag
        """)

    assert str(excinfo.value).endswith(
        "score 'flag' of resource 'alleles' has no default aggregator "
        "for value type 'bool'; name one on the query")


@pytest.fixture
def one_repo(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """An ``alleles`` resource of ``one`` row per allele.

    ``chr1:100 AA>A`` is a deletion whose row spans 100-101.
    """
    return (
        a_grr()
        .with_resource(
            "alleles",
            an_allele_score()
            .with_score("freq", "float", desc="a float score")
            .with_score("id", "str", desc="a string score")
            .with_data("""
                chrom  pos_begin  pos_end  reference  alternative  freq  id
                chr1   10         10       A          C            0.2   ac
                chr1   12         12       G          GT           0.6   ins
                chr1   100        101      AA         A            0.8   del
            """),
        )
        .build_repo(tmp_path)
    )


def test_one_answers_the_bare_row_values(
    one_repo: GenomicResourceRepo,
) -> None:
    """A substitution and an indel with a row get that row, unfolded.

    The ``str`` score is the row's value, not a one-element list: an
    exact match on a ``one`` resource reduces nothing.  An indel no row
    holds is ``None``.
    """
    with _pipeline(one_repo, """
        - source: freq
        - source: id
        - source: allele
          include_attributes: id
    """) as pipeline:
        substitution = pipeline.annotate(VCFAllele("chr1", 10, "A", "C"))
        indel = pipeline.annotate(VCFAllele("chr1", 12, "G", "GT"))
        no_row = pipeline.annotate(VCFAllele("chr1", 12, "G", "GA"))

    assert substitution == {
        "freq": 0.2, "id": "ac", "allele": ["chr1:10:A:C:ac"]}
    assert indel == {
        "freq": 0.6, "id": "ins", "allele": ["chr1:12:G:GT:ins"]}
    assert no_row == {"freq": None, "id": None, "allele": None}


def test_a_row_starting_before_pos_is_not_matched(
    one_repo: GenomicResourceRepo,
) -> None:
    """``1:100 AA>A`` spans 101, yet ``1:101 AA>A`` is not that allele.

    The match is exact on the position as well as the nucleotides.
    """
    with _pipeline(one_repo, """
        - source: freq
    """) as pipeline:
        own = pipeline.annotate(VCFAllele("chr1", 100, "AA", "A"))
        shifted = pipeline.annotate(VCFAllele("chr1", 101, "AA", "A"))

    assert own == {"freq": 0.8}
    assert shifted == {"freq": None}
