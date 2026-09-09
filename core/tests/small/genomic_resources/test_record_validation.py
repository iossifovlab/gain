"""The statistics scan's record validation, as a registry (gain#1269, ADR 0027).

ADR 0008 gave each score kind its own validation rule and gave the scan sole
ownership of applying it.  The rules used to sit on the read classes anyway;
this module is where they live now -- two ``functools.singledispatch``
functions whose base registration refuses a kind nobody wrote a rule for.

What these tests cover is the registry itself: that dispatch reaches the right
body, and that every kind the factory can build has been registered.  The rule
bodies are covered where they always were -- ``test_scan_read_door.py`` and
``test_scan_array_door.py`` -- through this module's front door.

An unregistered kind is covered there too, not here:
``test_a_score_kind_must_state_its_own_record_rules`` in
``test_scan_array_door.py`` has asked that question since gain#592, and asks it
of ``record_weight`` in the same breath.  What changed under it is which half
of the refusal is static -- see ADR 0027 and the comment on that test.
"""
# pylint: disable=C0116,W0212,W0621
import pathlib
from collections.abc import Generator

import numpy as np
import pytest
from gain.genomic_resources.genomic_position_table.record import Record
from gain.genomic_resources.genomic_scores import (
    AlleleRecordArrays,
    AlleleScore,
    FragmentScore,
    PositionScore,
    build_score_from_resource,
)
from gain.genomic_resources.resource_errors import (
    MalformedResourceError,
    inverted_span_error,
)
from gain.genomic_resources.statistics.alleles import (
    RegionAlleles,
    allele_arrays_folded_into,
)
from gain.genomic_resources.statistics.record_validation import (
    validate_record_arrays,
    validate_records,
)
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_position_score,
    an_allele_score,
)


def _a_record(chrom: str, pos: int) -> Record:
    """One raw allele record, as a backend yields it."""
    return (chrom, pos, pos, "A", "G", (chrom, str(pos), str(pos)))


def _an_allele_score(tmp_path: pathlib.Path) -> AlleleScore:
    return AlleleScore(
        an_allele_score()
        .with_score("s", "float")
        .with_data("""
            chrom  pos_begin  reference  alternative  s
            chr1   10         A          G            0.5
        """)
        .build_resource(tmp_path),
    )


def test_dispatch_reaches_the_rule_written_for_the_score_kind(
    tmp_path: pathlib.Path,
) -> None:
    # The tracer bullet: an allele score's records may repeat a position but
    # may not move backwards, and the refusal names the kind.  That the
    # message says "an allele score's" is what proves dispatch landed on the
    # allele body rather than on some shared default.
    score = _an_allele_score(tmp_path)

    with pytest.raises(MalformedResourceError) as excinfo:
        list(validate_records(score, iter([
            _a_record("chr1", 20), _a_record("chr1", 10),
        ])))

    assert "an allele score's records must not move backwards" in str(
        excinfo.value)


def test_records_that_hold_to_the_rule_pass_through_unchanged(
    tmp_path: pathlib.Path,
) -> None:
    # A transducer: what comes out is what went in, in order.  Two records at
    # ONE position is what an allele score is made of, so this stream is legal.
    score = _an_allele_score(tmp_path)
    records = [_a_record("chr1", 10), _a_record("chr1", 10),
               _a_record("chr1", 20)]

    assert list(validate_records(score, iter(records))) == records


def test_a_record_whose_span_runs_backwards_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    # A record whose pos_end (10) precedes its pos_begin (20): a resource no
    # reader can proceed past, and a different rule from the ordering one --
    # it is about one record's own two ends, so it raises OSError rather than
    # MalformedResourceError.  Every kind's rule reads its spans through the
    # same helper, so the scan refuses this whichever kind is reading.
    #
    # Asked through the door rather than of the helper: the helper is private
    # to this module now, and the door is where the scan meets the rule.
    score = _an_allele_score(tmp_path)
    backwards_span = ("chr1", 20, 10, None, None, ("chr1", "20", "10"))

    with pytest.raises(OSError, match="has a region") as excinfo:
        list(validate_records(score, iter([backwards_span])))

    assert not isinstance(excinfo.value, MalformedResourceError)


