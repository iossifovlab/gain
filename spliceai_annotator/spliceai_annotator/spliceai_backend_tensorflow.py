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

#: Maximum number of sequence positions (rows x window width) in a single
#: ``predict`` chunk.
#:
#: ``Model.predict`` splits its input along the batch axis and evaluates one
#: chunk at a time; left unset, Keras picks 32 *rows*. Rows are the wrong
#: unit. Cost tracks **positions**, and the default window and the widest one
#: lie on the same curve once indexed that way (relative cost, 1.00 = the
#: ~40k reference configuration of that run; two runs of
#: `scripts/measure_tf_batch_size.py`, 2 repetitions each, TF 2.21.0 CPU):
#:
#:      positions   width 10101   width 20201
#:      ~40k        1.00 (b=4)    1.00 (b=2)
#:      ~61k        1.01 (b=6)    1.01 (b=3)
#:      ~81k        1.02 (b=8)    0.98 (b=4)
#:      ~162k       1.09 (b=16)   1.12 (b=8)
#:      ~323k       2.18 (b=32)   2.14 (b=16)
#:
#: Each column is one invocation: `relative_cost` is normalised against a
#: configuration, so cells from separate runs are not comparable and must not
#: be pasted into one table.
#:
#: Indexed by rows instead, the two columns disagree: 16 rows costs 1.09 at
#: width 10101 and 2.14 at 20201. A plain row count is therefore only ever
#: right at one width -- and Keras's 32 is the ~323k row at the default
#: window, the worst point measured, with the widest window twice as far out
#: again.
#:
#: 65536 sits on the plateau at both widths (6 rows at 10101, 3 at 20201).
#: The plateau is flat to within ~2% from ~40k to ~81k, so the value is
#: chosen for margin against the knee past ~162k rather than for a sharp
#: optimum, and every width the annotator can build lands inside it: widths
#: 10001-22001 give 2-6 rows, i.e. 43,692-65,536 positions.
#:
#: Memory moves the *other* way. #427 expected a smaller chunk to help here
#: too and asked for it to be confirmed rather than assumed; confirmed, and
#: it does not. Peak RSS rises as the chunk shrinks, because the cost scales
#: with the number of predict *steps* rather than with one step's
#: activations. Five paired runs at width 10101 -- both arms in one
#: invocation, at 300 and 500 rows, with the arm order alternated, and one on
#: an idle host -- put the shipped budget at 1.07x to 1.19x the peak RSS of
#: Keras's 32. Only the ratio means anything: the same configuration spans
#: 1467-2410 MB across invocations on a shared machine, so no fixed MB
#: ceiling is testable here, and the widest ratios come from the busiest runs.
#:
#: Those same five pairs put the throughput win at 2.09x-2.17x, the 2.09x
#: being the idle-host run (390.8 -> 186.8 ms/window at 500 rows). It is
#: stable across load, batch size and arm order, which is why it is quoted
#: without a hedge where the memory figure is not.
#:
#: So the budget buys ~2.1x throughput at the production batch for ~7-19%
#: more peak RSS. That is a deliberate trade and not a free win: `-j`
#: defaults to the core count and each worker holds its own ensemble, so the
#: memory is paid per worker.
#:
#: 163840 -- 16 rows at the default window, the ~162k row above -- was
#: considered as the memory-sparing alternative and rejected. It still beats
#: Keras's 32 by ~2x, so it costs only ~8% of the throughput (1.09 against
#: 1.01, clean and repeatable), but the memory it buys back does not
#: reproduce: 13% below this budget in one invocation and 2.7% in another.
#: A measurable cost for an unmeasurable saving. Revisit it if a deployment
#: proves memory-bound, and measure the pair on *that* host.
#:
#: Numerically equal to `ONNX_POSITION_BUDGET`, and deliberately a separate
#: constant: that one is chosen to bound *memory* for a runtime that does not
#: chunk at all, this one to avoid a *throughput* cliff in one that does.
#: Same number today, different measurements, free to move apart.
TENSORFLOW_POSITION_BUDGET = 65536


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
    """Average the ensemble's predictions for the one-hot window ``x``.

    Keras does the chunking; the backend only chooses the size, sized from
    `TENSORFLOW_POSITION_BUDGET` so that it follows the window width instead
    of being fixed at a row count that is right for one width only.

    The floor of 1 is unreachable today and deliberately kept anyway. The
    widest window the annotator can build is 22001 (`distance` clamps at
    5000, `max_insertion_length` at 2000), and ``65536 // 22001 == 2``; a
    zero would need a window three times wider than that ceiling. It guards
    the budget, not the width -- lowering `TENSORFLOW_POSITION_BUDGET` below
    one window is what would otherwise ask Keras for ``batch_size=0``, which
    it rejects.

    Chunking changes no result beyond the batch-position non-determinism the
    runtime already has -- rows of the batch axis are independent. That
    drift is not nothing: #1275 measured ~4e-11 on its inputs and the
    equivalence test here sees up to 9.3e-10, still four orders below the
    corpus tolerance of 1e-5.
    """
    rows_per_run = max(1, TENSORFLOW_POSITION_BUDGET // x.shape[1])
    return cast(np.ndarray, np.mean([
        models[m].predict(x, batch_size=rows_per_run, verbose=0)
        for m in range(5)
    ], axis=0))
