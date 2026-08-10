# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""gain-core must declare the third-party packages it imports directly.

h5py and scipy are imported unconditionally by the ann_data 10x readers
but historically arrived only transitively via anndata (gain#711).

This reads ``core/pyproject.toml`` itself rather than installed dist-info,
which can lag the source under an editable install -- the pattern of
``test_legacy_entry_point_keys_are_declared_in_pyproject``.
"""
import pathlib
import re
import tomllib

CORE_PYPROJECT = pathlib.Path(__file__).parents[1] / "pyproject.toml"


def test_gain_core_declares_h5py_and_scipy() -> None:
    with CORE_PYPROJECT.open("rb") as infile:
        pyproject = tomllib.load(infile)

    # Resolved by walking up from __file__, so confirm we landed on core's
    # pyproject and not the workspace root's -- a wrong path that still
    # parsed would make this test pass while pinning nothing.
    assert pyproject["project"]["name"] == "gain-core"

    declared = {
        re.split(r"[^A-Za-z0-9._-]", dep, maxsplit=1)[0]
        for dep in pyproject["project"]["dependencies"]
    }
    assert "h5py" in declared
    assert "scipy" in declared
