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

import enum
import fnmatch
import functools
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from lark import Lark, LarkError, Token, Tree

if TYPE_CHECKING:
    from gain.genomic_resources.repository import GenomicResource


class ResourceQueryParseError(ValueError):
    """Raised when a resource query cannot be parsed."""


# ``.`` and the version parentheses are part of the charsets because real
# resource ids carry them -- ``hg38/scores/CADD_v1.7``,
# ``hg19/variant_frequencies/gnomAD_v2.1.1/exomes``, ``sub/two(1.0)``. They
# are roughly a sixth of the ids in the public GRRs, and without them a user
# cannot paste a line of `grr_manage list` output back in as a query. The
# same characters are allowed in a label value, so a version label
# (``[version="1.0"]``) is expressible too.
#
# ``]`` is deliberately absent from every charset: it is what closes the
# label filter.
RESOURCE_QUERY_GRAMMAR = """
    ?start: resource_id [filter]

    ?resource_id: (resource_name | wildcard)

    wildcard: /[\\w\\d\\/_\\-*.()]+/

    filter: "[" (equals | in | and_)+ "]"

    and_: operation "and" operation

    equals: (name"=\\""value"\\"") | (name"='"value"'")

    in: ("\\""value"\\"" " in " name) | ("'"value"'" " in " name)

    resource_name: /[\\w\\d\\/_\\-!@#$%^<>+.()]+/

    ?name: /[\\w\\d\\/_\\-!@#$%^<>+*.()]+/

    ?value: /[\\w\\d\\/ _\\-!@#$%^<>+*.()]+/

    ?operation: equals | in | and_

    %ignore " "
"""


# The longest query the parser will accept. The grammar above is ambiguous
# -- `and_` recurses through `?operation` -- so Earley costs roughly O(n^3)
# in the length of the query. Measured: 25 clauses (255 B) parse in 0.03s,
# 50 (505 B) in 0.16s, 100 (1005 B) in 1.04s, and the curve keeps its shape,
# so ~1000 clauses (10 KB, well inside a default HTTP body) is on the order
# of 900s of CPU for one call.
#
# The bound lives on the parser rather than on any one caller because the
# callers are the whole surface: the annotation config's wildcard
# expansion, the repository search, the group repository, and the REST
# search endpoint. Bounding one of them (iossifovlab/gain#443 bounded the
# endpoint) leaves the parser reachable through the rest -- which is what
# iossifovlab/gain#635 reported, via an anonymous config-validation POST.
#
# Note this is a bound on the *input*, not the policy-about-results this
# module's docstring disclaims: it caps what the caller may ask, not what
# the answer may contain. 256 characters is an order of magnitude more than
# a real query -- `hg38/scores/*[phenotype="autism" and "UCSC" in
# provenance]` is 57 -- and parses in ~20ms at its worst.
MAX_RESOURCE_QUERY_LENGTH = 256


@functools.cache
def _get_parser() -> Lark:
    """Build the grammar once, on first use.

    Cached rather than built at import: this module is imported by
    ``repository``, so every gain entry point pays the ~15ms Earley grammar
    construction otherwise, including the ones that never parse a query.
    """
    return Lark(RESOURCE_QUERY_GRAMMAR)


class LabelOperator(enum.Enum):
    """The comparisons a label clause can make."""

    EQUALS = "equals"
    CONTAINS = "contains"


@dataclass(frozen=True)
class LabelClause:
    """One condition on one label: ``key = value`` or ``value in key``.

    A clause is data, not a closure, so a caller that evaluates the query
    somewhere other than in Python -- against the FTS index, say -- can
    read what was asked without reimplementing what it means. Whatever
    engine runs the search, :meth:`matches` stays the only definition of
    the comparison.
    """

    key: str
    operator: LabelOperator
    value: str

    def matches(self, label: str) -> bool:
        """Check whether a rendered label value satisfies this clause."""
        if self.operator is LabelOperator.CONTAINS:
            return self.value in label
        return label == self.value or fnmatch.fnmatch(label, self.value)

    def matches_an_absent_label(self) -> bool:
        """Check whether this clause holds for a label that is not there.

        An absent label is matched as ``""``; a repository that can tell
        nothing else about a key -- because no resource in it carries the
        key at all -- can settle the whole clause with this.
        """
        return self.matches("")


