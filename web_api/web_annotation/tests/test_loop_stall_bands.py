"""Guards on the event-loop-stall detection bands themselves (#454).

Five proofs across ``web_annotation/tests`` all work the same way: inject a
slow pipeline build, watch a heartbeat (or lag) coroutine, and fail if the
event loop ever went quiet for too long. They are

* ``test_editor_read_async.test_concurrent_slow_status_builds_...``
* ``test_editor_write_async.test_concurrent_slow_aggregator_posts_...``
* ``test_pipelines_doc_async.test_concurrent_slow_doc_builds_...``
* ``test_single_annotation_async.test_concurrent_slow_builds_...``
* ``test_ws_notification_responsiveness.test_event_loop_not_parked_...``

Whether those proofs are trustworthy depends entirely on the two numbers they
use -- the injected build latency (the *signal*) and the stall threshold (the
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
    SABOTAGED_STALL_FLOOR_SECONDS,
    SLOW_BUILD_SECONDS,
    STALL_THRESHOLD_SECONDS,
    WORST_OBSERVED_HEALTHY_GAP_SECONDS,
    loop_parked,
)

# Every value here was measured on a real run and is cited in #433 or #454.
# Keep the provenance string -- it is what lets a future reader tell a
# measurement from a guess. The two extremes reference the constants rather
# than repeating them, so updating a recorded measurement cannot leave a stale
# copy behind here.
RECORDED_MEASUREMENTS = [
    (0.02, False, "healthy, idle local machine (#433)"),
    (0.03, False, "healthy, idle local machine, slowest sample (#433)"),
    (0.533, False, "healthy, loaded CI agent -- the flake #433 was filed for"),
    (WORST_OBSERVED_HEALTHY_GAP_SECONDS, False,
     "healthy, loaded CI agent -- the #454 flake, iss470 build #2"),
    (BROKEN_ON_LOOP_GAP_SECONDS, True,
     "broken: build resolved ON the loop thread (#433)"),
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

    The two required factors differ on purpose, and neither is a target to
    tune towards:

    * the noise side is the genuinely tight one (currently ~1.52x, since the
      worst healthy gap on record keeps climbing: 0.533s in #433, 0.659s in
      #454). 1.25x is the point at which the bands are close enough to call
      touching -- i.e. a recorded healthy gap above 0.8s. Reaching it means
      raise SLOW_BUILD_SECONDS, never nudge this factor down;
    * the signal side is comfortable (~2.07x) because the injected latency is
      ours to choose, so 1.5x costs nothing to require.
    """
    noise_margin = STALL_THRESHOLD_SECONDS / WORST_OBSERVED_HEALTHY_GAP_SECONDS
    signal_margin = BROKEN_ON_LOOP_GAP_SECONDS / STALL_THRESHOLD_SECONDS

    assert noise_margin >= 1.25, (
        f"only {noise_margin:.2f}x margin over the worst healthy gap "
        f"({WORST_OBSERVED_HEALTHY_GAP_SECONDS:.3f}s vs a "
        f"{STALL_THRESHOLD_SECONDS:.3f}s bound); the bands are touching -- "
        f"raise SLOW_BUILD_SECONDS and lift the bound with it"
    )
    assert signal_margin >= 1.5, (
        f"only {signal_margin:.2f}x headroom below a real park; "
        f"a partially-parked loop would slip through"
    )


def test_sabotage_floor_sits_between_the_bound_and_the_injected_latency(
) -> None:
    """The sabotage self-tests assert something the real proofs would fail.

    ``SABOTAGED_STALL_FLOOR_SECONDS`` has to clear ``STALL_THRESHOLD_SECONDS``
    -- otherwise a sabotaged run could satisfy the self-test while staying
    under the bound the real proof asserts against, and the self-test would no
    longer prove that proof can fail. It also has to stay below the injected
    latency, or no real sabotage could ever reach it.
    """
    assert STALL_THRESHOLD_SECONDS < SABOTAGED_STALL_FLOOR_SECONDS, (
        "a sabotaged run could clear the self-test without breaching the "
        "bound the real proof uses -- the self-test would prove nothing"
    )
    assert SABOTAGED_STALL_FLOOR_SECONDS < SLOW_BUILD_SECONDS, (
        "no sabotage can park the loop longer than the injected latency, so "
        "the self-test would be unsatisfiable"
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
