import sys
import types

import pytest

from healthbot import cli
from healthbot.drive_fetch import FetchError, _classify, fetch_public_folder, folder_id


def test_folder_id_from_url():
    assert folder_id("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp") \
        == "1AbCdEfGhIjKlMnOp"
    assert folder_id("1AbCdEfGhIjKlMnOp") == "1AbCdEfGhIjKlMnOp"


def test_folder_id_rejects_garbage():
    with pytest.raises(FetchError):
        folder_id("not a drive link")


def test_classify_ready_pdf(tmp_path):
    p = tmp_path / "labs.pdf"
    p.write_bytes(b"%PDF-1.4\n...")
    assert _classify(p)["status"] == "ready"


def test_classify_skips_non_pdf(tmp_path):
    p = tmp_path / "notes.docx"
    p.write_bytes(b"PK\x03\x04binarycontent")
    assert _classify(p)["status"] == "skipped_non_pdf"


def test_classify_catches_google_virus_scan_interstitial(tmp_path):
    p = tmp_path / "bigfile.pdf"
    p.write_bytes(b"<!DOCTYPE html><html>Google Drive can't scan this file</html>")
    assert _classify(p)["status"] == "blocked_by_google"


def test_classify_empty_file(tmp_path):
    p = tmp_path / "empty.pdf"
    p.write_bytes(b"")
    assert _classify(p)["status"] == "empty"


def _fake_gdown(monkeypatch, files_to_write):
    """Stub out gdown.download_folder: writes the given files into output, returns paths."""
    fake = types.ModuleType("gdown")

    def download_folder(url, output, quiet, use_cookies):
        from pathlib import Path
        out = Path(output)
        out.mkdir(parents=True, exist_ok=True)
        paths = []
        for name, content in files_to_write.items():
            p = out / name
            p.write_bytes(content)
            paths.append(str(p))
        return paths

    fake.download_folder = download_folder
    monkeypatch.setitem(sys.modules, "gdown", fake)


def test_fetch_public_folder_classifies_each_file(tmp_path, monkeypatch):
    _fake_gdown(monkeypatch, {
        "lab.pdf": b"%PDF-1.4\nreal pdf",
        "notes.gdoc": b"not a real export",
        "quota.pdf": b"<!DOCTYPE html>too many users have viewed this file</html>",
    })
    out = tmp_path / "out"
    results = fetch_public_folder("https://drive.google.com/drive/folders/1xyzABCDEFGHIJKLMNOP", out)
    by_name = {r["name"]: r["status"] for r in results}
    assert by_name["lab.pdf"] == "ready"
    assert by_name["notes.gdoc"] == "skipped_non_pdf"
    assert by_name["quota.pdf"] == "blocked_by_google"


def test_fetch_public_folder_empty_raises(tmp_path, monkeypatch):
    _fake_gdown(monkeypatch, {})
    with pytest.raises(FetchError):
        fetch_public_folder("1xyzABCDEFGHIJKLMNOP", tmp_path / "out")


def test_fetch_public_folder_too_many_files_raises(tmp_path, monkeypatch):
    _fake_gdown(monkeypatch, {f"f{i}.pdf": b"%PDF-1.4" for i in range(5)})
    with pytest.raises(FetchError):
        fetch_public_folder("1xyzABCDEFGHIJKLMNOP", tmp_path / "out", max_files=3)


def test_cli_moves_skipped_files_aside(tmp_path, monkeypatch):
    _fake_gdown(monkeypatch, {
        "lab.pdf": b"%PDF-1.4\nreal pdf",
        "quota.pdf": b"<!DOCTYPE html>blocked</html>",
    })
    out = tmp_path / "incoming"
    args = cli.build_parser().parse_args(
        ["fetch-drive-folder", "1xyzABCDEFGHIJKLMNOP", "--out", str(out)])
    code = args.func(args)
    assert code == 0
    assert (out / "lab.pdf").is_file()
    assert (out / "_skipped" / "quota.pdf").is_file()
    assert not (out / "quota.pdf").exists()
