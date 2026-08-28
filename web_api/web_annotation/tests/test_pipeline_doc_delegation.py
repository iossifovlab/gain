# pylint: disable=C0116
"""The download endpoint serves the shared pipeline document, unaltered.

Since #952 there is one renderer of the pipeline documentation page, in
``gain``, and this endpoint is one of its three callers. What is pinned
here is the part that is this project's own: that the download goes
through that renderer, and carries the default public-mirror addresses.

The document's *content* is pinned in ``core``, next to the renderer --
the address policy in ``tests/small/annotation/test_pipeline_doc.py`` and
the markdown rescue in ``test_annotate_doc_bogus_tag_rescue.py``. This
file deliberately does not restate them.

That split replaces a mirror of both suites that used to live here. The
mirror existed because this endpoint was a *separate renderer* that could
drift -- and did: it rendered through raw ``markdown2`` after the other
sinks moved to the shared wrapper (#742), and it addressed resources on
the repository's own url for two months after the CLI was corrected
(#841). It is no longer a separate renderer, and
``test_the_download_is_exactly_the_shared_document`` is what keeps it from
becoming one again: if the view ever re-inlines a render, that test goes
red without needing a copy of the content assertions to go red with it.
"""
from __future__ import annotations

import pathlib

from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.pipeline_doc import render_pipeline_doc
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.genomic_resources.testing.builders import a_position_score

from web_annotation.pipeline_cache import ThreadSafePipeline
from web_annotation.pipelines.views import PipelineDoc

PIPELINE = "- position_score: scores/pos1\n"

#: Prose whose "<thresh" a browser reads as the start of a tag. Carried
#: through a real annotator's documentation so the document under test is
#: one with content worth comparing, not an empty shell.
DESC = "values <thresh are dropped -- gainrescue952 -- and the rest kept"


def a_pipeline(
    tmp_path: pathlib.Path, public_url: str | None = "http://grr.example.org",
) -> ThreadSafePipeline:
    """Realize a one-score GRR and load the pipeline that annotates with it."""
    grr_dir = tmp_path / "grr"
    (a_position_score()
     .with_score("score", "float", column_name="s1", desc=DESC)
     .with_data("chrom  pos_begin  s1\nchr1   4          0.01\n")
     .realize_into(grr_dir / "scores" / "pos1"))

    definition: dict[str, object] = {
        "id": "main", "type": "dir", "directory": str(grr_dir),
    }
    if public_url is not None:
        definition["public_url"] = public_url
    repo = build_genomic_resource_repository(definition)

    pipeline = load_pipeline_from_yaml(
        PIPELINE, repo, work_dir=tmp_path / "work")
    return ThreadSafePipeline(pipeline, "test-pipeline")


def test_the_download_is_exactly_the_shared_document(
    tmp_path: pathlib.Path,
) -> None:
    pipeline = a_pipeline(tmp_path)

    assert PipelineDoc._render_doc(pipeline) == render_pipeline_doc(pipeline)


def test_the_download_addresses_resources_on_the_grrs_public_url(
    tmp_path: pathlib.Path,
) -> None:
    # The document is opened in the user's own browser, so a container-local
    # repository path resolves nowhere once it leaves the server (#841).
    pipeline = a_pipeline(tmp_path)

    html = PipelineDoc._render_doc(pipeline)

    assert 'href="http://grr.example.org/scores/pos1/index.html"' in html
    assert (
        'src="http://grr.example.org'
        '/scores/pos1/statistics/histogram_score.png"'
    ) in html


def test_the_download_names_no_pipeline_file(
    tmp_path: pathlib.Path,
) -> None:
    # The endpoint renders a stored pipeline, which has no path on disk to
    # show the reader; only the CLI has one.
    pipeline = a_pipeline(tmp_path)

    assert "Pipeline path:" not in PipelineDoc._render_doc(pipeline)
