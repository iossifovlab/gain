"""Regenerate the committed #321 extra-corpus baseline (TensorFlow, real GRR).

Loads the real-coordinate ``extra_manifest.json``, annotates every variant at
its distance through the **node-local real GRR** (real hg38 + GENCODE) --
pinning all 16 attributes -- and writes the gzipped
``tests/corpus/baseline_extra.json.gz``.

IMPORTANT (same footgun as ``regenerate_baseline.py``, issues gain#320/#299):
this is the TensorFlow ground truth the ONNX migration is checked against.  It
MUST be regenerated only through this TensorFlow path -- regenerating from ONNX
would silently redefine "correct" as "whatever ONNX now does".  Requires the
worktree TF venv AND the real GRR (``GRR_ROOT`` env var, default
``/data/cephfs/seqpipe/grr``).

Run:  ``python -m tests.corpus.regenerate_baseline_extra``  (from
spliceai_annotator).
"""
from __future__ import annotations

import gzip
import json
import os
import pathlib
from collections import defaultdict
from typing import Any

from gain.annotation.annotatable import VCFAllele
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)

from tests.corpus.pipeline_config import make_pipeline_yaml

# The grr-sync node-local tree on the integration agents (see
# build_extra_corpus.py): grr_sync_target_root=/data/grr, `grr` repo at
# /data/grr/grr.  Override with GRR_ROOT.
GRR_ROOT = os.environ.get("GRR_ROOT", "/data/grr/grr")
REAL_GENOME = "hg38/genomes/GRCh38.p14"
REAL_GENE_MODELS = "hg38/gene_models/GENCODE/49/comprehensive/CHR"

HERE = pathlib.Path(__file__).parent
EXTRA_MANIFEST_PATH = HERE / "extra_manifest.json"
BASELINE_EXTRA_PATH = HERE / "baseline_extra.json.gz"


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, str):
        return value
    return float(value)


def annotate_extra(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Annotate every extra variant through the real GRR -> {id: result}."""
    grr = build_genomic_resource_repository(
        {"id": "real", "type": "directory", "directory": GRR_ROOT})

    by_distance: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for variant in manifest["variants"]:
        by_distance[variant["distance"]].append(variant)

    results: dict[str, dict[str, Any]] = {}
    for distance in sorted(by_distance):
        pipeline = load_pipeline_from_yaml(
            make_pipeline_yaml(distance, REAL_GENOME, REAL_GENE_MODELS), grr)
        with pipeline.open() as opened:
            for variant in by_distance[distance]:
                annotatable = VCFAllele(
                    variant["contig"], variant["pos"],
                    variant["ref"], variant["alt"])
                result = opened.annotate(annotatable)
                results[variant["id"]] = {
                    key: _jsonable(val) for key, val in result.items()
                }
    return results


def main() -> None:
    manifest = json.loads(EXTRA_MANIFEST_PATH.read_text())
    results = annotate_extra(manifest)
    payload = json.dumps(results, sort_keys=True).encode()
    with gzip.open(BASELINE_EXTRA_PATH, "wb") as out:
        out.write(payload)
    print(f"extra baseline: {len(results)} variants -> {BASELINE_EXTRA_PATH}")


if __name__ == "__main__":
    main()
