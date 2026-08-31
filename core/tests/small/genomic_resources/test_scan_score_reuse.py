"""How many times one statistics-scan task builds its score (gain#1038).

A scan task's cost is dominated by ``GenomicScore.__init__``, which is
~98% the cerberus normalize-and-validate pass over the resource's config
-- and that pass scales with the score count, so a wide resource pays
milliseconds per construction.  ``grr_manage repo-repair`` runs one task
per region, ~1035 of them for an hg38-scale resource, so a task that
builds its score four times instead of once spends seconds per resource
re-validating a config that has not changed since the first read.

These tests pin the construction COUNT, at the two task entry points the
repair path schedules, on both the bulk and the per-record route each of
them can take.  The count is the behaviour; the results are pinned by the
bulk-vs-per-record parity suites beside this file, which run unchanged.

The per-record route is reached twice over, deliberately, because the
two gates refuse for unrelated reasons and a task frame has to build one
score either way: ``_int_tabix`` is refused by the histogram gate's
value-type PAIRING rule, ``_plain_text_score`` by the shared gate's
question about the BACKEND.  Neither fixture would catch a regression in
the other's rule.
"""
# pylint: disable=C0116,W0621
from __future__ import annotations

import pathlib
from typing import Any

import pytest
import pytest_mock
from gain.genomic_resources.genomic_scores import (
    build_score_from_resource,
)
from gain.genomic_resources.histogram import (
    CategoricalHistogramConfig,
    NumberHistogramConfig,
)
from gain.genomic_resources.implementations.genomic_scores_impl import (
    scan,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.builders import (
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


def _float_tabix(tmp_path: pathlib.Path) -> GenomicResource:
    """A float tabix position score -- the bulk path takes this one."""
    return (
        a_position_score()
        .with_score("s1", "float")
        .with_score("s2", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  s1    s2
            chr1   1          3        0.1   0.9
            chr1   4          4        0.5   .
            chr1   5          10       0.95  0.2
            chr1   12         20       1.0   0.5
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def _int_tabix(tmp_path: pathlib.Path) -> GenomicResource:
    """An int tabix position score -- for the per-record path.

    An ``int`` score under a CATEGORICAL histogram is the pairing the
    bulk gate refuses (the bulk read yields its column as ``float64``,
    which a categorical histogram will not count), so a task over this
    resource takes the per-record route.
    """
    return (
        a_position_score()
        .with_score("s", "int")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   1          3        3
            chr1   4          4        7
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def _plain_text_score(tmp_path: pathlib.Path) -> GenomicResource:
    """A float position score over a PLAIN TEXT table.

    No tabix, so the table serves no column-array read and
    :func:`scan.bulk_scan_eligible` refuses it -- the per-record route,
    reached without misconfiguring anything, and with real values to
    reduce.
    """
    return (
        a_position_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   1          3        0.1
            chr1   4          4        0.5
            chr1   5          10       1.0
            """)
        .build_resource(tmp_path)
    )


_ALLELE_TABLE = """
    chrom  pos_begin  reference  alternative  score
    chr1   10         A          G            0.1
    chr1   20         A          AT           0.4
    chr1   30         CT         C            0.5
"""


def _allele_tabix(tmp_path: pathlib.Path) -> GenomicResource:
    """A tabix ALLELE score -- the kind whose task opens its score twice.

    An allele score is the only kind ``region_alleles_for`` answers, so
    it is the only one whose task OPENS its score before the scan, to
    ask the backend whether it serves the nucleotides.  That open is
    closed again, so the scan that follows has to reopen the very same
    instance -- the one thing sharing a score costs that building a
    fresh one per call did not.
    """
    return (
        an_allele_score()
        .with_score("score", "float")
        .with_data(_ALLELE_TABLE)
        .with_tabix()
        .build_resource(tmp_path)
    )


def _spy_on_score_builds(mocker: pytest_mock.MockerFixture) -> Any:
    """Count the scan's score constructions, still building real scores.

    Every construction in ``scan`` goes through the module-global
    ``build_score_from_resource``, so wrapping that name counts them all
    -- and ``wraps`` delegates to the real factory, so the task under
    test does its real work and its result stays checkable.
    """
    return mocker.patch.object(
        scan, "build_score_from_resource",
        wraps=scan.build_score_from_resource)


def test_histogram_task_builds_the_score_once_on_the_bulk_path(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    resource = _float_tabix(tmp_path)
    confs: dict = {"s1": _hist_conf(), "s2": _hist_conf()}
    assert scan.can_bulk_histogram(resource, confs), \
        "fixture must reach the bulk path for this test to mean anything"

    spy = _spy_on_score_builds(mocker)
    result = scan.do_histogram_task(resource, confs, "chr1", 1, 20)

    assert spy.call_count == 1
    assert result.histograms["s1"].bars.sum() > 0


def test_histogram_task_builds_the_score_once_on_the_per_record_path(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """The route a resource the bulk gate refuses takes.

    An ``int`` score under a categorical histogram: a pairing
    :func:`scan.can_bulk_histogram` keeps off the bulk path, so the task
    reaches :func:`scan.do_histogram` -- the other of the two scans that
    used to build a score of its own.
    """
    resource = _int_tabix(tmp_path)
    confs: dict = {"s": CategoricalHistogramConfig.default_config()}
    assert not scan.can_bulk_histogram(resource, confs), \
        "fixture must reach the per-record path for this test to mean anything"

    spy = _spy_on_score_builds(mocker)
    result = scan.do_histogram_task(resource, confs, "chr1", 1, 20)

    assert spy.call_count == 1
    assert result.histograms["s"].raw_values == {3: 3, 7: 1}


def test_min_max_task_builds_the_score_once_on_the_bulk_path(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    resource = _float_tabix(tmp_path)
    assert scan.can_bulk_min_max(resource, ["s1", "s2"]), \
        "fixture must reach the bulk path for this test to mean anything"

    spy = _spy_on_score_builds(mocker)
    result = scan.do_min_max_task(resource, ["s1", "s2"], "chr1", 1, 20)

    assert spy.call_count == 1
    assert (result["s1"].min, result["s1"].max) == (0.1, 1.0)


def test_min_max_task_builds_the_score_once_on_the_per_record_path(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """A bounded region whose backend the bulk gate refuses.

    The task asks the gate -- and is then served by the per-record
    :func:`scan.do_min_max`: the pair of calls that used to build two
    scores between them.
    """
    resource = _plain_text_score(tmp_path)
    assert not scan.can_bulk_min_max(resource, ["s"]), \
        "fixture must reach the per-record path for this test to mean anything"

    spy = _spy_on_score_builds(mocker)
    result = scan.do_min_max_task(resource, ["s"], "chr1", 1, 10)

    assert spy.call_count == 1
    assert (result["s"].min, result["s"].max) == (0.1, 1.0)


def test_min_max_task_builds_the_score_once_for_an_unbounded_region(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """The ``--region-size 0`` shape: no bounds, so the gate is never asked.

    Green before gain#1038 as well -- this route only ever built one
    score -- and kept as the pin that it stays that way, since it is the
    route ``do_noregion_histograms`` drives a whole contig through.
    """
    resource = _float_tabix(tmp_path)

    spy = _spy_on_score_builds(mocker)
    result = scan.do_min_max_task(resource, ["s1", "s2"], "chr1", None, None)

    assert spy.call_count == 1
    assert (result["s1"].min, result["s1"].max) == (0.1, 1.0)


def test_histogram_task_reopens_the_shared_score_for_an_allele_scan(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """The one path that opens the shared score, closes it, and reads on.

    An allele score's task opens its score to ask whether the backend
    serves nucleotides, and that probe closes it again.  With a score per
    call that cost nothing -- the scan built a fresh, never-opened one.
    Sharing makes the scan reopen the instance the probe just closed, so
    the region's statistics are the assertion: an allele score whose
    reopen did not take would read nothing.
    """
    resource = _allele_tabix(tmp_path)
    confs: dict = {"score": _hist_conf()}
    assert scan.can_bulk_histogram(resource, confs), \
        "fixture must reach the bulk path for this test to mean anything"

    spy = _spy_on_score_builds(mocker)
    result = scan.do_histogram_task(resource, confs, "chr1", 1, 40)

    assert spy.call_count == 1
    assert result.alleles is not None, \
        "an allele score's task must carry its allele statistics back"
    assert result.histograms["score"].bars.sum() == 3


@pytest.mark.parametrize("already_opened", [False, True])
def test_a_gate_handed_a_score_builds_none_and_answers_the_same(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
    *,
    already_opened: bool,
) -> None:
    """The ``score=`` parameter is a saving, not a second opinion.

    Each gate is asked twice over one resource -- once standalone, the
    way every other suite in this directory asks it, and once handed a
    score -- and the two answers must agree.  The build count is what
    makes the second ask worth doing at all.

    ``already_opened`` asks it again from the state production actually
    reaches on the allele path, where the nucleotide probe has opened
    the score and handed it on CLOSED.  Not the same object as a fresh
    one: closing a tabix table keeps the parsed header and the resolved
    score indices and releases only the handle.  So "answered without
    opening the score" has to hold *after* an open as well as before
    one, and the gates must not be able to tell the two apart.
    """
    resource = _float_tabix(tmp_path)
    confs: dict = {"s1": _hist_conf(), "s2": _hist_conf()}
    standalone = (
        scan.can_bulk_histogram(resource, confs),
        scan.can_bulk_min_max(resource, ["s1", "s2"]),
        scan.bulk_scan_eligible(resource, ["s1", "s2"]),
    )
    score = build_score_from_resource(resource)
    if already_opened:
        with score.open():
            pass
        assert not score.is_open(), "the score must be handed on CLOSED"

    spy = _spy_on_score_builds(mocker)
    shared = (
        scan.can_bulk_histogram(resource, confs, score=score),
        scan.can_bulk_min_max(resource, ["s1", "s2"], score=score),
        scan.bulk_scan_eligible(resource, ["s1", "s2"], score=score),
    )

    assert shared == standalone
    assert standalone == (True, True, True), \
        "a gate answering False either way would agree vacuously"
    assert spy.call_count == 0


def test_a_scan_closes_the_score_it_was_handed(
    tmp_path: pathlib.Path,
) -> None:
    """The ownership half of the ``score=`` contract, stated as a test.

    A scan opens the score it is given and closes it on return -- which
    is what the freshly built one always got, and what keeps a task from
    leaving a pysam handle open behind it.  Pinned because it is the one
    thing a caller holding its own score could be surprised by, and
    because it holds even for a score handed in ALREADY OPEN: the scan
    does not adopt the caller's lifetime, it ends it.
    """
    resource = _float_tabix(tmp_path)
    score = build_score_from_resource(resource)
    score.open()

    result = scan.do_min_max(resource, ["s1"], "chr1", 1, 20, score=score)

    assert (result["s1"].min, result["s1"].max) == (0.1, 1.0), \
        "the scan must still read the region it was handed a score for"
    assert not score.is_open()
