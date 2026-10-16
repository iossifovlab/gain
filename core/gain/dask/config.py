import os
from typing import Any

import dask
import yaml

from gain import logging

logger = logging.getLogger(__name__)

#: The config key that holds the named clusters.
NAMED_CLUSTER_KEY = "gain_named_cluster"

#: The old config key of the named clusters. Its ``clusters`` value is a list
#: of cluster definitions, each with a ``name`` key.
LEGACY_NAMED_CLUSTER_KEY = "dae_named_cluster"

#: The GAIn release that stops reading :data:`LEGACY_NAMED_CLUSTER_KEY`.
#: Named in the warning: a notice that does not say when it bites cannot be
#: scheduled against.
LEGACY_NAMED_CLUSTER_REMOVAL_RELEASE = "2026.11.0"

NAMED_CLUSTERS_DOCS_URL = "https://iossifovlab.com/gaindocs/named_clusters.html"


def reconfigure() -> None:
    """Load default config into dask."""
    filename = os.path.join(os.path.dirname(__file__), "named_cluster.yaml")
    dask.config.ensure_file(source=filename)  # pyright: ignore

    with open(filename) as file:
        defaults = yaml.safe_load(file)

    dask.config.update_defaults(defaults)  # pyright: ignore

    distributed_config = {}
    distributed_config["distributed"] = defaults.get(
        NAMED_CLUSTER_KEY, {}).get(
        "distributed", {})
    dask.config.update_defaults(distributed_config)  # pyright: ignore

    translate_legacy_named_cluster()


def _spellings(config: dict[str, Any], key: str) -> list[str]:
    """Return the spellings of the key in the config.

    dask treats ``-`` and ``_`` in a key as the same character.
    """
    return [
        existing for existing in config
        if existing.replace("-", "_") == key]


def translate_legacy_named_cluster() -> None:
    """Translate the old named cluster key in the dask config.

    When the dask config holds ``dae_named_cluster``, the function converts
    its ``clusters`` list to a mapping keyed by each ``name`` and merges the
    result over ``gain_named_cluster``: the values of the old key win, and a
    key that the old key lacks keeps its value. An old cluster definition
    replaces the cluster definition of the same name whole. The ``default`` and
    ``distributed`` values of the old key come across too. The function
    removes the old key from the dask config and logs one warning. It does
    not change any file.

    Raises
    ------
    ValueError
        When an entry of the old ``clusters`` list has no ``name`` key.
    """
    config = dask.config.config  # pyright: ignore
    keys = _spellings(config, LEGACY_NAMED_CLUSTER_KEY)
    if not keys:
        return

    legacy: dict[str, Any] = {}
    for key in keys:
        legacy = dask.config.merge(  # pyright: ignore
            legacy, config.pop(key) or {})

    translated: dict[str, Any] = {
        field: legacy[field]
        for field in ("default", "distributed")
        if field in legacy
    }
    clusters: dict[str, Any] = {}
    for cluster_config in legacy.get("clusters") or []:
        cluster_config = dict(cluster_config)
        if "name" not in cluster_config:
            raise ValueError(
                f"A cluster definition in '{LEGACY_NAMED_CLUSTER_KEY}."
                f"clusters' has no 'name' key: {cluster_config}")
        clusters[cluster_config.pop("name")] = cluster_config

    target = next(
        iter(_spellings(config, NAMED_CLUSTER_KEY)), NAMED_CLUSTER_KEY)
    merged = dask.config.merge(  # pyright: ignore
        config.get(target) or {}, translated)
    if clusters:
        # Merge at the level of the clusters mapping, not inside each
        # cluster definition: before the new key, the old list replaced the
        # bundled list whole. A deep merge would bring back bundled keys
        # that the user removed, for example a number_of_workers next to the
        # user's adapt_params.
        merged["clusters"] = {**(merged.get("clusters") or {}), **clusters}
    config[target] = merged

    logger.warning(
        "The dask config key '%s' is deprecated and GAIn "
        "%s stops reading it. GAIn read it as the new key '%s' for this run. "
        "Delete the generated named_cluster.yaml in the dask config "
        "directory (for example ~/.config/dask/), or move its entries to "
        "the new shape. See %s",
        LEGACY_NAMED_CLUSTER_KEY,
        LEGACY_NAMED_CLUSTER_REMOVAL_RELEASE,
        NAMED_CLUSTER_KEY,
        NAMED_CLUSTERS_DOCS_URL,
    )


reconfigure()
