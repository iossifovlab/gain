# pylint: disable=W0621,C0114,C0116
"""Equivalence tests for the committed SpliceAI ONNX artifacts (issue #296).

The five ``models/spliceai{1..5}.h5`` files are the source of truth; the
``models/spliceai{1..5}.onnx`` files are derived artifacts produced offline by
``scripts/convert_models_to_onnx.py``. These tests pin the properties the
conversion must preserve:

* each ``.onnx`` passes ``onnx.checker`` and keeps a *dynamic* length axis
  (``TensorSpec((None, None, 4))``), so the runtime window
  ``10000 + 2*distance + 1`` (distance configurable 0-5000) still fits; and
* each ``.onnx`` reproduces its ``.h5`` numerically -- checked here to at least
  two decimal places, across the supported width range; and
* each ``.onnx`` was converted from *its own* ``.h5`` and not another ensemble
  member's -- pinned by a nearest-``.h5`` identity check, which the 2dp
  reproduction check alone cannot catch (see
  ``test_onnx_matches_its_own_h5_not_another_member``).

Both properties are only worth checking on input that actually drives the
network. On random one-hot DNA every ensemble member saturates to
``[1, 0, 0]``, so a 2dp reproduction check compares ``[1,0,0]`` against
``[1,0,0]`` and stays green whatever the artifacts contain (issue #395).
The suite therefore feeds a real splice-site window as well --
``test_suite_feeds_a_non_saturated_window`` guards that premise.

``onnx`` (the checker) is build-time-only tooling and lives in the ``dev``
dependency group, so these tests skip cleanly wherever it is absent (e.g. the
runtime-only CI image). ``onnxruntime`` is no longer dev-only -- issue #297
promoted it to a runtime dependency when the ONNX backend shipped. Only
``onnx`` is imported at module scope: ``tensorflow`` and ``onnxruntime`` are
imported by the fixtures that need them, so
``test_onnx_passes_checker_with_dynamic_length_axis`` still validates the
committed artifacts in a lean environment that has neither.
"""
import pathlib
from typing import Any

import numpy as np
import pytest

onnx = pytest.importorskip("onnx")

MODELS_DIR = (
    pathlib.Path(__file__).parent.parent
    / "spliceai_annotator" / "models"
)
FIXTURE_GENOME = (
    pathlib.Path(__file__).parent
    / "fixtures" / "hg38" / "genome" / "corpus.fa"
)
MODEL_INDICES = range(1, 6)


def _width(distance: int) -> int:
    """The annotator's input window for ``distance``.

    The SpliceAI CNN crops 10000 positions, so the output length is
    ``2*distance + 1``.
    """
    return 10000 + 2 * distance + 1


# A real donor site, so the network is exercised off saturation. The fixture
# hg38 genome's `sondonson` contig is a 66578 bp window of chr21 (from
# 33532738) carrying the gene SON; offset 13642 is an annotated end of a SON
# exon in the gene-models fixture, i.e. a splice donor. Centring a window
# there scores the donor channel ~0.99 in every ensemble member (measured),
# and the peak lands one position before the output centre.
MOTIF_CONTIG = "sondonson"
MOTIF_CENTRE = 13642

# One-hot A/C/G/T, matching `spliceai_annotator.utils.SPLICE_AI_MAPPING`.
# Deliberately re-stated here rather than imported: the annotator package's
# `__init__` pulls in `gain.annotation`, and this module must stay importable
# in a lean environment that has `onnx` and nothing else. The
# `test_motif_window_scores_a_donor_in_every_member` assertion below fails
# loudly if this ordering ever stops agreeing with the models.
BASES = "ACGT"


def _h5_path(index: int) -> pathlib.Path:
    return MODELS_DIR / f"spliceai{index}.h5"


def _onnx_path(index: int) -> pathlib.Path:
    return MODELS_DIR / f"spliceai{index}.onnx"


