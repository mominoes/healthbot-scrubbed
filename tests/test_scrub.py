import dataclasses
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from healthbot import cli
from healthbot.identity import load_identity
from healthbot.scrub import scrub_pdf

FIX = Path(__file__).parent / "fixtures"
IDENT = FIX / "identity.yaml"
PDFS = FIX / "pdfs"
OK_FIXTURES = ["lab-2025-03-lipid.pdf", "lab-2026-01-lipid-a1c.pdf",
               "clinic-2026-01-annual-physical.pdf", "immunization-record.pdf",
               "imaging-2025-11-echo.pdf"]
SCANNED = "scanned-page.pdf"

pytestmark = pytest.mark.skipif(not PDFS.is_dir(), reason="fixtures missing")


@pytest.fixture
def identity():
    return load_identity(IDENT)


@pytest.fixture
def env(tmp_path):
    data = tmp_path / "data"
    for d in ("incoming", "private", "quarantine", "records"):
        (data / d).mkdir(parents=True)
    return data


def run(capsys, *argv, data):
    args = cli.build_parser().parse_args(
        [argv[0], *argv[1:], "--identity", str(IDENT), "--data-dir", str(data)])
    code = args.func(args)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def identifier_strings(identity):
    s = ["testperson", "jonathan", "1984", "09/07/1984", "jul 9, 1984", "9, 1984"]
    s += [identity.dob, *identity.phones, *identity.emails, *(v for _, v in identity.ids)]
    s += [p for a in identity.addresses for p in a.split(",")[:1]]
    return [x.lower() for x in s if x]


@pytest.mark.parametrize("name", OK_FIXTURES)
def test_fixture_clean_and_ok(name, identity):
    res = scrub_pdf(PDFS / name, identity)
    assert res.status == "ok", (res.status, res.findings)
    low = res.text.lower()
    for s in identifier_strings(identity):
        assert s not in low
    assert "<!-- page 1 -->" in res.text
    assert res.counts  # something was redacted in every fixture


def test_clinical_content_survives(identity):
    lab = scrub_pdf(PDFS / "lab-2025-03-lipid.pdf", identity).text
    assert "LDL" in lab and "4.1" in lab
    assert any(l.strip().startswith("|") and "LDL" in l for l in lab.splitlines())
    imm = scrub_pdf(PDFS / "immunization-record.pdf", identity).text
    assert "Tdap" in imm and "2016-05-02" in imm
    assert "LVEF" in scrub_pdf(PDFS / "imaging-2025-11-echo.pdf", identity).text


def test_split_name_and_dob_formats(identity):
    r1 = scrub_pdf(PDFS / "clinic-2026-01-annual-physical.pdf", identity)
    assert r1.counts.get("name", 0) >= 1 and "[EMAIL]" in r1.text
    r2 = scrub_pdf(PDFS / "lab-2026-01-lipid-a1c.pdf", identity)
    assert r2.counts.get("dob", 0) >= 1


def test_scanned_is_quarantined(identity):
    res = scrub_pdf(PDFS / SCANNED, identity)
    assert res.status == "needs_ocr" and res.text == ""


@pytest.mark.parametrize("field,empty", [("dob", ""), ("names", []), ("ids", [])])
def test_verifier_independent_of_replacer(identity, field, empty):
    reduced = dataclasses.replace(identity, **{field: empty})
    if field == "names":
        reduced = dataclasses.replace(reduced, aliases=[])
    res = scrub_pdf(PDFS / "lab-2025-03-lipid.pdf", reduced, verify_identity=identity)
    assert res.status == "leak" and res.text == "" and res.findings
    assert not any(c.isdigit() for c in "".join(res.findings))  # categories only


def test_cli_scrub_and_show(capsys, env, tmp_path):
    src = tmp_path / "Jonathan Testperson labs.pdf"  # name-bearing filename
    shutil.copy(PDFS / "lab-2026-01-lipid-a1c.pdf", src)
    before = src.read_bytes()
    code, out, err = run(capsys, "scrub", str(src), data=env)
    assert code == 0 and out.startswith("OK doc-0001 (") and "QUARANTINED" not in out
    allout = out + err
    for s in ["testperson", "jonathan", "labs", str(tmp_path).lower(), "lab-2026"]:
        assert s not in allout.lower()
    assert src.read_bytes() == before  # outside file copied, never moved
    assert (env / "private" / "doc-0001.pdf").is_file()
    man = json.loads((env / "private" / "manifest.json").read_text())
    entry = man["docs"]["doc-0001"]
    assert entry["sha256"] == hashlib.sha256(before).hexdigest()

    # re-run is a no-op
    code, out, _ = run(capsys, "scrub", str(src), data=env)
    assert "SKIPPED doc-0001 (duplicate)" in out and "0 ok" in out
    assert len(json.loads((env / "private" / "manifest.json").read_text())["docs"]) == 1

    code, out, _ = run(capsys, "show", "doc-0001", data=env)
    assert code == 0 and "HbA1c" in out and "[NAME]" in out and "testperson" not in out.lower()
    code, out, _ = run(capsys, "show", "doc-0001", "--pages", "1", data=env)
    assert code == 0 and out.startswith("<!-- page 1 -->")
    assert run(capsys, "show", "doc-0001", "--pages", "9", data=env)[0] == 2
    assert run(capsys, "show", "doc-0042", data=env)[0] == 1


