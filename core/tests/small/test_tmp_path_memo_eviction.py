# pylint: disable=C0114,C0116
"""The predicate that picks the memo keys built over a test's tmp_path.

The autouse teardown in ``tests/conftest.py`` evicts these keys from the
process-wide memos. A key it misses hands the next test with the same
``tmp_path`` a stale object. A key it wrongly accepts evicts an entry
another test still uses.
"""
import pytest

from tests.conftest import _names_a_path_under

ROOT = "/work/pytest-of-u/pytest-0/test_x1"


@pytest.mark.parametrize("key", [
    ROOT,
    f"{ROOT}/grr",
    f"file://{ROOT}/grr",
    f"memory://{ROOT}",
    ("file", ROOT),
    ("proto-7", f"file://{ROOT}/sub/dir", 3),
    (1, None, f"{ROOT}/gene_models.txt"),
])
def test_a_key_naming_the_root_or_a_path_below_it_is_accepted(
    key: object,
) -> None:
    assert _names_a_path_under(ROOT)(key)


@pytest.mark.parametrize("key", [
    f"{ROOT}0",
    f"{ROOT}0/grr",
    f"file://{ROOT}0/grr",
    ("file", f"{ROOT}0"),
    "/work/pytest-of-u/pytest-0/test_x",
    ("file", 1, None),
    42,
    None,
])
def test_a_sibling_prefix_or_a_non_string_key_is_rejected(
    key: object,
) -> None:
    assert not _names_a_path_under(ROOT)(key)
