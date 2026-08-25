# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The validation result memo's own bounds (iossifovlab/gain#833).

The endpoint's behaviour is specified in ``test_pipeline_validation_async``;
what is specified here is what the memo promises *as a memo* -- that it stays
small, that a verdict cannot outlive the repository it was computed against,
and that it cannot answer with something arbitrarily old.
"""

from typing import cast

from gain.genomic_resources.repository import GenomicResourceRepo

from web_annotation.validation_cache import ValidationResultCache


def a_repository() -> GenomicResourceRepo:
    """A stand-in for the GRR.

    The memo compares generations by identity and never calls anything on
    them, so a bare object is a faithful stand-in; the cast is what says
    that is deliberate rather than a mis-wire. Typing the parameter is
    what stops a caller passing the wrong attribute of the view -- which
    would make the memo either always-clear or never-clear, both silent.
    """
    return cast(GenomicResourceRepo, object())


REPOSITORY = a_repository()


def test_a_verdict_is_remembered_and_returned() -> None:
    """The whole point, stated once: what went in comes back out."""
    cache = ValidationResultCache(capacity=4, ttl_seconds=60)

    config = "- position_score: scores/pos1"

    cache.put(config, "", repository=REPOSITORY)

    assert cache.get(config, repository=REPOSITORY) == ""


def test_an_unseen_config_is_a_miss() -> None:
    """A miss is ``None``, which is what makes an empty verdict storable.

    ``""`` is the verdict of a config that *builds* -- the most common answer
    the endpoint gives. If a miss were also falsy-but-present the two would be
    indistinguishable and every valid config would rebuild forever.
    """
    cache = ValidationResultCache(capacity=4, ttl_seconds=60)

    assert cache.get(
        "- position_score: scores/pos1", repository=REPOSITORY) is None


def test_the_memo_never_holds_more_than_its_capacity() -> None:
    """An anonymous caller cannot grow the memo without bound.

    The endpoint takes an unauthenticated body of up to ``MAX_CONFIG_LENGTH``
    and every distinct one is a distinct key, so without a bound a caller
    could mint entries for as long as it cared to. Entries are tiny, which is
    what makes a *count* the right bound -- but a bound there must be.
    """
    cache = ValidationResultCache(capacity=3, ttl_seconds=60)

    for i in range(10):
        cache.put(f"config {i}", "", repository=REPOSITORY)

    assert len(cache) == 3


def test_the_oldest_verdict_is_the_one_dropped() -> None:
    """At capacity the memo drops what was used longest ago.

    An editing session revisits the text it just had -- an undo, a revert, a
    settle after a pause -- so recency is the right thing to keep. Evicting
    the newest instead would throw away exactly the entries about to be
    asked for.
    """
    cache = ValidationResultCache(capacity=2, ttl_seconds=60)
    cache.put("oldest", "", repository=REPOSITORY)
    cache.put("newer", "", repository=REPOSITORY)

    cache.put("newest", "", repository=REPOSITORY)

    assert cache.get("oldest", repository=REPOSITORY) is None
    assert cache.get("newer", repository=REPOSITORY) == ""
    assert cache.get("newest", repository=REPOSITORY) == ""


def test_reading_a_verdict_makes_it_recent_again() -> None:
    """A hit renews an entry, so a config in active use is not evicted.

    Without this the bound would be insertion order, and the config the user
    keeps returning to would age out from under them while configs they typed
    once and abandoned survived.
    """
    cache = ValidationResultCache(capacity=2, ttl_seconds=60)
    cache.put("kept", "", repository=REPOSITORY)
    cache.put("other", "", repository=REPOSITORY)

    assert cache.get("kept", repository=REPOSITORY) == ""
    cache.put("newest", "", repository=REPOSITORY)

    assert cache.get("kept", repository=REPOSITORY) == ""
    assert cache.get("other", repository=REPOSITORY) is None


class FakeClock:
    """A monotonic clock the test moves by hand.

    Real sleeps would make the TTL tests either slow or flaky, and there is
    nothing about elapsed *wall* time under test here -- only what the memo
    does on either side of its own bound.
    """

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_a_verdict_within_the_ttl_is_still_returned() -> None:
    """The bound is an upper bound, not a per-request expiry.

    Without this, a TTL of any size would be indistinguishable from no cache
    at all and the tests below would still pass.
    """
    clock = FakeClock()
    cache = ValidationResultCache(capacity=4, ttl_seconds=60, clock=clock)
    cache.put("config", "", repository=REPOSITORY)

    clock.advance(59)

    assert cache.get("config", repository=REPOSITORY) == ""


def test_a_verdict_older_than_the_ttl_is_not_returned() -> None:
    """Nothing the memo answers with can be older than the TTL.

    The GRR is bind-mounted into the container and grr-sync rewrites those
    directories from GitHub while the server runs, so repository content is
    not fixed for the life of the process even though a live process does not
    currently re-read it. The TTL is what makes the memo's staleness a stated
    number rather than a consequence of how gain happens to cache today.
    """
    clock = FakeClock()
    cache = ValidationResultCache(capacity=4, ttl_seconds=60, clock=clock)
    cache.put("config", "", repository=REPOSITORY)

    clock.advance(61)

    assert cache.get("config", repository=REPOSITORY) is None


def test_an_expired_verdict_stops_occupying_the_bound() -> None:
    """An entry past its TTL is dropped, not merely refused.

    Left in place it would hold a slot in a bounded memo while being unable
    to answer anything -- capacity spent on entries that can only miss.
    """
    clock = FakeClock()
    cache = ValidationResultCache(capacity=4, ttl_seconds=60, clock=clock)
    cache.put("config", "", repository=REPOSITORY)

    clock.advance(61)
    cache.get("config", repository=REPOSITORY)

    assert len(cache) == 0


def test_a_verdict_is_not_returned_for_a_different_repository() -> None:
    """A verdict is about a config *and* a GRR, so it travels with both.

    "This config builds" is not a property of the text alone -- it is what
    the text means against the resources the server can see. Answering a
    caller holding a different repository would be answering a question that
    was never asked.

    A bare object stands in for the repository: the memo compares generations
    by identity and knows nothing else about them, so anything else in the
    test would be describing a coupling that does not exist.
    """
    cache = ValidationResultCache(capacity=4, ttl_seconds=60)
    first_repository, second_repository = a_repository(), a_repository()
    cache.put("config", "", repository=first_repository)

    assert cache.get("config", repository=second_repository) is None


def test_a_repository_change_empties_the_memo() -> None:
    """The old generation's verdicts are dropped, not merely skipped past.

    Skipping them would leave a memo full of entries no caller can ever be
    answered from -- the whole bound spent on a repository that is gone.
    """
    cache = ValidationResultCache(capacity=4, ttl_seconds=60)
    first_repository, second_repository = a_repository(), a_repository()
    cache.put("a", "", repository=first_repository)
    cache.put("b", "", repository=first_repository)

    cache.get("a", repository=second_repository)

    assert len(cache) == 0


def test_the_new_repository_is_then_the_one_remembered_against() -> None:
    """After a change the memo works normally against the new generation.

    A memo that cleared on every call *after* the first repository change
    would pass the three tests above and still cache nothing from then on.
    """
    cache = ValidationResultCache(capacity=4, ttl_seconds=60)
    first_repository, second_repository = a_repository(), a_repository()
    cache.put("config", "", repository=first_repository)
    cache.get("config", repository=second_repository)

    cache.put("config", "", repository=second_repository)

    assert cache.get("config", repository=second_repository) == ""


def test_the_key_is_a_digest_not_the_config_text() -> None:
    """Keys are digests, so an entry is the same small size whatever arrives.

    The endpoint is anonymous: the text is chosen by the caller and bounded
    only by ``MAX_CONFIG_LENGTH`` (64 KiB). Keying on the text itself would
    make a bounded *count* a 16 MB bound on memory; keying on a digest makes
    it a few kilobytes.
    """
    config = "- position_score: scores/pos1"

    key = ValidationResultCache.key(config)

    assert config not in key
    assert len(key) == 64
    assert key == ValidationResultCache.key(config)
    assert key != ValidationResultCache.key(config + "\n")


def test_an_enormous_verdict_is_not_stored() -> None:
    """The bound on entries is only a bound on memory if entries are small.

    The key is a digest and so is fixed-size, but the *value* is the endpoint's
    ``errors`` string, and that is not: the annotator-configuration message
    echoes the resource id back, so a 60 KB config the caller chose produces a
    60 KB verdict. At capacity that would be ~15 MB of attacker-chosen text
    held for the TTL -- bounded, but three orders of magnitude past what an
    entry is supposed to cost.

    Refusing to store it costs the caller nothing: the response is rendered
    from the verdict either way, so an oversized one is simply rebuilt next
    time rather than remembered.
    """
    cache = ValidationResultCache(capacity=4, ttl_seconds=60)
    enormous = "e" * (ValidationResultCache.MAX_VERDICT_LENGTH + 1)

    cache.put("config", enormous, repository=REPOSITORY)

    assert cache.get("config", repository=REPOSITORY) is None
    assert len(cache) == 0


def test_a_verdict_at_the_size_limit_is_still_stored() -> None:
    """The limit must leave room for every message the endpoint really emits.

    Without this the refusal above could be satisfied by storing nothing at
    all, and the memo would quietly do nothing for the failing configs it
    exists to make cheap.
    """
    cache = ValidationResultCache(capacity=4, ttl_seconds=60)
    at_limit = "e" * ValidationResultCache.MAX_VERDICT_LENGTH

    cache.put("config", at_limit, repository=REPOSITORY)

    assert cache.get("config", repository=REPOSITORY) == at_limit


def test_the_digest_is_defined_for_text_utf_8_cannot_encode() -> None:
    """Keying must be total: not every ``str`` is UTF-8-encodable.

    ``json.loads`` accepts a lone surrogate and DRF's JSONParser passes it
    through, so a two-byte ASCII body can put one in ``request.data``. The
    digest is the first thing on the request path that encodes the config,
    and a plain ``encode("utf-8")`` raises on it -- an anonymous 500 for a
    config the endpoint used to answer 200.
    """
    assert len(ValidationResultCache.key("\ud800")) == 64


def test_the_digest_does_not_collide_on_unencodable_text() -> None:
    """Being total must not be bought with a lossy encode.

    ``errors="replace"`` or ``"ignore"`` would also stop the raise, and both
    map distinct texts onto one digest -- so the memo would answer one config
    with another config's verdict, silently, both of them 200s. A memo may
    miss; it may never answer the wrong question.
    """
    keys = {
        ValidationResultCache.key(text)
        for text in ("\ud800", "\ud801", "\ud800\ud801", "?", "??", "")
    }

    assert len(keys) == 6
