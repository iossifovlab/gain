"""Shared bands and helpers for the event-loop-stall proofs (#454).

Several tests across ``web_annotation/tests`` prove the same property: a slow
pipeline build must be awaited *off* the event loop, so the loop stays
responsive while the build runs on the loader thread. They all do it the same
way -- inject a known build latency, tick a cheap heartbeat coroutine, and
fail if the loop ever went quiet for longer than a bound.

Two numbers decide whether such a proof is trustworthy:

``SLOW_BUILD_SECONDS``
    The injected build latency -- the *signal*. If the build wait leaks onto
    the loop, the loop parks for about this long.

``STALL_THRESHOLD_SECONDS``
    The longest quiet period tolerated -- the *bound*. Must sit strictly
    between host noise and the signal.

They are deliberately **different numbers**. Setting the bound equal to the
injected latency is the defect fixed in #433 and again in #454: the healthy
and broken bands then overlap, because a loaded CI agent's scheduler noise
(measured up to 0.659 s) can exceed a small injected latency (0.4 s), while a
genuinely broken run at that latency measured only 0.461 s. Signal below
noise -- no choice of a single shared constant can separate them. Enlarging
the injected latency to 2.0 s is what pulls the bands apart; the bound then
has somewhere to sit.

Measured gaps, all from #433/#454:

========================================  ============
run                                       worst gap
========================================  ============
healthy, idle local machine               ~0.02-0.03 s
healthy, loaded CI agent (worst on file)  0.659 s
broken, build resolved ON the loop        2.07 s
========================================  ============

``test_loop_stall_bands.py`` asserts these orderings and margins hold, so a
future edit that collapses the bands fails a test instead of quietly making
every stall proof vacuous.

**The broken case stalls for ONE build's duration, not N x it**, even though
the proofs fire N=4 concurrent requests. ``LRUPipelineCache.put_pipeline``
dedupes concurrent readers: the first caller installs the ``LoadingDetails``
future and the rest take the ``same_config`` branch and await that *shared*
future (the contract documented on ``BuildCancelled``, #140/#163). So raising
the bound on the theory that a regression costs ``N x SLOW_BUILD_SECONDS``
would be wrong -- it would put the bound above the signal and make every proof
permanently green. Enlarging ``SLOW_BUILD_SECONDS`` is what separates the
bands; raising ``STALL_THRESHOLD_SECONDS`` past it is what destroys them.

Note this module is deliberately *not* named ``test_*``: it holds shared
helpers, not tests of its own.
"""

# Latency injected into a single pipeline build, in seconds. Large enough that
# a leaked on-loop wait is unmistakable against host noise. Raising this is
# the lever that separates the bands -- see the module docstring.
SLOW_BUILD_SECONDS = 2.0

# Longest single quiet period the stall proofs tolerate, in seconds.
# Deliberately NOT SLOW_BUILD_SECONDS: ~1.5x above the worst healthy gap ever
# observed and ~2x below a real park.
STALL_THRESHOLD_SECONDS = 1.0

# How often the heartbeat coroutines tick. Also the floor on gap resolution:
# a healthy run's gaps are ~this value.
HEARTBEAT_INTERVAL_SECONDS = 0.02

# Worst gap ever observed on a *healthy* run: 0.659 s, on
# ``iossifovlab/gain/iss470-fragment-score-rename`` build #2 (#454), a branch
# touching no ``web_api`` file. The bound must stay clear of this.
WORST_OBSERVED_HEALTHY_GAP_SECONDS = 0.659

# Gap measured on a *broken* run with SLOW_BUILD_SECONDS injected: the loop
# parked for one full build (#433). Used to check the bound keeps headroom.
BROKEN_ON_LOOP_GAP_SECONDS = 2.07

# What a *deliberately sabotaged* run must reach, for the self-tests that
# prove a stall proof can actually fail. A sabotaged loop parks for a whole
# build (measured 2.04-2.05s), so requiring 80% of the injected latency is
# comfortably met while staying clear of the full value -- demanding the whole
# 2.0s would be its own coin flip, since timer granularity and a marginally
# short sleep land just under it. Stays above STALL_THRESHOLD_SECONDS, so a
# sabotaged run provably breaches the bound the real proofs assert against.
SABOTAGED_STALL_FLOOR_SECONDS = 0.8 * SLOW_BUILD_SECONDS


def loop_parked(samples: list[float], bound: float) -> bool:
    """Whether any sample reached the parked-loop magnitude.

    ``samples`` are either raw heartbeat gaps or measured loop lag -- the test
    that produced them decides which. Either way a parked loop shows up as a
    single outsized sample, so this is a max, never an average: averaging a
    2 s park across 200 healthy ticks would hide it completely.

    At/above ``bound`` counts as parked -- zero tolerance, no "allow k bad
    samples" slack. That is safe because the bound sits above every healthy
    value ever recorded here: lag samples run ~0.0002-0.001 s and heartbeat
    gaps ~0.02 s on an idle host, with the worst loaded-CI gap on record at
    ``WORST_OBSERVED_HEALTHY_GAP_SECONDS``. The margin over that worst case is
    the narrowest part of the whole scheme (~1.5x, versus ~1000x for lag on an
    idle host), which is why raising ``SLOW_BUILD_SECONDS`` -- not
    ``STALL_THRESHOLD_SECONDS`` -- is the correct response to a recurrence.
    """
    return max(samples, default=0.0) >= bound


def max_gap(timestamps: list[float]) -> float:
    """Longest quiet period between successive heartbeat ``timestamps``.

    Returns 0.0 for fewer than two timestamps -- with nothing to compare there
    is no evidence of a stall, and reporting 0.0 keeps a heartbeat that never
    got to run from being read as a park.

    Note this is the raw wall-clock gap, which includes the heartbeat's own
    sleep interval; it is not lag (excess over that interval). The difference
    is ``HEARTBEAT_INTERVAL_SECONDS``, immaterial against a bound of
    ``STALL_THRESHOLD_SECONDS``, but it is why a healthy gap reads ~0.02 s
    rather than ~0.
    """
    if len(timestamps) < 2:
        return 0.0
    return max(
        timestamps[i + 1] - timestamps[i]
        for i in range(len(timestamps) - 1)
    )
