"""Claude Agent SDK session for the web UI, plus the narrow tool allowlist.

The SDK runs the `claude` CLI with cwd = repo root and setting_sources=["project"], so it
loads the same AGENTS.md, skills, deny rules and guard_private.py hook as the terminal.
Nothing here replaces those guardrails; this module only narrows what may run unprompted.
Tools in ASK_TOOLS (WebSearch) prompt the user in the browser, as the terminal would;
everything else is denied.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import re
import shlex
import shutil
from pathlib import Path

from ..sources import TRUSTED_DOMAINS, is_trusted

HB_BINS = (".venv/bin/healthbot", "healthbot")
# Skills the web agent may use. ingest-drive-folder is left out on purpose: it needs
# `healthbot fetch-drive-folder`, which (like `scrub`) the web agent may not run.
SKILLS = ["ingest-records", "ask-health-question", "review-health-history"]
# Base tool set: anything not listed here does not exist for the web agent at all.
BASE_TOOLS = ["Bash", "Read", "Glob", "Grep", "Write", "Edit", "WebFetch", "WebSearch",
              "TodoWrite", "Skill"]
# Tools that are never pre-approved but may run after the user approves each call in the
# browser, like the terminal's permission prompt. Search sends query text to an outside
# service, so a human sees every query before it leaves.
ASK_TOOLS = {"WebSearch"}

DENY_MESSAGE = ("The healthbot web UI does not allow this tool call. Only these are permitted: "
                "`healthbot show|record write|index`, reading data/records, writing "
                "data/records/summaries, Glob/Grep inside data/records, the healthbot skills, "
                "WebFetch of trusted health sites, and WebSearch after the user approves it. "
                "Scrubbing is done by the web UI itself.")
SEARCH_DENIED = ("The user declined this web search. Do not retry it. Continue without it "
                 "(for example, fetch a known trusted page) and say that no search was run.")

WEB_PROMPT_APPEND = """
You are running inside the healthbot local web UI, not a terminal.
- The UI scrubs files itself (native folder picker -> healthbot scrub). You cannot run
  `healthbot scrub` or `healthbot fetch-drive-folder`; if the user wants to add files, tell
  them to use the "Choose folder…" button.
- When ingesting, skip the identity-file check and the scrub step of ingest-records: the UI
  already did both. Don't stop to wait for the optional review gate; just mention it.
- To write a record, use exactly this form (quoted heredoc, nothing before or after):
  .venv/bin/healthbot record write --stdin <<'EOF'
  ---
  ...frontmatter and body...
  EOF
- Glob and Grep must pass an explicit `path` inside data/records.
- WebSearch shows the user an Allow/Deny prompt for every query, as in the terminal. Keep
  queries generic (no personal values or record text). If a search is declined, don't retry it.
