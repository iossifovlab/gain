"""The ``meta:`` block shared by every GRR test-data builder.

Every resource type may carry a ``meta:`` block -- ``summary``,
``description`` and a free-form ``labels:`` mapping -- and it is read back
through the resource itself (``get_summary`` / ``get_description`` /
``get_labels``), by ``grr_manage``/``grr_browse``, the resource statistics
and the docs rendering.

Because it is a property of a *resource*, not of any one resource type,
:class:`MetaMixin` carries it once for all of the builders in
:mod:`.builders` and :mod:`.data_frame_builder` instead of every
``_render_config`` growing its own copy of the block.  A builder mixes it in
and then either

* appends :meth:`MetaMixin.render_meta` to the config text it renders
  itself, or
* calls :meth:`MetaMixin.append_meta_into` after delegating the config
  writing to a ``setup_*`` helper (the reference-genome path).

Both are no-ops when neither :meth:`MetaMixin.with_meta` nor
:meth:`MetaMixin.with_labels` was called, so a builder that does not use
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

    A frozen dataclass carrying the three ``meta:`` fields, so a builder
    that mixes it in gains ``with_meta``/``with_labels`` -- and the
    rendering behind them -- without redeclaring either.  All three fields
    default to ``None``, which is what keeps the block absent from the
    rendered config unless it was asked for.
    """

    meta_summary: str | None = None
    meta_description: str | None = None
    meta_labels: dict[str, Any] | None = None

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
        return dataclasses.replace(self, meta_labels=copy.deepcopy(labels))

    def render_meta(self) -> str:
        """Render the ``meta:`` block, or ``""`` when nothing was declared.

        Emitted through ``yaml.safe_dump`` so a summary or a label value
        carrying a colon, a newline or leading whitespace stays the string
        it was authored as.
        """
        meta: dict[str, Any] = {}
        if self.meta_summary is not None:
            meta["summary"] = self.meta_summary
        if self.meta_description is not None:
            meta["description"] = self.meta_description
        if self.meta_labels is not None:
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
        rendered = self.render_meta()
        if not rendered:
            return
        config_path = resource_dir / GR_CONF_FILE_NAME
        config = config_path.read_text()
        if not config.endswith("\n"):
            config += "\n"
        config_path.write_text(config + rendered)
