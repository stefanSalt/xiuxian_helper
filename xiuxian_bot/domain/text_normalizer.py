from __future__ import annotations

import unicodedata


_KEEP_EXTRA = {"@", "_"}


def normalize_match_text(text: str) -> str:
    """Normalize OCR-prone text for keyword matching without mutating raw content."""

    normalized = unicodedata.normalize("NFKC", text or "")
    chars: list[str] = []
    for ch in normalized:
        if unicodedata.category(ch) == "Cf":
            continue
        folded = ch.casefold()
        if folded.isalnum() or folded in _KEEP_EXTRA:
            chars.append(folded)
    return "".join(chars)
