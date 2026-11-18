"""Regex detectors for identifiers that are not (necessarily) in identity.yaml.

Dates are deliberately not detected: clinical dates matter. Only the DOB (from the
identity file) is redacted.
"""
from __future__ import annotations

import re

_PC = r"[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z]"
_PCT = r"\d[ABCEGHJ-NPRSTV-Z]\d"

# (name, tag, regex); order matters for a single left-to-right pass.
DETECTORS = [
    ("email", "EMAIL", r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}"),
    # North American phone: needs separators or parentheses (bare digit runs hit "id").
    ("phone", "PHONE",
     r"(?<!\d)(?:\+?1[ .\-])?(?:\(\d{3}\)[ .\-]?|\d{3}[ .\-])\d{3}[ .\-]\d{4}(?!\d)"),
    ("postal", "POSTAL", rf"(?<![A-Za-z0-9]){_PC}[ \-]?{_PCT}(?![A-Za-z0-9])"),
    ("zip4", "POSTAL", r"(?<!\d)\d{5}-\d{4}(?!\d)"),
    ("id", "ID:auto", r"(?<!\d)\d{8,}(?!\d)"),
]

_COMBINED = re.compile("|".join(f"({rx})" for _, _, rx in DETECTORS), re.IGNORECASE)


def redact(text: str):
    """Return (redacted_text, counts) where counts keys look like 'auto:phone'."""
    counts: dict = {}

    def sub(m):
        name, tag, _ = DETECTORS[m.lastindex - 1]
        counts[f"auto:{name}"] = counts.get(f"auto:{name}", 0) + 1
        return f"[{tag}]"

    return _COMBINED.sub(sub, text), counts


def scan(text: str) -> list:
    """Names of detector categories that still match in text (no values)."""
    return sorted({DETECTORS[m.lastindex - 1][0] for m in _COMBINED.finditer(text)})
