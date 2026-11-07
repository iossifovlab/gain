import os
from collections.abc import Mapping
from typing import Any

import dask
import yaml

from gain import logging

logger = logging.getLogger(__name__)

#: The config key that holds the named clusters.
NAMED_CLUSTER_KEY = "gain_named_cluster"

#: The old config key of the named clusters. GAIn no longer reads it.
#: :func:`reconfigure` refuses a dask config that holds it.
LEGACY_NAMED_CLUSTER_KEY = "dae_named_cluster"

NAMED_CLUSTERS_DOCS_URL = "https://iossifovlab.com/gaindocs/named_clusters.html"

_DICT_SHAPE_EXAMPLE = """\
gain_named_cluster:
  clusters:
    <name>:
      type: <cluster type>
      params: {...}"""

#: A refusal of the named cluster config that :func:`reconfigure` found.
#: :func:`check_named_cluster_config` raises it when GAIn reads the named
#: clusters, so that a run with ``-c`` is not stopped at import.
_deferred_error: ValueError | None = None  # pylint: disable=invalid-name


def clusters_shape_error(clusters: Any) -> ValueError:
    """Return the refusal of a ``clusters`` value that is not a mapping."""
    return ValueError(
        f"'{NAMED_CLUSTER_KEY}.clusters' must map each named cluster to "
        f"its cluster definition, not hold a "
        f"{type(clusters).__name__}:\n{_DICT_SHAPE_EXAMPLE}")


def check_named_cluster_config() -> None:
    """Raise the refusal that :func:`reconfigure` found, if any.

    Raises
    ------
    ValueError
        When the named cluster config that dask loaded has the wrong shape,
        or when the dask config holds the old key ``dae_named_cluster``.
    """
    if _deferred_error is not None:
        raise _deferred_error


def _check_user_clusters_shape(config: dict[str, Any]) -> ValueError | None:
    """Return the refusal of a user named cluster value that is not a mapping.

    The function checks the ``gain_named_cluster`` value and its
    ``clusters`` value. The check must run before the bundled defaults are
    merged: ``dask.config.update_defaults`` replaces a user value that is not
    a mapping with the bundled mapping, and the user value is lost.
    """
    for key in _spellings(config, NAMED_CLUSTER_KEY):
        named_clusters = config[key]
        if named_clusters is None:
            continue
        if not isinstance(named_clusters, Mapping):
            return _mapping_shape_error(NAMED_CLUSTER_KEY, named_clusters)
        clusters = named_clusters.get("clusters")
        if clusters is not None and not isinstance(clusters, Mapping):
            return clusters_shape_error(clusters)
    return None


def _keep_user_number_of_threads(config: dict[str, Any]) -> None:
    """Copy a user ``number_of_threads`` value to ``number_of_workers``.

    The function changes each user cluster definition that holds
    ``number_of_threads`` but no ``number_of_workers``. It must run before
    the bundled defaults are merged: a bundled ``number_of_workers`` would
    otherwise win over the user value of the deprecated key. The function
    keeps ``number_of_threads``, so the deprecation warning stays.

    The function also copies a ``null`` value. A user
    ``number_of_threads: null`` thus clears the bundled
    ``number_of_workers``, and the cluster adapts by ``adapt_params``.

    The function changes only the config that :func:`reconfigure` finds at
    the import of :mod:`gain.dask`. A ``number_of_threads`` value that a
    later ``dask.config.set`` or ``dask.config.refresh`` call adds is not
    copied. The bundled ``number_of_workers`` then wins over that value.
    """
    for key in _spellings(config, NAMED_CLUSTER_KEY):
        named_clusters = config[key]
        if not isinstance(named_clusters, Mapping):
            continue
        clusters = named_clusters.get("clusters")
        if not isinstance(clusters, Mapping):
            continue
        for cluster_config in clusters.values():
            if not isinstance(cluster_config, dict):
                continue
            if "number_of_threads" in cluster_config \
                    and "number_of_workers" not in cluster_config:
                cluster_config["number_of_workers"] = \
                    cluster_config["number_of_threads"]


def reconfigure() -> None:
    """Load default config into dask."""
    global _deferred_error  # pylint: disable=global-statement
    filename = os.path.join(os.path.dirname(__file__), "named_cluster.yaml")
    dask.config.ensure_file(source=filename)  # pyright: ignore

    with open(filename) as file:
        defaults = yaml.safe_load(file)

    _deferred_error = _check_user_clusters_shape(
        dask.config.config)  # pyright: ignore
    _keep_user_number_of_threads(
        dask.config.config)  # pyright: ignore[reportPrivateImportUsage]
    dask.config.update_defaults(defaults)  # pyright: ignore

    distributed_config = {}
    distributed_config["distributed"] = defaults.get(
        NAMED_CLUSTER_KEY, {}).get(
        "distributed", {})
    dask.config.update_defaults(distributed_config)  # pyright: ignore

    if _deferred_error is None:
        _deferred_error = _check_legacy_named_cluster(
            dask.config.config)  # pyright: ignore


def _spellings(config: dict[str, Any], key: str) -> list[str]:
    """Return the spellings of the key in the config.

    dask treats ``-`` and ``_`` in a key as the same character.
    """
    return [
        existing for existing in config
        if existing.replace("-", "_") == key]


def _check_legacy_named_cluster(
    config: dict[str, Any],
) -> ValueError | None:
    """Return the refusal of the old key, when the config holds it.

    Any value of the old key gets the refusal, ``null`` too.
    """
    if not _spellings(config, LEGACY_NAMED_CLUSTER_KEY):
        return None
    return ValueError(
        f"GAIn no longer reads the dask config key "
        f"'{LEGACY_NAMED_CLUSTER_KEY}'. Its replacement is the key "
        f"'{NAMED_CLUSTER_KEY}'. In your files in the dask config directory "
        f"(for example ~/.config/dask/), move the entries of "
        f"'{LEGACY_NAMED_CLUSTER_KEY}' to '{NAMED_CLUSTER_KEY}' in the new "
        f"shape:\n{_DICT_SHAPE_EXAMPLE}\nSee {NAMED_CLUSTERS_DOCS_URL}")


def _mapping_shape_error(key: str, value: Any) -> ValueError:
    """Return the refusal of a named cluster key that is not a mapping."""
    return ValueError(
        f"'{key}' must be a mapping, not a {type(value).__name__}:\n"
        f"{_DICT_SHAPE_EXAMPLE}")


reconfigure()
