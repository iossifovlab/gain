# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Backend selection and ONNX Runtime backend behaviour (issue #297)."""
from collections.abc import Iterator
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest
from spliceai_annotator.utils import one_hot_encode

from spliceai_annotator import spliceai_annotator_impl as impl

# The narrowest window the CNN accepts: it crops 10000 positions, so
# `distance=0` gives 10000 + 2*0 + 1 in and a single position out.
MIN_WIDTH = 10001
# The widest window the annotator ever builds: `_batch_width` at the maximum
# distance -- 10000 + 2*5000 + 1 + DEFAULT_MAX_INSERTION_LENGTH.
MAX_WIDTH = 20201


@pytest.fixture(scope="module")
def onnx_backend() -> ModuleType:
    return impl.load_spliceai_backend("onnx")


@pytest.fixture(scope="module")
def onnx_models(onnx_backend: ModuleType) -> Iterator[list]:
    models = onnx_backend.spliceai_load_models()
    yield models
    onnx_backend.spliceai_close()


@pytest.fixture(scope="module")
def tensorflow_backend() -> ModuleType:
    # Loaded explicitly rather than through `impl.SPLICEAI_MODELS`, which
    # holds whichever backend the *process* selected -- these tests compare
    # the two and must not collapse to one when SPLICEAI_BACKEND=onnx.
    pytest.importorskip("tensorflow")
    return impl.load_spliceai_backend("tensorflow")


@pytest.fixture(scope="module")
def tensorflow_models(tensorflow_backend: ModuleType) -> Iterator[list]:
    models = tensorflow_backend.spliceai_load_models()
    yield models
    tensorflow_backend.spliceai_close()


def a_one_hot_window(width: int, *, batch: int = 1) -> np.ndarray:
    """A one-hot window built the way the annotator builds it."""
    rng = np.random.default_rng(seed=width)
    windows = [
        one_hot_encode("".join(rng.choice(list("ACGT"), size=width)))[None, :]
        for _ in range(batch)
    ]
    return np.concatenate(windows, axis=0)


def test_default_backend_is_tensorflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(impl.SPLICEAI_BACKEND_ENV, raising=False)
    assert impl.spliceai_backend_name() == "tensorflow"


