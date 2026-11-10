# pylint: disable=C0114,C0116
import pytest
from gain.utils.log_safety import escape_unsafe_characters


@pytest.mark.parametrize("text, expected", [
    ("a\nb", "a\\x0ab"),
    ("\x00", "\\x00"),
    ("\r\t", "\\x0d\\x09"),
    ("\x1f\x7f", "\\x1f\\x7f"),
    ("\x85\x9f", "\\x85\\x9f"),
    ("a\u2028b", "a\\u2028b"),
    ("\u2029", "\\u2029"),
])
def test_escape_unsafe_characters_escapes_each_control_character(
    text: str, expected: str,
) -> None:
    assert escape_unsafe_characters(text) == expected


@pytest.mark.parametrize("text", [
    "", "resource/id v1.0", " ~", "\xa0\xff", "\u2027\u202a", "naïve ✓",
])
def test_escape_unsafe_characters_keeps_safe_text_unchanged(
    text: str,
) -> None:
    assert escape_unsafe_characters(text) == text
