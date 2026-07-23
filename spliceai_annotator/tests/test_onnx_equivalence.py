# pylint: disable=C0114,C0116
"""Equivalence tests for the committed SpliceAI ONNX artifacts (issue #296).

The five ``models/spliceai{1..5}.h5`` files are the source of truth; the
``models/spliceai{1..5}.onnx`` files are derived artifacts produced offline by
``scripts/convert_models_to_onnx.py``. These tests pin the two properties the
conversion must preserve:

* each ``.onnx`` passes ``onnx.checker`` and keeps a *dynamic* length axis
  (``TensorSpec((None, None, 4))``), so the runtime window
  ``10000 + 2*distance + 1`` (distance configurable 0-5000) still fits; and
* each ``.onnx`` reproduces its ``.h5`` numerically -- checked here to at least
  two decimal places, at more than one input width.

Build-time-only tooling (``onnx``, ``onnxruntime``) lives in the ``dev``
dependency group, not the annotator's runtime deps, so these tests skip cleanly
wherever that tooling is absent (e.g. the runtime-only CI image).
"""
import pathlib

import numpy as np
import pytest

onnx = pytest.importorskip("onnx")
ort = pytest.importorskip("onnxruntime")
tf = pytest.importorskip("tensorflow")

MODELS_DIR = (
    pathlib.Path(__file__).parent.parent
    / "spliceai_annotator" / "models"
)
MODEL_INDICES = range(1, 6)

# Widths straddle the 10000-position crop the SpliceAI CNN applies (output
# length = input length - 10000): distance 0 (10001) and distance 50 (10101).
# Both must survive the same converted graph, exercising the dynamic axis.
WIDTHS = (10001, 10101)


def _h5_path(index: int) -> pathlib.Path:
    return MODELS_DIR / f"spliceai{index}.h5"


def _onnx_path(index: int) -> pathlib.Path:
    return MODELS_DIR / f"spliceai{index}.onnx"


def _one_hot_input(width: int, *, seed: int) -> np.ndarray:
    """A realistic one-hot (A/C/G/T) input window of shape (1, width, 4)."""
    rng = np.random.default_rng(seed)
    bases = rng.integers(0, 4, size=width)
    x = np.zeros((1, width, 4), dtype=np.float32)
    x[0, np.arange(width), bases] = 1.0
    return x


@pytest.mark.parametrize("index", MODEL_INDICES)
def test_onnx_passes_checker_with_dynamic_length_axis(index: int) -> None:
    path = _onnx_path(index)
    assert path.exists(), f"missing converted artifact: {path}"

    model = onnx.load(str(path))
    onnx.checker.check_model(model)

    # Input tensor: (batch, length, 4) with a dynamic length axis.
    graph_input = model.graph.input[0]
    dims = graph_input.type.tensor_type.shape.dim
    assert len(dims) == 3, f"{path.name}: expected rank-3 input"
    # The length axis (dim 1) must NOT be a fixed integer -- a dynamic dim
    # carries a symbolic dim_param (or an unset dim_value of 0), never a
    # concrete positive extent.
    length_dim = dims[1]
    assert length_dim.dim_value == 0, (
        f"{path.name}: length axis is pinned to {length_dim.dim_value}, "
        f"expected a dynamic axis")
    # The channel axis (dim 2) is fixed at 4 (one-hot A/C/G/T).
    assert dims[2].dim_value == 4, (
        f"{path.name}: channel axis is {dims[2].dim_value}, expected 4")


@pytest.mark.parametrize("index", MODEL_INDICES)
def test_onnx_reproduces_h5_at_multiple_widths(index: int) -> None:
    keras_model = tf.keras.models.load_model(str(_h5_path(index)))
    session = ort.InferenceSession(str(_onnx_path(index)))
    input_name = session.get_inputs()[0].name

    for width in WIDTHS:
        x = _one_hot_input(width, seed=1000 * index + width)

        keras_out = np.asarray(keras_model.predict(x, verbose=0))
        onnx_out = np.asarray(session.run(None, {input_name: x})[0])

        assert onnx_out.shape == keras_out.shape, (
            f"spliceai{index} @ width {width}: shape "
            f"{onnx_out.shape} vs {keras_out.shape}")
        # At least two decimal places of agreement.
        np.testing.assert_allclose(
            onnx_out, keras_out, atol=1e-2,
            err_msg=f"spliceai{index} @ width {width}: onnx != h5 to 2dp")
