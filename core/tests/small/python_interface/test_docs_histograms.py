"""Pin the offline claims of the "Histograms" page.

The tests run the code that the page describes and assert the results
that the page states. They read no ``.rst`` file. The page is
``docs/source/development/resources/histograms.rst``. The examples of the
page use ``hg38/scores/phastCons100way`` and ClinVar, and show their
counts; the tests use mini-GRR histograms of the same types instead, and
histograms built in the test where mini-GRR holds none of the kind. The
tests pin these claims:

- ``Histogram`` and ``HistogramConfig`` are unions of the three classes.
- A ``NumberHistogram`` has one more bin boundary than bars, two
  out-of-range counts, ``min_value`` and ``max_value``.
- ``count`` is the sum of the bars and the out-of-range counts, ``std`` is
  the population deviation, and ``moments_summary`` gives the ``n``,
  ``mean`` and ``sd`` pairs. An older file and an empty histogram give
  ``None``.
- A ``CategoricalHistogram`` has ``raw_values``, ``display_values``,
  ``unique_values``, ``total_count`` and ``truncated``. The truncated
  sidecar keeps the counts of the whole distribution, and without a
  sidecar ``truncated=True`` gives the complete histogram.
- A ``NullHistogram`` is an object with a ``reason``, its ``plot`` draws
  nothing, and the default reason is ``Unspecified reason``. A score
  configured with a null histogram is answered from its configuration.
- ``load_histogram`` gives a ``NullHistogram`` for a missing file, an
  unknown extension and a body that fails to deserialize, and raises for
  a file without ``config``.
- ``get_histogram_filename`` gives the ``.yaml`` file that the manifest
  lists, otherwise the ``.json`` file, and the ``.json.gz`` file when the
  manifest lists it with a truncated sidecar.
- ``build_histogram_config`` gives ``None`` without a ``histogram:``
  block, and ``build_default_histogram_conf`` chooses by the value type.
- ``add_value``, ``add_batch`` and ``merge`` accumulate a histogram, and
  histograms with different bins do not merge.
- A categorical histogram that is not configured as categorical refuses
  more than ``UNIQUE_VALUES_LIMIT`` (100) values.
"""
import gzip
import io
import pathlib
import typing

