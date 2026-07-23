#!/usr/bin/env python
"""Offline, one-time conversion of the SpliceAI models from Keras HDF5 to ONNX.

The five ``spliceai_annotator/models/spliceai{1..5}.h5`` files are the **source
of truth** (Illumina SpliceAI, last retrained 2017, upstream archived). This
script derives ``spliceai{1..5}.onnx`` alongside them -- committed artifacts,
exactly as the ``.h5`` files are committed today. It is *not* part of the
runtime: TensorFlow, Keras and tf2onnx are build-time tools here, invoked once,
never imported by the annotator at inference time (see issue #296).

Conversion path
---------------
Keras' ``model.export(filepath, format="onnx")`` imports ``tf2onnx``
internally and calls ``patch_tf2onnx()`` to work around its numpy-2 gap. We
pass an explicit ``input_signature`` of ``TensorSpec((None, None, 4))`` to keep
the **length axis dynamic**: the annotator's window is ``10000 + 2*distance +
1`` with ``distance`` configurable 0-5000, so the sequence length genuinely
varies at runtime. The channel axis is fixed at 4 (one-hot A/C/G/T).

Requirements
------------
* ``CUDA_VISIBLE_DEVICES=-1`` **must** be set. Without it, conversion dies
  inside TensorFlow's grappler with ``Bad StatusOr access: INTERNAL: CUDA
  Runtime error: Error loading CUDA libraries`` -- grappler enumerates devices
  and hard-fails. This is an environment issue, not a converter bug, and it
  bites in CI. This script sets it defensively at import time, before
  TensorFlow is imported, but export it in your shell too.
* Build-time tooling: ``tensorflow``, ``keras`` and ``tf2onnx`` (and ``onnx``
  for the post-conversion check). In this repo they live in the
  ``gain-spliceai-annotator`` ``dev`` dependency group; provision with
  ``uv sync --package gain-spliceai-annotator --group dev``.

Usage
-----
    CUDA_VISIBLE_DEVICES=-1 \\
        python spliceai_annotator/scripts/convert_models_to_onnx.py

Each produced ``.onnx`` is validated with ``onnx.checker`` before the script
exits. The equivalence of each ``.onnx`` to its ``.h5`` is pinned separately by
``tests/test_onnx_equivalence.py``.
"""
import os

# Set BEFORE importing tensorflow: grappler enumerates CUDA devices during the
# graph optimization export runs, and hard-fails on a box without CUDA drivers.
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")

# pylint: disable=wrong-import-position
# (imports below deliberately follow the CUDA_VISIBLE_DEVICES setup above --
# tensorflow reads it at import time.)
import pathlib

import onnx
import tensorflow as tf

MODELS_DIR = (
    pathlib.Path(__file__).resolve().parent.parent
    / "spliceai_annotator" / "models"
)

# (batch, length, channel): length dynamic (window 10000 + 2*distance + 1),
# channels fixed at 4 (one-hot A/C/G/T).
INPUT_SIGNATURE = [tf.TensorSpec((None, None, 4), tf.float32, name="input")]


def convert_one(index: int) -> pathlib.Path:
    """Convert ``spliceai{index}.h5`` to ``spliceai{index}.onnx``."""
    h5_path = MODELS_DIR / f"spliceai{index}.h5"
    onnx_path = MODELS_DIR / f"spliceai{index}.onnx"
    if not h5_path.exists():
        raise FileNotFoundError(h5_path)

    print(f"Loading {h5_path.name} ...")
    # compile=False: export/inference needs only the forward pass, and the
    # 2017-era .h5 files carry no usable training-time compile objects.
    model = tf.keras.models.load_model(str(h5_path), compile=False)

    print(f"Exporting {onnx_path.name} (dynamic length axis) ...")
    model.export(
        str(onnx_path),
        format="onnx",
        input_signature=INPUT_SIGNATURE,
    )

    print(f"Checking {onnx_path.name} with onnx.checker ...")
    onnx.checker.check_model(onnx.load(str(onnx_path)))
    print(f"OK: {onnx_path.name}")
    return onnx_path


def main() -> None:
    """Convert all five models, refusing to run without CUDA disabled."""
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "-1":
        raise SystemExit(
            "CUDA_VISIBLE_DEVICES must be -1 (see module docstring); "
            f"got {os.environ.get('CUDA_VISIBLE_DEVICES')!r}")
    for index in range(1, 6):
        convert_one(index)
    print("All five models converted.")


if __name__ == "__main__":
    main()
