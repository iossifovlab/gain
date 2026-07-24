"""TensorFlow/Keras model runtime for the SpliceAI annotator.

The default backend: runs the five committed ``models/spliceai{1..5}.h5``
files, which are the source of truth for the ensemble. Selected unless
``SPLICEAI_BACKEND=onnx`` is set -- see ``spliceai_annotator_impl``.
"""
import gc
from importlib.resources import as_file, files
from typing import cast

import numpy as np
import tensorflow as tf


def spliceai_load_models() -> list:
    """Load the five Keras SpliceAI models."""
    package = files(__package__)
    models = []
    for i in range(1, 6):
        with as_file(package / "models" / f"spliceai{i}.h5") as model_path:
            models.append(tf.keras.models.load_model(str(model_path)))
    return models


def spliceai_close() -> None:
    gc.collect()


def spliceai_predict(
    models: list,
    x: np.ndarray,
) -> np.ndarray:
    return cast(np.ndarray, np.mean([
        models[m].predict(x, verbose=0)
        for m in range(5)
    ], axis=0))
