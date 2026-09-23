# pylint: disable=C0116,W0212
"""A ``.state`` is read as UTF-8, the encoding it is written in (gain#1631).

The interpreter fixes its default encoding at start-up, so a test running
in the suite's own (UTF-8) process cannot tell a UTF-8 read from a
default-encoding one. The load runs in a child interpreter whose default
encoding is ASCII instead. A plain ``LC_ALL=C`` is not enough for that:
under the C locale Python turns on UTF-8 mode (PEP 540), so it is switched
off explicitly.
"""
import codecs
import dataclasses
import json
import os
import pathlib
import subprocess
import sys

import yaml
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    setup_directories,
)

_NON_UTF8_ENV = {"LC_ALL": "C", "PYTHONUTF8": "0"}

_NON_ASCII_TEXT = "étag-ü"

_LOAD_IN_CHILD = """
import json, locale, pathlib, sys
from gain.genomic_resources.testing import build_filesystem_test_protocol
proto = build_filesystem_test_protocol(pathlib.Path(sys.argv[1]), repair=False)
state = proto.load_resource_file_state(proto.get_resource("one"), "data.txt")
print(json.dumps({
    "default_encoding": locale.getpreferredencoding(False),
    "change_token": state.change_token,
}))
"""


def test_a_non_ascii_state_loads_under_a_non_utf8_default_encoding(
        tmp_path: pathlib.Path) -> None:
    # Given a state whose text is non-ASCII, stored as UTF-8 bytes -- as a
    # dump with ``allow_unicode=True``, or a writer other than gain, leaves it
    setup_directories(tmp_path, {
        "one": {"genomic_resource.yaml": "", "data.txt": "data"}})
    proto = build_filesystem_test_protocol(tmp_path)
    resource = proto.get_resource("one")
    state = dataclasses.replace(
        proto.build_resource_file_state(resource, "data.txt"),
        change_token=_NON_ASCII_TEXT)
    path = proto._get_resource_file_state_path(resource, "data.txt")
    proto.filesystem.makedirs(os.path.dirname(path), exist_ok=True)
    with proto.filesystem.open(path, "wt", encoding="utf8") as outfile:
        outfile.write(yaml.safe_dump(
            dataclasses.asdict(state), allow_unicode=True))

    # When it is loaded by an interpreter whose default encoding is ASCII
    completed = subprocess.run(
        [sys.executable, "-c", _LOAD_IN_CHILD, str(tmp_path)],
        check=False, capture_output=True, text=True, encoding="utf8",
        env={**os.environ, **_NON_UTF8_ENV}, timeout=60)
    assert completed.returncode == 0, completed.stderr
    loaded = json.loads(completed.stdout)

    # Then the child really did default to something other than UTF-8...
    assert codecs.lookup(loaded["default_encoding"]).name != "utf-8", (
        "the child defaulted to UTF-8, so this test cannot see the defect")
    # ...and the state still came back exactly as written
    assert loaded["change_token"] == _NON_ASCII_TEXT
