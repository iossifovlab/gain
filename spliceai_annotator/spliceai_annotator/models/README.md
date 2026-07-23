# SpliceAI model artifacts

Illumina SpliceAI, five-model ensemble (last retrained 2017; upstream
archived). Two committed serializations of the **same** models live here:

| Files | Format | Role |
|-------|--------|------|
| `spliceai1.h5` … `spliceai5.h5` | Keras HDF5 | **Source of truth.** The original weights. |
| `spliceai1.onnx` … `spliceai5.onnx` | ONNX | Derived artifacts, converted from the `.h5` files. |

## Source of truth

The `.h5` files are authoritative. The `.onnx` files are **derived** from them
and are committed alongside — exactly as the `.h5` files are committed — so the
runtime never needs TensorFlow to load the ensemble (see issue #296; the
runtime backend swap itself is #297/#299). If the two ever disagree, the `.h5`
files win: regenerate the `.onnx` from them, never the reverse.

## Regenerating the `.onnx` files

Conversion is a one-time offline step — TensorFlow, Keras and tf2onnx are
build-time tools, not runtime dependencies. Re-run it only when the `.h5`
source changes:

```bash
CUDA_VISIBLE_DEVICES=-1 \
    python spliceai_annotator/scripts/convert_models_to_onnx.py
```

`CUDA_VISIBLE_DEVICES=-1` is **required**: without it TensorFlow's grappler
enumerates CUDA devices during export and hard-fails. See the script's module
docstring for details and for provisioning the build-time tooling
(`uv sync --package gain-spliceai-annotator --group dev`).

Each converted `.onnx` keeps a **dynamic length axis**
(`TensorSpec((None, None, 4))`) — the annotator window is
`10000 + 2*distance + 1` with `distance` configurable 0–5000 — and is validated
with `onnx.checker` on the way out. Its numerical equivalence to the
corresponding `.h5` (to at least two decimal places, at more than one input
width) is pinned by `tests/test_onnx_equivalence.py`.

## License

Both formats are covered by the CC BY-NC 4.0 `LICENSE` in this directory. The
format conversion is authorised by its §2(a)(4); the artifacts remain
NonCommercial with attribution.