def _build_label_clauses(node: Any) -> tuple[LabelClause, ...]:
    """Collect the label clauses of a parsed filter node.

    Clauses are kept as a flat sequence rather than folded into one
    predicate per key: several conditions of an `and` query may constrain
    the same label (``"a" in pheno and "b" in pheno``), and all of them
    must hold.
    """
    clauses: list[LabelClause] = []
    for child in node.children:
        if child.data.value == "equals":
            clauses.append(LabelClause(
                child.children[0].value,
                LabelOperator.EQUALS,
                child.children[1].value,
            ))
        elif child.data.value == "in":
            # the `in` rule spells the value BEFORE the label name
            # (`"value" in name`), the opposite of `equals`
            clauses.append(LabelClause(
                child.children[1].value,
                LabelOperator.CONTAINS,
                child.children[0].value,
            ))
        elif child.data.value == "and_":
            clauses.extend(_build_label_clauses(child))
        else:
            raise ResourceQueryParseError(
                f"Unsupported label query operation: {child.data}",
            )
    return tuple(clauses)


@dataclass(frozen=True)
class ResourceQuery:
    """A parsed resource query: an id glob plus label clauses."""

    resource_id_pattern: str
    label_clauses: tuple[LabelClause, ...]

    @staticmethod
    def parse(query: str) -> ResourceQuery:
        """Parse ``query`` into a matcher.

        Raises ``ResourceQueryParseError`` if the query is not well-formed,
        or if it is longer than ``MAX_RESOURCE_QUERY_LENGTH``.
        """
        if len(query) > MAX_RESOURCE_QUERY_LENGTH:
            # Refused on the length alone, before the grammar sees it --
            # the whole point is not to pay the parse.
            raise ResourceQueryParseError(
                f"Resource query is too long: {len(query)} characters, "
                f"at most {MAX_RESOURCE_QUERY_LENGTH} are accepted",
            )

        try:
            tree = _get_parser().parse(query)
        except LarkError as err:
            # Lark's own message carries the position and what it expected;
            # dropping it would leave the user with only their own input
            # echoed back.
            raise ResourceQueryParseError(
                f"Unparsable resource query '{query}': {err}",
            ) from err

        assert len(tree.children) == 2
        resource_id_node = tree.children[0]
        assert isinstance(resource_id_node, Tree)
        assert isinstance(resource_id_node.children[0], Token)
        resource_id_pattern = resource_id_node.children[0].value

        clauses: tuple[LabelClause, ...] = ()
        if tree.children[1] is not None:
            clauses = _build_label_clauses(tree.children[1])

        return ResourceQuery(resource_id_pattern, clauses)

    def match_id(self, resource_id: str) -> bool:
        """Check whether ``resource_id`` matches the query's id glob."""
        return fnmatch.fnmatch(resource_id, self.resource_id_pattern)

    def match_labels(self, labels: Mapping[str, Any]) -> bool:
        """Check whether ``labels`` satisfies every one of the query's clauses.

        ``meta.labels`` is a free-form YAML mapping, so a label value is
        whatever YAML made of it -- ``perturbed: False`` is a bool and
        ``year: 2019`` an int, both of which the production GRRs carry in
        bulk. The query language only ever spells values as text, so every
        label value is compared in its rendered form; without that both
        ``in`` and ``=`` raise a bare ``TypeError`` out of the predicate.

        A label the resource does not carry is matched as ``""``. The FTS
        index cannot represent the difference -- it stores ``""`` for every
        label column a resource does not carry -- so treating absence as a
        distinct case would put this matcher permanently out of step with
        the same query evaluated in SQL.
        """
        return all(
            clause.matches(str(labels.get(clause.key, "")))
            for clause in self.label_clauses
        )

    def match(self, resource: GenomicResource) -> bool:
        """Check whether ``resource`` matches the query."""
        return (
            self.match_id(resource.get_id())
            and self.match_labels(resource.get_labels())
        )
