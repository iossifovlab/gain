"""ONNX Runtime model runtime for the SpliceAI annotator.

Runs the five committed ``models/spliceai{1..5}.onnx`` artifacts -- derived
offline from the ``.h5`` source of truth by ``scripts/convert_models_to_onnx``
and pinned to it by ``tests/test_onnx_equivalence.py``. Selected with
``SPLICEAI_BACKEND=onnx`` -- see ``spliceai_annotator_impl``.
"""
import gc
from collections.abc import Iterator
from importlib.resources import as_file, files
from typing import cast

import numpy as np
import onnxruntime as ort

#: Maximum number of sequence positions (rows x window width) handed to ONNX
#: Runtime in a single `run`.
#:
#: `session.run` evaluates the whole batch axis in one graph invocation, so
#: its peak memory is *linear* in the batch -- a steady ~5.4 KB per position.
#: Keras hides this by chunking `predict` at 32 internally; ONNX Runtime does
#: not. That difference is not academic: `annotate_vcf` defaults to
#: `batch_size=500` and `_do_batch_annotate` concatenates a whole width
#: bucket into one call, so an unchunked ONNX pass would ask for ~27 GB at
#: the default `distance=50` and ~55 GB at the widest window, where the
#: TensorFlow backend it replaces stays flat at ~0.6 GB.
#:
#: Measured peak RSS per pass (32 windows at width 10101 / 16 at 20201):
#:
#:      budget      rows/run   peak @10101   peak @20201
#:      unchunked   all        1755 MB       (linear)
#:      262144      25         1531 MB       --
#:      131072      12         1288 MB       1272 MB
#:      65536        6          647 MB        643 MB
#:
#: 65536 is chosen to land on the ~0.6 GB plateau TensorFlow reaches at any
#: batch size, i.e. to preserve the memory contract callers already rely on.
#: Throughput is unaffected: with the budgets interleaved within each
#: repetition (so drift hits all of them equally), run-to-run spread is
#: +-25%, larger than any budget effect, and smaller chunks trend *faster*
#: at the narrow width.
#:
#: "Bounded", not literally flat: a residual ~0.17 MB/row at width 10101 and
#: ~0.56 MB/row at 20201 remains, from the retained per-chunk outputs and
#: the final concatenate. It is well under the TensorFlow backend's own
#: slope (~0.49 MB/row at 10101), and at width 20201 / batch 64 chunked ONNX
#: peaks at 702 MB against TensorFlow's 1215 MB -- so this is a bound in the
#: sense that matters, not an asymptote.
ONNX_POSITION_BUDGET = 65536


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


def _chunks(x: np.ndarray, rows: int) -> Iterator[np.ndarray]:
    for start in range(0, len(x), rows):
        yield x[start:start + rows]


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

    The batch axis is split into `ONNX_POSITION_BUDGET`-sized runs -- see
    that constant for why and for the measurements. Rows are independent, so
    chunking changes no result: a chunked pass is bitwise identical to an
    unchunked one.

    The float32 cast happens per chunk rather than once over the whole batch
    so that the widened copy is bounded too; casting up front would hold a
    4x-larger array (int8 -> float32) for the whole call, which is the
    largest remaining term in the residual per-row cost.
    """
    rows_per_run = max(1, ONNX_POSITION_BUDGET // x.shape[1])
    predictions = []
    for chunk in _chunks(x, rows_per_run):
        chunk = chunk.astype(np.float32, copy=False)
        predictions.append(np.mean([
            model.run(None, {model.get_inputs()[0].name: chunk})[0]
            for model in models
        ], axis=0))
    return cast(np.ndarray, np.concatenate(predictions, axis=0))
