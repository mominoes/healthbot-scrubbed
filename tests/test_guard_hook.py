import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "guard_private.py"


def run(tool, **tool_input):
    p = subprocess.run([sys.executable, str(HOOK)], capture_output=True, text=True,
                       input=json.dumps({"tool_name": tool, "tool_input": tool_input}))
    return p.returncode, p.stderr


def bash(cmd):
    return run("Bash", command=cmd)


def test_block_cat_incoming():
    code, err = bash("cat data/incoming/x.pdf")
    assert code == 2 and err


def test_block_read_identity():
    assert run("Read", file_path="identity.yaml")[0] == 2
    assert run("Read", file_path="/abs/repo/identity.yaml")[0] == 2


def test_block_read_pdf():
    assert run("Read", file_path="~/Downloads/labs.pdf")[0] == 2
    assert run("Read", file_path="/tmp/LABS.PDF")[0] == 2


def test_block_pdftotext():
    assert bash("pdftotext ~/Downloads/labs.pdf")[0] == 2


def test_block_other_private_dirs_and_tools():
    assert bash("ls data/private")[0] == 2
    assert run("Grep", pattern="x", path="data/quarantine")[0] == 2
    assert run("Glob", pattern="data/incoming/*")[0] == 2


def test_block_healthbot_chained():
    assert bash("healthbot show doc-1; cat identity.yaml")[0] == 2
    assert bash("healthbot show doc-1 && cat x.pdf")[0] == 2
    assert bash("healthbot show $(cat identity.yaml)")[0] == 2


def test_allow_healthbot():
    assert bash("healthbot scrub ~/Downloads/labs.pdf")[0] == 0
    assert bash("healthbot show doc-0001")[0] == 0
    assert bash(".venv/bin/healthbot scrub ~/Downloads/labs.pdf")[0] == 0


def test_allow_example_identity_and_records():
    assert run("Read", file_path="identity.example.yaml")[0] == 0
    assert bash("grep -r foo data/records")[0] == 0


def test_allow_dev_commands():
    assert bash(".venv/bin/pytest -q tests/test_scrub.py")[0] == 0
    assert bash("git status")[0] == 0
    assert bash(".venv/bin/python scripts/make_fixtures.py")[0] == 0


def test_allow_code_authoring_mentioning_private_names():
    src = "open('identity.yaml'); Path('x.pdf')"
    assert run("Write", file_path="src/healthbot/identity.py", content=src)[0] == 0
    assert run("Edit", file_path="src/a.py", old_string="identity.yaml",
               new_string="x.pdf")[0] == 0


def test_fail_closed_on_bad_input():
    p = subprocess.run([sys.executable, str(HOOK)], input="not json",
                       capture_output=True, text=True)
    assert p.returncode == 2
