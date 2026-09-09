"""Tests for the #1207 ONNX contention measurement harness.

The harness exists to answer one question -- does the ONNX backend's
multi-threaded intra-op pool return different answers under load -- and the
way it could fail is by answering "no" for the wrong reason: a summariser
that never notices a difference, or workers that never actually overlap and
so never contend. Both would look exactly like the reassuring result.

These tests pin the parts that can produce that false negative. They use
trivial payloads and synthetic arrays; nothing here loads a model.
"""
import importlib.util
import itertools
import multiprocessing as mp
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

#: Hands each worker of `_staggered_payload` a distinct index. Shared rather
#: than derived from the pid so the stagger is predictable.
_SETUP_INDEX = mp.Value("i", 0)

SCRIPT = Path(__file__).parent.parent / "scripts" / "measure_onnx_contention.py"


def _load_harness() -> ModuleType:
    """Import the harness by path -- ``scripts/`` is not an importable pkg."""
    spec = importlib.util.spec_from_file_location(
        "measure_onnx_contention", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered so the multiprocessing workers can pickle references to it.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


harness = _load_harness()


def test_identical_outputs_report_no_drift() -> None:
    outputs = [np.full((2, 3), 0.25, dtype=np.float32) for _ in range(4)]

    summary = harness.summarize_drift(outputs)

    assert summary.distinct == 1
    assert summary.max_abs_deviation == pytest.approx(0.0)


def test_outputs_differing_by_a_probability_report_that_drift() -> None:
    """3e-4 is the deviation #1206 measured; the summariser must see it.

    This is the test that fails a summariser which always reports agreement
    -- the failure mode that would turn the whole measurement into a
    reassuring false negative.
    """
    outputs = [np.full((2, 3), 0.25, dtype=np.float32) for _ in range(4)]
    drifted = outputs[-1].copy()
    drifted[1, 2] += 3e-4
    outputs[-1] = drifted

    summary = harness.summarize_drift(outputs)

    assert summary.distinct == 2
    assert summary.max_abs_deviation == pytest.approx(3e-4, rel=1e-3)


def test_reference_digest_identifies_the_answer_across_runs() -> None:
    """Two configurations agreeing within themselves may still disagree.

    Each run only ever compares its own outputs, so a thread count that is
    perfectly reproducible could still return a *different* answer than the
    shipped one -- which would mean changing the default moves published
    scores. The digest is what makes that comparable between runs.
    """
    answer = [np.full((2, 3), 0.25, dtype=np.float32)]
    same_answer = [np.full((2, 3), 0.25, dtype=np.float32)]
    other_answer = [np.full((2, 3), 0.25 + 3e-4, dtype=np.float32)]

    digest = harness.summarize_drift(answer).reference_digest

    assert harness.summarize_drift(same_answer).reference_digest == digest
    assert harness.summarize_drift(other_answer).reference_digest != digest


def test_reducing_to_distinct_answers_preserves_the_summary() -> None:
    """The child sends distinct answers, not every array; both must agree.

    Workers deduplicate before shipping results, so the parent summarises a
    reduced population. If that reduction changed any reported number, every
    measurement would be quietly wrong -- so it is pinned against summarising
    the full population directly.
    """
    outputs = [np.full((2, 3), 0.25, dtype=np.float32) for _ in range(5)]
    drifted = outputs[0].copy()
    drifted[1, 2] += 3e-4
    outputs[3] = drifted

    full = harness.summarize_drift(outputs)
    reduced = harness.summarize_samples(
        harness.distinct_answers(outputs), passes=len(outputs))

    assert reduced == full
    assert full.distinct == 2
    assert full.passes == 5


@dataclass
class _StubOptions:
    """Just enough of ``ort.SessionOptions`` to see the flag get set."""

    intra_op_num_threads: int = 4
    use_deterministic_compute: bool = False


@dataclass
class _StubBackend:
    """A stand-in for the ONNX backend module. Loads no model."""

    spliceai_session_options: Callable[[], _StubOptions] = _StubOptions


def test_requesting_determinism_reaches_the_session_options() -> None:
    """The deterministic arm must actually set the flag it is named for.

    It reaches the sessions by wrapping the backend's options factory, which
    works only because the shipped loader looks that factory up as a module
    global. If that ever stops being true the arm quietly becomes a second
    copy of the default one and reports "determinism changed nothing" -- so
    the wrapping itself is pinned here, without loading a model.
    """
    backend = _StubBackend()

    harness.OnnxEnsemblePayload._request_deterministic_compute(backend)

    assert backend.spliceai_session_options().use_deterministic_compute


def test_sessions_that_do_not_match_the_arm_are_refused() -> None:
    """A session not carrying the requested arm must stop the run.

    Reporting a row labelled "deterministic" that was measured without the
    flag is worse than not measuring it at all.
    """
    class _Model:
        @staticmethod
        def get_session_options() -> _StubOptions:
            return _StubOptions()

    harness.assert_sessions_configured(
        [_Model()], threads=4, deterministic=False)

    with pytest.raises(RuntimeError, match="use_deterministic_compute"):
        harness.assert_sessions_configured(
            [_Model()], threads=4, deterministic=True)


def test_summarising_nothing_is_refused() -> None:
    """An empty population must not come back looking like agreement."""
    with pytest.raises(ValueError, match="no outputs"):
        harness.summarize_drift([])


def test_workers_running_at_the_same_time_are_reported_as_concurrent() -> None:
    spans = [(0.0, 10.0), (1.0, 11.0), (2.0, 12.0)]

    summary = harness.summarize_overlap(spans)

    assert summary.max_concurrent == 3


def test_workers_that_took_turns_are_not_reported_as_concurrent() -> None:
    """Back-to-back spans touch but never coexist -- no contention happened.

    A run like this produces no drift because nothing contended, which must
    not read as evidence that the configuration is reproducible.
    """
    spans = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]

    summary = harness.summarize_overlap(spans)

    assert summary.max_concurrent == 1


