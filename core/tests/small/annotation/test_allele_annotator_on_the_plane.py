# pylint: disable=redefined-outer-name,C0114,C0116
"""What the allele annotator's region mode answers now that the SCORE
reduces (#1163).

The annotator no longer materialises a region's records and hands raw
per-source lists to the base to fold.  It asks
``AlleleScore.get_allele_scores_in_region_agg`` for the region already
reduced -- one ``ScoreAggregationQuery`` per score attribute, built and
resolved when the pipeline loads -- and hands the base an
``AggregatedValues`` keyed by attribute NAME, which the base passes
through untouched.  The virtual ``allele`` attribute rides the same read:
the keys come back beside the values, off one walk.

Everything a pipeline ANSWERS is meant to be unchanged, with two
exceptions this file pins deliberately:

- an attribute over a ``bool`` score that names no aggregator is refused
  when the pipeline LOADS, in ``allele`` mode as well as ``region`` mode
  (a CNV or a region takes the region path whatever the mode);
- the allele keys come back in first-seen order, where the old set gave
  an arbitrary one.
"""

import pathlib
import textwrap

import pytest
from gain.annotation.annotatable import Region, VCFAllele
from gain.annotation.annotation_config import AnnotationConfigurationError
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.annotation.annotator_base import AggregatedValues
from gain.genomic_resources.genomic_scores import AlleleScore
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import (
    a_grr,
    an_allele_score,
)


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """Two alleles at 10 and one at 16 on ``chr1``; nothing past 16.

    ``flag`` is a ``bool``, whose default aggregator is deliberately
    ``None`` -- the one score type an allele resource declares no
    reduction for.  The rows are in the order every backend serves them,
    so an ordered compare here pins the READ's order, not one table's.
    """
    return (
        a_grr()
        .with_resource(
            "alleles",
            an_allele_score()
            .with_score("freq", "float", desc="a float score")
            .with_score("id", "str", desc="a string score")
            .with_score("flag", "bool", desc="a bool score")
            .with_data("""
                chrom  pos_begin  reference  alternative  freq  id  flag
                chr1   10         A          C            0.2   ac  True
                chr1   10         A          G            0.1   ag  True
                chr1   16         C          T            0.3   ct  False
            """),
        )
        .build_repo(tmp_path)
    )


def _pipeline(
    repo: GenomicResourceRepo, attributes: str, *, mode: str = "region",
) -> AnnotationPipeline:
    return load_pipeline_from_yaml(textwrap.dedent(f"""
        - allele_score:
            resource_id: alleles
            mode: {mode}
            attributes:
{textwrap.indent(textwrap.dedent(attributes), " " * 12)}
        """), repo)


@pytest.mark.parametrize("mode", ["region", "allele"])
def test_a_bool_attribute_naming_no_aggregator_is_refused_at_load(
    repo: GenomicResourceRepo, mode: str,
) -> None:
    """Refused building the pipeline, not on the first region -- in BOTH modes.

    ``allele`` mode too, because a CNV or a ``Region`` takes the region
    path whatever the mode, so the query list exists in both and a
    pipeline that only ever met ``VCFAllele`` inputs would otherwise
    carry a misconfiguration discovered mid-run (D6).  The plane's own
    remedy survives the factory's wrapping, and that is what the tail
    pins.
    """
    with pytest.raises(AnnotationConfigurationError) as excinfo:
        _pipeline(repo, """
            - source: flag
              name: flag
        """, mode=mode)

    assert str(excinfo.value).endswith(
        "score 'flag' of resource 'alleles' has no default aggregator "
        "for value type 'bool'; name one on the query")


def test_a_bool_attribute_that_names_one_loads_and_answers(
    repo: GenomicResourceRepo,
) -> None:
    """The other side of that boundary: naming one is all it takes.

    Guards the refusal against over-reach -- were it refusing on the value
    type rather than on the missing default, this would fail too.
    """
    with _pipeline(repo, """
        - source: flag
          name: flag
          aggregator: bool
    """) as pipeline:
        result = pipeline.annotate(Region("chr1", 10, 16))

    assert result == {"flag": True}


