"""Turkish-text normalization shared by KAP classification and matching."""

from __future__ import annotations

_TURKISH_FOLD = str.maketrans(
    {
        "İ": "i",
        "I": "i",
        "ı": "i",
        "Ş": "s",
        "ş": "s",
        "Ğ": "g",
        "ğ": "g",
        "Ü": "u",
        "ü": "u",
        "Ö": "o",
        "ö": "o",
        "Ç": "c",
        "ç": "c",
    }
)


def fold_turkish(text: str) -> str:
    """ASCII-fold Turkish letters and lowercase, safely.

    Not the same as a naive ``text.lower()``: Python's default lowercasing
    mishandles the Turkish dotted/dotless I pair — ``'İ'.lower()`` yields
    ``'i̇'`` (a plain ``i`` plus a combining dot-above, U+0307), and
    ``'I'.lower()`` yields plain ``'i'`` too (not the Turkish dotless
    ``'ı'``) under Python's locale-independent Unicode rules. Both of
    those are wrong for comparing Turkish text case-insensitively, so
    ``İ``/``I``/``ı`` are all explicitly folded to plain ``i`` here
    before anything else runs.
    """
    return text.translate(_TURKISH_FOLD).lower()
