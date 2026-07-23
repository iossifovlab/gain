"""Single source of truth for the differential corpus pipeline.

Both the committed-baseline generator and the differential test must annotate
with the *same* pipeline, so the pipeline YAML (and the full 16-attribute list)
live here and nowhere else.
"""
from __future__ import annotations

import textwrap

# All 16 sources the annotator can emit.  The DEFAULT pipeline only emits
# gene/transcript_ids/DS_*/DS_MAX, so every source is listed explicitly --
# otherwise the DP_*, the four probability vectors and delta_score are never
# pinned.
ATTRIBUTES = [
    "gene", "transcript_ids",
    "DS_AG", "DS_AL", "DS_DG", "DS_DL", "DS_MAX",
    "DP_AG", "DP_AL", "DP_DG", "DP_DL",
    "ref_A_p", "ref_D_p", "alt_A_p", "alt_D_p",
    "delta_score",
]

GENOME_RESOURCE = "hg38/genome"
GENE_MODELS_RESOURCE = "hg38/gene_models"

# The probability strings and delta_score DP_* integers are exact batch-vs-
# sequential; the raw DS_* floats differ by fp non-associativity only.
DS_SOURCES = ("DS_AG", "DS_AL", "DS_DG", "DS_DL", "DS_MAX")
PROB_SOURCES = ("ref_A_p", "ref_D_p", "alt_A_p", "alt_D_p")
DP_SOURCES = ("DP_AG", "DP_AL", "DP_DG", "DP_DL")

# Tolerance for the raw floats (DS_* and the parsed probability vectors):
# ~40x above the observed TF->ONNX perturbation, 1000x below the 2dp step.
FLOAT_TOL = 1e-5
# Gate DP_*/delta_score assertions: below this DS_MAX the argmax position sits
# inside the noise floor and can flip.
DS_MAX_GATE = 0.01


def make_pipeline_yaml(distance: int) -> str:
    """Return the annotation-pipeline YAML pinning all 16 attributes."""
    attrs = "\n".join(f"        - source: {a}" for a in ATTRIBUTES)
    return textwrap.dedent(f"""
    - spliceai_annotator:
        genome: {GENOME_RESOURCE}
        gene_models: {GENE_MODELS_RESOURCE}
        distance: {distance}
        attributes:
""") + attrs + "\n"