def _random_one_hot(width: int, *, seed: int) -> np.ndarray:
    """A random one-hot (A/C/G/T) input window of shape (1, width, 4)."""
    rng = np.random.default_rng(seed)
    bases = rng.integers(0, 4, size=width)
    x = np.zeros((1, width, 4), dtype=np.float32)
    x[0, np.arange(width), bases] = 1.0
    return x


def _read_fixture_contig(name: str) -> str:
    """Read one contig out of the committed fixture genome.

    Parsed here with plain text handling rather than ``pysam`` to keep this
    module importable without the annotator's runtime dependencies.
    """
    for block in FIXTURE_GENOME.read_text().split(">")[1:]:
        header, _, sequence = block.partition("\n")
        if header.split()[0] == name:
            return "".join(sequence.split())
    raise AssertionError(
        f"contig {name} not found in {FIXTURE_GENOME}")


def _motif_one_hot(width: int) -> np.ndarray:
    """A one-hot window of real sequence centred on a real donor site."""
    sequence = _read_fixture_contig(MOTIF_CONTIG)
    start = MOTIF_CENTRE - width // 2
    assert start >= 0, f"window of {width} runs off the start of the contig"
    assert start + width <= len(sequence), (
        f"window of {width} runs off the end of the contig")
    window = sequence[start:start + width].upper()

    x = np.zeros((1, width, 4), dtype=np.float32)
    for position, base in enumerate(window):
        channel = BASES.find(base)
        if channel >= 0:  # anything else (N) stays all-zero, as in production
            x[0, position, channel] = 1.0
    return x


def _motif_peak_index(output_length: int) -> int:
    """Where the donor peak sits in an output of ``output_length``."""
    return output_length // 2 - 1


# The cases straddle the whole configurable distance range 0-5000: distance 0
# (the degenerate single-position output), distance 50, and distance 5000 (the
# documented upper bound). All must survive the same converted graph, which is
# what exercises the dynamic length axis.
#
# Random cases keep the original coverage; motif cases carry real signal.
# Distance 0 is random-only: its output is a single position, too narrow to
# hold a motif's flanking context.
RANDOM_CASES = ("random_d0", "random_d50")
MOTIF_CASES = ("motif_d50", "motif_d5000")
ALL_CASES = RANDOM_CASES + MOTIF_CASES


@pytest.fixture(scope="module")
def shared_inputs() -> dict[str, np.ndarray]:
    """The input window per case, shared across every model.

    The identity check below compares every ``.onnx`` against every ``.h5``
    on the *same* input, so the inputs must not vary per model.

    Built here rather than at import time so that a missing or unreadable
    genome fixture cannot stop
    ``test_onnx_passes_checker_with_dynamic_length_axis`` -- which needs
    only ``onnx`` -- from validating the committed artifacts.
    """
    return {
        "random_d0": _random_one_hot(_width(0), seed=_width(0)),
        "random_d50": _random_one_hot(_width(50), seed=_width(50)),
        "motif_d50": _motif_one_hot(_width(50)),
        "motif_d5000": _motif_one_hot(_width(5000)),
    }


@pytest.fixture(scope="module")
def keras() -> Any:
    """Keras, needed only to read the source-of-truth ``.h5`` files."""
    return pytest.importorskip("tensorflow").keras


@pytest.fixture(scope="module")
def onnxruntime() -> Any:
    """ONNX Runtime, needed only to run the converted artifacts."""
    return pytest.importorskip("onnxruntime")


@pytest.fixture(scope="module")
def h5_outputs(
    keras: Any,
    shared_inputs: dict[str, np.ndarray],
) -> dict[tuple[int, str], np.ndarray]:
    """``.h5`` predictions keyed by ``(model_index, case)``."""
    outputs: dict[tuple[int, str], np.ndarray] = {}
    for index in MODEL_INDICES:
        keras_model = keras.models.load_model(
            str(_h5_path(index)), compile=False)
        for case, x in shared_inputs.items():
            outputs[index, case] = np.asarray(
                keras_model.predict(x, verbose=0))
    return outputs


