# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib
import textwrap

import pytest
from gain.annotation.annotate_doc import cli
from gain.genomic_resources.testing import (
    setup_denovo,
    setup_directories,
)

pytestmark = pytest.mark.usefixtures("clean_genomic_context")


@pytest.fixture
def annotate_doc_root(tmp_path: pathlib.Path) -> pathlib.Path:
    root_path = tmp_path
    setup_directories(
        root_path,
        {
            "grr.yaml": textwrap.dedent(f"""
                id: t4c8_local
                type: directory
                directory: {root_path!s}
            """),
            "pipeline_config.yaml": textwrap.dedent("""
                preamble:
                    input_reference_genome: acgt
                    summary: asdf summary
                    description: sample description
                    metadata:
                        a: b
                annotators:
                    - position_score: one
            """),
            "one": {
                "genomic_resource.yaml": textwrap.dedent("""
                        type: position_score
                        table:
                            filename: data.txt
                        scores:
                        - id: score_one
                          type: float
                          name: score
                """),
            },
            "acgt": {
                "genomic_resource.yaml": textwrap.dedent("""
                    type: reference_genome
                    filename: genome.fa
                """),
                "genome.fa": """blabla""",
            },
        },
    )
    one_content = textwrap.dedent("""
        chrom  pos_begin  score
        chr1   4          0.01
    """)
    setup_denovo(root_path / "one" / "data.txt", one_content)
    return root_path


def test_annotate_doc(
    tmp_path: pathlib.Path,
    annotate_doc_root: pathlib.Path,
) -> None:
    root_path = annotate_doc_root
    pipeline_config = str(root_path / "pipeline_config.yaml")
    output_file = tmp_path / "output.html"

    cli([
        pipeline_config,
        "-o", str(output_file),
        "-g", str(root_path / "grr.yaml"),
    ])

    output_template = pathlib.Path(output_file).read_text()

    assert f"""src=\"file://{tmp_path}/one/statistics/histogram_score_one.png\"""" in output_template  # noqa: E501
    assert "<strong>aggregator</strong>" in output_template
    assert f"""href=\"file://{tmp_path}/one/index.html\"""" in output_template
    assert 'href="https://iossifovlab.com/gaindocs/annotation_infrastructure.html#position-score-annotator"' in output_template  # noqa: E501
    assert "Annotator to use with genomic scores depending on genomic position" in output_template  # noqa: E501

    assert "preamble" in output_template
    assert "acgt" in output_template
    assert "asdf summary" in output_template
    assert "sample description" in output_template
    assert "<th>Input reference genome</th>" in output_template
    assert f'<a href="file://{tmp_path}/acgt/index.html">' in output_template


def test_pipeline_without_a_reference_genome_omits_the_row(
    tmp_path: pathlib.Path,
    annotate_doc_root: pathlib.Path,
) -> None:
    """``input_reference_genome`` is optional, so its absence must render.

    The row used to be guarded on ``res_url(...input_reference_genome_res)``
    -- the address callable's own result -- and both address policies
    dereference their argument unconditionally, so evaluating the guard
    itself raised (#1021).  This tool renders with the default
    ``public_resource_url``, so here it was ``AttributeError: 'NoneType'
    object has no attribute 'get_public_url'``; the ``grr_manage`` path
    reaches the same template through the repository-relative policy and
    named ``get_url`` instead.
    """
    config_path = annotate_doc_root / "genome_less_pipeline_config.yaml"
    config_path.write_text(textwrap.dedent("""
        preamble:
            summary: asdf summary
            description: sample description
        annotators:
            - position_score: one
    """))
    output_file = tmp_path / "output.html"

    cli([
        str(config_path),
        "-o", str(output_file),
        "-g", str(annotate_doc_root / "grr.yaml"),
    ])

    output_template = pathlib.Path(output_file).read_text()

    assert "<th>Input reference genome</th>" not in output_template
    # the rest of the page is whole, not truncated at the missing row
    assert "asdf summary" in output_template
    assert "sample description" in output_template
    assert "<strong>aggregator</strong>" in output_template
    assert f'href="file://{tmp_path}/one/index.html"' in output_template


def test_running_without_a_pipeline_says_so(
    tmp_path: pathlib.Path,
    annotate_doc_root: pathlib.Path,
) -> None:
    """The pipeline argument is optional, so this is a reachable mistake.

    It used to reach the template as ``None`` and come back as
    ``UndefinedError: 'None' has no attribute 'preamble'`` -- a Jinja
    traceback naming an attribute the user never heard of.  #952 routed the
    tool through the same guard its sibling annotate tools use.
    """
    with pytest.raises(ValueError, match="no valid annotation pipeline"):
        cli([
            "-o", str(tmp_path / "output.html"),
            "-g", str(annotate_doc_root / "grr.yaml"),
        ])
