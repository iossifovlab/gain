# pylint: disable=W0621,C0114,C0116,W0212,W0613

import pathlib

from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.genomic_resources.repository import GenomicResourceRepo


def test_effect_annotator(
    t4c8_grr: GenomicResourceRepo,
    tmp_path: pathlib.Path,
) -> None:
    pipeline = load_pipeline_from_yaml(
        """- effect_annotator:
            gene_models: t4c8_genes
            genome: t4c8_genome
        """, t4c8_grr,
        work_dir=tmp_path,
    )
    assert pipeline