@pytest.fixture(scope="module")
def onnx_outputs(
    onnxruntime: Any,
    shared_inputs: dict[str, np.ndarray],
) -> dict[tuple[int, str], np.ndarray]:
    """``.onnx`` predictions keyed by ``(model_index, case)``."""
    outputs: dict[tuple[int, str], np.ndarray] = {}
    for index in MODEL_INDICES:
        session = onnxruntime.InferenceSession(str(_onnx_path(index)))
        input_name = session.get_inputs()[0].name
        for case, x in shared_inputs.items():
            outputs[index, case] = np.asarray(
                session.run(None, {input_name: x})[0])
    return outputs


def test_suite_feeds_a_non_saturated_window(
    h5_outputs: dict[tuple[int, str], np.ndarray],
) -> None:
    """At least one input must drive the acceptor/donor channels.

    Guards the premise every other assertion here rests on. On random
    one-hot DNA the network saturates to ``[1, 0, 0]`` everywhere, which
    makes the 2dp reproduction check compare ``[1,0,0]`` against
    ``[1,0,0]`` -- green no matter what the artifacts contain.
    """
    peak = max(
        float(np.max(out[0][:, 1:])) for out in h5_outputs.values())
    assert peak > 0.9, (
        f"no input drives a splice-signal channel above {peak:.2e}; the "
        f"suite is running entirely in the saturated regime and its "
        f"reproduction check is near-vacuous")


@pytest.mark.parametrize("case", MOTIF_CASES)
@pytest.mark.parametrize("index", MODEL_INDICES)
def test_motif_window_scores_a_donor_in_every_member(
    index: int,
    case: str,
    h5_outputs: dict[tuple[int, str], np.ndarray],
) -> None:
    """Pin the motif window as a real splice site for the whole ensemble.

    Anchors the fixture coordinate (and, implicitly, the one-hot channel
    order above): if either drifts, the window stops being a donor site and
    the suite silently slides back into the saturated regime that #395
    was about.
    """
    out = h5_outputs[index, case][0]
    peak_index = _motif_peak_index(out.shape[0])
    donor = float(out[peak_index, 2])
    assert donor > 0.9, (
        f"spliceai{index} @ {case}: donor channel at the expected peak "
        f"offset {peak_index} is {donor:.4f}, expected > 0.9 -- the window "
        f"is no longer centred on a splice donor")


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


@pytest.mark.parametrize("case", ALL_CASES)
@pytest.mark.parametrize("index", MODEL_INDICES)
def test_onnx_reproduces_h5(
    index: int,
    case: str,
    h5_outputs: dict[tuple[int, str], np.ndarray],
    onnx_outputs: dict[tuple[int, str], np.ndarray],
) -> None:
    keras_out = h5_outputs[index, case]
    onnx_out = onnx_outputs[index, case]

    assert onnx_out.shape == keras_out.shape, (
        f"spliceai{index} @ {case}: shape "
        f"{onnx_out.shape} vs {keras_out.shape}")
    # At least two decimal places of agreement.
    np.testing.assert_allclose(
        onnx_out, keras_out, atol=1e-2,
        err_msg=f"spliceai{index} @ {case}: onnx != h5 to 2dp")


def _diffs_to_every_h5(
    onnx_out: np.ndarray,
    case: str,
    h5_outputs: dict[tuple[int, str], np.ndarray],
) -> dict[int, float]:
    return {
        j: float(np.max(np.abs(onnx_out - h5_outputs[j, case])))
        for j in MODEL_INDICES
    }


