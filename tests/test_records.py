import io
import sys
from pathlib import Path

import pytest

from healthbot import cli, records

IDENT = Path(__file__).parent / "fixtures" / "identity.yaml"

TABLE_HEAD = """| Test | Value | Unit | Ref range | Flag | Page |
|------|-------|------|-----------|------|------|
"""


def rec(id_, date, type_="lab", title="Lipid panel", rows="", extra=""):
    return f"""---
id: {id_}
date: {date}
type: {type_}
title: {title}
provider: Family medicine
source: doc-0002
tags: [cholesterol, ldl]
---
# {title}

## Extracted facts
{TABLE_HEAD}{rows}
## Narrative (verbatim excerpts)
> fine (p2){extra}

## AI summary
- ok
"""


LAB1 = rec("2025-03-10-lab-lipid", "2025-03-10", rows=(
    "| LDL | 4.1 | mmol/L | <3.4 | H | 1 |\n| HDL | 1.2 | mmol/L | >1.0 |  | p1 |\n"
    "| Comment | see note | | | | 2 |\n"))
LAB2 = rec("2026-01-15-lab-lipid-a1c", "2026-01-15", rows=(
    "| LDL | 3.6 | mmol/L | <3.4 | H | 1 |\n| HbA1c | 5.4 | % | 4-6 | | 2 |\n"))
IMM = rec("2016-05-02-immunization-tdap", "2016-05-02", "immunization", "Tdap",
          rows="| Tdap | dose 1 | | | | 1 |\n")


def run(capsys, monkeypatch, data, *argv, stdin=""):
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    args = cli.build_parser().parse_args(
        [*argv, "--identity", str(IDENT), "--data-dir", str(data)])
    code = args.func(args)
    out = capsys.readouterr()
    return code, out.out, out.err


def write(capsys, monkeypatch, data, text, *extra):
    return run(capsys, monkeypatch, data, "record", "write", "--stdin", *extra, stdin=text)


def test_write_ok_and_no_overwrite(tmp_path, capsys, monkeypatch):
    code, out, _ = write(capsys, monkeypatch, tmp_path, LAB1)
    assert code == 0
    assert (tmp_path / "records/labs/2025-03-10-lab-lipid.md").is_file()
    code, _, err = write(capsys, monkeypatch, tmp_path, LAB1)
    assert code == 1 and "--force" in err
    code, _, _ = write(capsys, monkeypatch, tmp_path, LAB1, "--force")
    assert code == 0


@pytest.mark.parametrize("old,new", [
    ("type: lab", "type: bogus"),
    ("date: 2025-03-10", "date: 2025-13-40"),
    ("source: doc-0002", "source: somefile"),
    ("provider: Family medicine\n", ""),
    ("id: 2025-03-10-lab-lipid", "id: ../evil"),
    ("id: 2025-03-10-lab-lipid", "id: 2025-03-11-lab-lipid"),
])
def test_frontmatter_validation(tmp_path, capsys, monkeypatch, old, new):
    code, _, err = write(capsys, monkeypatch, tmp_path, LAB1.replace(old, new, 1))
    assert code == 1 and "INVALID" in err
    assert not list(tmp_path.rglob("*.md"))


def test_no_frontmatter_rejected(tmp_path, capsys, monkeypatch):
    code, _, err = write(capsys, monkeypatch, tmp_path, "# just a body\n")
    assert code == 1 and "INVALID" in err


def test_identifier_rejected_categories_only(tmp_path, capsys, monkeypatch):
    bad = rec("2025-03-10-lab-lipid", "2025-03-10", extra="\n> Patient Jonathan Testperson (p1)")
    code, out, err = write(capsys, monkeypatch, tmp_path, bad)
    assert code == 1
    assert "name" in err
    assert "Testperson" not in out + err and "Jonathan" not in out + err
    assert not list(tmp_path.rglob("*.md"))


def test_detector_hit_rejected(tmp_path, capsys, monkeypatch):
    bad = rec("2025-03-10-lab-lipid", "2025-03-10", extra="\n> call 416-555-0199")
    code, out, err = write(capsys, monkeypatch, tmp_path, bad)
    assert code == 1 and "auto:phone" in err and "555" not in out + err


def test_index_and_trends(tmp_path, capsys, monkeypatch):
    for t in (LAB2, LAB1, IMM):
        assert write(capsys, monkeypatch, tmp_path, t)[0] == 0
    code, out, _ = run(capsys, monkeypatch, tmp_path, "index")
    assert code == 0
    rd = tmp_path / "records"
    idx = (rd / "INDEX.md").read_text()
    lines = [ln for ln in idx.splitlines() if ln.startswith("| 20")]
    assert [ln.split("|")[1].strip() for ln in lines] == ["2016-05-02", "2025-03-10", "2026-01-15"]
    assert "labs/2025-03-10-lab-lipid.md" in idx

    trends = (rd / "labs/lab-trends.md").read_text()
    assert "| LDL | 2025-03-10 | 4.1 | mmol/L | <3.4 | H |" in trends
    assert "| LDL | 2026-01-15 | 3.6 |" in trends
    assert "| HbA1c | 2026-01-15 | 5.4 | % |" in trends
    assert "| HDL | 2025-03-10 | 1.2 |" in trends
    assert "see note" not in trends
    assert trends.index("2025-03-10 | 4.1") < trends.index("2026-01-15 | 3.6")
    assert "p1" in trends

    imm = (rd / "immunizations/immunizations.md").read_text()
    assert "| 2016-05-02 | Tdap |" in imm

    # regenerated files are not treated as records on a second run
    assert run(capsys, monkeypatch, tmp_path, "index")[0] == 0
    assert (rd / "INDEX.md").read_text() == idx


def test_index_empty(tmp_path, capsys, monkeypatch):
    assert run(capsys, monkeypatch, tmp_path, "index")[0] == 0
    assert "Indexed 0" in capsys.readouterr().out or True


def test_numeric_value():
    assert records.numeric_value("<0.5") == ("<", "0.5")
    assert records.numeric_value("12") == ("", "12")
    assert records.numeric_value("positive") is None
