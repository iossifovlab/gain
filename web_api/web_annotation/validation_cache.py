"""Bounded memo of ``/api/pipelines/validate`` verdicts (gain#833)."""
import hashlib
import time
from collections import OrderedDict
from collections.abc import Callable
from threading import Lock
from typing import NamedTuple

from gain import logging
from gain.genomic_resources.repository import GenomicResourceRepo

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

    Two things bound how stale an answer can be, and they catch different
    things:

    - The **repository generation** catches the repository *object* being
      replaced: a verdict is a statement about a config and a GRR, so it is
      only ever returned to a caller holding the same repository it was
      computed against. In ``web_api`` today that never happens -- the GRR is
      a module-level singleton built at import -- so this fires only in
      tests. It is here so that a future server which does rebuild its
      repository cannot be served verdicts about the old one.
    - The **TTL** catches content moving *under* an unchanged object, which
      the generation key provably cannot see, identity being unchanged in
      that case. That is the shape the real risk has:
      ``GenomicResourceRepoProtocol.invalidate`` exists, and the GRRs are
      bind-mounted directories grr-sync rewrites while the server runs. So
      the TTL is the operative bound, not the belt to the other's braces.

    Neither is load-bearing today, and that is a measured statement rather
    than an assumption: a live process does not observe a GRR content change
    at all, because the repository memoises what it has resolved for the
    life of the process. A rebuild would answer exactly what this memo
    answers. See ``docs/833-validate-result-memo.md``.
    """

    #: Longest ``errors`` string worth remembering.
    #:
    #: The key is a digest and so is fixed-size; the value is not. The
    #: annotator-configuration message echoes the resource id back, so a
    #: caller who posts a 60 KB config gets a 60 KB verdict -- and at capacity
    #: that would be ~15 MB of attacker-chosen text held for the TTL. A bound
    #: on the entry count is only a bound on memory if an entry is small.
    #:
    #: 4 KiB, against real messages in the tens to low hundreds of bytes. A
    #: verdict past it is not refused, only not remembered: the response is
    #: rendered from it either way, so such a config is simply rebuilt next
    #: time.
    MAX_VERDICT_LENGTH = 4 * 1024

    def __init__(
        self,
        *,
        capacity: int,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # Keyword-only: the two bounds are both plain ints in production (256
        # and 300), so a positional call that swapped them -- capacity 300,
        # TTL 256 seconds -- would be silent. This makes it unrepresentable
        # rather than something a comment has to warn about.
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
        self._generation: GenomicResourceRepo | None = None

    def _adopt(self, repository: GenomicResourceRepo) -> None:
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

        ``surrogatepass`` because this has to be **total**. Not every Python
        ``str`` is UTF-8-encodable: ``json.loads`` accepts a lone surrogate
        (the stdlib does not reject them) and DRF's JSONParser goes straight
        through it, so a two-byte ASCII body can put one in ``request.data``.
        A plain ``encode("utf-8")`` raises there, and since this is the first
        thing on the request path that encodes the config, that raise is a
        500 on an anonymous endpoint for a config the parser would otherwise
        have called invalid and answered 200.

        And ``surrogatepass`` rather than ``replace`` or ``ignore``, because
        it must also stay **injective**. A lossy encode maps distinct texts
        onto one digest, and a memo that does that answers one config with
        another config's verdict -- silently, both being 200s. The byte
        sequences ``surrogatepass`` produces for surrogates are exactly the
        ones strict UTF-8 never emits, so no surrogate-bearing text can
        collide with an ordinary one.
        """
        return hashlib.sha256(
            content.encode("utf-8", "surrogatepass")).hexdigest()

    def get(
        self, content: str, repository: GenomicResourceRepo,
    ) -> str | None:
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

    def put(
        self, content: str, errors: str,
        repository: GenomicResourceRepo,
    ) -> None:
        """Remember the ``errors`` this config text produced.

        ``repository`` is the GRR the verdict was computed against; see
        :meth:`get`.

        A verdict past :attr:`MAX_VERDICT_LENGTH` is dropped rather than
        stored. Nothing about the caller's response changes -- it is rendered
        from ``errors``, not from what was remembered.
        """
        if len(errors) > self.MAX_VERDICT_LENGTH:
            logger.info(
                "not memoising a %d-character validation verdict; the limit "
                "is %d", len(errors), self.MAX_VERDICT_LENGTH,
            )
            return
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
