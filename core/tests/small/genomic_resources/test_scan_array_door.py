"""The statistics scan's vectorized way in to a region (gain#591, ADR 0008).

The bulk counterpart of ``test_scan_read_door.py``.  The scan reads a region's
column arrays through ``validate_record_arrays(fetch_region_value_arrays(...))``
-- the same shape as the per-record door, one link over the stream the scan is
already pulling -- and every plain array read leaves that link out.

What these tests are for is the equivalence: a malformed resource must be
refused the SAME way whichever path it was eligible for.  Before gain#591 the
two paths read one class attribute and enforced different subsets of one rule,
which is the drift gain#585 is unwinding.
"""
# pylint: disable=C0116,W0212,W0621
import pathlib
from typing import ClassVar

import numpy as np
import pytest
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    FragmentScore,
    GenomicScore,
    PositionScore,
    RecordArrays,
    build_allele_score_from_resource,
    build_fragment_score_from_resource,
    build_position_score_from_resource,
)
from gain.genomic_resources.histogram import NumberHistogramConfig
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.resource_errors import MalformedResourceError
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_grr,
    a_position_score,
    an_allele_score,
)


def _hist_conf() -> NumberHistogramConfig:
    return NumberHistogramConfig.from_dict({
        "type": "number",
        "view_range": {"min": 0, "max": 1},
        "number_of_bins": 10,
        "x_log_scale": False,
        "y_log_scale": False,
    })


def _batch(begins: list[int], ends: list[int]) -> RecordArrays:
    """One batch of raw column arrays, as the backends produce them."""
    return (
        np.array(begins, dtype=np.int64),
        np.array(ends, dtype=np.int64),
        {"s": np.array([0.5] * len(begins), dtype=np.float64)},
    )


TOUCHING_RECORDS = """
    chrom  pos_begin  pos_end  s
    chr1   1          5        0.1
    chr1   5          9        0.2
"""


def _position_score_resource(
    tmp_path: pathlib.Path, resource_id: str, data: str,
) -> GenomicResource:
    return (
        a_grr()
        .with_resource(
            resource_id,
            a_position_score()
            .with_score("s", "float")
            .with_tabix()
            .with_data(data))
        .build_repo(tmp_path)
        .get_resource(resource_id)
    )


def _position_score(
    tmp_path: pathlib.Path, resource_id: str, data: str,
) -> PositionScore:
    score = build_position_score_from_resource(
        _position_score_resource(tmp_path, resource_id, data))
    score.open()
    return score


def test_the_array_scan_refuses_a_position_score_whose_records_touch(
    tmp_path: pathlib.Path,
) -> None:
    score = _position_score(tmp_path, "touching", TOUCHING_RECORDS)

    with pytest.raises(MalformedResourceError) as excinfo:
        list(score.validate_record_arrays(
            score.fetch_region_value_arrays("chr1", 1, 10, ["s"]), "chr1"))

    message = str(excinfo.value)
    assert "<touching>" in message
    assert "chr1:5" in message
    assert "at most one record per position" in message


def test_a_score_kind_must_state_its_own_record_rules(
    tmp_path: pathlib.Path,
) -> None:
    # gain#592 deleted the base defaults, so a kind that states neither rule
    # fails loudly instead of being validated by a rule nobody chose for it.
    # Enforced by the body rather than by ABCMeta: ``GenomicScore``'s MRO
    # carries no ABC, and making it one would retroactively enforce the
    # abstract ``get_schema`` inherited from ``ResourceConfigValidationMixin``
    # -- a blast radius this epic has no reason to take on.
    #
    # mypy and pylint both refuse this kind on the strength of the decorators
    # alone, which is the other half of the enforcement and the reason this
    # test has to silence them: a kind this incomplete does not get written by
    # accident, it gets written here on purpose.
    class _KindStatingNothing(GenomicScore):  # pylint: disable=W0223
        """A new score kind whose author stated no record rule."""

        DEFAULT_AGGREGATORS: ClassVar[dict[str, str | None]] = {
            "float": "max", "int": "max", "str": "join(,)", "bool": None,
        }

    kind = _KindStatingNothing(  # type: ignore[abstract]
        _position_score_resource(tmp_path, "any_kind", TOUCHING_RECORDS))

    with pytest.raises(NotImplementedError):
        list(kind.validate_records(iter([])))
    with pytest.raises(NotImplementedError):
        list(kind.validate_record_arrays(iter([]), "chr1"))


