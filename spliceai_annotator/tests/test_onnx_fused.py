# pylint: disable=W0621,C0114,C0116
"""The committed fused SpliceAI ONNX graph (issue #300).

``models/spliceai_fused.onnx`` holds the five ensemble members and an
element-wise mean of their outputs in one graph. It derives from the five
committed ``models/spliceai{1..5}.onnx`` files, not from the ``.h5`` files,
through ``scripts/fuse_onnx_models.py``.

``onnx`` is build-time tooling in the ``dev`` dependency group, so this module
skips where it is absent, as ``test_onnx_equivalence.py`` does. The
annotator package and ``onnxruntime`` are imported only by the fixtures that
run a graph, so the checker test still runs in an environment with ``onnx``
alone.
"""
import importlib.util
import pathlib
import subprocess
import sys
from collections.abc import Callable, Iterator
from types import ModuleType
from typing import Any

import numpy as np
import pytest

# This import skips the module where onnx is absent, as below.
from tests.test_onnx_equivalence import _motif_one_hot, _width

onnx = pytest.importorskip("onnx")

MODELS_DIR = (
    pathlib.Path(__file__).parent.parent
    / "spliceai_annotator" / "models"
)
FUSED_PATH = MODELS_DIR / "spliceai_fused.onnx"
FUSE_SCRIPT = (
    pathlib.Path(__file__).parent.parent / "scripts" / "fuse_onnx_models.py")


def _load_fuse_script() -> ModuleType:
    """Import the script by path: ``scripts/`` is not an importable package."""
    spec = importlib.util.spec_from_file_location(
        "fuse_onnx_models", FUSE_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fused_passes_checker_with_dynamic_length_axis() -> None:
    assert FUSED_PATH.exists(), f"missing fused artifact: {FUSED_PATH}"

    model = onnx.load(str(FUSED_PATH))
    onnx.checker.check_model(model, full_check=True)

    assert len(model.graph.input) == 1, "expected one shared input"
    dims = model.graph.input[0].type.tensor_type.shape.dim
    assert len(dims) == 3, "expected a rank-3 input"
    assert dims[1].dim_value == 0, (
        f"length axis is pinned to {dims[1].dim_value}, expected a dynamic "
        f"axis")
    assert dims[2].dim_value == 4
    assert len(model.graph.output) == 1, "expected one averaged output"


def test_regenerating_gives_the_committed_fused_graph(
    tmp_path: pathlib.Path,
) -> None:
    fuse = _load_fuse_script()
    output = tmp_path / "spliceai_fused.onnx"

    fuse.main(["--output", str(output)])

    assert output.read_bytes() == FUSED_PATH.read_bytes(), (
        "the committed fused graph is stale: run "
        "scripts/fuse_onnx_models.py again")


def test_the_fuse_script_never_imports_tensorflow() -> None:
    completed = subprocess.run(
        [sys.executable, "-c",
         ("import importlib.util, sys;"
          f"spec = importlib.util.spec_from_file_location("
          f"'fuse_onnx_models', {str(FUSE_SCRIPT)!r});"
          "module = importlib.util.module_from_spec(spec);"
          "spec.loader.exec_module(module);"
          "print('tensorflow' in sys.modules)")],
        capture_output=True, text=True, check=True)

    assert completed.stdout.strip() == "False"


def _random_one_hot(width: int) -> np.ndarray:
    rng = np.random.default_rng(seed=width)
    x = np.zeros((1, width, 4), dtype=np.float32)
    x[0, np.arange(width), rng.integers(0, 4, size=width)] = 1.0
    return x


@pytest.fixture(scope="module")
def windows() -> dict[str, np.ndarray]:
    """A real donor site at the default width, and a window at MAX_WIDTH.

    On random sequence a narrow window saturates every member to
    P(neither) ~ 1, and a fused graph that lost a member would still agree.
    The donor window drives the splice channels in every member, and at
    MAX_WIDTH the ensemble is not saturated either.
    """
    # Imported here, not at module scope: `test_spliceai_backends` imports
    # the annotator package, which only the fixtures that run a graph need.
    # pylint: disable=import-outside-toplevel
    from tests.test_spliceai_backends import MAX_WIDTH
    return {
        "donor-site-distance-50": _motif_one_hot(_width(50)),
        "random-max-width": _random_one_hot(MAX_WIDTH),
    }


@pytest.fixture(scope="module")
def impl() -> ModuleType:
    pytest.importorskip("onnxruntime")
    # pylint: disable=import-outside-toplevel
    from spliceai_annotator import spliceai_annotator_impl
    return spliceai_annotator_impl


@pytest.fixture(scope="module")
def five_session_predict(
    impl: ModuleType,
) -> Iterator[Callable[[np.ndarray], np.ndarray]]:
    backend = impl.load_spliceai_backend("onnx")
    models = backend.spliceai_load_models()
    yield lambda x: backend.spliceai_predict(models, x)
    backend.spliceai_close()


@pytest.fixture(scope="module")
def fused_backend(impl: ModuleType) -> ModuleType:
    return impl.load_spliceai_backend("onnx-fused")


def _fused_session(impl: ModuleType, model: Any) -> Any:
    ort = pytest.importorskip("onnxruntime")
    onnx_backend = impl.load_spliceai_backend("onnx")
    return ort.InferenceSession(
        model.SerializeToString(),
        sess_options=onnx_backend.spliceai_session_options())


@pytest.mark.parametrize(
    "window", ["donor-site-distance-50", "random-max-width"])
def test_fused_agrees_with_the_five_session_path(
    window: str,
    windows: dict[str, np.ndarray],
    impl: ModuleType,
    fused_backend: ModuleType,
    five_session_predict: Callable[[np.ndarray], np.ndarray],
) -> None:
    """The fused graph is the five members and their mean, nothing less.

    Agreement alone is not enough: the check must also tell a fused graph
    that lost a member from the real one. So the test fuses each set of four
    members and asserts that each moves the output by at least 100x the
    disagreement of the full fused graph, floored at 2 ulp at 1.0 so that
    the check cannot demand bit-exactness.
    """
    x = windows[window]
    fused_models = fused_backend.spliceai_load_models()
    reference = five_session_predict(x)

    fused = fused_backend.spliceai_predict(fused_models, x)

    np.testing.assert_allclose(fused, reference, atol=1e-5)
    own = max(
        float(np.max(np.abs(fused - reference))),
        2 * float(np.spacing(np.float32(1.0))),
    )
    fuse = _load_fuse_script()
    for dropped in range(len(fuse.MEMBER_PATHS)):
        subset = [
            path for i, path in enumerate(fuse.MEMBER_PATHS) if i != dropped]
        session = _fused_session(impl, fuse.fuse_models(subset))
        degraded = float(np.max(np.abs(
            fused_backend.spliceai_predict([session], x) - reference)))
        assert degraded > 1e-5, (
            f"a fused graph without member {dropped + 1} still agrees with "
            f"the five-session path to {degraded:.2e} @ {window}")
        assert own < degraded / 100, (
            f"dropping member {dropped + 1} moves the output by only "
            f"{degraded:.2e} against the full fused graph's {own:.2e} "
            f"@ {window}")
