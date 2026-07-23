"""Regenerate the committed SpliceAI differential baseline (TensorFlow).

Loads the *committed* fixture GRR, annotates every corpus variant at its
intended distance through ``pipeline.annotate`` -- pinning all 16 attributes --
and writes the gzipped ``tests/corpus/baseline.json.gz``.

IMPORTANT (issue iossifovlab/gain#320, and #299 which removes TensorFlow):
the baseline is the TensorFlow ground truth the ONNX migration is checked
against.  It MUST be regenerated only through this TensorFlow path.  After #299
removes TensorFlow, regenerating from ONNX would silently redefine "correct" as
"whatever ONNX now does" and discard the ground truth this harness protects.
Requires the worktree TF venv.

Run:  ``python -m tests.corpus.regenerate_baseline``  (from spliceai_annotator).
"""
from __future__ import annotations

import gzip
import json
import pathlib
from collections import defaultdict
from typing import Any

from gain.annotation.annotatable import VCFAllele
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.genomic_resources.testing import build_filesystem_test_repository

from tests.corpus.pipeline_config import make_pipeline_yaml

HERE = pathlib.Path(__file__).parent
FIXTURES = (HERE.parent / "fixtures").resolve()
MANIFEST_PATH = HERE / "corpus_manifest.json"
BASELINE_PATH = HERE / "baseline.json.gz"


def _jsonable(value: Any) -> Any:
    """Make one annotated attribute JSON-serializable.

    ``None`` (rejected) and the ``;``-joined strings pass through; the raw
    ``DS_*`` numpy floats become plain floats.
    """
    if value is None or isinstance(value, str):
        return value
    return float(value)


def annotate_corpus(
    manifest: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Annotate every corpus variant at its distance; return {id: result}."""
    grr = build_filesystem_test_repository(FIXTURES)

    by_distance: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for variant in manifest["variants"]:
        by_distance[variant["distance"]].append(variant)

    results: dict[str, dict[str, Any]] = {}
    for distance in sorted(by_distance):
        pipeline = load_pipeline_from_yaml(
            make_pipeline_yaml(distance), grr)
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
    """Regenerate and write the committed baseline."""
    manifest = json.loads(MANIFEST_PATH.read_text())
    results = annotate_corpus(manifest)
    payload = json.dumps(results, sort_keys=True).encode()
    with gzip.open(BASELINE_PATH, "wb") as out:
        out.write(payload)
    print(f"baseline: {len(results)} variants -> {BASELINE_PATH}")


if __name__ == "__main__":
    main()
