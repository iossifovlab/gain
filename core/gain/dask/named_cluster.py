import copy
from collections.abc import Callable
from typing import Any

import dask.config
from distributed.client import Client
from distributed.deploy import Cluster  # pyright: ignore

from gain import logging
from gain.dask.config import check_named_cluster_config, clusters_shape_error

_CLUSTER_TYPES: dict[str, Callable[[dict[str, Any]], Cluster]] = {}
logger = logging.getLogger(__name__)


def set_up_local_cluster(cluster_conf: dict[str, Any]) -> Cluster:
    """Create a local cluster using the passed cluster configuration.

    :meta private:
    """
    # pylint: disable=import-outside-toplevel
    from dask.distributed import LocalCluster

    kwargs = copy.copy(cluster_conf)
    number_of_workers = kwargs.pop("number_of_workers", None)
    threads_per_worker = kwargs.pop("threads_per_worker", None)
    if threads_per_worker is not None:
        logger.warning(
            "The 'threads_per_worker' key in the named cluster config is "
            "not supported. We support only one thread per worker.")
    threads_per_worker = 1

    if number_of_workers is not None:
        kwargs["n_workers"] = number_of_workers
    if threads_per_worker is not None:
        kwargs["threads_per_worker"] = threads_per_worker
    dask.config.set(scheduler="threads")
    return LocalCluster(**kwargs)


def set_up_sge_cluster(cluster_conf: dict[str, Any]) -> Cluster:
    """Create an SGE cluster using the passed cluster configuration.

    :meta private:
    """
    # pylint: disable=import-outside-toplevel
    from dask_jobqueue import SGECluster  # pyright: ignore
    kwargs = {
        key: value for key, value in cluster_conf.items()
        if key != "number_of_workers"}
    return SGECluster(**kwargs)


def set_up_slurm_cluster(cluster_conf: dict[str, Any]) -> Cluster:
    """Create a SLURM cluster using the passed cluster configuration.

    :meta private:
    """
    # pylint: disable=import-outside-toplevel
    from dask_jobqueue import SLURMCluster  # pyright: ignore
    kwargs = {
        key: value for key, value in cluster_conf.items()
        if key != "number_of_workers"}
    return SLURMCluster(**kwargs)


_CLUSTER_TYPES["local"] = set_up_local_cluster
_CLUSTER_TYPES["sge"] = set_up_sge_cluster
_CLUSTER_TYPES["slurm"] = set_up_slurm_cluster


def set_up_manual_client(cluster_conf: dict[str, Any]) -> Client:
    """Create a dask client using the passed cluster configuration.

    :meta private:
    """
    # pylint: disable=import-outside-toplevel
    params = dict(cluster_conf.get("params", {}))
    scheduler_address = params.pop("address", None)

    if scheduler_address is None:
        raise ValueError(
            "Cluster configuration must contain scheduler 'address' key.")

    return Client(address=scheduler_address, **params)


def setup_client_from_config(
    cluster_config: dict[str, Any], *,
    number_of_workers: int | None = None,
) -> tuple[Client, dict[str, Any]]:
    """Create a dask client from one cluster definition.

    Parameters
    ----------
    cluster_config
        A cluster definition: a mapping with the keys ``type``, ``params``
        and, optionally, ``number_of_workers`` or ``adapt_params``.
        ``type`` is ``local``, ``sge``, ``slurm`` or ``manual``. ``params``
        holds the arguments of the dask cluster class. For ``manual``,
        ``params`` holds the ``address`` of a running dask scheduler.
    number_of_workers
        The number of workers. A number here wins over the
        ``number_of_workers`` of the cluster definition. ``None`` keeps the
        number of the cluster definition. When the cluster definition has no
        ``number_of_workers``, the deprecated ``number_of_threads`` key gives
        the number. When no key gives a number, the cluster adapts by
        ``adapt_params``, if the cluster definition has that key.

    Returns
    -------
    tuple[Client, dict[str, Any]]
        The dask client and a copy of ``cluster_config``. The copy is equal
        to ``cluster_config``. It is not the same object.

    Raises
    ------
    ValueError
        When ``type`` is not a known cluster type, or when a ``manual``
        cluster definition has no ``address`` under ``params``.

    Notes
    -----
    For the ``local`` type, each worker runs one thread. When ``params``
    holds ``threads_per_worker``, the function logs a warning, also for the
    value 1, and uses 1. For ``sge`` and ``slurm``, the jobqueue parameters
    ``cores`` and ``processes`` in ``params`` set the threads of a worker.

    The function works on its own copy of ``cluster_config`` and does not
    change the mapping that it gets. Two calls with the same mapping build
    two clients with the same settings.
    """
    cluster_config = copy.deepcopy(cluster_config)
    logger.info("CLUSTER CONFIG: %s", cluster_config)
    _resilence_distributed_logging()
    _adjust_default_distributed_config()
    cluster_type = cluster_config["type"]
    if cluster_type != "manual" and cluster_type not in _CLUSTER_TYPES:
        supported = ", ".join(sorted({"manual", *_CLUSTER_TYPES}))
        raise ValueError(
            f"Unknown named cluster type {cluster_type!r}; "
            f"supported types: {supported}.")
    if cluster_type == "manual":
        return set_up_manual_client(cluster_config), cluster_config

    cluster_params = copy.deepcopy(cluster_config.get("params", {}))
    cluster_params["number_of_workers"] = number_of_workers

    threads_per_worker = cluster_params.get("threads_per_worker")
    if threads_per_worker is not None and threads_per_worker != 1:
        logger.warning(
            "The 'threads_per_worker' key in the named cluster config is "
            "not supported. We support only one thread per worker.")
        cluster_params["threads_per_worker"] = 1

    cluster = _CLUSTER_TYPES[cluster_type](cluster_params)

    number_of_threads_config = cluster_config.get("number_of_threads")
    if number_of_threads_config is not None:
        logger.warning(
            "The 'number_of_threads' key in the named cluster config is "
            "deprecated. Use 'number_of_workers' instead.")

    number_of_workers_config = cluster_config.get(
        "number_of_workers", number_of_threads_config)

    if number_of_workers is not None:
        logger.info(
            "Overriding the configurated number of workers from "
            "CLI parameter to %d workers.",
            number_of_workers,
        )
        number_of_workers_config = number_of_workers

    if number_of_workers_config is not None:
        cluster.scale(n=number_of_workers_config)
    elif "adapt_params" in cluster_config:
        cluster.adapt(**cluster_config["adapt_params"])

    client = Client(cluster)
    return client, cluster_config


