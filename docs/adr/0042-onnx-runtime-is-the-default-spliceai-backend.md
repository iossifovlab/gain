# 42. ONNX Runtime is the default SpliceAI backend, and TensorFlow is optional

- **Status:** accepted
- **Date:** 2026-11-18
- **Issues:** [gain#299](https://github.com/iossifovlab/gain/issues/299) (this
  decision), [gain#297](https://github.com/iossifovlab/gain/issues/297) (the
  ONNX backend), [gain#298](https://github.com/iossifovlab/gain/issues/298) and
  [gain#400](https://github.com/iossifovlab/gain/issues/400) (the measurements)

## Context

The SpliceAI annotator runs its five models on one of two runtimes, chosen per
process by `SPLICEAI_BACKEND`. The TensorFlow/Keras backend runs the `.h5`
models, the source of truth. The ONNX Runtime backend runs the `.onnx` models
that `scripts/convert_models_to_onnx.py` derives from them. The two agree to
about `2.4e-7` in a probability at every window the annotator builds.

TensorFlow was the default and a required dependency. That cost every install:

- TensorFlow installs at about 2.1 GB, against 113 MB for ONNX Runtime.
- TensorFlow has no Python 3.14 wheels, so the 3.14 matrix left the plugin out.
- A dask worker that loads the TensorFlow models starts about 3.5 s later and
  holds about 555 MB more memory than one that loads the ONNX models.

On CPU, ONNX Runtime is about 8% slower end to end than TensorFlow (#298, #400).
The comparison depends on the hardware.

## Decision

- `DEFAULT_SPLICEAI_BACKEND` is `onnx`. An unset `SPLICEAI_BACKEND` runs ONNX
  Runtime, and the ONNX backend never imports TensorFlow.
- `onnxruntime` stays a required dependency. `tensorflow` moves to the
  `tensorflow` extra of `gain-spliceai-annotator`. The conda recipe keeps one
  output, with `tensorflow` under `run_constraints` instead of `run`.
- `SPLICEAI_BACKEND=tensorflow` without TensorFlow raises an `ImportError` that
  names `pip install gain-spliceai-annotator[tensorflow]` and
  `mamba install tensorflow`. There is no fallback to ONNX.
- The TensorFlow CI tiers set `SPLICEAI_BACKEND=tensorflow` explicitly, and
  their images keep the extra. The Python 3.14 matrix cell builds without the
  extra and sets `SPLICEAI_EXPECT_NO_TENSORFLOW=1`, under which only the
  TensorFlow-backend tests skip.
- The baseline regeneration scripts refuse to run unless the TensorFlow backend
  is selected: the committed baselines are TensorFlow output.

## Why

The 8% throughput loss is smaller than what the default saves on every install
and every worker: 2 GB of disk, half a gigabyte of memory per worker, and a
plugin that runs on Python 3.14. The answers do not change at the precision the
annotator reports.

A silent fallback was rejected. A TensorFlow tier that quietly ran ONNX would
pass while testing nothing it claims to test.

Two alternatives were rejected:

- Removing the TensorFlow backend. The `.h5` files are the source of truth, and
  the equivalence test and the converter need TensorFlow to check the `.onnx`
  files against them.
- A second conda output with TensorFlow. `run_constraints` covers the case of a
  user who installs TensorFlow next to the package, at no release cost.

## Consequences

- A user who wants TensorFlow installs it and sets `SPLICEAI_BACKEND=tensorflow`.
- The developer environment (`uv sync ... --extra tensorflow`, and the generated
  `spliceai_annotator/spliceai-environment.yml`) still carries TensorFlow, for
  the TensorFlow tests and the equivalence check.
- `uv.lock` still resolves TensorFlow, as an optional dependency, so the h5py
  cap of `core/Dockerfile` still binds.
