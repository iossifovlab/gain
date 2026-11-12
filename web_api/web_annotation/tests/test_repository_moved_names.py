# pylint: disable=C0116
"""No ``web_api`` module imports a name that left ``repository``.

The names that moved out of ``gain.genomic_resources.repository``
(gain#2266) warn when a caller reads them from the old module. This
fence lives in ``web_api`` because the ``core`` CI image does not hold
the ``web_api`` tree.
"""
import ast
import pathlib

from gain.genomic_resources import repository

#: The ``web_api`` project root: this file is ``web_annotation/tests/``.
WEB_API_SRC = pathlib.Path(__file__).resolve().parents[2]


def test_no_web_api_module_imports_a_moved_name_from_the_old_module(
) -> None:
    views = WEB_API_SRC / "web_annotation" / "resources" / "views.py"
    assert views.exists()
    offenders: list[str] = []
    for py in sorted(WEB_API_SRC.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        offenders.extend(
            f"{py.relative_to(WEB_API_SRC).as_posix()}: {alias.name}"
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module == repository.__name__
            for alias in node.names
            if alias.name in repository._MOVED)

    assert not offenders, (
        f"import these names from their new modules: {offenders}")
