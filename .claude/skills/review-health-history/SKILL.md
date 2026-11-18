---
name: review-health-history
description: Use when the user wants an overview of their health history, a timeline, lab trends over time, overdue screenings or vaccines, missing records, or help preparing for a doctor's appointment or physical. Builds these from data/records and writes a one-page appointment-prep summary.
---

# Review health history and prep for an appointment

This is an educational demo, not medical advice. Read AGENTS.md sections 2, 5, and 6 if unsure.

## Hard rules

- Only read `data/records/`. Never touch `data/incoming/`, `data/private/`,
  `data/quarantine/`, the real identity file, or any raw PDF.
- Facts come verbatim from records with `[record-id p.N]` cites. Never invent values. Say
  "not in your records" when something is absent.
- Web lookups (for public schedules or guidelines) use generic terms only: no personal
  values, ages, or record text in queries. Cite only allowlisted domains
  (see `src/healthbot/sources.py`) and only pages fetched this session, as `[Source: URL]`.
- Never put identifiers in the summary file. Provider names, if any appear, are specialty
  or clinic types only.

## Steps

1. **Refresh the index.** Run `.venv/bin/healthbot index` so `INDEX.md`, `lab-trends.md`,
   and `immunizations.md` are current. If `data/records/` is empty, suggest the
   `ingest-records` skill and stop.

2. **Timeline.** Read `data/records/INDEX.md` and build a chronological list of encounters,
   labs, imaging, and immunizations, each with its record cite. Open individual records only
   when you need detail.

3. **Trends.** From `data/records/labs/lab-trends.md`, show each repeated test over time as a
   table (date, value, unit, ref range, flag, cite) and describe direction in neutral words
   ("higher than the previous result"). Don't call a result good or bad beyond the flag the
   record itself shows.

4. **Overdue and due-soon items.** Compare the immunization table and screening history
   against public schedules fetched from allowlisted sources (for example, a tetanus booster
   interval from cdc.gov or canada.ca). State the interval, the last date in the records, and
   that this is based only on what is in the records. Hedge: records may be incomplete.

5. **Missing records.** List things a person might expect but that are not in the records
   (for example no vaccine entry for a given disease, or a lab not repeated since a follow-up
   was planned). Phrase as "not in your records", not as "you never had it".

6. **Write the appointment-prep summary** to
   `data/records/summaries/<YYYY-MM-DD>-appointment-prep.md` (today's date; create the folder
   if needed). Keep it to about one page:
   - **Snapshot**: key conditions, medications, and risk factors documented in records, cited.
   - **Recent results and trends**: the few that matter most, cited.
   - **Due or overdue**: with cited public sources.
   - **Questions to ask**: 5 to 8 concrete questions for the clinician.
   - **Bring or request**: missing records and documents to ask for.
   - Disclaimer line: educational demo, not medical advice.

   Mark LLM-written interpretation as such, and keep it separate from verbatim facts.

7. **Tell the user** where the summary file is and that they can skim and edit it. Summarize
   it briefly in chat with the disclaimer.
