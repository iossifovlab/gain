# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Backend selection and ONNX Runtime backend behaviour (issue #297)."""
from collections.abc import Iterator
from types import ModuleType

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

    Measured equivalence is ~2.4e-7 against a `delta_score` reported at two
    decimals -- about six orders of magnitude of headroom. 1e-5 keeps a real
    bound (it is the differential harness's own DS_* tier) without pinning
    float noise.
    """
    x = a_one_hot_window(width, batch=batch)

    y_onnx = onnx_backend.spliceai_predict(onnx_models, x)
    y_tensorflow = tensorflow_backend.spliceai_predict(tensorflow_models, x)

    assert y_onnx.shape == (batch, width - 10000, 3)
    assert y_onnx.shape == y_tensorflow.shape
    np.testing.assert_allclose(y_onnx, y_tensorflow, atol=1e-5)