def test_cli_incoming_moved_and_quarantine(capsys, env):
    for n in (OK_FIXTURES[0], SCANNED):
        shutil.copy(PDFS / n, env / "incoming" / n)
    code, out, err = run(capsys, "scrub", data=env)
    assert code == 0
    assert "OK doc-0001" in out and "QUARANTINED doc-0002 (needs_ocr)" in out
    assert "lab-2025" not in out + err and "scanned-page" not in out + err
    assert not list((env / "incoming").glob("*.pdf"))  # moved, not copied
    assert (env / "quarantine" / "doc-0002.pdf").is_file()
    assert not (env / "private" / "doc-0002.pdf").exists()
    code, _, err = run(capsys, "show", "doc-0002", data=env)
    assert code == 1


def test_cli_leak_quarantines(capsys, env, tmp_path, monkeypatch):
    # replacer gets a reduced identity, verifier the full one => leak => quarantine
    from healthbot import scrub as scrub_mod
    full = load_identity(IDENT)
    monkeypatch.setattr(cli, "_load_identity", lambda a: dataclasses.replace(full, dob=""))
    orig = scrub_mod.scrub_pdf
    monkeypatch.setattr(
        scrub_mod, "scrub_pdf",
        lambda p, i, verify_identity=None: orig(p, i, verify_identity=full))
    f = tmp_path / "x.pdf"
    shutil.copy(PDFS / "lab-2025-03-lipid.pdf", f)
    code, out, err = run(capsys, "scrub", str(f), data=env)
    assert "QUARANTINED doc-0001 (leak: dob" in out
    assert "1984" not in out + err
    assert (env / "quarantine" / "doc-0001.pdf").is_file()


def test_path_handling(capsys, env, tmp_path, monkeypatch):
    d = tmp_path / "My Docs"
    d.mkdir()
    shutil.copy(PDFS / OK_FIXTURES[0], d / "a b.pdf")
    shutil.copy(PDFS / OK_FIXTURES[3], d / "c.pdf")
    monkeypatch.setenv("HOME", str(tmp_path))
    for arg in [str(d / "a b.pdf").replace(" ", "\\ "), f"'{d / 'a b.pdf'}'",
                f'"{d / "a b.pdf"}"', "~/My\\ Docs/a\\ b.pdf"]:
        e = env.parent / f"d{abs(hash(arg))}"
        shutil.copytree(env, e)
        code, out, _ = run(capsys, "scrub", arg, data=e)
        assert code == 0 and "OK doc-0001" in out, arg
    # directory + several pasted paths in one argument
    code, out, _ = run(capsys, "scrub", str(d), data=env)
    assert out.count("OK doc-") == 2
    e2 = env.parent / "e2"
    shutil.copytree(env, e2)
    shutil.rmtree(e2 / "private")
    esc = str(d).replace(" ", "\\ ")
    code, out, _ = run(capsys, "scrub", f"{esc}/a\\ b.pdf {esc}/c.pdf", data=e2)
    assert out.count("OK doc-") == 2
    code, out, _ = run(capsys, "scrub", str(tmp_path / "nope.pdf"), data=env)
    assert code == 1 and "nope" not in out and "argument #1" in out


def test_chunking_note(capsys, env, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "CHUNK_CHARS", 50)
    shutil.copy(PDFS / OK_FIXTURES[0], tmp_path / "a.pdf")
    run(capsys, "scrub", str(tmp_path / "a.pdf"), data=env)
    code, out, _ = run(capsys, "show", "doc-0001", data=env)
    pages = json.loads((env / "private" / "manifest.json").read_text())["docs"]["doc-0001"]["pages"]
    if pages > 1:
        assert "--pages 2" in out
    else:
        assert "truncated" not in out
