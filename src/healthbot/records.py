"""Records store: frontmatter schema, validated writes, and generated indexes."""
import datetime as _dt
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

TYPE_FOLDERS = {
    "lab": "labs",
    "imaging": "imaging",
    "clinical-note": "clinical-notes",
    "immunization": "immunizations",
    "medication": "medications",
    "other": "other",
}
FOLDERS = sorted(set(TYPE_FOLDERS.values()))
REQUIRED = ("id", "date", "type", "title", "provider", "source", "tags")
ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*$")
SOURCE_RE = re.compile(r"^doc-\d{4,}$")
GENERATED = {"INDEX.md", "immunizations.md", "lab-trends.md"}

_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---[ \t]*(?:\n|\Z)(.*)\Z", re.S)
_NUM_RE = re.compile(r"^\s*([<>]=?|≤|≥)?\s*(-?\d+(?:\.\d+)?)\s*$")
_PAGE_RE = re.compile(r"(\d+)")


class RecordError(ValueError):
    """Validation problem. Messages never include record values."""


@dataclass
class Record:
    meta: dict
    body: str
    path: Path | None = None


def _datestr(v):
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.strftime("%Y-%m-%d")
    return str(v)


def parse_record(text: str) -> Record:
    m = _FM_RE.match(text.lstrip("\ufeff"))
    if not m:
        raise RecordError("missing YAML frontmatter (--- block at top)")
    try:
        meta = yaml.safe_load(m.group(1))
    except (yaml.YAMLError, ValueError):
        raise RecordError("frontmatter is not valid YAML")
    if not isinstance(meta, dict):
        raise RecordError("frontmatter must be a mapping")
    return Record(meta, m.group(2))


def validate(meta: dict) -> list:
    """Return a list of problem descriptions (field names only, no values)."""
    errs = []
    for k in REQUIRED:
        if k not in meta or meta[k] in (None, ""):
            if k == "tags" and isinstance(meta.get(k), list):
                continue
            errs.append(f"missing field: {k}")
    if errs:
        return errs
    unknown = set(meta) - set(REQUIRED)
    if unknown:
        errs.append("unknown field(s): " + ", ".join(sorted(map(str, unknown))))
    date = _datestr(meta["date"])
    try:
        _dt.date.fromisoformat(date)
    except ValueError:
        errs.append("date must be YYYY-MM-DD")
    if meta["type"] not in TYPE_FOLDERS:
        errs.append("type must be one of: " + ", ".join(TYPE_FOLDERS))
    if not isinstance(meta["id"], str) or not ID_RE.match(meta["id"]):
        errs.append("id must look like YYYY-MM-DD-type-slug (lowercase, hyphens)")
    elif meta["id"][:10] != date:
        errs.append("id date prefix must equal date")
    for k in ("title", "provider"):
        if not isinstance(meta[k], str):
            errs.append(f"{k} must be a string")
    if not isinstance(meta["source"], str) or not SOURCE_RE.match(meta["source"]):
        errs.append("source must be a neutral id like doc-0001")
    tags = meta["tags"]
    if not isinstance(tags, list) or not all(isinstance(t, (str, int, float)) for t in tags):
        errs.append("tags must be a list")
    return errs


def record_path(records_dir: Path, meta: dict) -> Path:
    return Path(records_dir) / TYPE_FOLDERS[meta["type"]] / f"{meta['id']}.md"


