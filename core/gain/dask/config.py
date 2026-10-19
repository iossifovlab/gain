import os
from collections.abc import Mapping
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
        When the named cluster config that dask loaded has the wrong shape.
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

    error = translate_legacy_named_cluster()
    if _deferred_error is None:
        _deferred_error = error


def _spellings(config: dict[str, Any], key: str) -> list[str]:
    """Return the spellings of the key in the config.

    dask treats ``-`` and ``_`` in a key as the same character.
    """
    return [
        existing for existing in config
        if existing.replace("-", "_") == key]


def _legacy_clusters(clusters: Any) -> dict[str, Any]:
    """Convert the old ``clusters`` list to a mapping keyed by each name.

    Raises
    ------
    ValueError
        When the old ``clusters`` value is not a list, or when an entry is
        not a mapping or has no ``name`` key.
    """
    where = f"'{LEGACY_NAMED_CLUSTER_KEY}.clusters'"
    if clusters is None:
        return {}
    if not isinstance(clusters, list):
        raise ValueError(  # ruff: ignore[type-check-without-type-error]
            f"{where} must be a list of cluster definitions, not a "
            f"{type(clusters).__name__}.")
    result: dict[str, Any] = {}
    for cluster_config in clusters:
        if not isinstance(cluster_config, Mapping):
            raise ValueError(  # ruff: ignore[type-check-without-type-error]
                f"Each entry of {where} must be a mapping, not a "
                f"{type(cluster_config).__name__}: {cluster_config!r}")
        cluster_config = dict(cluster_config)
        if "name" not in cluster_config:
            raise ValueError(
                f"A cluster definition in {where} has no 'name' key: "
                f"{cluster_config}")
        result[cluster_config.pop("name")] = cluster_config
    return result


def _mapping_shape_error(key: str, value: Any) -> ValueError:
    """Return the refusal of a named cluster key that is not a mapping."""
    return ValueError(
        f"'{key}' must be a mapping, not a {type(value).__name__}:\n"
        f"{_DICT_SHAPE_EXAMPLE}")


def translate_legacy_named_cluster() -> ValueError | None:
    """Translate the old named cluster key in the dask config.

    When the dask config holds ``dae_named_cluster``, the function converts
    its ``clusters`` list to a mapping keyed by each ``name`` and merges the
    result over ``gain_named_cluster``: the values of the old key win, and a
    key that the old key lacks keeps its value. An old cluster definition
    replaces the cluster definition of the same name whole. The ``default`` and
    ``distributed`` values of the old key come across too. The function
    removes the old key from the dask config and logs one warning. It does
    not change any file.

    The function does not raise. When the old key, the new key or the old
    ``clusters`` value has the wrong shape, the function translates the other
    values and returns the first refusal. :func:`reconfigure` defers the
    refusal until GAIn reads the named clusters.

    Returns
    -------
    ValueError | None
        The refusal of a malformed named cluster value, or ``None``.
    """
    config = dask.config.config  # pyright: ignore
    keys = _spellings(config, LEGACY_NAMED_CLUSTER_KEY)
    if not keys:
        return None

    error: ValueError | None = None
    legacy: dict[str, Any] = {}
    for key in keys:
        value = config.pop(key) or {}
        if not isinstance(value, Mapping):
            error = error or _mapping_shape_error(
                LEGACY_NAMED_CLUSTER_KEY, value)
            continue
        legacy = dask.config.merge(legacy, value)  # pyright: ignore

    translated: dict[str, Any] = {
        field: legacy[field]
        for field in ("default", "distributed")
        if field in legacy
    }
    try:
        clusters = _legacy_clusters(legacy.get("clusters"))
    except ValueError as refusal:
        error = error or refusal
        clusters = {}

    target = next(
        iter(_spellings(config, NAMED_CLUSTER_KEY)), NAMED_CLUSTER_KEY)
    current = config.get(target) or {}
    if not isinstance(current, Mapping):
        error = error or _mapping_shape_error(NAMED_CLUSTER_KEY, current)
        current = {}
    merged = dask.config.merge(current, translated)  # pyright: ignore
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
    return error


reconfigure()
