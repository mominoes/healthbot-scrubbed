---
name: ask-health-question
description: Use when the user asks a question about their own health, such as a lab value, vaccine, medication, risk, trend, or "what does my record say", or any health question that their ingested records could inform. Answers from data/records with citations, plus a few allowlisted trusted sources, with a clear disclaimer.
---

# Answer a health question from records and trusted sources

This is an educational demo, not medical advice. Read AGENTS.md sections 2, 5, and 6 if unsure.

## Hard rules

- Only read `data/records/` (never `data/incoming/`, `data/private/`, `data/quarantine/`, or
  the real identity file, and never a raw PDF).
- **Privacy rule for web lookups:** never put personal values, dates, ages, record text, or
  anything derived from the user's records in a search query or fetch. Use generic terms only.
  Good: "LDL cholesterol target ranges adults". Bad: "LDL 4.1 male 41 years old".
- Only cite pages from allowlisted domains (see `src/healthbot/sources.py`:
  mayoclinic.org, medlineplus.gov, cdc.gov, nih.gov, ncbi.nlm.nih.gov, canada.ca,
  uspreventiveservicestaskforce.org, heart.org, diabetes.org), and only pages you actually
  fetched this session.
- Never invent values. If it is not in the records, say "not in your records".
- Don't diagnose. Hedge interpretation ("may", "worth discussing").

## Steps

1. **Search the records.** Start with `data/records/INDEX.md`, then the trend tables
   (`data/records/labs/lab-trends.md`, `data/records/immunizations/immunizations.md`), then
   `grep -ri` over `data/records/` by keyword and synonyms (e.g. "LDL", "cholesterol",
   "lipid"). Read only the relevant records. If `data/records/` is empty, suggest the
   `ingest-records` skill.

2. **Get general guidance.** Use WebSearch or WebFetch for 1 to 3 pages from allowlisted
   domains (for example `site:cdc.gov` in the query). Generic queries only. Skip this step if
   the question is purely about what the records say.

3. **Answer in exactly this structure:**
   - **Short answer**: one or two sentences.
   - **What your records show**: verbatim values, each cited `[record-id p.N]`.
   - **What trusted sources say**: general guidance, each cited `[Source: URL]`.
   - **How these relate**: hedged interpretation connecting the two. Label it as
     interpretation, not fact.
   - **Gaps / what to ask your doctor**: missing data, outdated results, concrete questions.
   - **Disclaimer**: educational demo, not medical advice.

4. **No relevant data?** Say so plainly ("not in your records") and still give generic
   guidance only if the user wants it. Don't speculate about what their values might be.

## Examples of the right shape

- "Did I get my Tdap?" Look in the immunizations table; cite the record and page. Then fetch
  the CDC or canada.ca booster schedule (generic query) and note whether the interval has
  passed, hedged.
- "How has my cholesterol changed?" Reproduce the relevant rows of `lab-trends.md` as a
  table with dates, values, units, and record cites.
- "What am I at risk for?" Combine multiple record facts (labs, vitals, family history,
  smoking status), cite at least three records and two trusted sources, hedge heavily, and
  recommend discussing with a clinician.
