# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Node-local real-GRR integration tier for the SpliceAI annotator (#321).

The #320 differential harness pins TensorFlow output against a *frozen*
hg38/GENCODE fixture cut from the real GRR.  This tier keeps that fixture
honest by running the same tiered assertions against the **real** node-local
GRR (real hg38 genome + GENCODE gene models), so drift in the real resources
surfaces here while the fixture tier (testing its frozen snapshot) stays green.

Two reused checks, plus a value-pinned breadth tier:

* **De-rebased corpus vs the frozen ``baseline.json.gz``** -- every #320 corpus
  variant, mapped back to its real hg38 coordinate
  (``real_pos = local_pos + window_start - 1``) and annotated through the real
  GRR, must reproduce the committed TensorFlow baseline within the #320 tiering.
  This is the literal fixture-honesty tie.  The two synthetic-gene rejection
  variants (``col6a2_reject_mixed_strand`` / ``col6a2_reject_near_end``) are
  dropped -- their crafted MIXSTR/EDGEGENE models exist only in the fixture; the
  near-chromosome-end refusal is re-covered on real resources in
  ``test_spliceai_integration_extra`` (the mixed-strand refusal is a
  within-gene check unreachable with real GENCODE, so it stays in the fixture
  tier).
* **``batch_annotate`` == ``annotate`` on the real GRR** -- same tiering.

Empirically (this machine, real GRR at /data/cephfs/seqpipe/grr): all 215
non-synthetic corpus variants reproduce the frozen baseline within tiering, with
zero gene-set changes and zero variants whose receptive field exceeds the
fixture window -- so no de-rebase exclusion beyond the two synthetic rejects is
needed.