def _allele_score(tmp_path: pathlib.Path, resource_id: str) -> AlleleScore:
    # The resource itself is well formed -- a table whose positions decrease
    # cannot be tabix-indexed, so the backwards stream is supplied directly,
    # as the per-record tests of this rule also do.
    score = build_allele_score_from_resource(
        a_grr()
        .with_resource(
            resource_id,
            an_allele_score()
            .with_score("s", "float")
            .with_tabix()
            .with_data("""
                chrom  pos_begin  reference  alternative  s
                chr1   10         A          G            0.1
            """))
        .build_repo(tmp_path)
        .get_resource(resource_id))
    score.open()
    return score


def test_the_array_scan_refuses_an_allele_record_that_moves_backwards(
    tmp_path: pathlib.Path,
) -> None:
    score = _allele_score(tmp_path, "backwards_allele")

    with pytest.raises(MalformedResourceError) as excinfo:
        list(score.validate_record_arrays(
            iter([_batch([10, 5], [10, 5])]), "chr1"))

    message = str(excinfo.value)
    assert "<backwards_allele>" in message
    assert "chr1:5" in message
    assert "an allele score's records must not move backwards" in message


def test_the_array_scan_accepts_allele_records_sharing_a_position(
    tmp_path: pathlib.Path,
) -> None:
    # Several records at one position is what an allele score IS -- one per
    # ref/alt pair -- so the rule compares strictly, and this must pass
    # through untouched.
    score = _allele_score(tmp_path, "shared_site")
    batch = _batch([10, 10, 10], [10, 10, 10])

    assert list(score.validate_record_arrays(iter([batch]), "chr1")) == [batch]


def test_the_allele_rule_reads_the_begins_and_not_the_ends(
    tmp_path: pathlib.Path,
) -> None:
    # An allele record may carry a ``pos_end`` column, and the rule must not
    # look at it: the record means the point it sits at, however wide that
    # column reaches.  Every other allele fixture here sets end == begin,
    # which cannot tell a begins-to-begins comparison from a begins-to-ends
    # one -- this one can, because its ends decrease while its begins rise.
    score = _allele_score(tmp_path, "allele_with_ends")
    batch = _batch([10, 20], [50, 20])

    assert list(score.validate_record_arrays(iter([batch]), "chr1")) == [batch]


def test_an_empty_batch_passes_through_every_kind(
    tmp_path: pathlib.Path,
) -> None:
    # A real backend does yield one: a tabix region query whose rows all
    # begin past the region's end cuts to an empty batch and yields it.  The
    # size guard is what keeps that from indexing off the end of an empty
    # carry column, which would raise IndexError -- an internal error, not a
    # resource refusal, failing a task on a perfectly healthy resource.
    empty = _batch([], [])
    scores = [
        _position_score(tmp_path, "empty_position", TOUCHING_RECORDS),
        _allele_score(tmp_path, "empty_allele"),
        _fragment_score(tmp_path, "empty_fragment"),
    ]

    for score in scores:
        assert list(score.validate_record_arrays(iter([empty]), "chr1")) == [
            empty]
        # An empty batch also must not disturb the carry: the pair below
        # straddles it and is still refused.
        with pytest.raises(MalformedResourceError):
            list(score.validate_record_arrays(
                iter([_batch([10], [10]), empty, _batch([5], [5])]), "chr1"))


def _backwards_arrays(*_args: object, **_kwargs: object) -> object:
    """Stand in for a backend handing the scan a backwards batch."""
    return iter([_batch([10, 5], [10, 5])])


