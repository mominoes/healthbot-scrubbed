"""WP10 web UI tests. No real Claude or osascript calls."""
import asyncio
import shutil
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("claude_agent_sdk")
pytest.importorskip("markdown_it")

from fastapi.testclient import TestClient  # noqa: E402

from healthbot.sources import TRUSTED_DOMAINS  # noqa: E402
from healthbot.web import agent, picker  # noqa: E402
from healthbot.web.server import create_app  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
PDFS = FIX / "pdfs"
PORT = 8765
TOKEN = "test-token-abc"
BASE = f"http://127.0.0.1:{PORT}"
ORIGIN = {"Origin": BASE}


class FakeSession:
    def __init__(self):
        self.busy = False
        self.sent = []
        self.queue = asyncio.Queue()

    async def send(self, text):
        self.sent.append(text)

    async def interrupt(self):
        pass

    def respond(self, request_id, allow):
        self.responses = getattr(self, "responses", []) + [(request_id, allow)]
        return request_id == "known"

    async def close(self):
        pass


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "repo"
    for d in ("incoming", "private", "quarantine", "records/labs"):
        (r / "data" / d).mkdir(parents=True)
    shutil.copy(FIX / "identity.yaml", r / "identity.yaml")
    return r


def make_client(root, choose=None, login=True):
    sessions = []

    def factory():
        s = FakeSession()
        sessions.append(s)
        return s

    app = create_app(port=PORT, token=TOKEN, root=root, choose_folder=choose,
                     session_factory=factory)
    c = TestClient(app, base_url=BASE)
    c.sessions = sessions
    if login:
        r = c.get(f"/?token={TOKEN}", follow_redirects=False)
        assert r.status_code == 303
    return c


# ---------------------------------------------------------------- local security

def test_token_exchange_sets_strict_httponly_cookie(root):
    c = make_client(root, login=False)
    assert c.get("/api/records").status_code == 401
    assert c.get("/").status_code == 401
    assert c.get("/?token=wrong", follow_redirects=False).status_code == 401
    r = c.get(f"/?token={TOKEN}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    sc = r.headers.get_list("set-cookie")
    auth = next(h for h in sc if h.startswith("hb_auth="))
    assert "httponly" in auth.lower() and "samesite=strict" in auth.lower()
    assert c.get("/api/records").status_code == 200
    assert c.get("/").status_code == 200


def test_bad_host_rejected(root):
    c = make_client(root)
    assert c.get("/api/records", headers={"Host": "evil.example:8765"}).status_code == 400
    assert c.get("/api/records", headers={"Host": "127.0.0.1:9999"}).status_code == 400
    assert c.get("/api/records", headers={"Host": f"localhost:{PORT}"}).status_code == 200


def test_foreign_or_missing_origin_rejected(root):
    c = make_client(root)
    body = {"message": "hi"}
    assert c.post("/api/chat", json=body,
                  headers={"Origin": "http://evil.example"}).status_code == 403
    assert c.post("/api/chat", json=body).status_code == 403  # no Origin
    assert c.post("/api/chat", json=body, headers=ORIGIN).status_code == 200
    assert c.sessions[-1].sent == ["hi"]


def test_no_upload_or_docs_routes(root):
    c = make_client(root)
    for path in ("/docs", "/openapi.json", "/api/upload"):
        assert c.get(path).status_code == 404
    assert c.post("/api/upload", headers=ORIGIN).status_code in (404, 405)


# ---------------------------------------------------------------- picker + scrub

def test_pick_scrubs_fixtures_without_leaking_names(root, tmp_path):
    src = tmp_path / "Jonathan Testperson records"
    src.mkdir()
    for p in sorted(PDFS.iterdir()):
        shutil.copy(p, src / f"testperson-{p.name}")
    (src / "notes.txt").write_text("x")
    c = make_client(root, choose=lambda: str(src) + "/")
    r = c.post("/api/pick", headers=ORIGIN)
    assert r.status_code == 200
    data = r.json()
    statuses = {x["doc_id"]: x["status"] for x in data["results"]}
    assert sorted(statuses.values()) == ["needs_ocr"] + ["ok"] * 5
    assert len(data["new_ok"]) == 5 and data["skipped_non_pdf"] == 1
    low = r.text.lower()
    for s in ["testperson", "jonathan", "records", str(tmp_path).lower(), "lab-2025",
              "scanned", "immunization-record", ".pdf"]:
        assert s not in low, s
    # originals copied, not moved
    assert len(list(src.iterdir())) == 7
    # a second pick of the same folder reports duplicates only
    data2 = c.post("/api/pick", headers=ORIGIN).json()
    assert {x["status"] for x in data2["results"]} == {"duplicate"} and not data2["new_ok"]


def test_pick_cancelled(root):
    def cancel():
        raise picker.PickerCancelled()
    c = make_client(root, choose=cancel)
    assert c.post("/api/pick", headers=ORIGIN).json() == {"cancelled": True}


def test_pick_identity_missing(root):
    (root / "identity.yaml").unlink()
    called = []
    c = make_client(root, choose=lambda: called.append(1) or "/nowhere")
    r = c.post("/api/pick", headers=ORIGIN)
    assert r.status_code == 409 and r.json()["error"] == "identity_missing"
    assert "identity.example.yaml" in r.json()["message"] and not called
    assert c.get("/api/status").json()["identity_present"] is False


def test_osascript_cancel_and_args(monkeypatch):
    import subprocess
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 1, "", "execution error: User canceled. (-128)")
    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(picker.PickerCancelled):
        picker.choose_folder()
    assert isinstance(seen["argv"], list) and seen["argv"][:2] == ["osascript", "-e"]

    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 0, "/Users/x/My Docs/\n", ""))
    assert picker.choose_folder() == "/Users/x/My Docs/"


