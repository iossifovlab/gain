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

# Tolerance for the raw DS_* floats: ~40x above the observed TF->ONNX
# perturbation (2.4e-7), far below the 2dp delta_score step.  These are
# compared at full precision, so they stay portable across fp environments.
FLOAT_TOL = 1e-5
# Tolerance for the probability vectors.  They are emitted as 4-decimal strings
# (``f"{p:.4f}"``), so their resolution is 1e-4: a raw value near a rounding
# boundary rounds to *adjacent* 4dp values across fp environments (the baseline
# host vs CI, or batch fp non-associativity vs sequential) -- a 1e-4 jump.  The
# tolerance must absorb a one-unit 4dp flip; comparing 4dp values any tighter
# than their own grid is not portable.
PROB_TOL = 2e-4
# Tolerance for the 2dp ``DS`` fields embedded in ``delta_score`` -- one 2dp
# unit, for the same rounding-boundary reason (the raw DS_* are pinned
# separately at FLOAT_TOL, so this only prevents spurious 2dp flips).
DELTA_DS_TOL = 2e-2
# Gate DP_*/delta_score position assertions: below this DS_MAX the argmax sits
# inside the noise floor and can flip.
DS_MAX_GATE = 0.01


def make_pipeline_yaml(
    distance: int,
    genome: str = GENOME_RESOURCE,
    gene_models: str = GENE_MODELS_RESOURCE,
) -> str:
    """Return the annotation-pipeline YAML pinning all 16 attributes.

    ``genome``/``gene_models`` default to the committed fixture GRR resource
    ids; the node-local-real-GRR tier (#321) passes the real ids
    (``hg38/genomes/GRCh38.p14`` / ``hg38/gene_models/GENCODE/49/comprehensive/
    CHR``) so the same pipeline shape runs against either GRR.
    """
    attrs = "\n".join(f"        - source: {a}" for a in ATTRIBUTES)
    return textwrap.dedent(f"""
    - spliceai_annotator:
        genome: {genome}
        gene_models: {gene_models}
        distance: {distance}
        attributes:
""") + attrs + "\n"
