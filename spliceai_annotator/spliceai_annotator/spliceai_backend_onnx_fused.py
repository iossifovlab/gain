"""Fused-graph ONNX Runtime model runtime for the SpliceAI annotator.

Runs ``models/spliceai_fused.onnx`` as one ONNX Runtime session: the five
ensemble members and an element-wise mean of their outputs in one graph.
``scripts/fuse_onnx_models.py`` derives the graph from the five committed
``models/spliceai{1..5}.onnx`` files.

Opt-in with ``SPLICEAI_BACKEND=onnx-fused``. The default stays the
five-session ``onnx`` backend (see ``spliceai_annotator_impl``). This backend
takes the session options, the ``ONNX_POSITION_BUDGET`` chunking and the
per-chunk float32 cast from ``spliceai_backend_onnx``, so the two ONNX paths
differ only in where the ensemble mean is taken. It never imports
TensorFlow.
"""
from importlib.resources import as_file, files

import numpy as np
import onnxruntime as ort

from . import spliceai_backend_onnx as five_sessions

FUSED_MODEL = "spliceai_fused.onnx"


def spliceai_load_models() -> list:
    """Load the fused SpliceAI graph as a list of one ONNX Runtime session."""
    package = files(__package__)
    with as_file(package / "models" / FUSED_MODEL) as model_path:
        session = ort.InferenceSession(
            str(model_path),
            sess_options=five_sessions.spliceai_session_options())
    return [session]


def spliceai_close() -> None:
    five_sessions.spliceai_close()


def spliceai_predict(
    models: list,
    x: np.ndarray,
) -> np.ndarray:
    """Return the ensemble mean for the one-hot window ``x``.

    ``models`` is the one-session list of `spliceai_load_models`. The graph
    computes the mean itself. The batch is split into chunks of at most
    ``spliceai_backend_onnx.ONNX_POSITION_BUDGET`` positions, each cast to
    float32, as in the five-session backend.
    """
    (session,) = models
    input_name = session.get_inputs()[0].name

    def run(chunk: np.ndarray) -> np.ndarray:
        return np.asarray(session.run(None, {input_name: chunk})[0])

    return five_sessions.predict_in_chunks(
        x, run, five_sessions.ONNX_POSITION_BUDGET)