import numpy as np
import pytest
from gain import grr
from gain.genomic_resources import histogram
from gain.genomic_resources.histogram import (
    CategoricalHistogram,
    CategoricalHistogramConfig,
    HistogramError,
    NullHistogram,
    NullHistogramConfig,
    NumberHistogram,
    NumberHistogramConfig,
    build_default_histogram_conf,
    build_histogram_config,
    load_histogram,
    truncated_histogram_filename,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.score_def import parse_scoredef_config
from gain.genomic_resources.testing.builders import a_grr, a_position_score

NUMBER_SCORE = "mini_positionscore_bedgraph_tabix_0"
NUMBER_SCORE_ID = "pos_bedgraph_0"
CATEGORICAL_SCORE = "mini_allelescore_tabix_1"
CATEGORICAL_SCORE_ID = "allele_tsv_1_cat"
NULL_SCORE = "mini_fragmentscore"
NULL_SCORE_ID = "cnv_name"


def _number_histogram(*values: float) -> NumberHistogram:
    hist = NumberHistogram(
        NumberHistogramConfig(view_range=(0.0, 1.0), number_of_bins=10))
    for value in values:
        hist.add_value(value)
    return hist


def test_aliases_are_unions_of_the_three_classes() -> None:
    """The aliases name the three classes and define no class of their own."""
    assert set(typing.get_args(histogram.Histogram)) == {
        NullHistogram, CategoricalHistogram, NumberHistogram}
    assert set(typing.get_args(histogram.HistogramConfig)) == {
        NullHistogramConfig, CategoricalHistogramConfig,
        NumberHistogramConfig}


def test_number_histogram_of_a_score(repo: GenomicResourceRepo) -> None:
    """``bins`` has one more element than ``bars``."""
    score = grr.build_score_from_resource_id(NUMBER_SCORE, repo)

    hist = score.get_score_histogram(NUMBER_SCORE_ID)

    assert isinstance(hist, NumberHistogram)
    assert len(hist.bins) == len(hist.bars) + 1 == 101
    assert list(hist.bins[:3]) == pytest.approx([0.0, 0.01, 0.02])
    assert hist.min_value == 0.0
    assert hist.max_value == pytest.approx(0.9)
    assert hist.out_of_range_bins == [0, 0]


def test_one_score_histogram_without_the_score_id(
    repo: GenomicResourceRepo,
) -> None:
    """The page's ``to_dict`` comparison holds for a one-score resource."""
    score = grr.build_score_from_resource_id(NUMBER_SCORE, repo)

    assert score.get_score_histogram().to_dict() == \
        score.get_score_histogram(NUMBER_SCORE_ID).to_dict()


def test_out_of_range_values_are_counted_below_and_above() -> None:
    """``out_of_range_bins`` counts the values below and above the range."""
    hist = _number_histogram(0.1, -1.0, -2.0, 2.0)

    assert hist.out_of_range_bins == [2, 1]
    assert (hist.min_value, hist.max_value) == (-2.0, 2.0)


def test_count_mean_and_population_std() -> None:
    """``count`` includes the out-of-range values, ``std`` uses ``n``."""
    values = [0.1, 0.2, 0.3, 0.4, 2.0, -1.0]
    hist = _number_histogram(*values)

    assert hist.count == sum(hist.bars) + sum(hist.out_of_range_bins) == 6
    assert hist.mean == pytest.approx(np.mean(values))
    assert hist.std == pytest.approx(np.std(values, ddof=0))


def test_moments_summary_gives_formatted_pairs() -> None:
    """The pairs are labelled ``n``, ``mean`` and ``sd``."""
    hist = _number_histogram(0.1, 0.2, 0.3, 0.4, 2.0, -1.0)

    assert hist.moments_summary() == (
        ("n", "6"), ("mean", "0.333"), ("sd", "0.879"))


def test_older_file_has_no_moments(repo: GenomicResourceRepo) -> None:
    """The mini-GRR histogram files predate the stored moments."""
    hist = grr.build_score_from_resource_id(
        NUMBER_SCORE, repo).get_score_histogram(NUMBER_SCORE_ID)

    assert isinstance(hist, NumberHistogram)
    assert (hist.count, hist.mean, hist.std) == (None, None, None)
    assert hist.moments_summary() is None


def test_empty_histogram_counts_zero_without_moments() -> None:
    """No values: ``count`` is zero, ``mean`` and ``std`` are ``None``."""
    hist = _number_histogram()

    assert hist.count == 0
    assert (hist.mean, hist.std) == (None, None)
    assert hist.moments_summary() is None


def test_categorical_histogram_of_a_score(repo: GenomicResourceRepo) -> None:
    """``display_values`` orders the categories by their counts."""
    hist = grr.build_score_from_resource_id(
        CATEGORICAL_SCORE, repo).get_score_histogram(CATEGORICAL_SCORE_ID)

    assert isinstance(hist, CategoricalHistogram)
    assert hist.truncated is False
    assert (hist.unique_values, hist.total_count) == (2, 6)
    assert hist.raw_values == {"good": 2, "bad": 4}
    assert list(hist.display_values.items()) == [("bad", 4), ("good", 2)]


def test_truncated_without_a_sidecar_gives_the_complete_histogram(
    repo: GenomicResourceRepo,
) -> None:
    """``truncated=True`` falls back to the complete histogram."""
    score = grr.build_score_from_resource_id(CATEGORICAL_SCORE, repo)

    small = score.get_score_histogram(CATEGORICAL_SCORE_ID, truncated=True)

    assert small.to_dict() == \
        score.get_score_histogram(CATEGORICAL_SCORE_ID).to_dict()


def test_truncated_sidecar_keeps_the_whole_distribution_counts() -> None:
    """The sidecar stores fewer categories, the same totals."""
    hist = CategoricalHistogram(
        CategoricalHistogramConfig(displayed_values_count=2))
    for value in "aaabbc":
        hist.add_value(value)

    small = CategoricalHistogram.deserialize(hist.serialize_truncated())

    assert small.truncated is True
    assert small.raw_values == {"a": 3, "b": 2}
    assert (small.unique_values, small.total_count) == (3, 6)


def _repo_with_sidecar(root: pathlib.Path) -> GenomicResourceRepo:
    """A categorical score stored as gzipped JSON with a sidecar."""
    a_grr().with_resource(
        "score", a_position_score().with_score("cat", "str")
        .with_data("chrom pos_begin cat\nchr1 1 a\nchr1 2 b\n")
        .with_histogram({"type": "categorical", "displayed_values_count": 1}),
    ).build_repo(root)
    hist = CategoricalHistogram(
        CategoricalHistogramConfig(displayed_values_count=1))
    for value in "aab":
        hist.add_value(value)
    resource_dir = root / "score"
    sidecar = resource_dir / truncated_histogram_filename(
        "statistics/histogram_cat.json")
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(hist.serialize_truncated())
    (resource_dir / "statistics" / "histogram_cat.json.gz").write_bytes(
        gzip.compress(hist.serialize().encode()))
    for stale in [*root.rglob(".MANIFEST"), *root.glob(".CONTENTS*")]:
        stale.unlink()
    return grr.build_genomic_resource_repository({
        "id": "sidecar", "type": "directory", "directory": str(root),
    })


def test_truncated_read_gives_the_sidecar(tmp_path: pathlib.Path) -> None:
    """Both histograms report the counts of the complete distribution."""
    score = grr.build_score_from_resource_id(
        "score", _repo_with_sidecar(tmp_path))

    full = score.get_score_histogram("cat")
    small = score.get_score_histogram("cat", truncated=True)

    assert isinstance(full, CategoricalHistogram)
    assert isinstance(small, CategoricalHistogram)
    assert (full.truncated, len(full.raw_values)) == (False, 2)
    assert (small.truncated, len(small.raw_values)) == (True, 1)
    for hist in (full, small):
        assert (hist.unique_values, hist.total_count) == (2, 3)


def test_histogram_filename_is_gzipped_with_a_sidecar(
    tmp_path: pathlib.Path,
) -> None:
    """The manifest lists the sidecar and the gzipped full histogram."""
    score = grr.build_score_from_resource_id(
        "score", _repo_with_sidecar(tmp_path))

    assert score.get_histogram_filename("cat") == \
        "statistics/histogram_cat.json.gz"


def test_histogram_filename_is_the_listed_yaml(
    repo: GenomicResourceRepo,
) -> None:
    """The mini-GRR manifest lists the legacy ``.yaml`` file."""
    score = grr.build_score_from_resource_id(NUMBER_SCORE, repo)

    assert score.get_histogram_filename(NUMBER_SCORE_ID) == \
        f"statistics/histogram_{NUMBER_SCORE_ID}.yaml"


def test_histogram_filename_is_json_otherwise(
    repo: GenomicResourceRepo,
) -> None:
    """A manifest without a ``.yaml`` file gives the ``.json`` file."""
    score = grr.build_score_from_resource_id(CATEGORICAL_SCORE, repo)

    assert score.get_histogram_filename(CATEGORICAL_SCORE_ID) == \
        f"statistics/histogram_{CATEGORICAL_SCORE_ID}.json"


def test_load_histogram_reads_the_file_of_a_score(
    repo: GenomicResourceRepo,
) -> None:
    """The page's example prints the type and the values domain."""
    score = grr.build_score_from_resource_id(NUMBER_SCORE, repo)
    resource = repo.get_resource(NUMBER_SCORE)

    hist = load_histogram(
        resource, score.get_histogram_filename(NUMBER_SCORE_ID))

    assert hist.type == "number_histogram"
    assert hist.values_domain() == "[0, 0.9]"


def test_null_histogram_of_a_score_comes_from_its_configuration(
    repo: GenomicResourceRepo,
) -> None:
    """A null histogram is an object with a reason, not ``None``."""
    hist = grr.build_score_from_resource_id(
        NULL_SCORE, repo).get_score_histogram(NULL_SCORE_ID)

    assert isinstance(hist, NullHistogram)
    assert hist.reason == "Unspecified reason"


def test_null_histogram_plot_draws_nothing() -> None:
    """``plot`` writes nothing to the output."""
    hist = NullHistogram(NullHistogramConfig("a reason"))
    outfile = io.BytesIO()

    hist.plot(outfile, "score")

    assert outfile.getvalue() == b""


def test_default_null_config_reason() -> None:
    """The default reason is ``Unspecified reason``."""
    assert NullHistogramConfig.default_config().reason == \
        "Unspecified reason"


@pytest.mark.parametrize(("filename", "content", "reason"), [
    ("statistics/missing.json", None, "Histogram file not found."),
    ("statistics/hist.txt", "{}", "Invalid histogram file format."),
    ("statistics/bad.json",
     '{"config": {"type": "number"}, "bars": "x"}',
     "Failed to deserialize histogram."),
])
def test_load_histogram_problems_give_a_null_histogram(
    tmp_path: pathlib.Path, filename: str, content: str | None,
    reason: str,
) -> None:
    """A missing file, an unknown extension or a bad body is handled."""
    resource = a_position_score().build_resource(tmp_path)
    if content is not None:
        (tmp_path / filename).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / filename).write_text(content)

    hist = load_histogram(resource, filename)

    assert isinstance(hist, NullHistogram)
    assert hist.reason == reason


