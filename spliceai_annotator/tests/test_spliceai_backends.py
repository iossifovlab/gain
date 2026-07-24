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
# `_batch_width` at the maximum distance with the *default* insertion
# length: 10000 + 2*5000 + 1 + DEFAULT_MAX_INSERTION_LENGTH. Not an absolute
# ceiling -- `max_insertion_length` is configurable up to 2000, which would
# make it 22001 -- but it is the widest window a default deployment builds.
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
    #
    # Deliberately not `importorskip`: the cross-backend assertions in this
    # file are the guard on the ONNX migration, and the run in which
    # TensorFlow disappears (#298) is exactly the run in which they must
    # fail loudly rather than turn into silent skips.
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
    # Deliberately no "probabilities sum to 1" assertion: the graph ends in a
    # softmax and a mean of softmax outputs is still a simplex point, so it
    # holds for any correctly-shaped output -- including a broken ensemble.
    # What this test pins is that int8 in does not raise, at the right shape
    # and dtype; equivalence is pinned below.
    assert y.shape == (1, MIN_WIDTH - 10000, 3)
    assert y.dtype == np.float32


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

    Measured max-abs disagreement is ~2.4e-7 at every window the annotator
    actually builds (1.2e-7 at 10101, 2.4e-7 at 10301 -- the default
    distance=50 -- and at 20201), against a `delta_score` reported at two
    decimals. 1e-5 keeps a real bound (it is the differential harness's own
    DS_* tier) without pinning float noise.

    MIN_WIDTH is the exception in both directions, and not because it is
    narrow: `distance=0` yields a *single* output position, which on a random
    sequence saturates to P(neither) ~ 1. Agreement there reads 3.4e-13, but
    so does the entire signal -- the whole 4-of-5 ensemble difference is
    below this tolerance, so at that width this assertion proves little.
    `test_onnx_ensemble_is_the_full_five_models` covers that gap.
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

    The equivalence bound above is not enough on its own: at a *narrow*
    window a random one-hot sequence saturates every ensemble member to
    ~P(neither)=1, the members then agree with each other well inside 1e-5,
    and a backend that silently ran four models -- or one -- stays green.
    (The sibling `test_onnx_equivalence.py` documents the same trap for the
    .h5 -> .onnx conversion and defends against it the same way.)

    Run at MAX_WIDTH deliberately. At MIN_WIDTH the whole 4-of-5 signal is
    5.96e-08 -- exactly half a float32 ulp at 1.0, one representable step --
    so asserting a margin over it would demand the two backends agree to
    *zero* ulps and would fail on a correct ensemble the first time a
    different CPU or ORT build moved the last bit. Here the ensemble is not
    saturated: dropping a member moves the output by ~2.2e-02 against a
    backend disagreement of ~2.4e-07, a margin of ~9e4x. 100x is asserted,
    floored at 2 ulp so the check cannot degenerate into bit-exactness.
    """
    x = a_one_hot_window(MAX_WIDTH)
    reference = tensorflow_backend.spliceai_predict(tensorflow_models, x)
    own = max(
        float(np.max(np.abs(
            onnx_backend.spliceai_predict(onnx_models, x) - reference))),
        2 * float(np.spacing(np.float32(1.0))),
    )

    for dropped in range(len(onnx_models)):
        subset = [m for i, m in enumerate(onnx_models) if i != dropped]
        degraded = np.max(np.abs(
            onnx_backend.spliceai_predict(subset, x) - reference))
        assert own < degraded / 100, (
            f"dropping model {dropped} moves the output by only {degraded:.2e} "
            f"against the full ensemble's {own:.2e} -- the equivalence check "
            f"cannot tell a degraded ensemble from the real one")


# A single ORT invocation costs a steady ~5.4 KB per sequence position, so
# this ceiling is what "the shipped budget bounds memory" means in practice:
# ~0.6 GB, the plateau the TensorFlow backend reaches at any batch size.
MAX_POSITIONS_PER_RUN = 100_000


@pytest.mark.parametrize("width", [MIN_WIDTH, MAX_WIDTH])
def test_shipped_budget_bounds_what_ort_is_asked_to_do(
    onnx_backend: ModuleType, width: int,
) -> None:
    """Exercise `ONNX_POSITION_BUDGET` itself, not a patched stand-in.

    The tests around this one patch the budget to drive the chunking
    mechanism. That leaves the shipped *value* -- the entire substance of the
    memory fix -- unasserted: raising it back to something unbounded restores
    the original defect with the whole suite still green. This test reads the
    module constant.
    """
    assert onnx_backend.ONNX_POSITION_BUDGET // width >= 1, (
        f"budget {onnx_backend.ONNX_POSITION_BUDGET} cannot fit even one "
        f"window of width {width}")
    batch_sizes: list[int] = []
    models = [_RecordingSession(batch_sizes, scale) for scale in range(1, 6)]
    # Sized off the ceiling, not off the budget: a batch that would breach
    # MAX_POSITIONS_PER_RUN in one run if the backend did not chunk -- while
    # staying small enough that an unbounded budget fails the assertion
    # rather than exhausting the machine.
    rows = MAX_POSITIONS_PER_RUN // width + 2
    x = np.zeros((rows, width, 4), np.int8)

    onnx_backend.spliceai_predict(models, x)

    assert max(batch_sizes) * width <= MAX_POSITIONS_PER_RUN, (
        f"one ORT run covered {max(batch_sizes) * width} positions at width "
        f"{width}; the shipped budget must keep it under "
        f"{MAX_POSITIONS_PER_RUN}")