@pytest.mark.parametrize("case", ALL_CASES)
@pytest.mark.parametrize("index", MODEL_INDICES)
def test_onnx_matches_its_own_h5_not_another_member(
    index: int,
    case: str,
    h5_outputs: dict[tuple[int, str], np.ndarray],
    onnx_outputs: dict[tuple[int, str], np.ndarray],
) -> None:
    """Pin *which* ``.h5`` each ``.onnx`` was converted from.

    The 2dp reproduction check above cannot do this: the five members agree
    with each other to well within 2dp on any single input, so ``onnx{i}``
    reproduces *every* member to 2dp -- that test stays green even if
    ``convert_one`` wrote the wrong ``.h5`` into ``spliceai{i}.onnx`` (e.g.
    an index bug collapsing the ensemble toward one model, silently
    degrading splice predictions at runtime).

    What separates the members is precision: the *same* graph round-tripped
    through ONNX reproduces its source ``.h5`` to float roundtrip precision
    (~1e-7 measured), while different-weight members differ by far more --
    so the nearest ``.h5`` to each ``.onnx`` must be its own.

    The margin is only meaningful off saturation, which is why the motif
    cases matter. On a saturated window both sides of the comparison are
    float32 quantisation artifacts rather than model signal: the own-``.h5``
    difference sits at 5.96e-8 (2**-24, half an ulp at 1.0) and the nearest
    foreign ``.h5`` at 1.17e-7 (2**-23, one ulp) -- a ratio no rebuild of
    onnxruntime, TensorFlow or the host is obliged to preserve. On the motif
    cases the foreign-``.h5`` difference is a real inter-member disagreement
    (4.8e-4 at distance 50, 0.25 at distance 5000, measured), so the fixed
    factor-of-10 margin below clears by >= 8000x and rests on the models,
    not on floating-point spacing. That is what
    ``test_identity_margin_rests_on_real_model_difference`` guards.
    """
    onnx_out = onnx_outputs[index, case]
    diff_to = _diffs_to_every_h5(onnx_out, case, h5_outputs)

    nearest = min(diff_to, key=diff_to.__getitem__)
    assert nearest == index, (
        f"spliceai{index}.onnx @ {case}: nearest .h5 is "
        f"spliceai{nearest}, not its own (max-abs diffs {diff_to}) -- the "
        f"artifact was likely converted from the wrong .h5")
    own = diff_to[index]
    closest_other = min(d for j, d in diff_to.items() if j != index)
    assert own < closest_other / 10, (
        f"spliceai{index}.onnx @ {case}: own-.h5 diff {own:.2e} is "
        f"not a clear winner over the next-nearest .h5 "
        f"({closest_other:.2e}); identity margin too thin to trust")


@pytest.mark.parametrize("case", MOTIF_CASES)
@pytest.mark.parametrize("index", MODEL_INDICES)
def test_identity_margin_rests_on_real_model_difference(
    index: int,
    case: str,
    h5_outputs: dict[tuple[int, str], np.ndarray],
    onnx_outputs: dict[tuple[int, str], np.ndarray],
) -> None:
    """The identity margin must come from the models, not from float32.

    On a saturated window the nearest foreign ``.h5`` differs by ~1e-7 --
    one ulp at 1.0 -- so the identity check above would be decided by
    numeric spacing, and a future onnxruntime/TensorFlow/hardware build
    could flip it on correctly-converted artifacts. On the motif cases the
    same quantity is a genuine ensemble disagreement, orders of magnitude
    above the noise floor.
    """
    onnx_out = onnx_outputs[index, case]
    diff_to = _diffs_to_every_h5(onnx_out, case, h5_outputs)
    closest_other = min(d for j, d in diff_to.items() if j != index)

    float32_ulp_at_one = float(np.spacing(np.float32(1.0)))
    assert closest_other > 100 * float32_ulp_at_one, (
        f"spliceai{index}.onnx @ {case}: nearest foreign .h5 differs by "
        f"{closest_other:.2e}, within 100 ulp of 1.0 "
        f"({float32_ulp_at_one:.2e}) -- the identity margin is decided by "
        f"float32 spacing rather than by the models, and is not robust to "
        f"a future onnxruntime/TensorFlow rebuild")
