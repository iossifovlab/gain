# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""gain-core must declare the third-party packages it imports directly.

h5py and scipy are imported unconditionally by the ann_data 10x readers
but historically arrived only transitively via anndata (gain#711).
"""
import re
from importlib.metadata import requires


def test_gain_core_declares_h5py_and_scipy() -> None:
    declared = {
        re.split(r"[\s<>=!~\[;]", dep, maxsplit=1)[0]
        for dep in requires("gain-core") or []
        if "extra ==" not in dep
    }
    assert "h5py" in declared
    assert "scipy" in declared
