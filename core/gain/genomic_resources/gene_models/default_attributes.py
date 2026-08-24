"""Encoding of the ``atts`` column of the ``default`` gene models format.

The column packs a transcript's attributes into one field as ``key:value``
pairs joined by ``;``. Both delimiters, and the escape character itself, are
backslash-escaped inside keys and values, so that an attribute value holding
either delimiter survives a save/load round trip.
"""

DEFAULT_ATTRIBUTE_SEPARATOR = ";"
DEFAULT_ATTRIBUTE_ASSIGNMENT = ":"
_ESCAPED_CHARS = (
    "\\", DEFAULT_ATTRIBUTE_SEPARATOR, DEFAULT_ATTRIBUTE_ASSIGNMENT,
)


def escape_default_attribute(value: str) -> str:
    """Escape the attribute delimiters in a key or a value."""
    for char in _ESCAPED_CHARS:
        value = value.replace(char, f"\\{char}")
    return value


def unescape_default_attribute(value: str) -> str:
    """Reverse `escape_default_attribute`.

    A backslash not followed by a delimiter or another backslash is literal,
    so free text that happens to carry one -- as NCBI RefSeq notes do --
    reads back unchanged. A backslash directly in front of one of those
    characters is always taken as an escape: the column records nothing that
    would tell an escape apart from a literal backslash there.
    """
    for char in reversed(_ESCAPED_CHARS):
        value = value.replace(f"\\{char}", char)
    return value


def _split_unescaped(
    data: str, separator: str, maxsplit: int = -1,
) -> list[str]:
    """Split `data` on `separator` occurrences that are not escaped."""
    parts: list[str] = []
    start = 0
    index = data.find(separator)
    while index != -1:
        if 0 <= maxsplit <= len(parts):
            break
        escapes = 0
        while index - escapes > start and data[index - escapes - 1] == "\\":
            escapes += 1
        if escapes % 2 == 0:
            parts.append(data[start:index])
            start = index + 1
        index = data.find(separator, index + 1)
    parts.append(data[start:])
    return parts


def format_default_attributes(attributes: dict) -> str:
    """Pack a transcript's attributes into the ``atts`` column."""
    return DEFAULT_ATTRIBUTE_SEPARATOR.join(
        escape_default_attribute(str(key))
        + DEFAULT_ATTRIBUTE_ASSIGNMENT
        + escape_default_attribute(str(value))
        for key, value in attributes.items()
    )


def parse_default_attributes(atts: str) -> dict[str, str]:
    """Unpack the ``atts`` column into the attributes it holds."""
    result = {}
    for fragment in _split_unescaped(atts, DEFAULT_ATTRIBUTE_SEPARATOR):
        if not fragment:
            continue
        pair = _split_unescaped(
            fragment, DEFAULT_ATTRIBUTE_ASSIGNMENT, maxsplit=1)
        if len(pair) != 2:
            raise ValueError(
                f"malformed gene models attribute {fragment!r}; "
                f"expected a 'key{DEFAULT_ATTRIBUTE_ASSIGNMENT}value' pair",
            )
        result[unescape_default_attribute(pair[0])] = \
            unescape_default_attribute(pair[1])
    return result
