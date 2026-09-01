"""The ``meta:`` block shared by every GRR test-data builder.

Every resource type may carry a ``meta:`` block -- ``summary``,
``description`` and a free-form ``labels:`` mapping -- and it is read back
through the resource itself (``get_summary`` / ``get_description`` /
``get_labels``), by ``grr_manage``/``grr_browse``, the resource statistics
and the docs rendering.

Because it is a property of a *resource*, not of any one resource type,
:class:`MetaMixin` carries it once for all of the builders in
:mod:`~.testing.builders` and :mod:`.data_frame_builder` instead of every
``_render_config`` growing its own copy of the block.  A builder mixes it in
and then either

* appends :meth:`MetaMixin.render_meta` to the config text it renders
  itself, or
* calls :meth:`MetaMixin.append_meta_into` after delegating the config
  writing to a ``setup_*`` helper (the reference-genome path).

Both are no-ops until one of :meth:`MetaMixin.with_meta`,
:meth:`MetaMixin.with_labels`, :meth:`MetaMixin.with_raw_labels` or
:meth:`MetaMixin.with_raw_meta` is called, so a builder that does not use
them realizes byte-identical output.

It lives in its own module (like :mod:`.score_specs`) so the builder DSL can
keep growing without ``builders.py`` turning into an unreadable slab.
"""
from __future__ import annotations

import copy
import dataclasses
import pathlib
from typing import Any, Self

import yaml

from gain.genomic_resources.repository import GR_CONF_FILE_NAME
from gain.genomic_resources.testing.score_specs import ResourceValidationError


@dataclasses.dataclass(frozen=True)
class MetaMixin:
    """Immutable ``meta:`` state shared by every resource builder.

    A frozen dataclass carrying the ``meta:`` fields, so a builder that
    mixes it in gains ``with_meta``/``with_labels`` -- and the rendering
    behind them -- without redeclaring either.  Nothing is declared by
    default, which is what keeps the block absent from the rendered
    config unless it was asked for.

    A value and a *declared* flag rather than a value alone: ``labels:``
    and ``meta:`` can each be declared as an explicit YAML ``null``, and
    that renders differently from not declaring them at all -- the one
    emits the key with nothing after it, the other emits no key.  The
    flag carries that distinction, so the value field holds exactly what
    the caller passed and stays comparable, copyable and picklable.
    """

    meta_summary: str | None = None
    meta_description: str | None = None
    # ``Any``, not ``dict[str, Any] | None``: `meta.labels` is free-form
    # YAML and ``with_raw_labels`` exists to declare whatever it allows.
    meta_labels: Any = None
    meta_labels_declared: bool = False
    #: The whole ``meta:`` block, when ``with_raw_meta`` replaced it.
    meta_raw: Any = None
    meta_raw_declared: bool = False

    def with_meta(
        self, *, summary: str | None = None, description: str | None = None,
    ) -> Self:
        """Declare the resource's ``summary`` and/or ``description``.

        Only the fields passed are set, so the two can be declared in
        separate calls without the second one clearing the first.  Calling
        with neither is a validation error rather than a silent no-op --
        emitting an empty ``meta:`` block is never what was meant.
        """
        if summary is None and description is None:
            raise ResourceValidationError(
                "with_meta requires at least one of summary or description")
        return dataclasses.replace(
            self,
            meta_summary=(
                self.meta_summary if summary is None else summary),
            meta_description=(
                self.meta_description if description is None
                else description),
        )

    def with_labels(self, **labels: Any) -> Self:
        """Emit a ``labels:`` mapping inside the resource's ``meta:`` block.

        Keys are passed through verbatim, e.g.
        ``with_labels(reference_genome="genome")`` -- the label vocabulary
        is a convention of the GRR content, not of the resource schema, so
        the builder models none of it.  The mapping REPLACES any previously
        declared one (the :meth:`~.builders._TableScoreBuilder`
        ``with_chrom_mapping`` precedent) and is deep-copied, so neither the
        mapping nor a mutable value inside it stays shared with the caller.
        """
        return self.with_raw_labels(labels)

    def with_raw_labels(self, labels: Any) -> Self:
        """Emit ``labels:`` as an arbitrary YAML value, mapping or not.

        ``meta.labels`` is free-form YAML, so what a curator writes there
        is not necessarily a mapping -- a scalar, a list and an explicit
        ``null`` are all things a resource can carry, and each one has to
        be expressible for the readers of ``meta.labels`` to be tested
        against it (gain#654).  :meth:`with_labels` takes keyword
        arguments and therefore only ever builds a mapping; this is its
        sibling for everything else, ``with_raw_labels(None)`` being the
        explicit ``labels: null`` spelling.  The value REPLACES any
        previously declared one and is deep-copied, on the same terms.
        """
        return dataclasses.replace(
            self,
            meta_labels=copy.deepcopy(labels),
            meta_labels_declared=True,
        )

    def with_raw_meta(self, meta: Any) -> Self:
        """Emit the whole ``meta:`` block as an arbitrary YAML value.

        ``meta:`` is as free-form as the ``labels:`` inside it, so a
        resource can declare it as a scalar or a list too, and a reader of
        ``meta.labels`` has to be tested against that shape as well
        (gain#654).  Where :meth:`with_raw_labels` replaces one field,
        this replaces the block: whatever is passed becomes the value of
        ``meta:`` verbatim, and any ``summary``/``description``/``labels``
        declared alongside it is not rendered.  ``with_raw_meta(None)`` is
        the explicit ``meta: null`` spelling, as ``with_raw_labels(None)``
        is for the field.  Deep-copied on the same terms as its sibling.
        """
        return dataclasses.replace(
            self,
            meta_raw=copy.deepcopy(meta),
            meta_raw_declared=True,
        )

    def render_meta(self) -> str:
        """Render the ``meta:`` block, or ``""`` when nothing was declared.

        Emitted through ``yaml.safe_dump`` so a summary or a label value
        carrying a colon, a newline or leading whitespace stays the string
        it was authored as.
        """
        if self.meta_raw_declared:
            return yaml.safe_dump(
                {"meta": self.meta_raw},
                default_flow_style=False, sort_keys=False)
        meta: dict[str, Any] = {}
        if self.meta_summary is not None:
            meta["summary"] = self.meta_summary
        if self.meta_description is not None:
            meta["description"] = self.meta_description
        if self.meta_labels_declared:
            meta["labels"] = self.meta_labels
        if not meta:
            return ""
        return yaml.safe_dump(
            {"meta": meta}, default_flow_style=False, sort_keys=False)

    def append_meta_into(self, resource_dir: pathlib.Path) -> None:
        """Append the ``meta:`` block to an already-written resource config.

        For the builders that delegate the whole ``genomic_resource.yaml``
        to a ``setup_*`` helper (``setup_genome``/``setup_genome_bgz``)
        rather than rendering it themselves.  A no-op when no meta was
        declared, so the delegated config is left byte-identical.
        """
        append_config_block(resource_dir, self.render_meta())


def append_config_block(resource_dir: pathlib.Path, rendered: str) -> None:
    """Append a rendered YAML block to an already-written resource config.

    The shared tail for every builder that delegates the whole
    ``genomic_resource.yaml`` to a ``setup_*`` helper and then has to add
    a key the helper does not write.  A no-op for an empty block, so the
    delegated config is left byte-identical.
    """
    if not rendered:
        return
    config_path = resource_dir / GR_CONF_FILE_NAME
    config = config_path.read_text()
    if not config.endswith("\n"):
        config += "\n"
    config_path.write_text(config + rendered)
