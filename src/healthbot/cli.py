"""healthbot command line interface."""
import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

CHUNK_CHARS = 20000
ID_RE = re.compile(r"^doc-\d{4,}$")


def _repo_root() -> Path:
    env = os.environ.get("HEALTHBOT_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


def _not_implemented(args):
    print("not implemented", file=sys.stderr)
    return 1


# ---------------------------------------------------------------- shared helpers

class _Dirs:
    def __init__(self, data_dir):
        self.data = Path(data_dir)
        self.incoming = self.data / "incoming"
        self.private = self.data / "private"
        self.quarantine = self.data / "quarantine"
        self.manifest = self.private / "manifest.json"


def _load_manifest(dirs):
    if dirs.manifest.exists():
        return json.loads(dirs.manifest.read_text(encoding="utf-8"))
    return {"version": 1, "docs": {}}


def _save_manifest(dirs, m):
    dirs.private.mkdir(parents=True, exist_ok=True)
    tmp = dirs.manifest.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, dirs.manifest)


def _load_identity(args):
    from .identity import load_identity
    path = Path(args.identity).expanduser() if args.identity else _repo_root() / "identity.yaml"
    if not path.is_file():
        print("ERROR: identity file not found. Copy identity.example.yaml to identity.yaml "
              "and edit it in your own editor.", file=sys.stderr)
        return None
    return load_identity(path)


def _dirs(args):
    return _Dirs(args.data_dir or _repo_root() / "data")


def _unescape(s):
    return re.sub(r"\\(.)", r"\1", s)


def _expand_arg(arg):
    """Turn one CLI argument (maybe quoted / backslash-escaped / several pasted paths)
    into a list of Paths, or [] if nothing exists."""
    cands = [[arg]]
    stripped = arg.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in "'\"":
        cands.append([stripped[1:-1]])
    cands.append([_unescape(stripped)])
    try:
        cands.append(shlex.split(stripped))
    except ValueError:
        pass
    for group in cands:
        paths = [Path(os.path.expanduser(p)) for p in group if p]
        if paths and all(p.exists() for p in paths):
            return paths
    return []


def _collect(args_paths, dirs):
    """Return (list of (Path, from_incoming), errors, skipped_non_pdf)."""
    errors, skipped = 0, 0
    files = []
    if not args_paths:
        roots = [dirs.incoming]
    else:
        roots = []
        for i, a in enumerate(args_paths, 1):
            found = _expand_arg(a)
            if not found:
                print(f"ERROR: argument #{i}: path not found")
                errors += 1
            roots += found
    incoming = dirs.incoming.resolve()
    seen = set()
    for root in roots:
        if root.is_dir():
            cand = sorted(p for p in root.rglob("*") if p.is_file())
        elif root.is_file():
            cand = [root]
        else:
            cand = []
        for p in cand:
            rp = p.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            if p.name.startswith("."):
                continue
            if p.suffix.lower() != ".pdf":
                skipped += 1
                continue
            files.append((p, rp.is_relative_to(incoming)))
    return files, errors, skipped


def _fmt_counts(counts):
    if not counts:
        return "none"
    return ", ".join(f"{k}×{n}" for k, n in counts.items())


# ---------------------------------------------------------------- scrub

