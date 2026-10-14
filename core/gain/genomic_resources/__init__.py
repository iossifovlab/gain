from collections.abc import Callable
from importlib.metadata import EntryPoint, entry_points

from gain import logging

from .repository import GenomicResource
from .resource_implementation import GenomicResourceImplementation

_FOUND_RESOURCE_IMPLEMENTATIONS: dict[str, EntryPoint] = {}


_REGISTERED_RESOURCE_IMPLEMENTATIONS: dict[
    str, Callable[[GenomicResource], GenomicResourceImplementation]] = {}


__all__ = [
    "get_resource_implementation_builder",
]


logger = logging.getLogger(__name__)
_IMPLEMENTATIONS_LOADED = False


def get_resource_implementation_builder(
    resource_type: str,
) -> Callable[[GenomicResource], GenomicResourceImplementation] | None:
    """Return the builder of the implementation of a resource type.

    The lookup checks the in-process registry first. It then falls back
    to the entry points of the group ``gain.genomic_resources.implementations``
    and caches the builder that it loads.

    Parameters
    ----------
    resource_type : str
        The resource type, the ``type`` field of ``genomic_resource.yaml``.

    Returns
    -------
    Callable[[GenomicResource], GenomicResourceImplementation] | None
        A callable that takes a resource and returns its implementation.
        ``None`` means that nothing registered the type.
    """
    if resource_type not in _REGISTERED_RESOURCE_IMPLEMENTATIONS:
        if resource_type not in _FOUND_RESOURCE_IMPLEMENTATIONS:
            return None
        entry_point = _FOUND_RESOURCE_IMPLEMENTATIONS[resource_type]
        loaded = entry_point.load()
        register_implementation(resource_type, loaded)

    return _REGISTERED_RESOURCE_IMPLEMENTATIONS[resource_type]


def register_implementation(
    resource_type: str,
    builder: Callable[[GenomicResource], GenomicResourceImplementation],
) -> None:
    """Register the builder of a resource type in the process registry.

    The registration lasts for the life of the process and replaces an
    earlier builder of the same type.

    Parameters
    ----------
    resource_type : str
        The resource type, the ``type`` field of ``genomic_resource.yaml``.
    builder : Callable[[GenomicResource], GenomicResourceImplementation]
        A callable that takes a resource and returns a ready to use
        implementation. An implementation class whose constructor takes
        only the resource is its own builder.
    """
    _REGISTERED_RESOURCE_IMPLEMENTATIONS[resource_type] = builder


def _find_implementations() -> None:
    """
    Find and record implementations specified in entry points.

    This will record all implementations specified in the
    setup.py of the project. The implementations are stored as entry points
    and will be loaded and registered later on demand to avoid cyclic imports.
    """
    # pylint: disable=global-statement
    global _IMPLEMENTATIONS_LOADED

    if _IMPLEMENTATIONS_LOADED:
        return

    discovered_implementations = entry_points(
        group="gain.genomic_resources.implementations",
    )

    for entry_point in discovered_implementations:
        _FOUND_RESOURCE_IMPLEMENTATIONS[entry_point.name] = entry_point

    _IMPLEMENTATIONS_LOADED = True


_find_implementations()