# ---------------------------------------------------------------- allowlist

def test_allowlist_builder():
    rules = agent.build_allowed_tools()
    bash = sorted(r for r in rules if r.startswith("Bash"))
    assert bash == sorted([
        "Bash(.venv/bin/healthbot show:*)", "Bash(.venv/bin/healthbot record write:*)",
        "Bash(.venv/bin/healthbot index)",
        "Bash(healthbot show:*)", "Bash(healthbot record write:*)", "Bash(healthbot index)"])
    webfetch = [r for r in rules if r.startswith("WebFetch")]
    assert webfetch == [f"WebFetch(domain:{d})" for d in TRUSTED_DOMAINS]
    assert "Bash" not in rules and "WebSearch" not in rules
    assert not any("scrub" in r or "fetch-drive" in r for r in rules)
    for r in rules:
        if r.startswith(("Write", "Edit")):
            assert r.endswith("(./data/records/summaries/**)")
    assert "Read(./data/records/**)" in rules


def test_options_load_project_settings():
    opts = agent.build_options()
    assert opts.setting_sources == ["project"]
    assert opts.permission_mode != "bypassPermissions"
    assert opts.can_use_tool is agent.can_use_tool
    assert "WebSearch" in opts.tools and "Agent" not in opts.tools
    assert "ingest-drive-folder" not in opts.skills
    assert Path(opts.cwd) == agent.repo_root()


REC = ".venv/bin/healthbot record write --stdin <<'EOF'\n---\nid: x\n---\nbody\nEOF"


@pytest.mark.parametrize("tool,inp", [
    ("Bash", {"command": ".venv/bin/healthbot show doc-0003 --pages 1-2"}),
    ("Bash", {"command": "healthbot index"}),
    ("Bash", {"command": "healthbot record write --stdin"}),
    ("Bash", {"command": REC}),
    ("Bash", {"command": REC.replace("--stdin", "--stdin --force")}),
    ("Read", {"file_path": "data/records/labs/x.md"}),
    ("Write", {"file_path": str(agent.repo_root() / "data/records/summaries/prep.md")}),
    ("Grep", {"pattern": "LDL", "path": "data/records"}),
    ("WebFetch", {"url": "https://www.cdc.gov/vaccines/"}),
    ("Skill", {"skill": "ask-health-question"}),
    ("TodoWrite", {"todos": []}),
])
def test_preapproved(tool, inp):
    assert agent.is_preapproved(tool, inp)


