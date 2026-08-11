"""Escaping that keeps untrusted text on one log line.

Anything caller-supplied that a message interpolates -- a resource name
read out of remote GRR content, a resource query, a piece of an
annotation config -- can carry a line break, and a line break in a
rendered message emits a second, fully-formed-looking record that can
assert the opposite of what the run found (gain#642, gain#655). This
module owns the character set and the escaping; the policy about
*refusing* such text belongs to the boundary that receives it.
"""

import re

# The whole C0/C1 range goes rather than the handful that bite today:
# none of them belongs in a name or a query, and a list tuned to one url
# parser's or one terminal's current quirks is one change away from a
# hole.
#
# U+2028 and U+2029 join them because a line break is not only ``\n``:
# ``str.splitlines`` breaks on both, so anything that post-processes a
# captured log splits there, and a UAX #14 consumer (an html log viewer)
# renders them as mandatory breaks. U+0085 NEL is already in the C1
# range.
UNSAFE_CHARACTER_RE = re.compile(
    r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def _escape_one_character(match: re.Match[str]) -> str:
    code = ord(match.group())
    if code <= 0xFF:
        return f"\\x{code:02x}"
    return f"\\u{code:04x}"


def escape_unsafe_characters(text: str) -> str:
    """Render untrusted text safe to interpolate into ONE log line.

    ``\\xNN``/``\\uNNNN`` rather than ``repr``: it leaves every other
    character untouched, so a reader still sees the text that was
    written, with only the invisible part made visible. The two widths
    matter -- ``\\x2028`` for U+2028 would read as ``\\x20`` followed by
    the literal text ``28``, which is different (and legitimate) text.
    """
    return UNSAFE_CHARACTER_RE.sub(_escape_one_character, text)
