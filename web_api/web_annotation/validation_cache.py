"""Bounded memo of ``/api/pipelines/validate`` verdicts (gain#833)."""
import hashlib
import time
from collections import OrderedDict
from collections.abc import Callable
from threading import Lock
from typing import NamedTuple

from gain import logging

logger = logging.getLogger(__name__)


class _Verdict(NamedTuple):
    """What the endpoint answered, and when it was computed."""

    errors: str
    computed_at: float


class ValidationResultCache:
    """Remember the verdict a config text got, so a repeat costs nothing.

    Validation needs only the *outcome* -- the ``errors`` string, empty when
    the config builds -- not the pipeline the build produced. So what is
    stored is a short string per config, which is why an entry count is a
    real bound on the memory this can hold.

    Two things bound how stale an answer can be:

    - The **repository generation**: a verdict is a statement about a config
      *and* a GRR, so it is only ever returned to a caller holding the same
      repository object it was computed against. Handed a different one, the
      memo forgets everything rather than answering for a repository it never
      saw.
    - The **TTL**: an upper bound on the age of any answer, whatever the
      repository has been doing. It is the belt to the generation key's
      braces -- ``GenomicResourceRepoProtocol.invalidate`` exists, so a
      repository object that starts re-reading its content under a running
      server would leave the generation key unchanged while the content moved
      beneath it.
    """

    def __init__(
        self,
        capacity: int,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.capacity = capacity
        self.ttl_seconds = ttl_seconds
        # Monotonic by default, deliberately: a wall clock that steps
        # backwards over an NTP correction would make entries look younger
        # than they are, which is the one direction a staleness bound must
        # not be wrong in.
        self._clock = clock
        self._entries: OrderedDict[str, _Verdict] = OrderedDict()
        self._lock = Lock()
        #: The repository every entry was computed against. Compared by
        #: identity and never called: what the memo needs from it is only
        #: that it is the same object, so anything is a valid generation.
        self._generation: object | None = None

    def _adopt(self, repository: object) -> None:
        """Drop every verdict if this is not the repository they are about.

        Caller holds ``self._lock``.
        """
        if self._generation is repository:
            return
        if self._generation is not None and self._entries:
            logger.info(
                "forgetting %d validation verdicts: the repository they "
                "were computed against is no longer the one in use",
                len(self._entries),
            )
        self._entries.clear()
        self._generation = repository

    @staticmethod
    def key(content: str) -> str:
        """Return the cache key for a config text.

        A digest, not the text: the endpoint is anonymous, so the keys are
        attacker-chosen and bounded only by ``MAX_CONFIG_LENGTH``. A digest
        keeps an entry the same small size whatever was posted, which is what
        makes a bound on the entry *count* a bound on memory too.
        """
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def get(self, content: str, repository: object) -> str | None:
        """Return the remembered ``errors`` for this config, or None.

        ``None`` is the miss, and it has to be, because ``""`` is the verdict
        of a config that builds -- the commonest answer the endpoint gives.

        ``repository`` is the GRR the caller is about to validate against.
        Handed a different one than the entries were computed against, the
        memo forgets them and misses.
        """
        key = self.key(content)
        with self._lock:
            self._adopt(repository)
            entry = self._entries.get(key)
            if entry is None:
                return None
            if self._clock() - entry.computed_at > self.ttl_seconds:
                # Dropped, not merely refused: an expired entry can only ever
                # miss, so leaving it would spend capacity on nothing.
                del self._entries[key]
                return None
            # Reading renews the entry: an editing session returns to the
            # text it just had, so recency is what the bound should keep.
            self._entries.move_to_end(key)
            return entry.errors

    def put(self, content: str, errors: str, repository: object) -> None:
        """Remember the ``errors`` this config text produced.

        ``repository`` is the GRR the verdict was computed against; see
        :meth:`get`.
        """
        key = self.key(content)
        with self._lock:
            self._adopt(repository)
            self._entries[key] = _Verdict(errors, self._clock())
            self._entries.move_to_end(key)
            while len(self._entries) > self.capacity:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        """Forget every remembered verdict, and which GRR they were about."""
        with self._lock:
            self._entries.clear()
            self._generation = None

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
