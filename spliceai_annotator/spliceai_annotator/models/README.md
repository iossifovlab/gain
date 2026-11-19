# SpliceAI model artifacts

Illumina SpliceAI, five-model ensemble (last retrained 2017; upstream
archived). Two committed serializations of the **same** models live here,
plus one fused graph derived from the `.onnx` files:

| Files | Format | Role |
|-------|--------|------|
| `spliceai1.h5` … `spliceai5.h5` | Keras HDF5 | **Source of truth.** The original weights. |
| `spliceai1.onnx` … `spliceai5.onnx` | ONNX | Derived artifacts, converted from the `.h5` files. |
| `spliceai_fused.onnx` | ONNX | Derived from the five `.onnx` files: the five members and their mean in one graph. |

## Source of truth

The `.h5` files are authoritative. The `.onnx` files are **derived** from them
and are committed alongside — exactly as the `.h5` files are committed — so the
runtime never needs TensorFlow to load the ensemble (see issue #296). If the
two ever disagree, the `.h5` files win: regenerate the `.onnx` from them, never
the reverse.

Which serialization actually runs is a per-process choice: `SPLICEAI_BACKEND`
selects `onnx` (these `.onnx` files, under ONNX Runtime — the default since
#299) or `tensorflow` (the `.h5` files; needs the optional `tensorflow` extra).
ADR 0042 records the choice.

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
(`uv sync --package gain-spliceai-annotator --extra tensorflow --group dev`).

Each converted `.onnx` keeps a **dynamic length axis**
(`TensorSpec((None, None, 4))`) — the annotator window is
`10000 + 2*distance + 1` with `distance` configurable 0–5000 — and is validated
with `onnx.checker` on the way out. Its numerical equivalence to the
corresponding `.h5` (to at least two decimal places, at more than one input
width) is pinned by `tests/test_onnx_equivalence.py`.

## The fused graph

`spliceai_fused.onnx` holds the five `.onnx` members side by side and an ONNX
`Mean` node that averages their outputs, so ONNX Runtime runs the ensemble as
one session. `SPLICEAI_BACKEND=onnx-fused` selects it. The backend is opt-in:
the default stays the five-session `onnx` backend.

It derives from the five `.onnx` files, not from the `.h5` files. Regenerate
it whenever a member `.onnx` changes:

```bash
python spliceai_annotator/scripts/fuse_onnx_models.py
```

The script needs only `onnx` from the `dev` dependency group and never
imports TensorFlow. Its output is reproducible byte for byte, and
`tests/test_onnx_fused.py` regenerates the graph and compares it with the
committed file. The graph keeps a dynamic length axis.

The file is about 16 MB (16447964 bytes), close to the sum of the five
members, and ships in the wheel with them.
`scripts/measure_onnx_fused.py` compares its wall time and peak RSS with the
five-session backend.

## License

Both formats are covered by the CC BY-NC 4.0 `LICENSE` in this directory. The
format conversion is authorised by its §2(a)(4); the artifacts remain
NonCommercial with attribution.