def write_record(text: str, records_dir, identity, force=False):
    """Validate, verify, and write. Returns (path, None) or (None, (kind, [details])).

    kind is 'invalid', 'leak' (details are category names only) or 'exists'.
    """
    from .scrub import verify_text
    try:
        rec = parse_record(text)
    except RecordError as e:
        return None, ("invalid", [str(e)])
    errs = validate(rec.meta)
    if errs:
        return None, ("invalid", errs)
    findings = verify_text(text, identity)
    if findings:
        return None, ("leak", findings)
    path = record_path(records_dir, rec.meta)
    if path.exists() and not force:
        return None, ("exists", ["record exists (use --force to overwrite)"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path, None


# ---------------------------------------------------------------- reading / index

def load_records(records_dir) -> list:
    records_dir = Path(records_dir)
    out = []
    for folder in FOLDERS:
        for p in sorted((records_dir / folder).glob("*.md")):
            if p.name in GENERATED:
                continue
            try:
                rec = parse_record(p.read_text(encoding="utf-8"))
            except (RecordError, OSError):
                continue
            if validate(rec.meta):
                continue
            rec.path = p
            out.append(rec)
    out.sort(key=lambda r: (_datestr(r.meta["date"]), r.meta["id"]))
    return out


def _cell(v) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ").strip()


def parse_fact_rows(body: str) -> list:
    """Rows of `| Test | Value | Unit | Ref range | Flag | Page |` tables in the body.

    Returns dicts with test, value, unit, ref, flag, page (strings). Header and
    separator rows are skipped. Rows with fewer than 6 cells are ignored.
    """
    rows = []
    for line in body.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", s.strip("|"))]
        if len(cells) != 6:
            continue
        if cells[0].lower() == "test" or set("".join(cells)) <= set("-: "):
            continue
        rows.append(dict(zip(("test", "value", "unit", "ref", "flag", "page"),
                             (c.replace("\\|", "|") for c in cells))))
    return rows


def numeric_value(v: str):
    """Return (qualifier, number_str) for a numeric cell, else None."""
    m = _NUM_RE.match(v)
    if not m:
        return None
    return (m.group(1) or "", m.group(2))


def _table(headers, rows) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return "\n".join(lines) + "\n"


def _page(p: str) -> str:
    m = _PAGE_RE.search(p or "")
    return f"p{m.group(1)}" if m else ""


def build_index(records, records_dir) -> str:
    rd = Path(records_dir)
    rows = [(_datestr(r.meta["date"]), r.meta["type"], r.meta["title"],
             ", ".join(str(t) for t in r.meta["tags"]), r.path.relative_to(rd).as_posix())
            for r in records]
    return ("# Records index\n\n<!-- generated by `healthbot index`; do not edit -->\n\n"
            + _table(["Date", "Type", "Title", "Tags", "Path"], rows))


def build_trends(records, records_dir) -> str:
    rd = Path(records_dir)
    rows = []
    for r in records:
        if r.meta["type"] != "lab":
            continue
        rel = r.path.relative_to(rd).as_posix()
        for f in parse_fact_rows(r.body):
            nv = numeric_value(f["value"])
            if nv is None:
                continue
            rows.append((f["test"], _datestr(r.meta["date"]), "".join(nv), f["unit"],
                         f["ref"], f["flag"], rel, _page(f["page"])))
    rows.sort(key=lambda x: (x[0].lower(), x[1]))
    return ("# Lab trends\n\n<!-- generated by `healthbot index`; do not edit -->\n\n"
            + _table(["Test", "Date", "Value", "Unit", "Ref range", "Flag", "Record", "Page"],
                     rows))


def build_immunizations(records, records_dir) -> str:
    rd = Path(records_dir)
    rows = []
    for r in records:
        if r.meta["type"] != "immunization":
            continue
        rel = r.path.relative_to(rd).as_posix()
        facts = parse_fact_rows(r.body)
        d = _datestr(r.meta["date"])
        if facts:
            for f in facts:
                detail = " ".join(x for x in (f["value"], f["unit"]) if x)
                rows.append((d, f["test"], detail, rel, _page(f["page"])))
        else:
            rows.append((d, r.meta["title"], "", rel, ""))
    rows.sort(key=lambda x: (x[0], x[1].lower()))
    return ("# Immunizations\n\n<!-- generated by `healthbot index`; do not edit -->\n\n"
            + _table(["Date", "Vaccine", "Detail", "Record", "Page"], rows))


def rebuild_index(records_dir) -> dict:
    """Regenerate INDEX.md, immunizations.md, lab-trends.md. Returns counts."""
    rd = Path(records_dir)
    records = load_records(rd)
    outputs = {
        rd / "INDEX.md": build_index(records, rd),
        rd / "immunizations" / "immunizations.md": build_immunizations(records, rd),
        rd / "labs" / "lab-trends.md": build_trends(records, rd),
    }
    for p, content in outputs.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return {"records": len(records)}
