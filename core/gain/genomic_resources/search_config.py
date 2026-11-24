"""The ``search.yaml`` of a GRR: the facets of its extended search.

ADR 0045 decides the shape of the file::

    facets:
      - label: assay
        title: Assay
      - label: species

The list is flat, and the facets appear in file order.  ``title`` is an
optional display name; a facet without one shows its label key.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import yaml

from gain import logging

logger = logging.getLogger(__name__)

#: The name of the file at the root of a GRR.
SEARCH_CONFIG_FILE_NAME = "search.yaml"


@dataclass(frozen=True)
class Facet:
    """One facet of the extended search.

    Attributes
    ----------
    label
        The label key that the facet offers.
    title
        The display name of the facet: the ``title`` of the file, or the
        label key when the file gives none.
    """

    label: str
    title: str


def parse_search_config(text: str, source: str) -> list[Facet]:
    """Return the facets that the text of a ``search.yaml`` names.

    Parameters
    ----------
    text
        The content of the file.
    source
        The path of the file, for the messages.

    Returns
    -------
    list of Facet
        The facets in file order. The list is empty when the file names
        no facet.

    Raises
    ------
    ValueError
        The text is not valid YAML, or it does not have the shape of
        ADR 0045. The message names ``source``.
    """
    facets: list[Facet] = []
    for entry in _facet_entries(_load_yaml(text, source), source):
        facet = _parse_facet(entry, source)
        if any(seen.label == facet.label for seen in facets):
            raise ValueError(
                f"{source}: the label {facet.label!r} has more than one "
                "facet")
        facets.append(facet)
    return facets


def _load_yaml(text: str, source: str) -> object:
    """Return the content of the YAML text, or raise ``ValueError``."""
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as err:
        raise ValueError(f"{source} is not valid YAML: {err}") from err


def _facet_entries(content: object, source: str) -> list[object]:
    """Return the ``facets`` list of the content of a ``search.yaml``.

    An empty file names no facet. Any other content must be a mapping
    whose ``facets`` key, when present, holds a list.
    """
    if content is None:
        return []
    entries = (content.get("facets") or []) \
        if isinstance(content, dict) else None
    if not isinstance(entries, list):
        # A ValueError, as for invalid YAML: grr_manage stops on it with
        # the message, as on a misconfigured repository.
        raise ValueError(  # ruff: ignore[type-check-without-type-error]
            f"{source} must be a mapping with a 'facets' list")
    return entries


def _parse_facet(entry: object, source: str) -> Facet:
    """Return the facet of one entry of the ``facets`` list."""
    fields = entry if isinstance(entry, dict) else {}
    label = fields.get("label")
    if not isinstance(label, str) or not label:
        raise ValueError(
            f"{source}: each facet must have a 'label' string, "
            f"not {entry!r}")
    title = fields.get("title")
    return Facet(label, label if title is None else str(title))


def carried_facets(
    facets: Iterable[Facet], label_keys: set[str], source: str,
) -> list[Facet]:
    """Return the facets whose label key some resource carries.

    A facet on a key that no resource carries is logged as a warning and
    left out.
    """
    kept = []
    for facet in facets:
        if facet.label in label_keys:
            kept.append(facet)
            continue
        logger.warning(
            "%s: no resource carries the label %r, so the facet is skipped",
            source, facet.label)
    return kept
