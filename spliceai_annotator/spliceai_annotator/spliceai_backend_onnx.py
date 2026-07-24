"""ONNX Runtime model runtime for the SpliceAI annotator.

Runs the five committed ``models/spliceai{1..5}.onnx`` artifacts -- derived
offline from the ``.h5`` source of truth by ``scripts/convert_models_to_onnx``
and pinned to it by ``tests/test_onnx_equivalence.py``. Selected with
``SPLICEAI_BACKEND=onnx`` -- see ``spliceai_annotator_impl``.
"""
import gc
from importlib.resources import as_file, files
from typing import cast

import numpy as np
import onnxruntime as ort


def spliceai_session_options() -> ort.SessionOptions:
    """Session settings without which this backend is far slower.

    Not tuning -- load-bearing, and each looks like premature
    micro-optimisation to a reader who has not measured it, hence the numbers
    below. Stock ONNX Runtime runs this ensemble at roughly 1.4x the time of
    the settings below (measured on a 32-core box, one ensemble pass: 747 ms
    -> 525 ms at the default distance=50 window, 1189 ms -> 969 ms at the
    widest 20,201 window; #297's own study measured 576 ms -> 340 ms).

    Note what these do *not* buy: on that box ONNX Runtime so configured was
    still slightly slower than the TensorFlow backend (525 ms vs 486 ms, and
    969 ms vs 768 ms), where #297 expected 1.35x the other way. The settings
    are still the best ONNX configuration measured -- the cross-backend
    comparison is what did not reproduce, and it is hardware-dependent. See
    #400 before treating ONNX as the faster runtime.
    """
    options = ort.SessionOptions()

    # Biggest lever (576 -> 340 ms in the #297 study). ORT's intra-op threads
    # spin-wait between ops; the five coexisting ensemble sessions keep ~20
    # threads fighting over the cores while only one session is ever working.
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")

    # EXTENDED, deliberately *not* the default ENABLE_ALL: at ENABLE_ALL the
    # NCHWc transformer wraps a layout reorder around every dilated conv and
    # never amortizes it. ORT's fast path is a trap for this model.
    options.graph_optimization_level = \
        ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED

    # Five sessions share the box; 4 intra-op threads each stops them from
    # oversubscribing the cores. Measured best of {4, 8, 16, ORT default} at
    # the widest window on a 32-core box (969 / 1010 / 1667 / 1167 ms), so the
    # win is not just "more threads is worse" -- ORT's own default is worse
    # than both 4 and 8 here.
    options.intra_op_num_threads = 4

    return options


def spliceai_load_models() -> list:
    """Load the five SpliceAI models as ONNX Runtime sessions.

    A shape-pinned graph would be worth a further 340 -> 263 ms (#297's
    study), but the pin is config-specific -- the window width depends on the
    annotator's `distance` -- so this ships the dynamic graph.
    """
    package = files(__package__)
    options = spliceai_session_options()
    models = []
    for i in range(1, 6):
        with as_file(package / "models" / f"spliceai{i}.onnx") as model_path:
            models.append(
                ort.InferenceSession(str(model_path), sess_options=options))
    return models


def spliceai_close() -> None:
    gc.collect()


def spliceai_predict(
    models: list,
    x: np.ndarray,
) -> np.ndarray:
    """Average the ensemble's predictions for the one-hot window ``x``.

    The cast is required, not defensive: ONNX Runtime refuses the annotator's
    int8 one-hot outright ("INVALID_ARGUMENT: Unexpected input data type.
    Actual: (tensor(int8)), expected: (tensor(float))") where TensorFlow cast
    it silently. It belongs here rather than in `one_hot_encode`, which
    returns int8 deliberately -- the memory-cheap choice for a 20,201 x 4
    array.
    """
    x = x.astype(np.float32, copy=False)
    return cast(np.ndarray, np.mean([
        model.run(None, {model.get_inputs()[0].name: x})[0]
        for model in models
    ], axis=0))