def test_load_histogram_without_config_raises(
    tmp_path: pathlib.Path,
) -> None:
    """A missing ``config`` raises instead of giving a null histogram."""
    resource = a_position_score().build_resource(tmp_path)
    (tmp_path / "statistics").mkdir(exist_ok=True)
    (tmp_path / "statistics" / "hist.json").write_text('{"bars": []}')

    with pytest.raises(KeyError):
        load_histogram(resource, "statistics/hist.json")


def test_score_definitions_without_a_histogram_block() -> None:
    """No ``histogram:`` block gives ``None``, not a default."""
    definitions = parse_scoredef_config(
        {"scores": [{"id": "x", "type": "float", "name": "x"}]})

    assert list(definitions) == ["x"]
    assert definitions["x"].hist_conf is None
    assert build_histogram_config({"id": "x"}) is None


def test_default_configuration_depends_on_the_value_type() -> None:
    """Numbers get a number histogram, strings a categorical one."""
    assert isinstance(
        build_default_histogram_conf("float"), NumberHistogramConfig)
    assert isinstance(
        build_default_histogram_conf("int"), NumberHistogramConfig)
    assert isinstance(
        build_default_histogram_conf("str"), CategoricalHistogramConfig)
    assert isinstance(
        build_default_histogram_conf("bool"), NullHistogramConfig)


