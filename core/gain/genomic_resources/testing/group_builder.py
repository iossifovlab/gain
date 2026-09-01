"""Immutable builder composing several GRRs into a group repository.

A group of directory repositories -- each with its own resources and its
own advertised ``public_url`` -- is the shape a deployment actually runs,
and the one a test needs to prove that a resource's public address comes
from the child repository it was found in rather than from a single base
url.

This lives beside :mod:`gain.genomic_resources.testing.builders` rather
than inside it: that module is already over pylint's ``max-module-lines``
and carries a suppression for it, so the convention is that a new builder
gets a sibling module importing the shared seam one way.  ``builders``
does not import back, and there is no re-export.

Example::

    a_grr_group().with_child("main", a_grr()...).build_repo(tmp_path)
"""
from __future__ import annotations

import dataclasses
import pathlib
from typing import Any

from gain.genomic_resources.fsspec_protocol import canonical_public_url
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_group_repository,
)
from gain.genomic_resources.testing import short_identity_digest
from gain.genomic_resources.testing.builders import (
    GRR_RESOURCES_DIRNAME,
    GRRBuilder,
    ResourceValidationError,
    write_grr_definition,
)

#: Repository id the group carries when a test names none.
_DEFAULT_GROUP_ID = "test_grr"


def _child_root(
    root: pathlib.Path, repo_id: str, builder: GRRBuilder,
) -> pathlib.Path:
    """Locate the directory one child realizes into.

    A child's repository id is also the id of the protocol built for it,
    and callers look a resource up by that id -- so, unlike a standalone
    GRR, a group child cannot take its advertised url into its *id* to
    stay distinct.  It takes it into its *directory* instead, which is the
    other half of the ``(proto_id, url)`` memo key.

    Without that, two groups over one root whose children advertise
    different urls collide: the second build is refused outright, because
    a protocol's ``public_url`` is part of its identity and a rebuild
    cannot repoint it.  That is exactly the comparison the group form
    exists to make, so it must not be the one shape it cannot express.

    The url is canonicalized first, so two spellings of one address (a
    trailing separator, say) name one directory -- matching what the
    refusal itself treats as the same url, rather than splitting on a
    difference the protocol layer does not see.

    An unadvertised child keeps the bare id, so the common layout stays
    readable and a group carrying no urls realizes where it always did.
    """
    if builder.public_url is None:
        return root / repo_id
    digest = short_identity_digest(canonical_public_url(builder.public_url))
    return root / f"{repo_id}-{digest}"


@dataclasses.dataclass(frozen=True)
class GRRGroupBuilder:
    """Immutable builder composing whole GRRs into a group repository.

    Children are :class:`GRRBuilder` s, so a child expresses everything a
    standalone GRR does -- its resources and its advertised
    ``public_url``.  Each realizes into its own ``root / child_id``
    directory, which is what keeps two children carrying the *same*
    resource id from realizing over each other.
    """

    children: tuple[tuple[str, GRRBuilder], ...] = ()

    def with_child(
        self, repo_id: str, grr_builder: GRRBuilder,
    ) -> GRRGroupBuilder:
        """Attach a child GRR under ``repo_id``.

        Rejects a duplicate id fast at the call site: the group repository
        refuses duplicate child ids anyway, and a child id is also a cache
        directory name, so two children sharing one would realize into the
        same directory with the second silently winning.
        """
        if any(rid == repo_id for rid, _ in self.children):
            raise ResourceValidationError(
                f"duplicate child repository id {repo_id!r} declared "
                f"more than once")
        return dataclasses.replace(
            self,
            children=(*self.children, (repo_id, grr_builder)),
        )

    def build_repo(self, tmp_path: pathlib.Path) -> GenomicResourceRepo:
        """Realize every child under ``tmp_path`` and build the group.

        Returns the plain :class:`GenomicResourceRepo` seam rather than a
        protocol repository: a group is not one protocol, and narrowing
        the annotation would be a promise this cannot keep.

        Each child is built through its own ``GRRBuilder``, so a child of a
        group is realized, repaired and named exactly as the same builder
        would be on its own -- a fixture does not change shape by being
        composed into a group.
        """
        return build_genomic_resource_group_repository(_DEFAULT_GROUP_ID, [
            builder.build_repo(
                _child_root(tmp_path, repo_id, builder), proto_id=repo_id)
            for repo_id, builder in self.children
        ])

    def build_definition(
        self, root: pathlib.Path, *, grr_id: str = _DEFAULT_GROUP_ID,
    ) -> pathlib.Path:
        """Realize the children into ``root/grr`` and write ``root/grr.yaml``.

        Returns the path of the written definition file.  As for a single
        GRR, the definition is written OUTSIDE the directory holding the
        children, so it is not walked as though it were a resource.
        """
        resources_dir = root / GRR_RESOURCES_DIRNAME
        self.realize_all(resources_dir)
        return write_grr_definition(
            root, self.definition(resources_dir, grr_id=grr_id))

    def realize_all(self, root: pathlib.Path) -> None:
        """Realize every child GRR under its own directory in ``root``."""
        for repo_id, builder in self.children:
            builder.realize_all(_child_root(root, repo_id, builder))

    def definition(
        self, root: pathlib.Path, *, grr_id: str = _DEFAULT_GROUP_ID,
    ) -> dict[str, Any]:
        """Render this group as a ``group`` repository definition.

        Each child is described exactly as a standalone GRR describes
        itself: ``GRRBuilder`` owns both halves, realizing its resources
        and rendering its own definition.
        """
        return {
            "id": grr_id,
            "type": "group",
            "children": [
                builder.definition(
                    _child_root(root, repo_id, builder), grr_id=repo_id)
                for repo_id, builder in self.children
            ],
        }


def a_grr_group() -> GRRGroupBuilder:
    """Start building a group of GRRs."""
    return GRRGroupBuilder()
