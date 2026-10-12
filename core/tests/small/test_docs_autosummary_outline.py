"""Check that docs autosummary outlines match the public members of classes.

A docs page lists the members of a class by hand in an ``autosummary``
block. These tests compare that list with the class, so a new public member
cannot stay out of the outline without a failure. The page source is read as
text. No Sphinx build runs.
"""
import inspect
import re
from collections.abc import Sequence
from pathlib import Path

import pytest
from gain.genomic_resources.gene_models import (
    Exon,
    GeneModels,
    TranscriptModel,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
GENE_MODELS_PAGE = (
    REPO_ROOT / "docs/source/development/resources/gene_models.rst"
)

_DIRECTIVE = re.compile(r"^(\s*)\.\. autosummary::")


def autosummary_entries(page: str) -> list[str]:
    """Return the names listed in all autosummary blocks of a page."""
    entries: list[str] = []
    lines = page.splitlines()
    index = 0
    while index < len(lines):
        match = _DIRECTIVE.match(lines[index])
        index += 1
        if match is None:
            continue
        base = len(match.group(1))
        while index < len(lines):
            line = lines[index]
            stripped = line.strip()
            indent = len(line) - len(line.lstrip())
            if stripped and indent <= base:
                break
            index += 1
            if stripped and not stripped.startswith(":"):
                entries.append(stripped)
    return entries


def _documented_attributes(cls: type) -> set[str]:
    """Return the names in the Attributes section of a class docstring."""
    doc = inspect.getdoc(cls) or ""
    _, _, section = doc.partition("Attributes:\n")
    return set(re.findall(r"^[ ]{4}(\w+) \(", section, re.MULTILINE))


def public_members(cls: type) -> set[str]:
    """Return the public names that a class defines itself.

    The class docstring describes the names in its Attributes section.
    The outline does not list them.
    """
    return {
        name for name in vars(cls)
        if not name.startswith("_")
    } - _documented_attributes(cls)


def read_docs_page(path: Path) -> str:
    """Return the source of a docs page, or fail when it is missing.

    A missing page must not skip the test: the CI image copies the docs
    sources (core/Dockerfile), so a skip would hide a broken image.
    """
    if not path.exists():
        pytest.fail(
            f"docs page {path} is missing: copy docs/source into the "
            "test image (core/Dockerfile)")
    return path.read_text(encoding="utf-8")


def outline_problems(page: str, classes: Sequence[type]) -> list[str]:
    """Return the differences between a page outline and the classes."""
    entries = autosummary_entries(page)
    problems: list[str] = []
    for cls in classes:
        prefix = f"{cls.__name__}."
        listed = {e[len(prefix):] for e in entries if e.startswith(prefix)}
        actual = public_members(cls)
        problems.extend(
            f"{cls.__name__}.{name} is public but missing from the outline"
            for name in sorted(actual - listed))
        problems.extend(
            f"{cls.__name__}.{name} is listed in the outline "
            f"but {cls.__name__} has no such member"
            for name in sorted(listed - actual))
    return problems


CLASSES = [GeneModels, TranscriptModel, Exon]


def _page_for(classes: Sequence[type]) -> str:
    """Build an in-memory page that lists every public member."""
    blocks = []
    for cls in classes:
        names = sorted(public_members(cls))
        body = "\n".join(f"   {cls.__name__}.{n}" for n in names)
        blocks.append(f".. autosummary::\n   :nosignatures:\n\n{body}\n")
    return "\n".join(blocks)


def _page_without(name: str) -> str:
    page = _page_for(CLASSES)
    lines = page.splitlines()
    kept = [ln for ln in lines if ln.strip() != name]
    assert len(kept) == len(lines) - 1
    return "\n".join(kept)


def test_gene_models_page_outline_matches_classes() -> None:
    """The Gene Models outline lists every public member."""
    page = read_docs_page(GENE_MODELS_PAGE)

    problems = outline_problems(page, CLASSES)

    assert not problems


def test_complete_outline_has_no_problems() -> None:
    """An in-memory page that lists every member passes."""
    assert not outline_problems(_page_for(CLASSES), CLASSES)


def test_removed_member_is_reported_with_class_and_member() -> None:
    """A removed member is reported with its class name."""
    page = _page_without("TranscriptModel.cds_len")

    problems = outline_problems(page, CLASSES)

    assert problems == [
        "TranscriptModel.cds_len is public but missing from the outline",
    ]


def test_unknown_name_is_reported() -> None:
    """A listed name without a member is reported."""
    page = _page_for(CLASSES) + "\n   Exon.no_such_method\n"

    problems = outline_problems(page, CLASSES)

    assert len(problems) == 1
    assert "Exon.no_such_method" in problems[0]


def test_missing_docs_page_fails(tmp_path: Path) -> None:
    """A missing page fails the test run instead of skipping it."""
    with pytest.raises(pytest.fail.Exception, match="is missing"):
        read_docs_page(tmp_path / "absent.rst")
