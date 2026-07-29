# pylint: disable=C0114,C0116
"""Guards on the event-loop-stall detection bands themselves (#454).

The five ``*_do_not_park_event_loop`` proofs across ``web_annotation/tests``
all work the same way: inject a slow pipeline build, watch a heartbeat
coroutine, and fail if the event loop ever went quiet for too long. Whether
those proofs are trustworthy depends entirely on the two numbers they use --
the injected build latency (the *signal*) and the stall threshold (the
*bound*).

Get those two numbers wrong and the proof fails in one of two ways, both of
which have actually happened in this repo:

* threshold too low -- scheduler noise on a loaded CI agent trips it and a
  healthy build goes red (#433, then #454 again);
* threshold at or above the signal -- the proof can never fail and silently
  goes vacuous.

The tests here pin the bands against real recorded measurements so neither
regression can be reintroduced without a test going red.
"""
import pytest

from web_annotation.tests.loop_stall import (
    BROKEN_ON_LOOP_GAP_SECONDS,
    SLOW_BUILD_SECONDS,
    STALL_THRESHOLD_SECONDS,
    WORST_OBSERVED_HEALTHY_GAP_SECONDS,
    loop_parked,
)

# Every value here was measured on a real run and is cited in #433 or #454.
# Keep the provenance string -- it is what lets a future reader tell a
# measurement from a guess.
RECORDED_MEASUREMENTS = [
    (0.02, False, "healthy, idle local machine (#433)"),
    (0.03, False, "healthy, idle local machine, slowest sample (#433)"),
    (0.533, False, "healthy, loaded CI agent -- the flake #433 was filed for"),
    (0.659, False,
     "healthy, loaded CI agent -- the #454 flake, iss470 build #2"),
    (2.07, True, "broken: build resolved ON the loop thread (#433)"),
]


@pytest.mark.parametrize(
    ("gap", "expected_parked", "provenance"), RECORDED_MEASUREMENTS,
)
def test_recorded_gap_is_classified_correctly(
    gap: float, expected_parked: bool, provenance: str,
) -> None:
    """Each measurement recorded in #433/#454 lands on the correct side.

    This is the regression test for #454: with the old threshold of 0.4s (the
    injected sleep reused as its own bound) the 0.533s and 0.659s rows are
    classified as parked -- a false red on a healthy run.
    """
    assert loop_parked([gap], STALL_THRESHOLD_SECONDS) is expected_parked, (
        f"{gap:.3f}s should {'' if expected_parked else 'NOT '}count as a "
        f"parked loop -- {provenance}"
    )


def test_threshold_sits_strictly_between_the_noise_and_signal_bands() -> None:
    """The bound separates healthy noise from a real park, with margin.

    This is the invariant that both #433 and #454 violated: they used the
    injected sleep as the threshold, which collapses the two bands onto the
    same number. Asserting the ordering here means the collapse cannot be
    reintroduced silently -- setting the threshold equal to the injected sleep
    fails this test.
    """
    assert WORST_OBSERVED_HEALTHY_GAP_SECONDS < STALL_THRESHOLD_SECONDS, (
        "threshold must sit above the worst healthy gap ever observed, "
        "otherwise scheduler noise reds the build"
    )
    assert STALL_THRESHOLD_SECONDS < SLOW_BUILD_SECONDS, (
        "threshold must sit below the injected build latency, otherwise a "
        "genuinely parked loop slips under the bound and the proof is vacuous"
    )


def test_bands_keep_a_healthy_margin_on_both_sides() -> None:
    """Ordering alone is not enough -- the gaps must be wide, not hairline.

    #433's measurements put a broken run (0.461s at a 0.4s sleep) *below* a
    healthy loaded-CI run (0.533s): correctly ordered constants would still
    have flaked because the bands touched. Require real headroom on each side.
    """
    noise_margin = STALL_THRESHOLD_SECONDS / WORST_OBSERVED_HEALTHY_GAP_SECONDS
    signal_margin = BROKEN_ON_LOOP_GAP_SECONDS / STALL_THRESHOLD_SECONDS

    assert noise_margin >= 1.5, (
        f"only {noise_margin:.2f}x margin over the worst healthy gap; "
        f"a slower agent will flake"
    )
    assert signal_margin >= 1.5, (
        f"only {signal_margin:.2f}x headroom below a real park; "
        f"a partially-parked loop would slip through"
    )


def test_a_single_parked_sample_is_enough_to_fail() -> None:
    """One bad sample among many healthy ones still reports parked.

    A parked loop shows up as exactly one long gap, not a raised average, so
    the detector must not average or otherwise dilute it.
    """
    healthy = [0.02] * 200

    assert not loop_parked(healthy, STALL_THRESHOLD_SECONDS)
    assert loop_parked(
        [*healthy, BROKEN_ON_LOOP_GAP_SECONDS], STALL_THRESHOLD_SECONDS,
    )


def test_loop_parked_is_inclusive_at_the_bound() -> None:
    """At the bound counts as parked; just under does not."""
    assert loop_parked([STALL_THRESHOLD_SECONDS], STALL_THRESHOLD_SECONDS)
    assert not loop_parked(
        [STALL_THRESHOLD_SECONDS - 0.01], STALL_THRESHOLD_SECONDS,
    )


def test_no_samples_is_not_parked() -> None:
    """An empty sample list must not be read as a stall."""
    assert not loop_parked([], STALL_THRESHOLD_SECONDS)