@pytest.mark.parametrize("value", ["onnx", "ONNX", " onnx "])
def test_env_selects_the_onnx_backend(
    monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    monkeypatch.setenv(impl.SPLICEAI_BACKEND_ENV, value)
    assert impl.spliceai_backend_name() == "onnx"


def test_unknown_backend_is_rejected_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(impl.SPLICEAI_BACKEND_ENV, "torch")
    with pytest.raises(ValueError, match="torch"):
        impl.spliceai_backend_name()


@pytest.mark.parametrize("name", ["tensorflow", "onnx"])
def test_backend_provides_the_model_runtime_interface(name: str) -> None:
    backend = impl.load_spliceai_backend(name)
    assert callable(backend.spliceai_load_models)
    assert callable(backend.spliceai_predict)
    assert callable(backend.spliceai_close)


def test_onnx_predicts_from_the_annotators_int8_one_hot(
    onnx_backend: ModuleType, onnx_models: list,
) -> None:
    """ONNX Runtime rejects int8 outright where TensorFlow casts silently.

    `one_hot_encode` returns int8 deliberately (the memory-cheap choice for a
    20,201 x 4 array), so the backend -- not the encoder -- owns the cast.
    """
    x = a_one_hot_window(MIN_WIDTH)
    assert x.dtype == np.int8, "premise: the annotator feeds an int8 one-hot"

    y = onnx_backend.spliceai_predict(onnx_models, x)

    # (batch, width - 10000, 3): P(neither) / P(acceptor) / P(donor).
    assert y.shape == (1, MIN_WIDTH - 10000, 3)
    np.testing.assert_allclose(y.sum(axis=-1), 1.0, atol=1e-5)


def test_onnx_sessions_carry_the_measured_performance_settings(
    onnx_models: list,
) -> None:
    """These three are load-bearing, not tuning -- see #297.

    Without them ONNX Runtime takes roughly 1.4x as long on this ensemble
    (measured; see `spliceai_session_options`). Each one looks like premature
    micro-optimisation to a reader who has not measured it, so pin them: a
    silent revert would be a performance regression no output assertion can
    see.
    """
    ort = pytest.importorskip("onnxruntime")

    for session in onnx_models:
        options = session.get_session_options()
        assert options.get_session_config_entry(
            "session.intra_op.allow_spinning") == "0"
        assert options.graph_optimization_level == (
            ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED)
        assert options.intra_op_num_threads == 4


@pytest.mark.parametrize(
    ("width", "batch"),
    [
        # distance=0, the narrowest window.
        (MIN_WIDTH, 1),
        # distance=5000 + max_insertion_length: the widest window the
        # annotator builds, and the one the ONNX study never measured.
        (MAX_WIDTH, 1),
        # The batch path concatenates windows into a >1 batch axis; the
        # converted graph keeps that axis dynamic too.
        (MAX_WIDTH, 3),
    ],
    ids=["distance-0", "distance-5000-plus-insertion", "batched"],
)
def test_onnx_matches_tensorflow_on_the_annotators_windows(
    width: int,
    batch: int,
    onnx_backend: ModuleType,
    onnx_models: list,
    tensorflow_backend: ModuleType,
    tensorflow_models: list,
) -> None:
    """The two backends must agree far tighter than the output's resolution.

    Measured max-abs disagreement: 3.4e-13 at width 10001, 2.4e-7 at 20201,
    3.3e-7 batched -- against a `delta_score` reported at two decimals. 1e-5
    keeps a real bound (it is the differential harness's own DS_* tier)
    without pinning float noise.

    How much this bound proves depends on the window. At 20201 the ensemble
    is not saturated and 1e-5 is a sharp test: dropping one of the five
    models moves the output by ~2e-2, three orders above the tolerance. At
    10001 a *random* window saturates to P(neither) ~ 1 and the whole signal
    is below 1e-5, so this assertion is weak there -- that gap is covered by
    `test_onnx_ensemble_is_the_full_five_models` below, not by this bound.
    """
    x = a_one_hot_window(width, batch=batch)

    y_onnx = onnx_backend.spliceai_predict(onnx_models, x)
    y_tensorflow = tensorflow_backend.spliceai_predict(tensorflow_models, x)

    assert y_onnx.shape == (batch, width - 10000, 3)
    assert y_onnx.shape == y_tensorflow.shape
    np.testing.assert_allclose(y_onnx, y_tensorflow, atol=1e-5)


class _RecordingSession:
    """Stand-in for an `ort.InferenceSession` that records what it is fed.

    Returns a per-row constant taken from the input, so a chunking bug that
    reorders, drops or duplicates rows shows up in the output.
    """

    def __init__(self, batch_sizes: list[int], scale: float) -> None:
        self.batch_sizes = batch_sizes
        self.scale = scale

    def get_inputs(self) -> list:
        return [SimpleNamespace(name="input")]

    def run(self, _outputs: object, feed: dict) -> list[np.ndarray]:
        x = feed["input"]
        self.batch_sizes.append(x.shape[0])
        row_id = x[:, 0, 0].astype(np.float32)
        out = np.zeros((x.shape[0], x.shape[1] - 10000, 3), np.float32)
        out[:, :, 0] = (row_id * self.scale)[:, None]
        return [out]


def test_onnx_never_hands_ort_an_unbounded_batch(
    onnx_backend: ModuleType, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Peak memory must not scale with the caller's batch size.

    `session.run` evaluates the whole batch axis in one graph invocation,
    where `keras.Model.predict` chunks internally. Unchunked, an ONNX pass
    costs ~55 MB per window at the default distance and ~109 MB at the widest
    -- and `annotate_vcf` hands the annotator batches of 500.
    """
    monkeypatch.setattr(
        onnx_backend, "ONNX_POSITION_BUDGET", 4 * (MIN_WIDTH + 1))
    batch_sizes: list[int] = []
    models = [_RecordingSession(batch_sizes, scale) for scale in range(1, 6)]
    x = np.zeros((10, MIN_WIDTH, 4), np.int8)
    x[:, 0, 0] = np.arange(10, dtype=np.int8)

    y = onnx_backend.spliceai_predict(models, x)

    assert max(batch_sizes) <= 4, (
        f"ORT was handed a batch of {max(batch_sizes)} rows at width "
        f"{MIN_WIDTH}; the budget allows 4")
    # Rows must survive chunking in order, unduplicated: each row's value is
    # its own index times the mean of the five models' scales.
    np.testing.assert_allclose(
        y[:, 0, 0], np.arange(10) * np.mean(range(1, 6)))


def test_onnx_chunking_does_not_change_results(
    onnx_backend: ModuleType,
    onnx_models: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chunking is a memory device, not a numerical one.

    Rows of the batch axis are independent, so splitting them across `run`
    calls must reproduce the single-invocation result exactly -- including
    when the batch is not a multiple of the chunk size (5 rows, 2 per run).
    """
    x = a_one_hot_window(MIN_WIDTH, batch=5)

    monkeypatch.setattr(
        onnx_backend, "ONNX_POSITION_BUDGET", 1024 * MIN_WIDTH)
    unchunked = onnx_backend.spliceai_predict(onnx_models, x)
    monkeypatch.setattr(
        onnx_backend, "ONNX_POSITION_BUDGET", 2 * MIN_WIDTH)
    chunked = onnx_backend.spliceai_predict(onnx_models, x)

    assert chunked.shape == unchunked.shape == (5, 1, 3)
    np.testing.assert_array_equal(chunked, unchunked)


def test_onnx_ensemble_is_the_full_five_models(
    onnx_backend: ModuleType,
    onnx_models: list,
    tensorflow_backend: ModuleType,
    tensorflow_models: list,
) -> None:
    """Pin that the ONNX backend averages all five models, not a subset.

    On a random narrow window every ensemble member saturates to
    ~P(neither)=1 and the members agree with each other to well within the
    equivalence bound above -- so that bound alone stays green for a backend
    that silently ran four models, or one. (The sibling
    `test_onnx_equivalence.py` documents the same trap for the .h5 -> .onnx
    conversion and defends against it the same way.)

    What separates them is precision: the full ONNX ensemble reproduces the
    full TensorFlow ensemble to float-roundtrip accuracy, orders of magnitude
    tighter than the residual left by dropping any one member. Measured
    margin here is ~1.7e5x; 100x is asserted.
    """
    x = a_one_hot_window(MIN_WIDTH)
    reference = tensorflow_backend.spliceai_predict(tensorflow_models, x)
    own = np.max(np.abs(
        onnx_backend.spliceai_predict(onnx_models, x) - reference))

    for dropped in range(len(onnx_models)):
        subset = [m for i, m in enumerate(onnx_models) if i != dropped]
        degraded = np.max(np.abs(
            onnx_backend.spliceai_predict(subset, x) - reference))
        assert own < degraded / 100, (
            f"dropping model {dropped} moves the output by only {degraded:.2e} "
            f"against the full ensemble's {own:.2e} -- the equivalence check "
            f"cannot tell a degraded ensemble from the real one")
