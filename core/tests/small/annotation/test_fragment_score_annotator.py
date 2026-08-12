# pylint: disable=W0621,C0114,C0116

import textwrap

import pytest
from gain.annotation.annotatable import Annotatable, Position, Region
from gain.annotation.annotation_config import AnnotationConfigurationError

# VCFAllele
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing import (
    build_inmemory_test_repository,
    convert_to_tab_separated,
)


@pytest.fixture(scope="module")
def grr() -> GenomicResourceRepo:
    return build_inmemory_test_repository({
        "fragments": {
            "genomic_resource.yaml": textwrap.dedent("""
                type: fragment_score
                table:
                  filename: data.mem
                scores:
                - id: frequency
                  name: frequency
                  type: float
                  desc: some populaton frequency
                - id: collection
                  name: collection
                  type: str
                  desc: SSC or AGRE
                - id: affected_status
                  name: affected_status
                  type: str
                  aggregator: join(,)
                  desc: |
                        shows if the child that has the de novo is
                        affected or unaffected
            """),
            "data.mem": convert_to_tab_separated("""
               chrom  pos_begin  pos_end  frequency  collection affected_status
               1      10         20       0.02       SSC        affected
               1      50         100      0.1        AGRE       affected
               2      1          2        0.00001    AGRE       unaffected
               2      16         20       0.3        SSC        affected
               2      200        203      0.0002     AGRE       unaffected
               15     16         20       0.2        AGRE       affected
            """)},
    })


@pytest.fixture(scope="module")
def larger_grr() -> GenomicResourceRepo:
    return build_inmemory_test_repository({
        "fragments": {
            "genomic_resource.yaml": textwrap.dedent("""
                type: fragment_score
                table:
                  filename: data.mem
                scores:
                - id: frequency
                  name: frequency
                  type: int
                  desc: some populaton frequency
                - id: collection
                  name: collection
                  type: str
                  desc: SSC or AGRE
                - id: affected_status
                  name: "affected_status"
                  type: str
                  desc: |
                        shows if the child that has the de novo is
                        affected or unaffected
                - id: size
                  name: size
                  type: int
                  desc: size
            """),
            "data.mem": convert_to_tab_separated("""
chrom  pos_begin  pos_end  frequency  collection  affected_status  size
chr1   10         20       1          SSC         affected         100
chr1   50         100      1          AGRE        affected         130
chr1   1          2        1          AGRE        unaffected       250
chr1   16         20       3          SSC         affected         360
chr1   32         65       2          AGRE        unaffected       560
chr1   15         60       2          AGRE        affected         670
chr1   12         78       3          SSC        unaffected       550
chr1   16         35       4          AGRE        unaffected       300
chr1   25         67       5          SSc        affected         50
chr1   24         35       2          AGRE        affected         900
            """)},
    })


@pytest.mark.parametrize("annotatable, fragment_count", [
    (Position("1", 15), 1),
    (Region("1", 15, 60), 2),
    (Region("1", 30, 40), 0),
])
def test_basic(
        annotatable: Annotatable,
        fragment_count: int, grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - fragment_score: fragments
            """),
        grr)

    atts = pipeline.annotate(annotatable)
    assert atts["count"] == fragment_count


@pytest.mark.parametrize("annotatable, fragment_count", [
    (Position("1", 15), 1),
    (Region("1", 15, 60), 1),
    (Region("1", 30, 40), 0),
])
def test_fragment_filter(
        annotatable: Annotatable, fragment_count: int,
        grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - fragment_score:
                resource_id: fragments
                fragment_filter: frequency < 0.05 or collection == "SSC"
            """),
        grr)

    atts = pipeline.annotate(annotatable)
    assert atts["count"] == fragment_count


@pytest.mark.parametrize("annotatable, fragment_count", [
    (Position("1", 15), 1),
    (Region("1", 15, 60), 1),
    (Region("1", 30, 40), 0),
])
def test_fragment_filter_in(
        annotatable: Annotatable, fragment_count: int,
        grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - fragment_score:
                resource_id: fragments
                fragment_filter: '"SSC" in collection'
            """),
        grr)

    atts = pipeline.annotate(annotatable)
    assert atts["count"] == fragment_count


@pytest.mark.parametrize("annotatable, fragment_count", [
    (Position("1", 15), 1),
    (Region("1", 15, 60), 1),
    (Region("1", 30, 40), 0),
])
def test_fragment_filter_on_newline(
        annotatable: Annotatable, fragment_count: int,
        grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - fragment_score:
                resource_id: fragments
                fragment_filter: >
                    frequency < 0.05 or collection == "SSC"
            """),
        grr)

    atts = pipeline.annotate(annotatable)
    assert atts["count"] == fragment_count


@pytest.fixture(scope="module")
def digit_named_grr() -> GenomicResourceRepo:
    """A fragment score named the way real population scores are.

    A leading-digit name is what the fragment grammar could not express;
    ``1000G`` is the shape the GRR actually publishes.
    """
    return build_inmemory_test_repository({
        "fragments": {
            "genomic_resource.yaml": textwrap.dedent("""
                type: fragment_score
                table:
                  filename: data.mem
                scores:
                - id: 1000G
                  name: 1000G
                  type: float
                  desc: a population frequency
            """),
            "data.mem": convert_to_tab_separated("""
               chrom  pos_begin  pos_end  1000G
               1      10         20       0.02
               1      50         100      0.10
            """)},
    })


