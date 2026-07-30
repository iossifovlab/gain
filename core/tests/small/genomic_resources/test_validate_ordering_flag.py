# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""``open(validate_ordering=...)``: reading and checking are separable.

A region read does two things that are not the same job -- it produces the
records' values, and it checks that their order is one the resource kind can
mean.  The check is what a *statistics build* is for; an annotation read of a
resource whose statistics are current has already had it done, and paying for
it again costs per record on a path that is per-record-Python-bound.

So the check is a property of the OPEN, not of the read: one decision per
score rather than one per record, and the default is to check, so a caller
that has not thought about it cannot silently lose the guard.

What these tests pin, per kind:

* with the check on (the default) a ``DISJOINT`` resource whose records touch
  raises; with it off the same resource reads, yielding every record, and the
  stream handed back is the kind's own producer with nothing in the way;
* ``SHARED``'s rule -- refuse a record that moves BACKWARDS -- is driven
  directly, because no backend can deliver a contig out of order;
* the point read (``fetch_position_scores``) follows the same flag, and with
  the check off returns the FIRST record rather than refusing;
* the vectorized statistics scan is NOT governed by the flag.  That is
  deliberate, not an oversight: it is reached only from a statistics build,
  which is the thing that must check.
"""
import pathlib
import textwrap
from collections.abc import Generator

import pytest
from gain.annotation.annotatable import VCFAllele
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.genomic_resources.genomic_scores import (
    PositionScore,
    build_allele_score_from_resource,
    build_position_score_from_resource,
)
from gain.genomic_resources.histogram import (
    NumberHistogramConfig,
)
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation,
)
from gain.genomic_resources.score_def import ScoreValue
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
    an_allele_score,
)


def _overlapping_position_score(tmp_path: pathlib.Path) -> PositionScore:
    """Two records whose spans touch -- a DISJOINT violation."""
    return build_position_score_from_resource(
        a_position_score()
        .with_score("s", "float")
        .with_data("""
            chrom  pos_begin  pos_end  s
            chr1   10         20       0.1
            chr1   15         25       0.2
        """)
        .build_resource(tmp_path),
    )


def test_a_touching_position_score_raises_when_checking(
    tmp_path: pathlib.Path,
) -> None:
    score = _overlapping_position_score(tmp_path)
    with score.open() as opened, \
            pytest.raises(ValueError, match="multiple values for positions"):
        list(opened.fetch_region_values("chr1", 1, 100, ["s"]))


def test_a_touching_position_score_reads_when_not_checking(
    tmp_path: pathlib.Path,
) -> None:
    score = _overlapping_position_score(tmp_path)
    with score.open(validate_ordering=False) as opened:
        assert list(opened.fetch_region_values("chr1", 1, 100, ["s"])) == [
            (10, 20, [0.1]), (15, 25, [0.2]),
        ]


def test_shared_ordering_refuses_a_record_that_moves_backwards(
    tmp_path: pathlib.Path,
) -> None:
    """SHARED's rule, driven directly -- no resource can express it.

    Every backend delivers a contig's records in ascending position order:
    tabix refuses to index a file whose positions decrease, the in-memory
    backend sorts on load, bigWig intervals are ordered by construction, and
    a VCF table is tabix-backed.  So this branch cannot be reached through a
    resource, and a fixture claiming to reach it would silently test nothing
    -- as one written for this test did, coming back sorted.

    The guard is kept anyway, and pinned here rather than deleted: it costs
    one comparison, and it is the per-record half of a rule the vectorized
    scan also states.  Deleting it would leave that rule half-stated.
    """
    score = build_allele_score_from_resource(
        an_allele_score()
        .with_score("s", "float")
        .with_data("""
            chrom  pos_begin  reference  alternative  s
            chr1   10         A          G            0.1
        """)
        .build_resource(tmp_path),
    )

    def backwards() -> Generator[
            tuple[int, int, list[ScoreValue] | None], None, None]:
        yield 20, 20, [0.2]
        yield 10, 10, [0.1]

    with score.open() as opened, \
            pytest.raises(ValueError, match="multiple values for positions"):
        list(opened._enforce_ordering(backwards(), "chr1"))


def test_not_checking_hands_back_the_producer_untouched(
    tmp_path: pathlib.Path,
) -> None:
    """With the check off there is no guard in the way at all.

    Not merely "does not raise": the stream a caller gets is the kind's own
    producer, so nothing is buffering or inspecting records on the way past.
    """
    score = _overlapping_position_score(tmp_path)
    with score.open(validate_ordering=False) as opened:
        stream = opened.fetch_region_values("chr1", 1, 100, ["s"])
        assert stream.gi_code is opened._region_values.__code__


def test_shared_ordering_permits_a_repeated_position_either_way(
    tmp_path: pathlib.Path,
) -> None:
    """Several records at ONE position is what an allele score IS.

    So it must pass with the check ON -- the flag is about a violating
    resource, and this one is not violating.
    """
    score = build_allele_score_from_resource(
        an_allele_score()
        .with_score("s", "float")
        .with_data("""
            chrom  pos_begin  reference  alternative  s
            chr1   10         A          G            0.1
            chr1   10         A          T            0.2
        """)
        .build_resource(tmp_path),
    )
    with score.open() as opened:
        assert list(opened.fetch_region_values("chr1", 1, 100, ["s"])) == [
            (10, 10, [0.1]), (10, 10, [0.2]),
        ]


def test_the_point_read_follows_the_same_flag(
    tmp_path: pathlib.Path,
) -> None:
    """``fetch_position_scores`` refuses, or takes the first record."""
    score = build_position_score_from_resource(
        a_position_score()
        .with_score("s", "float")
        .with_data("""
            chrom  pos_begin  pos_end  s
            chr1   10         10       0.1
            chr1   10         10       0.2
        """)
        .build_resource(tmp_path),
    )
    with score.open() as opened, \
            pytest.raises(ValueError, match="multiple values"):
        opened.fetch_position_scores("chr1", 10, ["s"])

    score = build_position_score_from_resource(
        a_position_score()
        .with_score("s", "float")
        .with_data("""
            chrom  pos_begin  pos_end  s
            chr1   10         10       0.1
            chr1   10         10       0.2
        """)
        .build_resource(tmp_path / "second"),
    )
    with score.open(validate_ordering=False) as opened:
        assert opened.fetch_position_scores("chr1", 10, ["s"]) == [0.1]


def test_an_absent_position_is_still_none_either_way(
    tmp_path: pathlib.Path,
) -> None:
    score = build_position_score_from_resource(
        a_position_score()
        .with_score("s", "float")
        .with_data("""
            chrom  pos_begin  pos_end  s
            chr1   10         10       0.1
        """)
        .build_resource(tmp_path),
    )
    with score.open(validate_ordering=False) as opened:
        assert opened.fetch_position_scores("chr1", 50, ["s"]) is None


def test_aggregate_region_inherits_the_flag(
    tmp_path: pathlib.Path,
) -> None:
    """The annotation path reads through ``aggregate_region``.

    It is built on ``fetch_region_values``, so it inherits the decision
    rather than making one of its own.
    """
    score = _overlapping_position_score(tmp_path)
    with score.open() as opened, \
            pytest.raises(ValueError, match="multiple values for positions"):
        opened.aggregate_region("chr1", 1, 100, ["s"])

    score = _overlapping_position_score(tmp_path / "second")
    with score.open(validate_ordering=False) as opened:
        assert opened.aggregate_region("chr1", 1, 100, ["s"]) is not None


def test_an_unopened_score_still_reports_itself_unopened(
    tmp_path: pathlib.Path,
) -> None:
    """The flag's default must not turn a clear refusal into AttributeError.

    ``fetch_region_values`` reads ``_validate_ordering`` before it reads
    anything else, so the attribute needs a class-level default; without one
    an unopened score would fail with AttributeError instead of saying it is
    not open.
    """
    score = _overlapping_position_score(tmp_path)
    with pytest.raises(ValueError, match="not open"):
        list(score.fetch_region_values("chr1", 1, 100, ["s"]))


def test_the_bulk_scan_checks_regardless_of_the_flag(
    tmp_path: pathlib.Path,
) -> None:
    """The vectorized path is NOT governed by ``validate_ordering``.

    It is reached only from a statistics build, which is precisely the caller
    that must check -- so its guard is unconditional, and a score opened with
    the flag off still raises through it.  Pinning this keeps the asymmetry a
    decision rather than a thing someone tidies away.
    """
    resource = (
        a_position_score()
        .with_score("s", "float")
        .with_data("""
            chrom  pos_begin  pos_end  s
            chr1   10         20       0.1
            chr1   15         25       0.2
        """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    confs: dict = {"s": NumberHistogramConfig((0.0, 1.0), 4)}
    with pytest.raises(ValueError, match="multiple values for positions"):
        GenomicScoreImplementation._do_histogram_bulk(
            resource, confs, "chr1", 1, 100)


def test_the_score_annotator_opts_out_end_to_end(
    tmp_path: pathlib.Path,
) -> None:
    """The payoff: annotating a touching position score does not raise.

    A direct read of this same resource does raise (see
    ``test_a_touching_position_score_raises_when_checking``), so this pins
    the opt-out end to end rather than white-box.  It is what the ADR trades
    away: a resource that never went through ``resource-repair`` is annotated
    from unvalidated data instead of refusing.
    """
    repo = (
        a_grr()
        .with_resource("scores/touching", (
            a_position_score()
            .with_score("s", "float")
            .with_data("""
                chrom  pos_begin  pos_end  s
                chr1   10         20       0.1
                chr1   15         25       0.2
            """)))
        .build_repo(tmp_path)
    )
    pipeline = load_pipeline_from_yaml(textwrap.dedent("""
        - position_score:
            resource_id: scores/touching
            attributes:
              - source: s
                name: s
    """), repo)

    with pipeline.open() as opened:
        annotator = opened.annotators[0]
        # The annotator's open() is what carries the decision.
        assert annotator.score._validate_ordering is False
        # And the read it performs completes rather than refusing.
        assert opened.annotate(
            VCFAllele("chr1", 12, "A", "T"), {}) is not None