def cmd_scrub(args):
    from .scrub import OK, scrub_pdf
    identity = _load_identity(args)
    if identity is None:
        return 2
    dirs = _dirs(args)
    for d in (dirs.incoming, dirs.private, dirs.quarantine):
        d.mkdir(parents=True, exist_ok=True)
    files, errors, skipped = _collect(args.paths, dirs)
    manifest = _load_manifest(dirs)
    n_ok = n_q = n_dup = 0
    for path, from_incoming in files:
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        dup = next((d for d in manifest["docs"].values() if d["sha256"] == sha), None)
        if dup:
            n_dup += 1
            print(f"SKIPPED {dup['id']} (duplicate)")
            if from_incoming:
                path.unlink()  # byte-identical copy is already archived
            continue
        nums = [int(k[4:]) for k in manifest["docs"]]
        doc_id = f"doc-{(max(nums) if nums else 0) + 1:04d}"
        res = scrub_pdf(path, identity)
        ok = res.status == OK
        dest_dir = dirs.private if ok else dirs.quarantine
        dest = dest_dir / f"{doc_id}.pdf"
        if from_incoming:
            shutil.move(str(path), str(dest))
        else:
            shutil.copy2(str(path), str(dest))
        manifest["docs"][doc_id] = {
            "id": doc_id, "sha256": sha, "original_name": path.name,
            "status": "ok" if ok else "quarantined",
            "reason": None if ok else res.status,
            "pages": len(res.pages) if ok else None,
            "archived": str(dest.relative_to(dirs.data)),
            "added": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _save_manifest(dirs, manifest)
        if ok:
            n_ok += 1
            print(f"OK {doc_id} ({_fmt_counts(res.counts)})")
        else:
            n_q += 1
            why = res.status
            if res.findings:
                why += ": " + ", ".join(res.findings)
            print(f"QUARANTINED {doc_id} ({why})")
    print(f"Summary: {n_ok} ok, {n_q} quarantined, {n_dup} duplicate"
          + (f", {skipped} non-PDF skipped" if skipped else "")
          + (f", {errors} path error(s)" if errors else ""))
    return 1 if errors else 0


# ---------------------------------------------------------------- fetch-drive-folder

def cmd_fetch_drive_folder(args):
    from .drive_fetch import fetch_public_folder, FetchError
    dirs = _dirs(args)
    out_dir = Path(args.out) if args.out else dirs.incoming
    try:
        results = fetch_public_folder(args.url, out_dir, max_files=args.max_files)
    except FetchError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    skipped_dir = out_dir / "_skipped"
    n_ready = n_skip = 0
    for r in results:
        if r["status"] == "ready":
            n_ready += 1
            print(f"READY {r['name']} ({r['size']} bytes)")
        else:
            n_skip += 1
            skipped_dir.mkdir(exist_ok=True)
            Path(r["path"]).rename(skipped_dir / r["path"].name)
            print(f"SKIPPED {r['name']} ({r['status']})")
    print(f"Summary: {n_ready} ready in {out_dir}, {n_skip} skipped")
    if n_ready:
        print(f"Next: healthbot scrub {out_dir}")
    return 0


# ---------------------------------------------------------------- show

def _parse_pages(spec, n):
    pages = set()
    for part in spec.split(","):
        part = part.strip()
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise ValueError("bad --pages spec (use e.g. 2 or 1-3 or 1,3-4)")
        a = int(m.group(1))
        b = int(m.group(2) or a)
        if a < 1 or b < a:
            raise ValueError("bad --pages spec")
        pages.update(range(a, min(b, n) + 1))
    if not pages:
        raise ValueError(f"no such pages (document has {n})")
    return sorted(pages)


def cmd_show(args):
    from .scrub import OK, scrub_pdf
    if not ID_RE.match(args.id):
        print("ERROR: document id must look like doc-0001", file=sys.stderr)
        return 2
    identity = _load_identity(args)
    if identity is None:
        return 2
    dirs = _dirs(args)
    entry = _load_manifest(dirs)["docs"].get(args.id)
    pdf = dirs.private / f"{args.id}.pdf"
    if entry is None or entry.get("status") != "ok" or not pdf.is_file():
        print(f"ERROR: {args.id} is unknown or was quarantined", file=sys.stderr)
        return 1
    res = scrub_pdf(pdf, identity)
    if res.status != OK:
        print(f"ERROR: {args.id} failed verification ({res.status}); no text shown",
              file=sys.stderr)
        return 1
    n = len(res.pages)
    try:
        sel = _parse_pages(args.pages, n) if args.pages else list(range(1, n + 1))
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    out, used = [], []
    total = 0
    for p in sel:
        chunk = f"<!-- page {p} -->\n{res.pages[p - 1].strip()}"
        if used and total + len(chunk) > CHUNK_CHARS:
            break
        out.append(chunk)
        used.append(p)
        total += len(chunk) + 2  # a single oversized page is still shown whole
    print("\n\n".join(out))
    rest = [p for p in sel if p not in used]
    if rest:
        print(f"\n<!-- output truncated at ~{CHUNK_CHARS} chars: showed pages "
              f"{used[0]}-{used[-1]} of {n}. Use --pages {rest[0]}-{rest[-1]} "
              f"to read the rest. -->")
    return 0


# ---------------------------------------------------------------- records

def cmd_record_write(args):
    from .records import write_record
    identity = _load_identity(args)
    if identity is None:
        return 2
    dirs = _dirs(args)
    text = sys.stdin.read()
    path, err = write_record(text, dirs.data / "records", identity, force=args.force)
    if err is None:
        print(f"WROTE {path.relative_to(dirs.data / 'records').as_posix()}")
        return 0
    kind, details = err
    if kind == "leak":
        print("REFUSED: record contains identifiers (categories: "
              + ", ".join(details) + "). Nothing written.", file=sys.stderr)
    elif kind == "exists":
        print("REFUSED: " + details[0], file=sys.stderr)
    else:
        print("INVALID: " + "; ".join(details), file=sys.stderr)
    return 1


def cmd_index(args):
    from .records import rebuild_index
    dirs = _dirs(args)
    info = rebuild_index(dirs.data / "records")
    print(f"Indexed {info['records']} record(s): INDEX.md, immunizations/immunizations.md, "
          "labs/lab-trends.md")
    return 0


# ---------------------------------------------------------------- parser

def build_parser():
    p = argparse.ArgumentParser(prog="healthbot")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--identity", help="identity YAML (default: ./identity.yaml in repo root)")
        sp.add_argument("--data-dir", help=argparse.SUPPRESS)

    s = sub.add_parser("scrub", help="scrub PDFs (paths anywhere; default data/incoming)")
    s.add_argument("paths", nargs="*", help="files or directories; omit for data/incoming")
    common(s)
    s.set_defaults(func=cmd_scrub)

    s = sub.add_parser("fetch-drive-folder",
                        help="download files from a PUBLIC Google Drive folder (no auth) "
                             "straight to disk; bytes never pass through an LLM")
    s.add_argument("url", help="Drive folder URL or id; must be shared \"Anyone with the link\"")
    s.add_argument("--out", help="destination dir (default: data/incoming)")
    s.add_argument("--max-files", type=int, default=50, help="refuse folders larger than this")
    common(s)
    s.set_defaults(func=cmd_fetch_drive_folder)

    s = sub.add_parser("show", help="print the scrubbed Markdown of an archived document")
    s.add_argument("id", help="neutral document id, e.g. doc-0001")
    s.add_argument("--pages", help="page range, e.g. 1-3 or 2 or 1,3-4")
    common(s)
    s.set_defaults(func=cmd_show)

    r = sub.add_parser("record", help="record operations")
    rsub = r.add_subparsers(dest="record_command", required=True)
    w = rsub.add_parser("write", help="validate, verify and write a record from stdin")
    w.add_argument("--stdin", action="store_true", required=True,
                   help="read the full record (frontmatter + body) from stdin")
    w.add_argument("--force", action="store_true", help="overwrite an existing record")
    common(w)
    w.set_defaults(func=cmd_record_write)

    s = sub.add_parser("index", help="rebuild records index")
    common(s)
    s.set_defaults(func=cmd_index)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
