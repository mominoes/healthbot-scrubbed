---
name: ingest-records
description: Use when the user adds, drops, or mentions a new medical document, lab result, imaging report, immunization record, or any health record file or path (for example a path dragged into the terminal, or "I put records in incoming"). Scrubs identifiers with healthbot, then turns each clean document into a de-identified Markdown record.
---

# Ingest health records

Goal: raw files go through `healthbot scrub` (deterministic, no LLM), and you only ever see
scrubbed text. Read AGENTS.md section 2 first if unsure about any privacy rule.

## Hard rules

- Never open, read, cat, or inspect the raw file. Pass its path to `healthbot scrub` unread.
- If the user `@`-mentioned or attached a PDF, the content already reached you. Say so plainly,
  do not summarize or quote it, and explain the safe ways (drag the file into the terminal,
  type the path, or drop it in `data/incoming/`). Then scrub the file the safe way.
- Never repeat identifiers. Refer to documents by neutral ID (`doc-0003`) only.
- Never save `healthbot show` output to a file or paste it back to the user wholesale.

## Steps

1. **Identity file check.** Run `test -f identity.yaml`. If missing, tell the user to run
   `cp identity.example.yaml identity.yaml` and edit it in their own editor, then stop. Do not
   create or fill it in yourself, and do not read it.

2. **Scrub.** Run `.venv/bin/healthbot scrub <paths the user gave>` (quote the paths), or with
   no arguments to process `data/incoming/`. Relay the per-file summary lines. For each status:
   - `ok`: continue.
   - `needs_ocr`: scanned or image-only page; OCR is not supported yet. Quarantined.
   - `encrypted`: password-protected. Ask the user to remove the password with their own tool
     and re-submit.
   - `leak`: verifier found a leftover identifier. Quarantined. Suggest adding the missing
     variant (nickname, maiden name, extra term) to their identity file in their editor, then
     re-submitting.
   - `error`: could not process. Quarantined; suggest re-exporting the file.
   Explain next steps without naming any identifier. Never try to look at quarantined files.

3. **Optional review gate (offer once per session, before reading anything).** Tell the user
   they can inspect the redacted text first by running `healthbot show <id> | less` in their
   own terminal. Not via `!`, which would put the output into this conversation. Wait for
   their go-ahead if they want that.

4. **Read each OK document.** Run `.venv/bin/healthbot show <id>`. If output is truncated,
   continue with `--pages 1-3`, `--pages 4-6`, and so on. Keep the `[NAME]`, `[DOB]`,
   `[ID:...]` tags as they are. If the text still contains something that looks like missed
   PII (a relative's name, a physician's name, a street), leave it out of the record and tell
   the user the category only (e.g. "a personal name in the narrative"), never the value.

5. **Draft the record.** Decide `type`, date of service (not upload date), `title`, `provider`
   (specialty or clinic type only, never a person's name), `source` (the doc ID), and `tags`.
   Frontmatter:

   ```yaml
   ---
   id: 2026-01-15-lab-lipid-a1c
   date: 2026-01-15
   type: lab   # lab | imaging | clinical-note | immunization | medication | other
   title: Lipid panel + HbA1c
   provider: Family medicine
   source: doc-0002
   tags: [cholesterol, ldl, diabetes-screening]
   ---
   ```

   Body template:

   ```markdown
   # <title>

   ## Extracted facts
   <!-- Verbatim from the source. Every row cites the page. -->
   | Test | Value | Unit | Ref range | Flag | Page |
   |------|-------|------|-----------|------|------|

   ## Narrative (verbatim excerpts)
   > ... (p2)

   ## AI summary
   <!-- Clearly LLM-generated. Plain language, 5 bullets max, no new numbers. -->
   ```

   Facts are verbatim with page cites. Numbers in the AI summary must already appear in the
   facts table. Keep the summary strictly separate from facts.

6. **Write.** Send the full record to `.venv/bin/healthbot record write --stdin` (add
   `--force` only if the user agrees to overwrite). Use a quoted heredoc with nothing before
   or after it, because that is the only form the web UI allows:

   ```bash
   .venv/bin/healthbot record write --stdin <<'EOF'
   ---
   id: ...
   ---
   ...
   EOF
   ```

   If it refuses because it found an identifier, remove that content, retry, and tell the
   user the category only.

7. **Index.** After all records are written, run `.venv/bin/healthbot index`.

8. **Report.** Tell the user they can skim the new files in `data/records/`. Give a table of
   what was ingested: doc ID, record id, type, date, status. Neutral IDs only, no filenames.
   Mention quarantined docs and next steps. End with the not-medical-advice note if you
   discussed any health content.
