"""Check that docs autosummary outlines match the public members of classes.

A docs page lists the members of a class by hand in an ``autosummary``
block. These tests compare that list with the class, so a new public member
cannot stay out of the outline without a failure. The test finds each
``autoclass`` directive on the pages under
``docs/source/development/resources``. A page of functions, such as the
Data frames and AnnData page, has no class. The test then finds each
``autofunction`` directive, and the outline must list each function. A page
that is not rolled out yet sits on ``NOT_ROLLED_OUT``. The page source is
read as text. No Sphinx build runs.
"""
import importlib
import inspect
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from gain import grr
from gain.genomic_resources.gene_models import (
    Exon,
    GeneModels,
    TranscriptModel,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
RESOURCES_DIR = REPO_ROOT / "docs/source/development/resources"
GENE_MODELS_PAGE = RESOURCES_DIR / "gene_models.rst"

# Pages whose API section is not rolled out yet. The slice of a page
# removes the page from this set.
NOT_ROLLED_OUT = frozenset({
    "factories.rst",
    "gene_sets.rst",
    "histograms.rst",
    "adding_a_resource_type.rst",
})

_DIRECTIVE = re.compile(r"^(\s*)\.\. autosummary::")
_AUTOCLASS = re.compile(r"^\.\. autoclass:: (\S+)", re.MULTILINE)
_AUTOFUNCTION = re.compile(r"^\.\. autofunction:: (\S+)", re.MULTILINE)
_CURRENT_MODULE = re.compile(r"^\.\. currentmodule:: (\S+)", re.MULTILINE)
_INHERITED = re.compile(r"^\s*Inherited from ``(\w+)``", re.IGNORECASE)
_GRR_NAME = re.compile(r"``grr\.(\w+)``")


def autosummary_blocks(page: str) -> list[tuple[str | None, list[str]]]:
    """Return the autosummary blocks of a page.

    Each block is a pair. The first item is the base class name when the
    line before the block reads "Inherited from ``Base``", else ``None``.
    The second item is the list of entries, without a leading ``~``.
    """
    blocks: list[tuple[str | None, list[str]]] = []
    lines = page.splitlines()
    index = 0
    while index < len(lines):
        match = _DIRECTIVE.match(lines[index])
        index += 1
        if match is None:
            continue
        label: str | None = None
        for previous in reversed(lines[:index - 1]):
            if previous.strip():
                inherited = _INHERITED.match(previous)
                label = inherited.group(1) if inherited else None
                break
        base = len(match.group(1))
        entries: list[str] = []
        while index < len(lines):
            line = lines[index]
            stripped = line.strip()
            indent = len(line) - len(line.lstrip())
            if stripped and indent <= base:
                break
            index += 1
            if stripped and not stripped.startswith(":"):
                entries.append(stripped.lstrip("~"))
        blocks.append((label, entries))
    return blocks


def autosummary_entries(page: str) -> list[str]:
    """Return the names listed in the own-member blocks of a page."""
    return [
        entry
        for label, entries in autosummary_blocks(page) if label is None
        for entry in entries
    ]


def inherited_entries(page: str, base: str) -> list[str]:
    """Return the names listed in the "Inherited from base" blocks."""
    return [
        entry
        for label, entries in autosummary_blocks(page) if label == base
        for entry in entries
    ]


def _documented_attributes(cls: type) -> set[str]:
    """Return the names in the Attributes section of a class docstring."""
    doc = inspect.getdoc(cls) or ""
    _, _, section = doc.partition("Attributes:\n")
    return set(re.findall(r"^[ ]{4}(\w+) \(", section, re.MULTILINE))


def public_members(cls: type) -> set[str]:
    """Return the public names that a class defines itself.

    The class docstring describes the names in its Attributes section.
    The outline does not list them. A member with ``:meta private:`` in
    its docstring is not user API, so the outline does not list it.
    """
    return {
        name for name, value in vars(cls).items()
        if not name.startswith("_")
        and ":meta private:" not in (getattr(value, "__doc__", None) or "")
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
        problems.extend(_inherited_problems(page, cls))
    return problems


def _inherited_problems(page: str, cls: type) -> list[str]:
    """Compare the inherited outline of a subclass with its base class."""
    problems: list[str] = []
    labels = {label for label, _ in autosummary_blocks(page)}
    for base in cls.__mro__[1:]:
        if base.__name__ not in labels:
            continue
        prefix = f"{base.__name__}."
        listed = {
            e[len(prefix):] for e in inherited_entries(page, base.__name__)
            if e.startswith(prefix)}
        expected = public_members(base) - set(vars(cls))
        problems.extend(
            f"{cls.__name__} inherits {base.__name__}.{name} "
            "but the inherited outline does not list it"
            for name in sorted(expected - listed))
        problems.extend(
            f"{cls.__name__} inherited outline lists {base.__name__}.{name} "
            f"but {base.__name__} has no such member"
            for name in sorted(listed - expected))
    return problems


def page_classes(page: str) -> list[type]:
    """Return the classes named by the autoclass directives of a page."""
    classes: list[type] = []
    module = ""
    for line in page.splitlines():
        current = _CURRENT_MODULE.match(line)
        if current:
            module = current.group(1)
        directive = _AUTOCLASS.match(line)
        if directive:
            name = directive.group(1)
            if "." not in name:
                name = f"{module}.{name}"
            module_name, _, class_name = name.rpartition(".")
            classes.append(
                getattr(importlib.import_module(module_name), class_name))
    return classes


def page_functions(page: str) -> list[Callable[..., Any]]:
    """Return the functions named by the autofunction directives of a page."""
    functions: list[Callable[..., Any]] = []
    module = ""
    for line in page.splitlines():
        current = _CURRENT_MODULE.match(line)
        if current:
            module = current.group(1)
        directive = _AUTOFUNCTION.match(line)
        if directive:
            name = directive.group(1)
            if "." not in name:
                name = f"{module}.{name}"
            module_name, _, function_name = name.rpartition(".")
            functions.append(
                getattr(importlib.import_module(module_name), function_name))
    return functions


def function_outline_problems(
    page: str, functions: Sequence[Callable[..., Any]],
) -> list[str]:
    """Return the differences between a page outline and its functions.

    An outline entry without a dot names a function. An entry with a dot
    names a member of a class, and ``outline_problems`` checks it.
    """
    listed = {e for e in autosummary_entries(page) if "." not in e}
    class_names = {cls.__name__ for cls in page_classes(page)}
    actual = {function.__name__ for function in functions}
    problems = [
        f"{name} is documented but missing from the outline"
        for name in sorted(actual - listed)]
    problems.extend(
        f"{name} is listed in the outline but the page does not document it"
        for name in sorted(listed - actual - class_names))
    return problems


def exported_name_problems(
    page: str, classes: Sequence[Callable[..., Any]],
) -> list[str]:
    """Compare the ``grr.<Name>`` names of a page with ``gain.grr``.

    ``classes`` holds the classes and functions that the page documents.
    """
    named = set(_GRR_NAME.findall(page))
    exported = set(grr.__all__)
    problems = [
        f"{cls.__name__} is exported by gain.grr "
        f"but the page does not name ``grr.{cls.__name__}``"
        for cls in classes
        if cls.__name__ in exported
        and getattr(grr, cls.__name__, None) is cls
        and cls.__name__ not in named
    ]
    problems.extend(
        f"``grr.{name}`` is named on the page but gain.grr does not export it"
        for name in sorted(named - exported))
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


def page_problems(page: str) -> list[str]:
    """Return all outline problems of a page with an autoclass or function."""
    classes = page_classes(page)
    if not autosummary_blocks(page):
        return ["the page has an autoclass but no autosummary outline"]
    if classes:
        return outline_problems(page, classes) + exported_name_problems(
            page, classes)
    functions = page_functions(page)
    return function_outline_problems(
        page, functions) + exported_name_problems(page, functions)


def _rolled_out_pages() -> list[Path]:
    return sorted(
        path for path in RESOURCES_DIR.glob("*.rst")
        if path.name not in NOT_ROLLED_OUT
        and (_AUTOCLASS.search(path.read_text(encoding="utf-8"))
             or _AUTOFUNCTION.search(path.read_text(encoding="utf-8"))))


def test_gene_models_page_outline_matches_classes() -> None:
    """The Gene Models outline lists every public member."""
    page = read_docs_page(GENE_MODELS_PAGE)

    assert not page_problems(page)
    assert GENE_MODELS_PAGE.name not in NOT_ROLLED_OUT


def test_every_page_with_an_autoclass_is_rolled_out_or_skipped() -> None:
    """Each page with an autoclass or autofunction passes, or is skipped."""
    read_docs_page(GENE_MODELS_PAGE)
    failures = {
        path.name: problems for path in _rolled_out_pages()
        if (problems := page_problems(path.read_text(encoding="utf-8")))}

    assert not failures


def test_skip_list_names_only_existing_pages_with_an_api() -> None:
    """A stale skip list entry fails, so a finished page leaves the list."""
    read_docs_page(GENE_MODELS_PAGE)
    for name in NOT_ROLLED_OUT:
        page = read_docs_page(RESOURCES_DIR / name)
        assert _AUTOCLASS.search(page) or _AUTOFUNCTION.search(page), (
            f"{name} has no autoclass or autofunction: drop it")


def test_data_frames_page_documents_its_loaders() -> None:
    """The page of functions lists and exports the four loaders."""
    path = RESOURCES_DIR / "data_frames_and_anndata.rst"
    page = read_docs_page(path)

    assert len(page_functions(page)) == 4
    assert not page_problems(page)
    assert path.name not in NOT_ROLLED_OUT


def test_function_missing_from_outline_is_reported() -> None:
    """A documented function that the outline lacks fails."""
    page = (
        ".. currentmodule:: gain.genomic_resources.data_frame_resource\n\n"
        ".. autosummary::\n\n   load_data_frame_from_resource\n\n"
        ".. autofunction:: load_data_frame_from_resource\n"
        ".. autofunction:: load_data_frame_from_resource_id\n")

    problems = function_outline_problems(page, page_functions(page))

    assert problems == [(
        "load_data_frame_from_resource_id is documented "
        "but missing from the outline"
    )]


def test_exported_function_must_be_named() -> None:
    """A function that gain.grr exports must be named as grr.<name>."""
    problems = exported_name_problems(
        "no names here", [grr.load_data_frame_from_resource])

    assert problems == [(
        "load_data_frame_from_resource is exported by gain.grr "
        "but the page does not name ``grr.load_data_frame_from_resource``"
    )]


def test_page_without_outline_is_reported() -> None:
    """A page with an autoclass and no outline fails."""
    page = (
        ".. currentmodule:: gain.genomic_resources.gene_models\n\n"
        ".. autoclass:: Exon\n   :members:\n")

    assert page_problems(page) == [
        "the page has an autoclass but no autosummary outline"]


def test_new_public_member_must_be_in_outline(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """A new public member that the outline lacks fails."""
    page = _page_for([Exon])
    monkeypatch.setattr(Exon, "brand_new", lambda _self: None, raising=False)

    problems = outline_problems(page, [Exon])

    assert problems == [
        "Exon.brand_new is public but missing from the outline"]


def test_meta_private_member_is_not_required(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """A member with ``:meta private:`` is not required in the outline."""
    def hook(_self: Exon) -> None:
        """Hook for the engine.

        :meta private:
        """
    monkeypatch.setattr(Exon, "hook", hook, raising=False)

    assert not outline_problems(_page_for([Exon]), [Exon])
    assert "hook" not in public_members(Exon)


class _Base:
    def shared(self) -> None:
        """Shared."""

    def overridden(self) -> None:
        """Overridden."""


class _Child(_Base):
    def overridden(self) -> None:
        """Overridden."""

    def own(self) -> None:
        """Own."""


def _child_page(inherited: str) -> str:
    return (
        ".. autosummary::\n\n   _Child\n   _Child.overridden\n"
        "   _Child.own\n\n"
        "Inherited from ``_Base``\n\n"
        f".. autosummary::\n\n{inherited}\n")


def test_inherited_outline_matches_base_members() -> None:
    """The inherited outline lists the base members that are not overridden."""
    page = _child_page("   _Base.shared")

    assert not outline_problems(page, [_Child])


def test_inherited_outline_missing_member_is_reported() -> None:
    """A base member missing from the inherited outline is reported."""
    page = _child_page("   _Base.other")

    problems = outline_problems(page, [_Child])

    assert len(problems) == 2
    assert "_Base.shared" in problems[0]
    assert "_Base.other" in problems[1]


def test_class_exported_by_grr_must_be_named() -> None:
    """A class that gain.grr exports must be named as grr.<Name>."""
    problems = exported_name_problems("no names here", [GeneModels, Exon])

    assert problems == [(
        "GeneModels is exported by gain.grr "
        "but the page does not name ``grr.GeneModels``"
    )]


def test_name_in_sentence_must_be_exported() -> None:
    """A ``grr.<Name>`` name that gain.grr does not export fails."""
    problems = exported_name_problems(
        "It is ``grr.GeneModels`` and ``grr.Exon``.", [GeneModels])

    assert problems == [
        "``grr.Exon`` is named on the page but gain.grr does not export it"]


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