def test_the_bulk_histogram_scan_applies_the_allele_rule(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Net-new enforcement: before gain#591 the vectorized guard was gated on
    # DISJOINT, so an allele score's records could run backwards through a
    # completed bulk scan and it still certified the resource.
    resource = (
        an_allele_score()
        .with_score("s", "float")
        .with_data("""
            chrom  pos_begin  reference  alternative  s
            chr1   10         A          G            0.1
        """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    monkeypatch.setattr(
        AlleleScore, "fetch_region_value_arrays", _backwards_arrays)

    with pytest.raises(MalformedResourceError, match="move backwards"):
        GenomicScoreImplementation._do_histogram_bulk(
            resource, {"s": _hist_conf()}, "chr1", 1, 20)


def test_the_bulk_min_max_scan_applies_the_allele_rule(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Both scan passes read through the door, so neither can be the one that
    # forgets -- a resource refused by the histogram pass is refused by the
    # min/max pass that runs before it.
    resource = (
        an_allele_score()
        .with_score("s", "float")
        .with_data("""
            chrom  pos_begin  reference  alternative  s
            chr1   10         A          G            0.1
        """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    monkeypatch.setattr(
        AlleleScore, "fetch_region_value_arrays", _backwards_arrays)

    with pytest.raises(MalformedResourceError, match="move backwards"):
        GenomicScoreImplementation._do_min_max_bulk(
            resource, ["s"], "chr1", 1, 20)


def _fragment_score(tmp_path: pathlib.Path, resource_id: str) -> FragmentScore:
    score = build_fragment_score_from_resource(
        a_grr()
        .with_resource(
            resource_id,
            a_fragment_score()
            .with_score("s", "float")
            .with_tabix()
            .with_data("""
                chrom  pos_begin  pos_end  s
                chr1   10         20       0.1
            """))
        .build_repo(tmp_path)
        .get_resource(resource_id))
    score.open()
    return score


def test_the_array_scan_refuses_a_fragment_that_moves_backwards(
    tmp_path: pathlib.Path,
) -> None:
    score = _fragment_score(tmp_path, "backwards_fragment")

    with pytest.raises(MalformedResourceError) as excinfo:
        list(score.validate_record_arrays(
            iter([_batch([10, 5], [40, 50])]), "chr1"))

    message = str(excinfo.value)
    assert "<backwards_fragment>" in message
    assert "chr1:5" in message
    assert "a fragment score's records must not move backwards" in message


def test_the_array_scan_accepts_fragments_that_overlap(
    tmp_path: pathlib.Path,
) -> None:
    # Fragments overlap freely and several may share a start; a fragment's
    # own end takes no part in the rule, so an interval reaching back over
    # its predecessor is the normal case.
    score = _fragment_score(tmp_path, "overlapping_fragments")
    batch = _batch([10, 10, 12], [100, 20, 15])

    assert list(score.validate_record_arrays(iter([batch]), "chr1")) == [batch]


def test_the_array_rules_carry_across_a_batch_boundary(
    tmp_path: pathlib.Path,
) -> None:
    # Batches are a read-granularity artefact -- the backend's window, or a
    # batch_size hint -- so no verdict may depend on where one happens to
    # break.  Each pair below is legal within its own batch and illegal
    # across the two.
    allele = _allele_score(tmp_path, "allele_carry")
    with pytest.raises(MalformedResourceError, match="chr1:5"):
        list(allele.validate_record_arrays(
            iter([_batch([10], [10]), _batch([5], [5])]), "chr1"))

    fragment = _fragment_score(tmp_path, "fragment_carry")
    with pytest.raises(MalformedResourceError, match="chr1:5"):
        list(fragment.validate_record_arrays(
            iter([_batch([10], [40]), _batch([5], [50])]), "chr1"))

    position = _position_score(tmp_path, "position_carry", TOUCHING_RECORDS)
    with pytest.raises(MalformedResourceError, match="chr1:5"):
        list(position.validate_record_arrays(
            iter([_batch([1], [5]), _batch([5], [9])]), "chr1"))


def test_a_malformed_resource_is_refused_the_same_way_down_both_paths(
    tmp_path: pathlib.Path,
) -> None:
    # The point of the slice.  One resource, both scan paths, one message:
    # before gain#591 the vectorized path enforced a different subset of the
    # rule than the per-record one while both read a single class attribute,
    # so "the scan passed" meant different things depending on which path the
    # resource happened to be eligible for.
    resource = (
        a_position_score()
        .with_score("s", "float")
        .with_data(TOUCHING_RECORDS)
        .with_tabix()
        .build_resource(tmp_path)
    )
    confs: dict = {"s": _hist_conf()}

    with pytest.raises(MalformedResourceError) as per_record:
        GenomicScoreImplementation._do_histogram(
            resource, confs, "chr1", 1, 9)
    with pytest.raises(MalformedResourceError) as bulk:
        GenomicScoreImplementation._do_histogram_bulk(
            resource, confs, "chr1", 1, 9)

    assert str(bulk.value) == str(per_record.value)
