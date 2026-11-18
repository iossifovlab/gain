# pylint: disable=C0114,C0116
"""The baselines are TensorFlow ground truth; regeneration refuses ONNX.

Since #299 the process default is ONNX, so a plain run of a regenerate script
would otherwise rewrite the ground truth from the runtime it checks.
"""
from types import ModuleType

import pytest

from spliceai_annotator import spliceai_annotator_impl as impl
from tests.corpus import regenerate_baseline, regenerate_baseline_extra


@pytest.mark.parametrize(
    "script", [regenerate_baseline, regenerate_baseline_extra],
    ids=["baseline", "baseline_extra"])
def test_regeneration_refuses_a_process_running_onnx(
    script: ModuleType, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(impl, "SPLICEAI_BACKEND_NAME", "onnx")

    def must_not_annotate(*_: object) -> None:
        raise AssertionError("annotated before the backend check")

    for name in ("annotate_corpus", "annotate_extra"):
        monkeypatch.setattr(script, name, must_not_annotate, raising=False)

    with pytest.raises(SystemExit) as refused:
        script.main()

    assert "SPLICEAI_BACKEND=tensorflow" in str(refused.value)
