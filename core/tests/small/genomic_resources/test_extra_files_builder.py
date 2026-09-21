# pylint: disable=C0116
"""``with_file`` on the score builders: an extra payload beside the data.

A stand-in for a file the statistics build would have written, for a test
about that file's presence rather than its content.  It may add to what
a builder realizes; it may not replace it -- the builder's own files are
described by its config, and a shadowed one would leave the two disagreeing
in silence.  The knob's own contract (replace by name, refuse the config)
is pinned on the ``basic`` builder, whose payload it is.
"""
import pathlib

import pytest
from gain.genomic_resources.testing.builders import (
    BigWigScoreBuilder,
    GeneScoreBuilder,
    PositionScoreBuilder,
    ResourceValidationError,
    VcfInfoScoreBuilder,
    a_bigwig_score,
    a_gene_score,
    a_position_score,
    a_vcf_info_score,
)

ScoreBuilder = (
    PositionScoreBuilder | GeneScoreBuilder
    | BigWigScoreBuilder | VcfInfoScoreBuilder
)


def test_an_extra_file_lands_in_the_resource_and_its_manifest(
    tmp_path: pathlib.Path,
) -> None:
    res = (
        a_position_score()
        .with_file("statistics/histogram_score.png", "drawn")
        .build_resource(tmp_path)
    )

    assert res.get_file_content("statistics/histogram_score.png") == "drawn"
    assert "statistics/histogram_score.png" in res.get_manifest()


@pytest.mark.parametrize("builder, filename", [
    pytest.param(a_position_score(), "data.txt", id="table-data"),
    pytest.param(a_position_score().with_tabix(), "data.txt.gz",
                 id="tabix-data"),
    pytest.param(a_position_score().with_tabix(), "data.txt.gz.tbi",
                 id="tabix-index"),
    pytest.param(a_gene_score(), "data.tsv", id="gene-data"),
    pytest.param(a_bigwig_score(), "data.bw", id="bigwig-data"),
    pytest.param(a_vcf_info_score(), "data.vcf.gz", id="vcf-data"),
])
def test_a_file_the_builder_renders_itself_is_refused(
    builder: ScoreBuilder, filename: str, tmp_path: pathlib.Path,
) -> None:
    # Refused when realized, not when declared: which names a builder
    # renders is settled only once its other knobs are.
    shadowing = builder.with_file(filename, "garbage")

    with pytest.raises(ResourceValidationError, match=filename):
        shadowing.build_resource(tmp_path)
