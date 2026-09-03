"""One value rendered as annotation output renders it.

Here rather than in :mod:`gain.annotation.annotate_utils`, where it grew
up and is still re-exported from, because the allele score builds its
allele keys with it (:func:`~gain.genomic_resources.genomic_scores.allele
.allele_key`) and a score module cannot import the annotation package
without a cycle -- ``annotate_utils`` pulls in the pipeline factory, which
pulls in the annotators, which pull in the scores.
"""

import urllib.parse
from typing import Any

import numpy as np


def stringify(value: Any, *, vcf: bool = False) -> str:
    """Format the value to a string for human-readable output."""
    if value is None:
        return "." if vcf else ""
    if isinstance(value, (float, np.floating)):
        if 100 <= value < 100_000:
            return f"{value:.6g}"
        return f"{value:.3g}"
    if isinstance(value, bool):
        return "yes" if value else ("." if vcf else "")
    if vcf is True and value == "":
        return "."
    if isinstance(value, (list, tuple)):
        s = str(list(value))
        return urllib.parse.quote(s, safe="") if vcf else s
    if isinstance(value, dict):
        if vcf:
            return urllib.parse.quote(str(value), safe="")
        return ";".join(
            f"{stringify(k, vcf=vcf)}:{stringify(v, vcf=vcf)}"
            for k, v in value.items()
        )
    return str(value)
