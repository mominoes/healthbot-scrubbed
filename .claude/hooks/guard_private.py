#!/usr/bin/env python3
"""PreToolUse hook: block tool calls that touch private paths or any PDF.

Contract (Claude Code hooks): JSON on stdin ({"tool_name", "tool_input", ...});
exit 0 = allow, exit 2 = block and stderr is shown to Claude. Any other exit code is a
non-blocking error, so this script fails closed by exiting 2 on unparseable input.

Scanned: every string in tool_input EXCEPT free-text bodies (content, old_string,
new_string, new_source, description), so authoring code that merely mentions
'identity.yaml' or '.pdf' is not blocked. Path-like and command fields are scanned.
Allowlist: a Bash command whose first token is `healthbot` or `.venv/bin/healthbot`
and which contains no shell chaining/redirection/substitution characters.
"""
import json
import re
import shlex
import sys

SKIP_KEYS = {"content", "old_string", "new_string", "new_source", "description"}
PRIVATE = re.compile(r"data/+(incoming|private|quarantine)\b|identity\.yaml", re.I)
PDF = re.compile(r"\.pdf(?![A-Za-z0-9_])", re.I)
CHAIN = re.compile(r"[;&|`<>\n\r]|\$\(")
TRUSTED = {"healthbot", ".venv/bin/healthbot", "./.venv/bin/healthbot"}


def strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k not in SKIP_KEYS:
                yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)


def trusted_bash(tool_name, tool_input):
    if tool_name != "Bash" or not isinstance(tool_input, dict):
        return False
    cmd = tool_input.get("command")
    if not isinstance(cmd, str) or CHAIN.search(cmd):
        return False
    try:
        first = shlex.split(cmd)[0]
    except (ValueError, IndexError):
        return False
    return first in TRUSTED


def main():
    try:
        data = json.load(sys.stdin)
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input", {})
    except Exception:
        print("guard_private: could not parse hook input; blocking (fail closed).",
              file=sys.stderr)
        return 2
    if trusted_bash(tool_name, tool_input):
        return 0
    for s in strings(tool_input):
        if PRIVATE.search(s) or PDF.search(s):
            print(
                "BLOCKED by guard_private: this call references a private path "
                "(data/incoming, data/private, data/quarantine, identity.yaml) or a .pdf "
                "file. Raw records must never be read directly. Use `healthbot scrub <file>` "
                "to ingest and `healthbot show <doc-id>` to view redacted text.",
                file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