def test_every_buildable_kind_is_registered(
    tmp_path: pathlib.Path,
) -> None:
    # The test that stands in for a compile-time check.  While the rules were
    # @abstractmethod on GenomicScore, a kind that omitted one was refused by
    # mypy ([abstract]) and pylint (W0223) before anything ran; a missing
    # singledispatch registration has no such analogue.  ADR 0001's gain#1261
    # Amendment retired the bulk scan's kind gate on the strength of that
    # refusal, so this is where the guarantee is kept now (ADR 0027).
    #
    # The kinds are read off the factory rather than listed here: a fourth
    # kind wired into build_score_from_resource and registered nowhere is
    # exactly the case this must catch.
    resources = [
        a_position_score().with_score("s", "float").with_data("""
            chrom  pos_begin  pos_end  s
            chr1   10         20       0.5
        """),
        an_allele_score().with_score("s", "float").with_data("""
            chrom  pos_begin  reference  alternative  s
            chr1   10         A          G            0.5
        """),
        a_fragment_score().with_score("s", "float").with_data("""
            chrom  pos_begin  pos_end  s
            chr1   10         20       0.5
        """),
    ]

    kinds = {
        type(build_score_from_resource(
            builder.build_resource(tmp_path / f"kind{index}")))
        for index, builder in enumerate(resources)
    }

    assert kinds == {PositionScore, AlleleScore, FragmentScore}
    for kind in kinds:
        assert kind in validate_records.registry
        assert kind in validate_record_arrays.registry


class _BackwardsAlleleScore(AlleleScore):
    """An allele score whose bulk read yields batches that move backwards.

    A tabix index cannot express such a resource -- the indexer refuses the
    rows -- so the only way to put a backwards batch in front of the fold is
    to hand it one.  Overriding the read this function calls is the seam;
    everything below it, including which rule dispatch picks, is real.
    """

    def fetch_region_allele_arrays(  # type: ignore[override]
        self, chrom: str, start: int | None, end: int | None,
        scores: list[str], *, batch_size: int,
    ) -> Generator[AlleleRecordArrays, None, None]:
        for begin in (20, 10):
            yield AlleleRecordArrays(
                pos_begin=np.array([begin], dtype=np.int64),
                pos_end=np.array([begin], dtype=np.int64),
                values={"s": np.array([0.5], dtype=np.float64)},
                reference=np.array(["A"]),
                alternative=np.array(["G"]),
            )


def test_the_allele_fold_still_reads_through_the_validation_door(
    tmp_path: pathlib.Path,
) -> None:
    # The fold widens the bulk read to carry the nucleotides this statistic
    # needs, and hands each batch's [:3] slice onward.  What it must not do is
    # become a second way into a region that skips validation (ADR 0008): a
    # resource the scan would refuse must still be refused when the allele
    # statistic is the thing reading it.
    score = _BackwardsAlleleScore(
        an_allele_score()
        .with_score("s", "float")
        .with_data("""
            chrom  pos_begin  reference  alternative  s
            chr1   10         A          G            0.5
        """)
        .build_resource(tmp_path),
    )
    alleles = RegionAlleles("chr1", 1, 100)

    with pytest.raises(MalformedResourceError) as excinfo:
        list(allele_arrays_folded_into(
            score, "chr1", 1, 100, ["s"],
            batch_size=1, alleles=alleles))

    assert "an allele score's records must not move backwards" in str(
        excinfo.value)


def test_inverted_span_error_names_the_record_and_its_two_ends() -> None:
    error = inverted_span_error("chr1", 20, 10, None, None)

    assert isinstance(error, OSError)
    assert str(error) == (
        "The resource record chr1:20-10 has a region with end 10 smaller "
        "than the beginning 20."
    )


def test_inverted_span_error_names_the_alleles_when_the_record_carries_them(
) -> None:
    # An allele record's ref/alt are what tell two records at one position
    # apart, so the refusal names them.  A record without them -- a position
    # or fragment row -- says nothing in their place, rather than "None->None".
    error = inverted_span_error("chr1", 20, 10, "A", "G")

    assert str(error) == (
        "The resource record chr1:20-10 A->G has a region with end 10 "
        "smaller than the beginning 20."
    )