#: Setup cost of the slow payloads, an order of magnitude above the per-pass
#: cost so the two are impossible to confuse in a throughput figure -- and no
#: larger, since every one of these seconds is paid on every CI run.
SETUP_SECONDS = 0.2
PASS_SECONDS = 0.02


def _fixed_pass() -> np.ndarray:
    """One pass that always returns the same answer."""
    time.sleep(PASS_SECONDS)
    return np.full((2, 2), 0.5, dtype=np.float32)


def _constant_payload() -> Callable[[], np.ndarray]:
    """A payload factory whose every pass returns the same answer."""
    return _fixed_pass


def _slow_setup_payload() -> Callable[[], np.ndarray]:
    """Expensive to build, cheap to run -- like opening five ONNX sessions."""
    time.sleep(SETUP_SECONDS)
    return _fixed_pass


def test_throughput_excludes_the_setup_the_barrier_waits_for() -> None:
    """passes/s must describe inference, not model loading.

    Opening five ONNX sessions dwarfs a pass, and it costs a different amount
    at each thread count. Charging it to the throughput figure would compare
    the arms on how fast they *load*, which is not the question -- and the
    error is invisible, because a plausible number still comes out.
    """
    workers, passes = 2, 2

    result = harness.run_probe(_slow_setup_payload, workers=workers,
                               passes=passes)

    setup_inclusive_rate = workers * passes / (SETUP_SECONDS + PASS_SECONDS)
    assert result.passes_per_second > setup_inclusive_rate * 2
    # The setup is still real time the run took, and stays visible as such.
    assert result.wall_seconds > SETUP_SECONDS


def _staggered_payload() -> Callable[[], np.ndarray]:
    """Each worker takes a different, large amount of time to get ready.

    Workers claim an index from a shared counter, so worker *i* is ready
    `i * SETUP_SECONDS` after the others. Without a barrier their timed
    windows cannot overlap; with one they all start together.
    """
    with _SETUP_INDEX.get_lock():
        index = _SETUP_INDEX.value
        _SETUP_INDEX.value += 1
    time.sleep(index * SETUP_SECONDS)
    return _fixed_pass


def test_probe_runs_every_worker_concurrently() -> None:
    """The barrier must make the workers coexist, or nothing contends.

    The workers here become ready at wildly different times, so the timed
    windows only overlap if something holds the early ones back. With equal
    setup costs this test would pass whether or not the barrier existed --
    and the harness's whole claim to be measuring contention rests on it.
    """
    result = harness.run_probe(_staggered_payload, workers=3, passes=2)

    assert result.drift.passes == 6
    assert result.overlap.max_concurrent == 3
    assert result.drift.distinct == 1


def _drifting_payload() -> Callable[[], np.ndarray]:
    """A payload whose successive passes deliberately disagree by 3e-4."""
    counter = itertools.count()

    def run_once() -> np.ndarray:
        step = next(counter)
        return np.full((2, 2), 0.5 + step * 3e-4, dtype=np.float32)
    return run_once


def test_probe_surfaces_drift_produced_inside_the_workers() -> None:
    """A disagreeing payload must come back as drift, across process bounds.

    Pooling outputs from several processes is where a real difference could
    quietly be dropped -- by comparing only within a worker, or by keeping
    just the last answer. Then every future run would report agreement no
    matter what ONNX Runtime did.
    """
    result = harness.run_probe(_drifting_payload, workers=3, passes=2)

    assert result.drift.passes == 6
    assert result.drift.distinct == 2
    assert result.drift.max_abs_deviation == pytest.approx(3e-4, rel=1e-3)


def test_measuring_refuses_a_parent_that_already_loaded_onnx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sessions must be opened per worker, never inherited through fork.

    If the parent has imported ONNX Runtime, every child starts holding a
    copy of its state and thread pools: Python warns that forking a
    multi-threaded process can deadlock, and the run would no longer measure
    workers that each opened their own ensemble -- which is the shape being
    measured. Refusing is the only safe answer, because the corrupted run
    still produces plausible-looking numbers.
    """
    monkeypatch.setitem(sys.modules, "onnxruntime", ModuleType("onnxruntime"))

    with pytest.raises(RuntimeError, match="onnxruntime"):
        harness.refuse_to_measure_with_onnxruntime_loaded()


def test_the_engine_itself_runs_with_onnx_imported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard must not fire inside this package's own test suite.

    Collecting `tests/` imports the annotator, which imports the backend,
    which imports ONNX Runtime -- before any test runs. A guard inside
    `run_probe` therefore fails these tests in the ONNX CI tier, which runs
    the whole suite with SPLICEAI_BACKEND=onnx and propagates pytest's exit
    code. The guard belongs at the measurement entry point, not in the
    reusable engine.
    """
    monkeypatch.setitem(sys.modules, "onnxruntime", ModuleType("onnxruntime"))

    result = harness.run_probe(_constant_payload, workers=2, passes=1)

    assert result.drift.passes == 2