def test_fragment_filter_takes_digit_names_and_negative_numbers(
    digit_named_grr: GenomicResourceRepo,
) -> None:
    """This annotator reaches the shared compiler, not its own grammar.

    Both halves of the expression were unwritable on the fragment side
    before: its ``word`` was letters-only and its ``number`` unsigned.  The
    grammar itself is pinned where it lives, in ``test_score_filter``; what
    this adds is that a fragment pipeline really routes there.
    """
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - fragment_score:
                resource_id: fragments
                fragment_filter: "1000G > 0.05 and 1000G > -1"
            """),
        digit_named_grr)

    atts = pipeline.annotate(Region("1", 1, 200))
    assert atts["count"] == 1


@pytest.mark.parametrize("parameter", [
    "fragment_filter",
    # Deliberately the legacy spelling: what is pinned is that the message
    # names whichever one was written.
    pytest.param("cnv_filter", marks=pytest.mark.legacy_vocabulary),
])
def test_fragment_filter_naming_an_unknown_score_is_refused(
    parameter: str, grr: GenomicResourceRepo,
) -> None:
    """Refused while the pipeline builds, under the spelling the user wrote.

    Both spellings are one parameter, and whichever one the configuration
    says is the one named back -- reporting the other sends the author
    looking for a key that appears nowhere in their file (gain#477).
    """
    with pytest.raises(AnnotationConfigurationError) as excinfo:
        load_pipeline_from_yaml(
            textwrap.dedent(f"""
                - fragment_score:
                    resource_id: fragments
                    {parameter}: 'freq < 0.05'
                """),
            grr)

    message = str(excinfo.value)
    assert f"Error parsing {parameter}" in message
    assert "'frequency'" in message


@pytest.mark.parametrize(
    "annotatable, fragment_count, status, status2, collection", [
        (Position("1", 15), 1, "affected", "affected", "SSC"),
        (Region("1", 15, 60), 2,
         "affected,affected", "affected", "SSC,AGRE"),
        (Region("1", 30, 40), 0, None, None, None),
    ])
def test_fragment_filter_and_attribute(
    annotatable: Annotatable, fragment_count: int,
    status: str, status2: str, collection: str,
    grr: GenomicResourceRepo,
) -> None:
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - fragment_score:
                resource_id: fragments
                fragment_filter: frequency < 0.05 or collection == "AGRE"
                attributes:
                - count
                - name: status
                  source: "affected_status"
                - name: status2
                  source: "affected_status"
                  aggregator: mode
                - source: "collection"
            """),
        grr)

    atts = pipeline.annotate(annotatable)
    assert "status" in atts
    assert "status2" in atts
    assert "collection" in atts

    assert atts["count"] == fragment_count
    assert atts["status"] == status
    assert atts["status2"] == status2
    assert atts["collection"] == collection

    status_info = pipeline.get_attribute_info("status")
    status2_info = pipeline.get_attribute_info("status2")
    collection_info = pipeline.get_attribute_info("collection")
    assert status_info is not None
    assert status2_info is not None
    assert collection_info is not None

    assert "aggregator: join(,)" in status_info.documentation
    assert "aggregator: mode" in status2_info.documentation
    assert "aggregator: join(,)" in collection_info.documentation


def test_fragment_aggregators(
    larger_grr: GenomicResourceRepo,
) -> None:
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - fragment_score:
                resource_id: fragments
                attributes:
                - count
                - name: size_max
                  source: "size"
                  aggregator: max
                - name: affected_status_count
                  source: "affected_status"
                  aggregator: count
                - name: size_median
                  source: "size"
                  aggregator: median
                - name: frequency_median
                  source: "frequency"
                  aggregator: median
                - name: frequency_list
                  source: "frequency"
                  aggregator: list
                - name: size_mode
                  source: "size"
                  aggregator: mode
                - name: collection_join
                  source: "collection"
                  aggregator: join(;)
            """),
        larger_grr)
    annotatable = Region("chr1", 1, 100)
    atts = pipeline.annotate(annotatable)

    assert "count" in atts
    assert "size_max" in atts
    assert "affected_status_count" in atts
    assert "size_median" in atts
    assert "frequency_median" in atts
    assert "frequency_list" in atts
    assert "size_mode" in atts
    assert "collection_join" in atts

    assert atts["count"] == 10
    assert atts["size_max"] == 900
    assert atts["affected_status_count"] == 10
    assert atts["size_median"] == 330.0
    assert atts["frequency_median"] == 2.0
    assert atts["frequency_list"] == [1, 1, 3, 2, 3, 4, 2, 5, 2, 1]
    assert atts["size_mode"] == 50
    assert atts["collection_join"] == \
        "AGRE;SSC;SSC;AGRE;SSC;AGRE;AGRE;SSc;AGRE;AGRE"
