# pylint: disable=W0621,C0114,C0115,C0116,W0212,W0613
import pathlib
import textwrap
from html.parser import HTMLParser

import pytest
from gain.genomic_resources.genomic_scores import (
    build_score_from_resource,
)
from gain.genomic_resources.implementations.annotation_pipeline_impl import (
    AnnotationPipelineImplementation,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    setup_directories,
)


@pytest.fixture
def grr_fixture(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    root_path = tmp_path / "grr"
    setup_directories(root_path, {
        "one": {
            "genomic_resource.yaml": """
                type: position_score
                table:
                    filename: data.txt
                scores:
                - id: score
                  type: float
                  desc: |
                      A score description testtest
                  name: s1
            """,
        },
        "pipeline": {
            "genomic_resource.yaml": """
                type: annotation_pipeline
                filename: annotation.yaml
            """,
            "annotation.yaml": """
                - position_score: one
            """,
        },
    })
    return build_filesystem_test_repository(root_path)


@pytest.fixture
def alt_grr_fixture(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    root_path = tmp_path / "alt_grr"
    setup_directories(root_path, {
        "other_score": {
            "genomic_resource.yaml": """
                type: position_score
                table:
                    filename: data.txt
                scores:
                - id: score
                  type: float
                  name: s1
            """,
        },
    })
    return build_filesystem_test_repository(root_path)


def test_pipeline_impl_init(grr_fixture: GenomicResourceRepo) -> None:
    assert AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"))


def test_pipeline_impl_info(grr_fixture: GenomicResourceRepo) -> None:
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"))
    info = impl.get_info(repo=grr_fixture)
    assert info
    assert "position_score" in info
    assert "one" in info
    assert "A score description testtest" in info


def test_pipeline_doc_resource_url(
    grr_fixture: GenomicResourceRepo,
    alt_grr_fixture: GenomicResourceRepo,
) -> None:
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"))

    res = grr_fixture.get_resource("one")
    other_res = alt_grr_fixture.get_resource("other_score")

    assert impl._make_resource_url(res) == "../one"
    assert impl._make_resource_url(other_res) == other_res.get_url()


def test_pipeline_doc_histogram_url(
    grr_fixture: GenomicResourceRepo,
    alt_grr_fixture: GenomicResourceRepo,
) -> None:
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"))

    score = build_score_from_resource(
        grr_fixture.get_resource("one"))
    other_score = build_score_from_resource(
        alt_grr_fixture.get_resource("other_score"))

    assert impl._make_histogram_url(score, "s1") \
        == "../one/statistics/histogram_s1.png"
    assert impl._make_histogram_url(other_score, "s1") == \
        other_score.get_histogram_image_url("s1")


class _PageDom(HTMLParser):
    """Read a page the way a browser's tokenizer does.

    Script content is kept apart from the rest of the text: the page's
    own base template ships script tags, so what tells an injection from
    the page's own markup is whether the payload token ended up inside
    one (the lesson of #558).
    """

    def __init__(self) -> None:
        super().__init__()
        self.attributes: list[tuple[str, str | None]] = []
        self.script_data: list[str] = []
        self.text: list[str] = []
        self._open_scripts = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        self.attributes.extend(attrs)
        if tag == "script":
            self._open_scripts += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._open_scripts:
            self._open_scripts -= 1

    def handle_data(self, data: str) -> None:
        if self._open_scripts:
            self.script_data.append(data)
        else:
            self.text.append(data)


def _parse_page(page: str) -> _PageDom:
    dom = _PageDom()
    dom.feed(page)
    return dom


@pytest.fixture
def injected_grr_fixture(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """A GRR whose score desc carries HTML, as a remote GRR's may.

    The page an ``annotation_pipeline`` resource renders is the
    ``index.html`` ``grr_manage`` writes and the GRR site serves, so a
    score description reaching it as markup is stored XSS against every
    visitor of that resource page.
    """
    root_path = tmp_path / "injected_grr"
    setup_directories(root_path, {
        "one": {
            "genomic_resource.yaml": textwrap.dedent("""
                type: position_score
                table:
                    filename: data.txt
                scores:
                - id: score
                  type: float
                  desc: 'DESC<script>gainxss623impl()</script>'
                  name: s1
            """),
        },
        "pipeline": {
            "genomic_resource.yaml": textwrap.dedent("""
                type: annotation_pipeline
                filename: annotation.yaml
            """),
            "annotation.yaml": textwrap.dedent("""
                - position_score: one
            """),
        },
    })
    return build_filesystem_test_repository(root_path)


def test_pipeline_impl_info_escapes_a_script_in_a_score_desc(
    injected_grr_fixture: GenomicResourceRepo,
) -> None:
    """A script tag in a score desc is text on the pipeline's info page."""
    impl = AnnotationPipelineImplementation(
        injected_grr_fixture.get_resource("pipeline"))

    info = impl.get_info(repo=injected_grr_fixture)

    dom = _parse_page(info)
    assert "gainxss623impl" not in "".join(dom.script_data)
    assert "gainxss623impl" in "".join(dom.text)