def test_histogram_config_of_a_score_applies_the_default(
    tmp_path: pathlib.Path,
) -> None:
    """A score without a ``histogram:`` block still has a configuration."""
    score = grr.build_score_from_resource(
        a_position_score().with_score("s", "float")
        .with_data("chrom pos_begin s\nchr1 1 0.5\n")
        .build_resource(tmp_path))

    assert isinstance(score.get_histogram_config("s"), NumberHistogramConfig)


def test_add_value_add_batch_and_merge_accumulate() -> None:
    """Values added one by one, in a batch and by a merge are counted."""
    first = _number_histogram(0.1, 0.2)
    second = _number_histogram()
    second.add_batch(np.array([0.5, 0.6]), np.array([1, 1]))

    first.merge(second)

    assert first.count == 4


def test_histograms_with_different_bins_do_not_merge() -> None:
    """Merging needs compatible bin boundaries."""
    ten = _number_histogram(0.1)
    five = NumberHistogram(
        NumberHistogramConfig(view_range=(0.0, 1.0), number_of_bins=5))

    with pytest.raises(ValueError):
        ten.merge(five)


def test_cardinality_limit_applies_to_a_default_configuration() -> None:
    """More than 100 values raise ``HistogramError``."""
    assert CategoricalHistogram.UNIQUE_VALUES_LIMIT == 100
    config = CategoricalHistogramConfig.default_config()
    assert config.enforce_type is False
    hist = CategoricalHistogram(config)
    for value in range(100):
        hist.add_value(str(value))

    with pytest.raises(HistogramError):
        hist.add_value("one too many")


def test_cardinality_limit_does_not_apply_to_explicit_categorical() -> None:
    """A score configured as categorical has no limit."""
    hist = CategoricalHistogram(
        CategoricalHistogramConfig.from_dict({"type": "categorical"}))

    for value in range(150):
        hist.add_value(str(value))

    assert hist.unique_values == 150
