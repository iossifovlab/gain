"""The statistics build of a resource in one task, contig by contig."""
from __future__ import annotations

from gain.genomic_resources.genomic_scores import (
    build_score_from_resource,
)
from gain.genomic_resources.repository import (
    GenomicResource,
)

from .scan import (
    unpack_score_defs,
)
from .scan_histograms import do_histogram_task, merge_and_save_histograms
from .scan_min_max import do_min_max_task, merge_min_max

__all__ = [
    "do_noregion_histograms",
]


def do_noregion_histograms(
    resource: GenomicResource,
) -> None:
    """Compute a resource's statistics in one task, contig by contig.

    The ``--region-size 0`` path: one task, no splitting *within* a
    contig.  The region-read family requires a contig, so the iteration
    over contigs is stated here rather than smuggled down as a null.

    Each contig is scanned through the very task functions a split run
    calls and folded together by the very functions that fold those
    tasks' results, so this produces what a split run produces -- not
    something merely believed to match, and a resource either of them
    refuses is attributed there rather than reaching the reader as a
    reason-less "statistics were not built".  An empty contig (an
    unscored alt, of which hg38 has hundreds) contributes an empty
    histogram, which merges cleanly; ``merge_histograms`` only
    nullifies on a genuine error.
    """
    all_min_max_scores, all_hist_confs = unpack_score_defs(resource)

    with build_score_from_resource(resource).open() as score:
        chroms = list(score.get_all_chromosomes())

    if all_min_max_scores:
        all_hist_confs = merge_min_max(
            all_min_max_scores,
            all_hist_confs,
            *(do_min_max_task(
                resource, all_min_max_scores, chrom, None, None)
              for chrom in chroms),
        )
    merge_and_save_histograms(
        resource,
        *(do_histogram_task(
            resource, all_hist_confs, chrom, None, None)
          for chrom in chroms),
    )