@pytest.mark.parametrize("tool,inp", [
    ("Bash", {"command": "healthbot scrub /tmp/x"}),
    ("Bash", {"command": "healthbot fetch-drive-folder https://x"}),
    ("Bash", {"command": "cat identity.yaml"}),
    ("Bash", {"command": "healthbot show doc-0001; cat identity.yaml"}),
    ("Bash", {"command": "healthbot show doc-0001 > out.md"}),
    ("Bash", {"command": "healthbot show doc-0001 --identity other.yaml"}),
    ("Bash", {"command": "healthbot index && rm -rf data"}),
    ("Bash", {"command": REC + "\ncat identity.yaml"}),
    ("Bash", {"command": REC.replace("'EOF'", "EOF")}),  # unquoted heredoc expands $()
    ("Bash", {"command": "cat <<'EOF' | healthbot record write --stdin\nx\nEOF"}),
    ("Read", {"file_path": "identity.yaml"}),
    ("Read", {"file_path": "data/records/../private/manifest.json"}),
    ("Write", {"file_path": "data/records/labs/x.md"}),
    ("Edit", {"file_path": "AGENTS.md"}),
    ("Grep", {"pattern": "x"}),
    ("Glob", {"pattern": "../**", "path": "data/records"}),
    ("WebFetch", {"url": "https://example.com/"}),
    ("WebFetch", {"url": "https://cdc.gov.evil.com/"}),
    ("WebSearch", {"query": "ldl"}),
    ("Skill", {"skill": "ingest-drive-folder"}),
    ("Agent", {"prompt": "x"}),
])
def test_not_preapproved(tool, inp):
    assert not agent.is_preapproved(tool, inp)


def test_can_use_tool_denies_unlisted():
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
    deny = asyncio.run(agent.can_use_tool("WebSearch", {"query": "x"}, None))
    assert isinstance(deny, PermissionResultDeny) and "web UI" in deny.message
    allow = asyncio.run(agent.can_use_tool("Bash", {"command": "healthbot index"}, None))
    assert isinstance(allow, PermissionResultAllow)


def test_tool_summary_hides_record_body():
    s = agent.summarize_tool("Bash", {"command": REC.replace("body", "SECRET")})
    assert "SECRET" not in s and "record write --stdin" in s and "omitted" in s


