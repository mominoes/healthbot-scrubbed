"""Fetch files from a PUBLICLY shared Google Drive folder straight to local disk.

Deterministic, no-LLM code, same trust tier as scrub.py. This module intentionally
does no OAuth / authenticated Drive API calls — it only works for folders shared
"Anyone with the link", using gdown's unauthenticated folder-listing trick. That is
a feature, not a limitation: it keeps this path honest about what it can reach,
instead of becoming a backdoor for private content.

Bytes never pass through an LLM: gdown writes each file straight to `out_dir`, and
the only inspection done here is a few header bytes per file, to catch Google's
"can't scan this file for viruses" / quota-exceeded interstitial pages, which Drive
returns as an HTML page instead of the real file.
"""
import re
from pathlib import Path

FOLDER_ID_RE = re.compile(r"(?:/folders/|folderview\?id=|^)([a-zA-Z0-9_-]{10,})")
HTML_SNIFF = re.compile(rb"^\s*<(!doctype\s+html|html)", re.I)


class FetchError(Exception):
    pass


def folder_id(url_or_id: str) -> str:
    s = url_or_id.strip()
    m = FOLDER_ID_RE.search(s)
    if not m:
        raise FetchError(f"could not find a Drive folder id in {url_or_id!r}")
    return m.group(1)


def _classify(path: Path) -> dict:
    size = path.stat().st_size
    if size == 0:
        return {"path": path, "name": path.name, "size": size, "status": "empty"}
    head = path.read_bytes()[:512]
    if HTML_SNIFF.match(head):
        return {"path": path, "name": path.name, "size": size, "status": "blocked_by_google"}
    if path.suffix.lower() != ".pdf":
        return {"path": path, "name": path.name, "size": size, "status": "skipped_non_pdf"}
    return {"path": path, "name": path.name, "size": size, "status": "ready"}


def fetch_public_folder(url_or_id: str, out_dir: Path, max_files: int = 50) -> list[dict]:
    """Download a public folder's files into out_dir and classify each one.

    Returns a list of dicts: {path, name, size, status}, status one of
    "ready" | "skipped_non_pdf" | "blocked_by_google" | "empty".
    Raises FetchError if the folder can't be reached at all (not public, bad id,
    empty, network error).
    """
    import gdown  # imported lazily: only needed for this one command

    out_dir.mkdir(parents=True, exist_ok=True)
    fid = folder_id(url_or_id)
    url = f"https://drive.google.com/drive/folders/{fid}"
    try:
        downloaded = gdown.download_folder(
            url=url, output=str(out_dir), quiet=False, use_cookies=False,
        )
    except Exception as e:  # gdown raises a mix of its own + requests exceptions
        raise FetchError(f"could not download folder: {e}") from e
    if not downloaded:
        raise FetchError(
            "no files found. Check the folder is shared \"Anyone with the link\" and is not empty."
        )
    if len(downloaded) > max_files:
        raise FetchError(
            f"folder has {len(downloaded)} files, more than --max-files {max_files}; "
            "re-run with a higher --max-files if that's expected."
        )
    return [_classify(Path(p)) for p in downloaded if Path(p).is_file()]
