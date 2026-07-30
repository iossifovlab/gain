"""The query language that selects resources out of a repository.

A query is an fnmatch glob over the resource id, optionally followed by a
bracketed query over the resource's ``meta.labels``::

    hg38/scores/*[phenotype="aut*" and "UCSC" in provenance]

This module owns the grammar and the matching, and nothing else. It answers
one question -- does this resource match this query -- and deliberately holds
no policy about what a caller does with the answer: no result cap, no error
when a query selects nothing. Those are the annotation layer's rules about
building a pipeline, not the repository's rules about listing resources.

It lives here rather than in ``annotation`` so that the pipeline config, the
repositories and the CLIs cannot disagree about what ``*`` means.
"""
from __future__ import annotations

import fnmatch
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from lark import Lark, LarkError, Token, Tree

if TYPE_CHECKING:
    from gain.genomic_resources.repository import GenomicResource


class ResourceQueryParseError(ValueError):
    """Raised when a resource query cannot be parsed."""


RESOURCE_QUERY_GRAMMAR = """
    ?start: resource_id [filter]

    ?resource_id: (resource_name | wildcard)

    wildcard: /[\\w\\d\\/_\\-*]+/

    filter: "[" (equals | in | and_)+ "]"

    and_: operation "and" operation

    equals: (name"=\\""value"\\"") | (name"='"value"'")

    in: ("\\""value"\\"" " in " name) | ("'"value"'" " in " name)

    resource_name: /[\\w\\d\\/_\\-!@#$%^<>+]+/

    ?name: /[\\w\\d\\/_\\-!@#$%^<>+*]+/

    ?value: /[\\w\\d\\/ _\\-!@#$%^<>+*]+/

    ?operation: equals | in | and_

    %ignore " "
"""

_PARSER = Lark(RESOURCE_QUERY_GRAMMAR)


def _equals_predicate(value: str) -> Callable[[str], bool]:
    def predicate(label: str) -> bool:
        return label == value or fnmatch.fnmatch(label, value)
    return predicate


def _contains_predicate(value: str) -> Callable[[str], bool]:
    def predicate(label: str) -> bool:
        return value in label
    return predicate


def _add_label_predicate(
    predicates: dict[str, Callable[[str], bool]],
    key: str,
    predicate: Callable[[str], bool],
) -> None:
    """Conjoin a predicate with the ones already collected for ``key``.

    Several conditions of an `and` query may constrain the same label
    (``"a" in pheno and "b" in pheno``); all of them must hold.
    """
    existing = predicates.get(key)
    if existing is None:
        predicates[key] = predicate
        return

    def combined(label: str) -> bool:
        return bool(existing(label)) and predicate(label)

    predicates[key] = combined


def _build_label_predicates(
    node: Any,
    predicates: dict[str, Callable[[str], bool]] | None = None,
) -> dict[str, Callable[[str], bool]]:
    """Build the label predicates from a parsed filter node."""
    if predicates is None:
        predicates = {}

    for child in node.children:
        if child.data.value == "equals":
            key = child.children[0].value
            value = child.children[1].value
            _add_label_predicate(predicates, key, _equals_predicate(value))
        elif child.data.value == "in":
            # the `in` rule spells the value BEFORE the label name
            # (`"value" in name`), the opposite of `equals`
            value = child.children[0].value
            key = child.children[1].value
            _add_label_predicate(predicates, key, _contains_predicate(value))
        elif child.data.value == "and_":
            _build_label_predicates(child, predicates)
        else:
            raise ResourceQueryParseError(
                f"Unsupported label query operation: {child.data}",
            )
    return predicates


@dataclass(frozen=True)
class ResourceQuery:
    """A parsed resource query: an id glob plus label predicates."""

    resource_id_pattern: str
    label_predicates: Mapping[str, Callable[[str], bool]]

    @staticmethod
    def parse(query: str) -> ResourceQuery:
        """Parse ``query`` into a matcher.

        Raises ``ResourceQueryParseError`` if the query is not well-formed.
        """
        try:
            tree = _PARSER.parse(query)
        except LarkError as err:
            raise ResourceQueryParseError(
                f"Unparsable resource query: '{query}'",
            ) from err

        assert len(tree.children) == 2
        resource_id_node = tree.children[0]
        assert isinstance(resource_id_node, Tree)
        assert isinstance(resource_id_node.children[0], Token)
        resource_id_pattern = resource_id_node.children[0].value

        predicates: dict[str, Callable[[str], bool]] = {}
        if tree.children[1] is not None:
            predicates = _build_label_predicates(tree.children[1])

        return ResourceQuery(resource_id_pattern, predicates)

    def match_id(self, resource_id: str) -> bool:
        """Check whether ``resource_id`` matches the query's id glob."""
        return fnmatch.fnmatch(resource_id, self.resource_id_pattern)

    def match_labels(self, labels: Mapping[str, Any]) -> bool:
        """Check whether ``labels`` satisfies the query's label predicates.

        ``meta.labels`` is a free-form YAML mapping, so a label value is
        whatever YAML made of it -- ``perturbed: False`` is a bool and
        ``year: 2019`` an int, both of which the production GRRs carry in
        bulk. The query language only ever spells values as text, so every
        label value is compared in its rendered form; without that both
        ``in`` and ``=`` raise a bare ``TypeError`` out of the predicate.
        """
        for key, predicate in self.label_predicates.items():
            if key not in labels:
                return False
            if not predicate(str(labels[key])):
                return False
        return True

    def match(self, resource: GenomicResource) -> bool:
        """Check whether ``resource`` matches the query."""
        return (
            self.match_id(resource.get_id())
            and self.match_labels(resource.get_labels())
        )
