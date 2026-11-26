---
name: setup-healthbot
description: Use right after cloning this repo, or when the user says "set this up", "get started", "install", or when `healthbot` is missing or errors with command not found / ModuleNotFoundError. Walks a non-technical user through installing dependencies, creating their identity file themselves, and handing over their first documents. Does not apply once setup works and the user just wants to ingest or ask questions (use ingest-records / ask-health-question).
---

# Set up healthbot

Goal: get a non-developer from a fresh clone to "first document scrubbed" with as little
terminal work as possible. Assume the user is not comfortable with a terminal: one step at a
time, plain language, no jargon, say what each command does before running it. Read AGENTS.md
section 2 first if unsure about any privacy rule.

Every step is idempotent. Check state first and skip what's already done, so re-running this
skill is always safe.

## Hard rules

- **Never read, open, cat, edit, write, or diff `identity.yaml`.** Not to check it, not to
  help fill it in. `.claude/hooks/guard_private.py` blocks any Bash command mentioning it
  (unless the command starts with `healthbot`), and deny rules block Read/Edit/Write. Don't try
  to route around either.
- Never ask the user to paste identifiers into chat. They edit the file in their own editor.
- Never open a PDF. Paths go unread to `healthbot scrub`.
- If the user pastes PII anyway, don't repeat it back or store it.

## Step 1: Check prerequisites

Run `python3 --version` and `git --version`.

- Python must be 3.11 or newer (`pyproject.toml` says `requires-python >= 3.11`).
- If Python is missing or too old, stop and explain how to install it. This is the one thing
  you can't do for them:
  - macOS: `brew install python@3.12` if they have Homebrew, otherwise the installer from
    python.org/downloads.
  - Windows: python.org installer, tick "Add Python to PATH".
  - Linux: their package manager (`python3.12`).
  Ask them to tell you when it's done, then re-check.

## Step 2: Install

Skip only if `.venv/bin/healthbot --help` succeeds **and** the web dependencies are present
(`.venv/bin/python -c "import fastapi, uvicorn, claude_agent_sdk"` exits 0). If the CLI works
but the web check fails, just run the install command below again.

1. `python3 -m venv .venv` (creates a private folder for this project's packages).
2. `.venv/bin/pip install -e '.[web]'` (always install this exact form: it installs both the
   CLI and the browser UI dependencies in one go. Never install the CLI alone.)
3. Verify with `.venv/bin/healthbot --help`.

If pip fails, read the error and explain it in plain words. Common causes: wrong Python
version, no network, or a build tool missing for `pymupdf`. Retry once after fixing; don't loop.

## Step 3: Identity file (the user does this part)

`identity.yaml` lists the names, dates of birth, addresses, phone numbers, emails and IDs that
healthbot must remove from their records. It stays on their machine and is never sent to an
LLM, including you.

1. Ask the user to create it by typing this at the prompt, including the `!`. It runs the copy
   in this session and prints nothing:

   `! cp identity.example.yaml identity.yaml`

   (If they say it already exists, don't overwrite it. Skip to the next point.)
2. Ask them to open `identity.yaml` in their own editor (TextEdit, VS Code, Notepad, etc.) and
   replace every example value with their own.
   - Offer `! open -e identity.yaml` on macOS to open it in TextEdit.
3. Explain each field using the comments in `identity.example.yaml` (you may read that file; it
   is the committed template with fake data):
   - `names` / `aliases`: full legal name plus nicknames and maiden names. Variants are
     generated automatically.
   - `dob`, `addresses`, `phones`, `emails`.
   - `ids`: health card, MRN, insurance numbers.
   - `extra_terms`: anything else to always redact, like an employer or family names.
4. Tell them: more is better. Anything missing here can leak into a record. Remind them they
   can re-edit any time.
5. **Stop and wait** until they say it's done. You cannot verify the contents; don't try. Just
   take their word, and mention that `healthbot scrub` will quarantine anything it can't
   verify as clean.

## Step 4: Hand over documents

Explain the three safe ways, in this order of friendliness:

1. Drag the file into the terminal window (this pastes its path as text).
2. Drop files into the `data/incoming/` folder.
3. Type the path.

If their records are in a shared Google Drive folder ("anyone with the link"), offer the
`ingest-drive-folder` skill instead.

**Warn clearly:** do not `@`-mention a PDF or attach it in the desktop or web app. That sends
the contents to the model before any safeguard runs. If it happens, say so plainly, don't
summarize or quote it, and restate the safe ways.

Once they have files in place, continue into the `ingest-records` skill. Don't `ls` or peek into
`data/incoming/`; just run `healthbot scrub` as that skill directs.

## Step 5: Choose how to continue

Offer both, recommend the browser for anyone who dislikes the terminal:

- **Browser UI:** `.venv/bin/healthbot serve`. Opens a page on their own machine with a
  folder picker and chat. Its dependencies were installed in Step 2. Tell them to keep the terminal
  window open while they use it, and not to share the URL.
- **Terminal:** keep chatting here. Use `ask-health-question` for questions and
  `review-health-history` for timelines and appointment prep.

## Wrap-up

Summarize in a few lines what's installed and what's left. Include the standard disclaimer:
this is an educational demo, not medical advice.

## Known limitation

The hook prevents this skill from creating or validating `identity.yaml` itself. A future
`healthbot init` subcommand (first token `healthbot`, so allowlisted) could copy the template
and report only "missing / still the template / customized" without printing contents, which
would remove the manual `!` copy step and let Step 3 verify completion.