- Other tool calls get no prompt: a denied call is final, do not retry it.
- Your replies are rendered as Markdown.
""".strip()

_CHAIN = re.compile(r"[;&|`<>\n\r$\\]")
_DOC_ID = re.compile(r"^doc-\d{4,}$")
_PAGES = re.compile(r"^\d+(-\d+)?(,\d+(-\d+)?)*$")
_HEREDOC = re.compile(
    r"^(?P<bin>\S+) record write --stdin(?P<force> --force)? <<(?P<q>['\"])(?P<tag>[A-Za-z_]+)(?P=q)\n")


def repo_root() -> Path:
    env = os.environ.get("HEALTHBOT_ROOT")
    return Path(env) if env else Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------- allowlist

def build_allowed_tools() -> list[str]:
    """Rules passed to ClaudeAgentOptions.allowed_tools (auto-approved, no prompt)."""
    rules = []
    for hb in HB_BINS:
        rules += [f"Bash({hb} show:*)", f"Bash({hb} record write:*)", f"Bash({hb} index)"]
    rules += ["Read(./data/records/**)",
              "Write(./data/records/summaries/**)", "Edit(./data/records/summaries/**)",
              "TodoWrite"]
    rules += [f"WebFetch(domain:{d})" for d in TRUSTED_DOMAINS]
    return rules


def _bash_ok(cmd: str) -> bool:
    cmd = cmd.strip()
    m = _HEREDOC.match(cmd)
    if m:
        # `healthbot record write --stdin [--force] <<'TAG'` ... body ... `TAG` (last line).
        # Quoted delimiter = no expansion; the delimiter must be the final line, so
        # nothing can be chained after the heredoc.
        if m.group("bin") not in HB_BINS:
            return False
        lines = cmd[m.end():].split("\n")
        tag = m.group("tag")
        return len(lines) >= 1 and lines[-1].strip() == tag and \
            all(l.strip() != tag for l in lines[:-1])
    if _CHAIN.search(cmd):
        return False
    try:
        t = shlex.split(cmd)
    except ValueError:
        return False
    if len(t) < 2 or t[0] not in HB_BINS:
        return False
    rest = t[1:]
    if rest == ["index"]:
        return True
    if rest[:1] == ["show"] and len(rest) >= 2 and _DOC_ID.match(rest[1]):
        extra = rest[2:]
        return extra == [] or (len(extra) == 2 and extra[0] == "--pages"
                               and bool(_PAGES.match(extra[1])))
    return rest in (["record", "write", "--stdin"], ["record", "write", "--stdin", "--force"])


def _under(p: str | None, base: Path) -> bool:
    if not p or not isinstance(p, str):
        return False
    root = repo_root().resolve()
    path = Path(p)
    if not path.is_absolute():
        path = root / path
    try:
        return path.resolve().is_relative_to(base.resolve())
    except (OSError, ValueError):
        return False


def needs_approval(tool_name: str, tool_input: dict) -> bool:
    """True if this call may run once the user approves it in the browser."""
    if tool_name not in ASK_TOOLS or not isinstance(tool_input, dict):
        return False
    q = tool_input.get("query")
    return isinstance(q, str) and bool(q.strip())


def is_preapproved(tool_name: str, tool_input: dict) -> bool:
    """Mirror of build_allowed_tools, applied in can_use_tool. Pure, no I/O besides resolve."""
    if not isinstance(tool_input, dict):
        return False
    records = repo_root() / "data" / "records"
    if tool_name == "Bash":
        return isinstance(tool_input.get("command"), str) and _bash_ok(tool_input["command"])
    if tool_name == "Read":
        return _under(tool_input.get("file_path"), records)
    if tool_name in ("Write", "Edit"):
        fp = tool_input.get("file_path")
        return _under(fp, records / "summaries") and str(fp).endswith(".md")
    if tool_name in ("Glob", "Grep"):
        pat = str(tool_input.get("pattern", ""))
        if tool_name == "Glob" and (pat.startswith("/") or ".." in pat):
            return False
        return _under(tool_input.get("path"), records)
    if tool_name == "WebFetch":
        url = tool_input.get("url")
        return isinstance(url, str) and url.lower().startswith(("https://", "http://")) \
            and is_trusted(url)
    if tool_name == "Skill":
        return tool_input.get("skill") in SKILLS
    if tool_name == "TodoWrite":
        return True
    return False


async def can_use_tool(tool_name, tool_input, context):
    """Session-less fallback: allowlist only, no way to ask the user, so ASK_TOOLS are denied.
    ChatSession.can_use_tool is the one the web UI actually uses."""
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
    if is_preapproved(tool_name, tool_input):
        return PermissionResultAllow()
    return PermissionResultDeny(message=DENY_MESSAGE)


def build_options(can_use_tool=can_use_tool):
    from claude_agent_sdk import ClaudeAgentOptions
    kw = {}
    cli = shutil.which("claude")
    if cli:
        kw["cli_path"] = cli  # same version as the user's terminal
    return ClaudeAgentOptions(
        cwd=str(repo_root()),
        setting_sources=["project"],  # REQUIRED: loads CLAUDE.md, skills, deny rules, hook
        tools=BASE_TOOLS,
        allowed_tools=build_allowed_tools(),
        skills=SKILLS,
        can_use_tool=can_use_tool,
        permission_mode="default",
        include_partial_messages=True,
        system_prompt={"type": "preset", "preset": "claude_code", "append": WEB_PROMPT_APPEND},
        **kw,
    )


def api_key_warning() -> str | None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return ("ANTHROPIC_API_KEY is set: the chat will bill that API key, not your Claude "
                "subscription. Unset it and restart to use your claude login.")
    return None


# ---------------------------------------------------------------- message -> UI events

def summarize_tool(name: str, inp: dict) -> str:
    """One line describing a tool call. Never includes a record body."""
    inp = inp if isinstance(inp, dict) else {}
    root = str(repo_root().resolve()) + os.sep

    def rel(p):
        return str(p or "").replace(root, "")

    if name == "Bash":
        cmd = str(inp.get("command", ""))
        first = cmd.split("\n", 1)[0]
        if "\n" in cmd:
            first = re.sub(r"\s*<<.*$", "", first) + " (record body omitted)"
        line = first
    elif name in ("Read", "Write", "Edit"):
        line = f"{name} {rel(inp.get('file_path'))}"
    elif name in ("Glob", "Grep"):
        line = f"{name} {inp.get('pattern', '')!r} in {rel(inp.get('path')) or '.'}"
    elif name == "WebFetch":
        line = f"WebFetch {inp.get('url', '')}"
    elif name == "WebSearch":
        line = f"WebSearch {inp.get('query', '')!r}"
        doms = inp.get("allowed_domains")
        if isinstance(doms, list) and doms:
            line += " on " + ", ".join(str(d) for d in doms)
    elif name == "Skill":
        line = f"Skill {inp.get('skill', '')}"
    elif name == "TodoWrite":
        line = "Update todo list"
    else:
        line = name
    return line if len(line) <= 200 else line[:197] + "…"


class EventTranslator:
    """Turns SDK messages into small JSON events for the browser."""

    def __init__(self):
        self._saw_delta = False

    def __call__(self, msg) -> list[dict]:
        from claude_agent_sdk import (AssistantMessage, ResultMessage, StreamEvent,
                                      TextBlock, ToolResultBlock, ToolUseBlock, UserMessage)
        out = []
        if isinstance(msg, StreamEvent):
            if msg.parent_tool_use_id:
                return out
            ev = msg.event or {}
            if ev.get("type") == "content_block_delta":
                d = ev.get("delta") or {}
                if d.get("type") == "text_delta" and d.get("text"):
                    self._saw_delta = True
                    out.append({"type": "text", "text": d["text"]})
            return out
        if isinstance(msg, AssistantMessage):
            if msg.parent_tool_use_id:
                return out
            for b in msg.content:
                if isinstance(b, TextBlock) and not self._saw_delta and b.text:
                    out.append({"type": "text", "text": b.text})
                elif isinstance(b, ToolUseBlock):
                    out.append({"type": "tool", "id": b.id,
                                "summary": summarize_tool(b.name, b.input)})
            self._saw_delta = False
            return out
        if isinstance(msg, UserMessage) and isinstance(msg.content, list):
            for b in msg.content:
                if isinstance(b, ToolResultBlock):
                    out.append({"type": "tool_status", "id": b.tool_use_id,
                                "status": "error" if b.is_error else "done"})
            return out
        if isinstance(msg, ResultMessage):
            out.append({"type": "result", "cost_usd": msg.total_cost_usd,
                        "turns": msg.num_turns, "is_error": msg.is_error,
                        "subtype": msg.subtype})
        return out


class ChatSession:
    """One long-lived ClaudeSDKClient per browser session."""

    def __init__(self, options_factory=build_options, client_factory=None):
        self._options_factory = options_factory
        self._client_factory = client_factory
        self.client = None
        self.queue: asyncio.Queue = asyncio.Queue()
        self.busy = False
        self._task = None
        self._pending: dict[str, asyncio.Future] = {}

    # ---------------------------------------------------------- permission prompts

    async def can_use_tool(self, tool_name, tool_input, context):
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
        if is_preapproved(tool_name, tool_input):
            return PermissionResultAllow()
        if not needs_approval(tool_name, tool_input):
            return PermissionResultDeny(message=DENY_MESSAGE)
        rid = secrets.token_urlsafe(8)
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self.queue.put({"type": "permission", "id": rid, "tool": tool_name,
                              "summary": summarize_tool(tool_name, tool_input)})
        try:
            allowed = await fut
        finally:
            self._pending.pop(rid, None)
        await self.queue.put({"type": "permission_resolved", "id": rid,
                              "allowed": bool(allowed)})
        if allowed:
            return PermissionResultAllow()
        return PermissionResultDeny(message=SEARCH_DENIED)

    def respond(self, request_id: str, allow: bool) -> bool:
        """Answer a pending permission prompt. False if there is no such prompt."""
        fut = self._pending.get(request_id)
        if fut is None or fut.done():
            return False
        fut.set_result(bool(allow))
        return True

    def _deny_pending(self):
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_result(False)

    async def _ensure(self):
        if self.client is None:
            opts = self._options_factory(can_use_tool=self.can_use_tool)
            if self._client_factory:
                client = self._client_factory(opts)
            else:
                from claude_agent_sdk import ClaudeSDKClient
                client = ClaudeSDKClient(opts)
            await client.connect()
            self.client = client

    async def send(self, text: str):
        if self.busy:
            raise RuntimeError("busy")
        self.busy = True
        try:
            await self._ensure()
            await self.client.query(text)
        except Exception:
            self.busy = False
            await self.queue.put({"type": "error", "message": "Could not start Claude. Is "
                                  "the `claude` CLI installed and logged in?"})
            await self.queue.put({"type": "idle"})
            return
        self._task = asyncio.create_task(self._pump())

    async def _pump(self):
        tr = EventTranslator()
        try:
            async for msg in self.client.receive_response():
                for ev in tr(msg):
                    await self.queue.put(ev)
        except Exception:
            await self.queue.put({"type": "error", "message": "The chat session failed."})
        finally:
            self.busy = False
            await self.queue.put({"type": "idle"})

    async def interrupt(self):
        self._deny_pending()
        if self.client is not None and self.busy:
            await self.client.interrupt()

    async def close(self):
        self._deny_pending()
        if self._task and not self._task.done():
            self._task.cancel()
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        self.client = None
        self.busy = False
