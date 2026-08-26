"""The gene score memo must tell two caller built resources apart."""
import pathlib
from typing import Any

from gain.gene_scores.gene_scores import build_gene_score_from_resource
from gain.genomic_resources.fsspec_protocol import build_local_resource

ALPHA_TSV = "gene\tscore\nTP53\t1.0\nCHD8\t0.5\n"
BETA_TSV = "gene\tscore\nBRCA1\t2.0\n"


def gene_score_config(filename: str) -> dict[str, Any]:
    """Return the config a gene score resource carries."""
    return {
        "type": "gene_score",
        "filename": filename,
        "scores": [{
            "id": "score",
            "type": "float",
            "desc": "test score",
        }],
    }


def test_two_caller_built_resources_keep_their_own_gene_scores(
    tmp_path: pathlib.Path,
) -> None:
    """A caller built root resource is identified by the file it describes.

    ``build_local_resource`` roots the resource at the repository root, so
    two resources over one directory share an id and a repository url and
    differ only in their config.  Keying the memo on identity alone makes
    them collide -- the second caller is served the first caller's table
    (#912).

    The process wide memo needs no clearing here: it is keyed by repository
    url among other things, and ``tmp_path`` is unique to this test, so no
    other test can have populated the entries this one reads.
    """
    (tmp_path / "alpha.tsv").write_text(ALPHA_TSV)
    (tmp_path / "beta.tsv").write_text(BETA_TSV)

    alpha_resource = build_local_resource(
        str(tmp_path), gene_score_config("alpha.tsv"))
    beta_resource = build_local_resource(
        str(tmp_path), gene_score_config("beta.tsv"))

    alpha = build_gene_score_from_resource(alpha_resource)
    beta = build_gene_score_from_resource(beta_resource)

    assert alpha.get_genes("score") == {"TP53", "CHD8"}
    assert beta.get_genes("score") == {"BRCA1"}


def test_one_resource_is_still_memoised(tmp_path: pathlib.Path) -> None:
    """Telling two resources apart must not stop reuse of either."""
    (tmp_path / "alpha.tsv").write_text(ALPHA_TSV)
    resource = build_local_resource(
        str(tmp_path), gene_score_config("alpha.tsv"))

    first = build_gene_score_from_resource(resource)
    second = build_gene_score_from_resource(resource)

    assert first is second
