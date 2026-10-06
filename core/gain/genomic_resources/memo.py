"""Process-wide memos of objects built from a resource or a local file.

Gene models, gene scores, gene set collections and liftover chains are
expensive to build and are shared by every caller in a process. Each
factory keeps one memo; a resource-built object is keyed on
``GenomicResource.get_memo_key()``, a file-built one on the file name and
``config_memo_key()`` of the synthetic config it builds. The rule, and the
two resource-keyed caches that deliberately do not follow it, are recorded
in ADR 0036 (``docs/adr/``).
"""
from __future__ import annotations

from collections.abc import Callable, Hashable, Mapping
from operator import itemgetter
from threading import Lock
from typing import Any


def _canonical_config(value: Any) -> Any:
    """Return ``value`` with the order it was written in taken out.

    A mapping becomes a tuple of pairs sorted by key, so two configs
    differing only in the order their keys were written canonicalise
    alike; a sequence keeps its order. Keys are stringified first, a bare
    number being a legal yaml key that cannot be sorted against a string
    one, and the sort looks at the key alone, so values of unrelated
    types are never compared. Leaves are returned untouched: the caller
    ``repr``\\ s the result, so a value with no JSON spelling -- a bare
    date -- needs no handling of its own.

    The result is not injective. Two keys that differ only until they are
    stringified collapse together, as do a mapping and a sequence of
    pairs that canonicalise alike; neither is reachable from a parsed
    ``genomic_resource.yaml``. A config holding itself recurses until the
    stack runs out.
    """
    if isinstance(value, Mapping):
        return tuple(sorted(
            ((str(key), _canonical_config(val)) for key, val in value.items()),
            key=itemgetter(0)))
    if isinstance(value, (list, tuple)):
        return tuple(_canonical_config(val) for val in value)
    return value


def config_memo_key(config: Any) -> str:
    """Return the memo-key spelling of a resource config.

    Two configs differing only in the order their keys were written get
    the same key. Any value a parsed yaml can hold is accepted, including
    a bare date and a number used as a key beside string ones.
    """
    return repr(_canonical_config(config))


class Memo[K: Hashable, V]:
    """A thread-safe, never-evicting memo from a key to a built object.

    ``get_or_build`` returns the object stored under a key, building and
    storing it first on a miss. The build runs under the memo's lock, so
    two callers racing on one key build it once and both receive the very
    same object; a builder must therefore not call back into the same
    memo. Objects are held by strong reference until ``clear``.
    """

    def __init__(self) -> None:
        self._built: dict[K, V] = {}
        self._lock = Lock()

    def get_or_build(self, key: K, build: Callable[[], V]) -> V:
        """Return the object memoised under ``key``, building it if absent.

        A builder that raises leaves nothing stored under ``key``.
        """
        with self._lock:
            if key in self._built:
                return self._built[key]
            built = build()
            self._built[key] = built
            return built

    def clear(self) -> None:
        """Drop every memoised object."""
        with self._lock:
            self._built.clear()

    def __len__(self) -> int:
        return len(self._built)