def test_an_unknown_include_attributes_id_is_refused_at_load(
    repo: GenomicResourceRepo,
) -> None:
    """Resolved through the score as the pipeline loads, with the valid names.

    The old path raised a ``KeyError`` per record, and only once a region
    holding a record arrived.
    """
    with pytest.raises(AnnotationConfigurationError) as excinfo:
        _pipeline(repo, """
            - source: allele
              include_attributes: nope
        """)

    assert str(excinfo.value).endswith(
        "score 'nope' is not defined by resource 'alleles'; it has "
        "['flag', 'freq', 'id']")


def test_a_region_answers_aggregated_values(
    repo: GenomicResourceRepo,
) -> None:
    """The annotator hands the base finished values, keyed by NAME.

    Asked of ``_do_annotate`` directly because that is the seam the marker
    type exists for: the base recognises an ``AggregatedValues`` and folds
    nothing, where a plain dict would be taken for raw lists still to be
    reduced.  A renamed attribute shows the keys are names, not sources.
    """
    with _pipeline(repo, """
        - source: freq
          name: as_max
        - source: allele
          name: keys
    """) as pipeline:
        # Entering the pipeline does not open its annotators; annotate()
        # does that lazily, and this test bypasses annotate().
        pipeline.open()
        annotator = pipeline.annotators[0]
        result = annotator._do_annotate(Region("chr1", 10, 16), {})

    assert isinstance(result, AggregatedValues)
    assert result == {
        "as_max": 0.3, "keys": ["chr1:10:A:C", "chr1:10:A:G", "chr1:16:C:T"]}


def test_one_source_asked_twice_answers_twice(
    repo: GenomicResourceRepo,
) -> None:
    """Two aggregators over one source are two answers, keyed by NAME."""
    with _pipeline(repo, """
        - source: freq
          name: as_min
          aggregator: min
        - source: freq
          name: as_max
          aggregator: max
    """) as pipeline:
        result = pipeline.annotate(Region("chr1", 10, 16))

    assert result == {"as_min": 0.1, "as_max": 0.3}


def test_allele_keys_come_in_first_seen_order(
    repo: GenomicResourceRepo,
) -> None:
    """Ordered, where the old set promised nothing (D2).

    A ``VCFAllele`` in ``region`` mode takes the same path as a
    ``Region``, and the suffix follows ``include_attributes`` in the
    order asked.
    """
    with _pipeline(repo, """
        - source: allele
          include_attributes:
          - id
          - freq
    """) as pipeline:
        for_region = pipeline.annotate(Region("chr1", 10, 16))
        for_allele = pipeline.annotate(VCFAllele("chr1", 10, "A", "C"))

    assert for_region == {"allele": [
        "chr1:10:A:C:ac,0.2", "chr1:10:A:G:ag,0.1", "chr1:16:C:T:ct,0.3"]}
    assert for_allele == {"allele": [
        "chr1:10:A:C:ac,0.2", "chr1:10:A:G:ag,0.1"]}


def test_the_annotator_no_longer_materialises_the_region(
    repo: GenomicResourceRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``fetch_allele_records`` is not on the annotation path any more.

    The read that materialised a list per region is what gain#834
    measured; the annotator reaches the score through the folding read
    alone, in both modes and for both annotatable shapes that take the
    region path.
    """
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("fetch_allele_records was called")

    monkeypatch.setattr(AlleleScore, "fetch_allele_records", refuse)
    with _pipeline(repo, """
        - source: freq
          name: as_max
        - source: allele
          name: keys
    """) as pipeline:
        for_region = pipeline.annotate(Region("chr1", 10, 16))
        for_allele = pipeline.annotate(VCFAllele("chr1", 16, "C", "T"))
        for_nothing = pipeline.annotate(Region("chr1", 100, 200))

    assert for_region == {
        "as_max": 0.3, "keys": ["chr1:10:A:C", "chr1:10:A:G", "chr1:16:C:T"]}
    assert for_allele == {"as_max": 0.3, "keys": ["chr1:16:C:T"]}
    assert for_nothing == {"as_max": None, "keys": None}
