"""Checks for characters that should not appear in short labels."""


def has_invisible_control(text: str) -> bool:
    """Return whether text contains bidi overrides or selected zero-width characters.

    Rejected: U+202A–U+202E, U+2066–U+2069, U+200B, U+2060, and U+FEFF.
    U+200C and U+200D stay allowed.
    """

    for char in text:
        code = ord(char)
        if 0x202A <= code <= 0x202E or 0x2066 <= code <= 0x2069:
            return True
        if code in {0x200B, 0x2060, 0xFEFF}:
            return True
    return False
