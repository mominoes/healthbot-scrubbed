"""PDF -> Markdown -> find/replace -> verify, all in memory. Nothing is written here."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import detectors
from .identity import Identity, compile_replacer, normalize, variant_regex

MIN_PAGE_CHARS = 20
# Normalized variants at least this long are searched as substrings of the normalized
# text; shorter ones (e.g. a 4-letter first name) use a word-bounded regex instead, to
# avoid false "leaks" from concatenated unrelated text.
MIN_SUBSTRING_LEN = 5

OK, NEEDS_OCR, ENCRYPTED, LEAK, ERROR = "ok", "needs_ocr", "encrypted", "leak", "error"


@dataclass
class Result:
    status: str
    text: str = ""              # only populated when status == "ok"
    counts: dict = field(default_factory=dict)
    pages: list = field(default_factory=list)  # redacted text per page, status == "ok" only
    findings: list = field(default_factory=list)  # category names (never values)
    detail: str = ""


def scrub_text(text: str, identity: Identity):
    """Replace identity variants (single pass, longest first), then run detectors."""
    counts: dict = {}
    rx, variants = compile_replacer(identity)
    if rx is not None:
        def sub(m):
            v = variants[m.lastindex - 1]
            counts[v.count_key] = counts.get(v.count_key, 0) + 1
            return f"[{v.tag}]"
        text = rx.sub(sub, text)
    text, auto = detectors.redact(text)
    counts.update(auto)
    return text, counts


def verify_text(text: str, identity: Identity) -> list:
    """Independent leak check. Returns category names that still match (empty = clean).

    Normalizes (lowercase, strip non-alphanumerics) and searches for every normalized
    variant, then re-runs the detectors. Build `identity` from the FULL identity file,
    not from whatever the replacer used. Also reused by `record write`.
    """
    findings = set()
    norm = normalize(text)
    low = text.lower()
    for v in identity.variants():
        n = normalize(v.text)
        if not n:
            continue
        if len(n) >= MIN_SUBSTRING_LEN:
            if n in norm:
                findings.add(v.count_key)
        elif re.search(variant_regex(v.text), low, re.IGNORECASE):
            findings.add(v.count_key)
    findings.update(f"auto:{d}" for d in detectors.scan(text))
    return sorted(findings)


def _extract_pages(doc) -> list:
    import contextlib
    import io
    # pymupdf4llm prints parser warnings to stdout; keep stdout clean for `show`.
    with contextlib.redirect_stdout(io.StringIO()):
        import pymupdf4llm
        chunks = pymupdf4llm.to_markdown(doc, page_chunks=True, show_progress=False)
    return [c["text"] for c in chunks]


def scrub_pdf(path, identity: Identity, verify_identity: Identity | None = None) -> Result:
    """Scrub one PDF. Fails closed: anything but status 'ok' carries no text."""
    import pymupdf
    try:
        doc = pymupdf.open(str(path))
    except Exception:
        return Result(ERROR, detail="cannot open")
    try:
        if doc.needs_pass or doc.is_encrypted:
            return Result(ENCRYPTED)
        if doc.page_count == 0:
            return Result(NEEDS_OCR, detail="no pages")
        for page in doc:
            if len(page.get_text().strip()) < MIN_PAGE_CHARS:
                return Result(NEEDS_OCR, detail=f"page {page.number + 1}")
        raw_pages = _extract_pages(doc)
    except Exception:
        return Result(ERROR, detail="extraction failed")
    finally:
        doc.close()

    counts: dict = {}
    pages = []
    for raw in raw_pages:
        t, c = scrub_text(raw, identity)
        pages.append(t)
        for k, n in c.items():
            counts[k] = counts.get(k, 0) + n
    text = "\n\n".join(f"<!-- page {i} -->\n{t.strip()}" for i, t in enumerate(pages, 1))
    findings = verify_text(text, verify_identity or identity)
    if findings:
        return Result(LEAK, counts=counts, findings=findings)
    return Result(OK, text=text, counts=counts, pages=pages)
