# AGENTS.md

Operating manual for any agent working in this repo. `CLAUDE.md` just imports this file.

## 1. Purpose and disclaimer

healthbot-scrubbed turns a person's raw medical PDFs into de-identified Markdown records, then
answers health questions grounded in those records plus trusted public sources.

This is an **educational demo, not medical advice.** Say so in every answer that discusses
health content (a short closing line is enough).

## 2. Privacy rules (non-negotiable)

The core promise: raw records and the identity file are never sent to an LLM. You are an LLM.

- Never read, cat, grep, open, list-and-peek, or print anything under `data/incoming/`,
  `data/private/`, `data/quarantine/`, or the user's real identity file. Only `healthbot`
  touches them. A hook in `.claude/hooks/guard_private.py` blocks these, and also any
  reference to a PDF path, but do not rely on it; don't try.
- Never open a raw PDF yourself, not even "to check what it is". When the user gives a PDF
  path, pass it **unread** to `healthbot scrub`.
- Never ask the user to paste identifiers or raw records into chat. Never fill in the identity
  file for them. They edit it in their own editor.
- If the user pastes PII anyway, do not repeat it back or store it anywhere.
- Never put personal values or record text in web searches or fetches. Generic terms only.
- Never write `healthbot show` output to a file, except via the scrubbed, verified
  `healthbot record write` path.

**Safe ways to hand over files:**
1. Drag the file into the terminal (this pastes its path as plain text).
2. Type the path.
3. Drop the file into `data/incoming/` and say so.

**Unsafe:** `@`-mentioning a PDF, or attaching it in the desktop or web app. Those send the
file's contents to the model before any tool or hook can run. If the user does this, say so
plainly, do not summarize or quote the content, and explain the safe ways above.

## 3. Data layout

| Path | What | Agent may read? |
|------|------|-----------------|
| `data/incoming/` | raw files the user drops | NO |
| `data/private/` | archived originals, `manifest.json` | NO |
| `data/quarantine/` | files that failed scrub or verify | NO |
| `data/records/` | normalized, de-identified Markdown records | yes |
| `data/records/INDEX.md` | date-sorted table of all records | yes |
| `data/records/labs/lab-trends.md` | every numeric lab value over time | yes |
| `data/records/immunizations/immunizations.md` | all immunizations | yes |
| `data/records/summaries/` | appointment-prep summaries (agent writes) | yes |
| `identity.example.yaml` | committed template | yes |
| identity file (real, gitignored) | the user's identifiers | NO |

Document IDs are neutral (`doc-0001`); original filenames are never shown.

## 4. Commands

Run via `.venv/bin/healthbot` (or `healthbot` if the venv is active). All commands read the
identity file from the repo root; `--identity PATH` overrides it (tests only).

- `healthbot scrub [PATH ...]`: scrub PDFs. PATH can be files or directories anywhere on disk;
  with no args it processes `data/incoming/`. Archives the original under a neutral ID and
  prints one summary line per file. Statuses:
  - `ok`: scrubbed and verified clean.
  - `needs_ocr`: scanned page with no text layer. Quarantined (no OCR yet).
  - `encrypted`: password-protected. Quarantined.
  - `leak`: verifier found a leftover identifier. Quarantined, no text printed.
  - `error`: could not process. Quarantined.
  Anything not `ok` never reaches you.
- `healthbot show ID [--pages 1-3]`: print the redacted Markdown of an archived doc. This is
  the only way you read a source document. Long output is truncated with a note; use `--pages`
  (e.g. `2`, `1-3`, `1,3-4`).
- `healthbot record write --stdin [--force]`: read a full record (frontmatter + body) from
  stdin, validate it, re-run the identifier verifier, and write to
  `data/records/<type-folder>/<id>.md`. Refuses to overwrite without `--force`; refuses any
  record containing identifiers.
- `healthbot index`: rebuild `INDEX.md`, `lab-trends.md`, and `immunizations.md`.

Record frontmatter: `id` (date-type-slug), `date` (date of service), `type` (lab | imaging |
clinical-note | immunization | medication | other), `title`, `provider` (specialty or clinic
type only, never a person's name), `source` (doc ID), `tags`.

## 5. Facts vs. interpretation

- Cite records as `[record-id p.N]` and web sources as `[Source: URL]`.
- Quote values verbatim from records. Never invent, round, or infer values.
- If it isn't in the records, say "not in your records".
- Keep LLM-written interpretation visibly separate from extracted facts, and hedge it.
- Do not diagnose. Suggest questions for the user's clinician instead.

## 6. Trusted sources

Only these domains (and their subdomains) may be cited; the list lives in
`src/healthbot/sources.py` (`TRUSTED_DOMAINS`, `is_trusted(url)`):

mayoclinic.org, medlineplus.gov, cdc.gov, nih.gov, ncbi.nlm.nih.gov (guidelines), canada.ca,
uspreventiveservicestaskforce.org, heart.org, diabetes.org.

Only cite pages you actually fetched in this session. If nothing allowlisted covers the
topic, say so rather than citing something else.

## 7. Skills

- `ingest-records`: new documents, paths, or "ingest what's in incoming".
- `ask-health-question`: questions answered from records plus trusted sources.
- `review-health-history`: timeline, trends, overdue items, appointment prep.