def test_event_translator():
    from claude_agent_sdk import (AssistantMessage, ResultMessage, StreamEvent, TextBlock,
                                  ToolUseBlock)
    tr = agent.EventTranslator()
    ev = StreamEvent(uuid="u", session_id="s", event={
        "type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}})
    assert tr(ev) == [{"type": "text", "text": "Hi"}]
    msg = AssistantMessage(content=[TextBlock("Hi"), ToolUseBlock(
        "t1", "Bash", {"command": "healthbot index"})], model="m")
    assert tr(msg) == [{"type": "tool", "id": "t1", "summary": "healthbot index"}]
    # no deltas seen -> full text is emitted from the AssistantMessage
    assert tr(AssistantMessage(content=[TextBlock("Bye")], model="m")) == [
        {"type": "text", "text": "Bye"}]
    res = ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                        num_turns=3, session_id="s", total_cost_usd=0.01)
    assert tr(res)[0] == {"type": "result", "cost_usd": 0.01, "turns": 3, "is_error": False,
                          "subtype": "success"}


# ---------------------------------------------------------------- records viewer

def test_records_render_escapes_raw_html(root):
    (root / "data/records/labs/2026-01-20-lab-test.md").write_text(
        "---\nid: 2026-01-20-lab-test\ndate: 2026-01-20\ntype: lab\ntitle: Test <b>x</b>\n"
        "provider: Family medicine\nsource: doc-0001\ntags: [a]\n---\n"
        "# Test\n\n<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n"
        "[link](javascript:alert(1))\n\n| A | B |\n|---|---|\n| 1 | 2 |\n",
        encoding="utf-8")
    c = make_client(root)
    lst = c.get("/api/records").json()["records"]
    assert [r["id"] for r in lst] == ["2026-01-20-lab-test"]
    html = c.get("/api/records/2026-01-20-lab-test").json()["html"]
    assert "<script" not in html and "&lt;script&gt;" in html
    assert "<img" not in html and 'href="javascript' not in html
    assert "<table>" in html
    assert c.get("/api/records/..%2Fprivate").status_code in (400, 404)
    assert c.get("/api/views/lab-trends").status_code == 200
    assert c.get("/api/views/manifest").status_code == 404
    r = c.post("/api/render", json={"text": "<script>x</script>"}, headers=ORIGIN)
    assert "<script" not in r.json()["html"]


# ---------------------------------------------------------------- permission prompts

def test_needs_approval_only_for_websearch():
    assert agent.needs_approval("WebSearch", {"query": "ldl cholesterol guidelines"})
    assert not agent.needs_approval("WebSearch", {"query": "  "})
    assert not agent.needs_approval("WebSearch", {})
    assert not agent.needs_approval("Bash", {"command": "cat identity.yaml"})
    assert not agent.needs_approval("WebFetch", {"url": "https://example.com/"})


def test_session_websearch_waits_for_user():
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

    async def run(allow):
        s = agent.ChatSession()
        task = asyncio.create_task(s.can_use_tool(
            "WebSearch", {"query": "aspirin heart disease", "allowed_domains": ["cdc.gov"]},
            None))
        ev = await asyncio.wait_for(s.queue.get(), 1)
        assert ev["type"] == "permission" and ev["tool"] == "WebSearch"
        assert "aspirin heart disease" in ev["summary"] and "cdc.gov" in ev["summary"]
        assert not task.done()  # blocked until the user answers
        assert not s.respond("wrong-id", True)
        assert s.respond(ev["id"], allow)
        res = await asyncio.wait_for(task, 1)
        done = await s.queue.get()
        assert done == {"type": "permission_resolved", "id": ev["id"], "allowed": allow}
        assert not s.respond(ev["id"], True)  # already answered
        return res

    assert isinstance(asyncio.run(run(True)), PermissionResultAllow)
    deny = asyncio.run(run(False))
    assert isinstance(deny, PermissionResultDeny) and "declined" in deny.message


def test_session_interrupt_denies_pending_prompt():
    from claude_agent_sdk import PermissionResultDeny

    async def run():
        s = agent.ChatSession()
        task = asyncio.create_task(s.can_use_tool("WebSearch", {"query": "x"}, None))
        await asyncio.wait_for(s.queue.get(), 1)
        await s.interrupt()
        return await asyncio.wait_for(task, 1)

    assert isinstance(asyncio.run(run()), PermissionResultDeny)


def test_session_never_prompts_for_unlisted_tools():
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

    async def run():
        s = agent.ChatSession()
        deny = await s.can_use_tool("Bash", {"command": "cat identity.yaml"}, None)
        allow = await s.can_use_tool("Bash", {"command": "healthbot index"}, None)
        return deny, allow, s.queue.empty()

    deny, allow, empty = asyncio.run(run())
    assert isinstance(deny, PermissionResultDeny) and "web UI" in deny.message
    assert isinstance(allow, PermissionResultAllow) and empty


def test_session_passes_its_callback_to_options():
    seen = {}

    class FakeClient:
        def __init__(self, opts):
            seen["opts"] = opts

        async def connect(self):
            pass

    async def run():
        s = agent.ChatSession(client_factory=FakeClient)
        await s._ensure()
        return s

    s = asyncio.run(run())
    assert seen["opts"].can_use_tool == s.can_use_tool


def test_permission_endpoint(root):
    c = make_client(root)
    assert c.post("/api/chat/permission", json={"id": "known", "allow": True},
                  headers=ORIGIN).status_code == 200
    assert c.post("/api/chat/permission", json={"id": "gone", "allow": False},
                  headers=ORIGIN).status_code == 404
    assert c.post("/api/chat/permission", json={"id": "known", "allow": "yes"},
                  headers=ORIGIN).status_code == 400
    assert c.post("/api/chat/permission", json={"id": "known", "allow": True},
                  headers={"Origin": "http://evil.example"}).status_code == 403
    assert c.sessions[-1].responses == [("known", True), ("gone", False)]


def test_tool_summary_websearch():
    s = agent.summarize_tool("WebSearch", {"query": "ldl range", "allowed_domains": ["cdc.gov"]})
    assert s == "WebSearch 'ldl range' on cdc.gov"
