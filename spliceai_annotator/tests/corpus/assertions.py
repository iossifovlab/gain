# pylint: disable=C0116
"""Shared differential assertions for the SpliceAI corpus (issue #320/#321).

The differential harness makes two independent checks per variant -- live vs a
committed baseline, and batch vs sequential -- both with the *same* tiering:

* labels (``gene``/``transcript_ids``) exact;
* the four 4dp probability vectors within ``PROB_TOL`` (their 4dp grid is not
  portable any tighter);
* the raw ``DS_*`` floats within ``FLOAT_TOL`` (full precision, portable);
* ``DP_*`` / ``delta_score`` positions only above the ``DS_MAX`` argmax noise
  floor.

Both the #320 fixture tier (``test_spliceai_differential``) and the #321
node-local-real-GRR tier import these helpers so the tiering is defined once.
The baseline is a *parameter* here (the fixture tier passes its frozen
``baseline.json.gz``; the real tier passes either that same baseline -- the
fixture-honesty tie -- or its own ``baseline_extra.json.gz``).
"""
from __future__ import annotations

from typing import Any

from gain.annotation.annotatable import VCFAllele

from tests.corpus.pipeline_config import (
    DELTA_DS_TOL,
    DP_SOURCES,
    DS_MAX_GATE,
    DS_SOURCES,
    FLOAT_TOL,
    PROB_SOURCES,
    PROB_TOL,
)


def allele(variant: dict[str, Any]) -> VCFAllele:
    return VCFAllele(
        variant["contig"], variant["pos"], variant["ref"], variant["alt"])


def parse_prob(value: str) -> list[float]:
    """Flatten a ``;``-joined (per gene), ``,``-joined (per pos) prob string."""
    return [
        float(x)
        for gene_vec in value.split(";")
        for x in gene_vec.split(",")
    ]


def assert_probs_close(vid: str, label: str, got: str, want: str) -> None:
    """Assert two 4dp probability strings agree within ``PROB_TOL``."""
    live = parse_prob(got)
    ref = parse_prob(want)
    assert len(live) == len(ref), f"{vid} {label} length"
    for g, e in zip(live, ref, strict=True):
        assert abs(g - e) <= PROB_TOL, f"{vid} {label}: {g} vs {e}"


def delta_score_close(got: str, want: str) -> bool:
    """Compare ``delta_score`` robustly across fp environments.

    It is ``;``-joined per gene, each ``alt|gene|DS x4 (2dp)|DP x4 (int)``.
    Alt/gene and the DP integers must match exactly; the 2dp ``DS`` fields
    (which can round to adjacent values across fp environments) must agree
    within one 2dp unit -- the raw ``DS_*`` are pinned separately at
    ``FLOAT_TOL``.
    """
    got_genes = got.split(";")
    want_genes = want.split(";")
    if len(got_genes) != len(want_genes):
        return False
    for g, w in zip(got_genes, want_genes, strict=True):
        gf = g.split("|")
        wf = w.split("|")
        if len(gf) != 10 or len(wf) != 10:
            return False
        if gf[:2] != wf[:2] or gf[6:] != wf[6:]:  # alt|gene and DP integers
            return False
        if any(abs(float(a) - float(b)) > DELTA_DS_TOL
               for a, b in zip(gf[2:6], wf[2:6], strict=True)):  # 2dp DS
            return False
    return True


def assert_matches_baseline(
    variant: dict[str, Any],
    result: dict[str, Any],
    baseline: dict[str, dict[str, Any]],
) -> None:
    """Assert a live annotation matches ``baseline[variant['id']]`` (tiered)."""
    expected = baseline[variant["id"]]

    if all(v is None for v in expected.values()):
        assert all(v is None for v in result.values()), (
            f"{variant['id']}: expected all-None (rejected), got {result}")
        return

    # probability vectors: parse to floats, compare at PROB_TOL (4dp grid)
    for source in PROB_SOURCES:
        assert_probs_close(
            variant["id"], source, result[source], expected[source])

    # raw DS_* floats: full precision, portable across fp environments
    for source in DS_SOURCES:
        assert abs(result[source] - expected[source]) <= FLOAT_TOL, (
            f"{variant['id']} {source}: {result[source]} vs "
            f"{expected[source]}")

    # DP_* and delta_score embed argmax positions -- only trustworthy above
    # the noise floor.
    if expected["DS_MAX"] > DS_MAX_GATE:
        for source in DP_SOURCES:
            assert result[source] == expected[source], (
                f"{variant['id']} {source}: {result[source]} vs "
                f"{expected[source]}")
        assert delta_score_close(
            result["delta_score"], expected["delta_score"]), (
            f"{variant['id']} delta_score: {result['delta_score']} vs "
            f"{expected['delta_score']}")


def assert_batch_equals_sequential(
    variant: dict[str, Any],
    seq_result: dict[str, Any],
    batch_result: dict[str, Any],
) -> None:
    """Assert ``batch_annotate`` == ``annotate`` for one variant (tiered)."""
    if all(v is None for v in seq_result.values()):
        assert all(v is None for v in batch_result.values()), (
            f"{variant['id']}: batch not all-None for rejected")
        return
    # Every annotated variant: batch must equal sequential.  Deletions with
    # ref_len-1 > distance -- the only case where the two padding paths would
    # structurally diverge (batch mis-reconstructs vs Illumina SpliceAI) --
    # are refused by the annotator, so they never reach here as annotated
    # results.  What remains is only batch fp non-associativity, which the
    # tiering below absorbs.
    for source in ("gene", "transcript_ids"):
        assert batch_result[source] == seq_result[source], (
            f"{variant['id']} {source}: batch != sequential")
    # probability vectors can flip a 4dp unit under batch fp non-associativity
    for source in PROB_SOURCES:
        assert_probs_close(
            variant["id"], source, batch_result[source], seq_result[source])
    # raw DS_* floats at full precision
    for source in DS_SOURCES:
        assert abs(batch_result[source] - seq_result[source]) <= FLOAT_TOL, (
            f"{variant['id']} {source}: batch {batch_result[source]} vs "
            f"sequential {seq_result[source]}")
    # DP_* and delta_score positions: trustworthy only above the noise floor
    # (a weak channel's argmax can flip under batch fp non-associativity, even
    # when DS_MAX is high -- the same near-tie effect the baseline gates on).
    if seq_result["DS_MAX"] > DS_MAX_GATE:
        for source in DP_SOURCES:
            assert batch_result[source] == seq_result[source], (
                f"{variant['id']} {source}: batch != sequential")
        assert delta_score_close(
            batch_result["delta_score"], seq_result["delta_score"]), (
            f"{variant['id']} delta_score: batch != sequential")