Requires the node-local GRR (``real_grr`` fixture; skips locally when absent,
hard-fails on the agent -- see ``tests/conftest.py``).
"""
import gzip
import json
import pathlib
from collections import defaultdict
from collections.abc import Iterator
from typing import Any

import pytest
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.genomic_resources.repository import GenomicResourceRepo

from tests.corpus.assertions import (
    allele,
    assert_batch_equals_sequential,
    assert_matches_baseline,
)
from tests.corpus.pipeline_config import make_pipeline_yaml

pytestmark = pytest.mark.integration

# The real resource ids in the node-local GRR (issue #321).  The fixture tier
# uses the cut-down ``hg38/genome`` / ``hg38/gene_models`` ids; here we pass the
# real ids through the same ``make_pipeline_yaml``.
REAL_GENOME = "hg38/genomes/GRCh38.p14"
REAL_GENE_MODELS = "hg38/gene_models/GENCODE/49/comprehensive/CHR"

CORPUS_DIR = pathlib.Path(__file__).parent / "corpus"
MANIFEST = json.loads((CORPUS_DIR / "corpus_manifest.json").read_text())
with gzip.open(CORPUS_DIR / "baseline.json.gz", "rb") as _bf:
    BASELINE: dict[str, dict[str, Any]] = json.loads(_bf.read())
CONTIGS = MANIFEST["contigs"]

# The synthetic-gene rejection variants have no real GENCODE analog (MIXSTR /
# EDGEGENE are crafted only in the fixture gene models).
_SYNTHETIC_ONLY = {"col6a2_reject_mixed_strand", "col6a2_reject_near_end"}


def _derebase(variant: dict[str, Any]) -> dict[str, Any]:
    """Map a local-contig corpus variant back onto its real hg38 coordinate."""
    contig = CONTIGS[variant["contig"]]
    return {
        **variant,
        "contig": contig["real_chrom"],
        "pos": variant["pos"] + contig["window_start"] - 1,
    }


REBASED = [
    _derebase(v) for v in MANIFEST["variants"]
    if v["id"] not in _SYNTHETIC_ONLY
]
_BY_DISTANCE: dict[int, list[dict[str, Any]]] = defaultdict(list)
for _v in REBASED:
    _BY_DISTANCE[_v["distance"]].append(_v)
DISTANCES = sorted(_BY_DISTANCE)

# --- extra real-coordinate loci (#321 part b) ---------------------------------
# Breadth the fixture cannot hold: variants at real GENCODE splice sites on
# chr13 (+) / chr17 (-) plus a real near-chromosome-end refusal on chrM, all in
# real coordinates with their own committed TF baseline.  Built by
# tests/corpus/build_extra_corpus.py, pinned by baseline_extra.json.gz
# (regenerate_baseline_extra.py).
EXTRA_MANIFEST = json.loads((CORPUS_DIR / "extra_manifest.json").read_text())
with gzip.open(CORPUS_DIR / "baseline_extra.json.gz", "rb") as _ef:
    BASELINE_EXTRA: dict[str, dict[str, Any]] = json.loads(_ef.read())
EXTRA_VARIANTS = EXTRA_MANIFEST["variants"]
_EXTRA_BY_DISTANCE: dict[int, list[dict[str, Any]]] = defaultdict(list)
for _v in EXTRA_VARIANTS:
    _EXTRA_BY_DISTANCE[_v["distance"]].append(_v)
EXTRA_DISTANCES = sorted(_EXTRA_BY_DISTANCE)


@pytest.fixture(scope="session")
def real_pipelines(
    real_grr: GenomicResourceRepo,
) -> Iterator[dict[int, AnnotationPipeline]]:
    """One opened all-16-attribute pipeline per distance, on the real GRR."""
    pipelines = {}
    for distance in DISTANCES:
        pipeline = load_pipeline_from_yaml(
            make_pipeline_yaml(distance, REAL_GENOME, REAL_GENE_MODELS),
            real_grr)
        pipelines[distance] = pipeline.open()
    yield pipelines
    for pipeline in pipelines.values():
        pipeline.close()


@pytest.mark.parametrize(
    "variant", REBASED, ids=[v["id"] for v in REBASED])
def test_derebased_matches_frozen_baseline(
    variant: dict[str, Any],
    real_pipelines: dict[int, AnnotationPipeline],
) -> None:
    """Real-GRR annotation at the de-rebased locus == the frozen baseline."""
    pipeline = real_pipelines[variant["distance"]]
    result = pipeline.annotate(allele(variant))
    assert_matches_baseline(variant, result, BASELINE)


@pytest.mark.parametrize("distance", DISTANCES)
def test_real_batch_equals_sequential(
    distance: int,
    real_pipelines: dict[int, AnnotationPipeline],
) -> None:
    """``batch_annotate`` == ``annotate`` across the corpus on the real GRR."""
    pipeline = real_pipelines[distance]
    variants = _BY_DISTANCE[distance]
    alleles = [allele(v) for v in variants]

    seq_results = [pipeline.annotate(a) for a in alleles]
    batch_results = pipeline.batch_annotate(alleles)
    assert len(batch_results) == len(variants)

    for variant, seq_result, batch_result in zip(
            variants, seq_results, batch_results, strict=True):
        assert_batch_equals_sequential(variant, seq_result, batch_result)


@pytest.mark.parametrize(
    "variant", EXTRA_VARIANTS, ids=[v["id"] for v in EXTRA_VARIANTS])
def test_extra_loci_value_pinned(
    variant: dict[str, Any],
    real_pipelines: dict[int, AnnotationPipeline],
) -> None:
    """Real-GRR annotation at the extra loci == the committed TF baseline."""
    pipeline = real_pipelines[variant["distance"]]
    result = pipeline.annotate(allele(variant))
    assert_matches_baseline(variant, result, BASELINE_EXTRA)


@pytest.mark.parametrize("distance", EXTRA_DISTANCES)
def test_extra_batch_equals_sequential(
    distance: int,
    real_pipelines: dict[int, AnnotationPipeline],
) -> None:
    """``batch_annotate`` == ``annotate`` across the extra loci."""
    pipeline = real_pipelines[distance]
    variants = _EXTRA_BY_DISTANCE[distance]
    alleles = [allele(v) for v in variants]

    seq_results = [pipeline.annotate(a) for a in alleles]
    batch_results = pipeline.batch_annotate(alleles)
    assert len(batch_results) == len(variants)

    for variant, seq_result, batch_result in zip(
            variants, seq_results, batch_results, strict=True):
        assert_batch_equals_sequential(variant, seq_result, batch_result)