def _resilence_distributed_logging() -> None:
    """Re-assert third-party logger verbosity after ``distributed`` import.

    Importing ``distributed`` runs dask's ``initialize_logging``, which resets
    the ``distributed`` logger to INFO and thereby clobbers the silencing
    applied at CLI startup by ``VerbosityConfiguration`` (#123). ``distributed``
    is imported lazily when the cluster is built, i.e. after that startup
    silencing, so re-apply it here now that the import has happened.
    """
    # pylint: disable=import-outside-toplevel
    from gain.utils.verbosity_configuration import VerbosityConfiguration
    VerbosityConfiguration.adjust_verbosity(
        logging.getLogger().getEffectiveLevel())


def _adjust_default_distributed_config() -> None:
    """Adjust some default distributed config values if they are not set."""
    task_duration = dask.config.get(
        "gain_named_cluster.distributed.scheduler.unknown-task-duration")
    dask.config.config[
        "distributed"]["scheduler"]["unknown-task-duration"] = task_duration

    memory_pause = dask.config.get(
        "gain_named_cluster.distributed.worker.memory.pause")
    dask.config.config[
        "distributed"]["worker"]["memory"]["pause"] = memory_pause


def _named_clusters() -> dict[str, dict[str, Any]]:
    """Return the named clusters, checked for the dict shape."""
    check_named_cluster_config()
    clusters = dask.config.get(  # pyright: ignore
        "gain_named_cluster.clusters")
    if not isinstance(clusters, dict):
        # ValueError, not the TypeError that TRY004 asks for: the wrong
        # shape is a configuration error, and the issue (gain#1972) asks
        # for ValueError like the other refusals of the named clusters.
        raise clusters_shape_error(clusters)
    for name, cluster_config in clusters.items():
        if isinstance(cluster_config, dict) and "name" in cluster_config:
            raise ValueError(
                f"The cluster definition of the named cluster {name!r} "
                "holds a 'name' key. The key under "
                "'gain_named_cluster.clusters' is the name. Put a dask job "
                "name under 'params'.")
    return clusters


def setup_client(
    cluster_name: str | None = None,
    number_of_workers: int | None = None,
) -> tuple[Client, dict[str, Any]]:
    """Create a dask client for a named cluster.

    Parameters
    ----------
    cluster_name
        The name of a named cluster under ``gain_named_cluster.clusters``.
        ``None`` selects the named cluster in ``gain_named_cluster.default``.
    number_of_workers
        The number of workers. ``None`` keeps the number in the cluster
        definition.

    Returns
    -------
    tuple[Client, dict[str, Any]]
        The dask client and a copy of the cluster definition that built
        it.

    Raises
    ------
    ValueError
        When ``gain_named_cluster.clusters`` is not a mapping from names to
        cluster definitions, when a cluster definition holds a ``name`` key,
        or when no named cluster has the name ``cluster_name``. Also when
        :func:`setup_client_from_config` refuses the cluster definition.

    Notes
    -----
    The function does not change the cluster definition in the dask config.
    Two calls for the same named cluster build two clients with the same
    settings.
    """
    if cluster_name is None:
        cluster_name = dask.config.get(  # pyright: ignore
            "gain_named_cluster.default")

    clusters = _named_clusters()
    if cluster_name not in clusters:
        defined = ", ".join(sorted(clusters))
        raise ValueError(
            f"Unknown named cluster {cluster_name!r}; "
            f"defined named clusters: {defined}.")
    cluster_config = clusters[cluster_name]
    return setup_client_from_config(
        cluster_config,
        number_of_workers=number_of_workers,
    )
