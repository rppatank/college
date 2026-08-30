# College Triage

A deployable Streamlit app that helps US high school students build a triaged
college-application list (**stretch / reach / safety**).

## Status

**Phase 3.** Conversational one-question-at-a-time interview, in-state/out-of-state
tuition with multi-state residency and reciprocity, curated application deadlines
+ EA/ED info for 63 colleges, heuristic aid-probability estimates, LLM-powered
agent (gap detection, profile summary, per-college rationale), CSV export,
saved profiles, and a triaged results view.

## Quick start

```bash
pip install -r requirements.txt

# 1. Build the local college store (downloads ~25MB Scorecard CSV)
python scripts/build_db.py

# 2. Generate curated deadlines
python scripts/build_deadlines.py

# 3. Run the app
streamlit run app.py
```

## Interview flow

The interview is conversational — one question at a time, in a chat-style UI:

1. **GPA** (required), **home state**, **residency states** (parent/grandparent —
   e.g. FL grandparent residency qualifies for in-state tuition at FL publics)
2. **Test scores** — SAT, ACT, or no score
3. **In-state vs out-of-state focus**, **preferred states**, **distance tolerance**
4. **Budget**, **majors** (free-text, mapped to CIP-2 codes), **high school**

At the end of the interview, the agent reviews your answers and asks targeted
follow-ups for anything unclear (missing GPA, no budget, no majors, etc.).

## Results cards

Each college card shows:

- Acceptance rate, SAT bar vs. your score
- **Effective cost** (in-state / out-of-state / private) with sticker comparison
- 🏠 in-state tag when applicable
- **💰 aid probability** — heuristic estimate of % receiving grant aid + expected amount
- **📅 application deadlines** — Regular Decision + Early Action / Early Decision
- Fit score, city/state, admissions-page link

## In-state tuition

The app distinguishes sticker price from what you'd actually pay:

- **In-state public**: `tuition_in_state` + living allowance
- **Out-of-state public**: `tuition_out_state` + living allowance
- **Private**: same rate regardless of state
- **Reciprocity agreements** (MN-WI, ND-MN/SD, NY-VT/MA, etc.) are factored in
- **Multi-state residency** — parent/grandparent residency states qualify for
  in-state tuition at those states' public universities

## Deadlines

`scripts/build_deadlines.py` generates `data/college_deadlines.csv` with
curated 2025–2026 application deadlines and EA/ED info for 63 common colleges
(Ivy League, top national, UC system, state flagships, privates). Unitids are
verified against the local store. Results cards show deadlines merged by unitid.

## Feeder high school data

Per-highschool admission pipeline data **does not exist publicly at scale**.
The only source is university CDS Section C2, published by ~5–10% of colleges.
`scripts/fetch_cds.py` and `fetchers.py` are best-effort scrapers with curated
URL maps; coverage is expected to remain near-zero for live CDS pages (modern
pages are JS-rendered). The app relies on individual student stats vs. college
benchmarks instead. Curated deadlines + heuristic aid estimates fill the gap.

## Data flow

```
Scorecard CSV ──[scripts/build_db.py]──> data/colleges.db + data/colleges.parquet
                                            │
                                            ▼
                                     app.py (Streamlit)
                                   interview → rank → classify → triage

scripts/build_deadlines.py ──> data/college_deadlines.csv ──┐
                                                            │ merge by unitid
                                                            ▼
```

## Phases

| Phase | Scope | Status |
|---|---|---|
| 0 | Local store + interview + in-state tuition + ranking + results view | ✅ done |
| 1 | Deadlines + EA/ED, aid probability, multi-state residency, reciprocity | ✅ done |
| 2 | LLM agent: gap detection, profile summary, per-college rationale, list summary | ✅ done |
| 3 | CSV export, saved profiles, expanded URL map, CI workflow | ✅ done |

## Export & save (Phase 3)

- **CSV export** — download your triaged list as a spreadsheet with tier, acceptance
  rate, fit score, effective cost, aid probability, and deadlines.
- **Saved profiles** — save your interview answers to `data/students.db` and reload
  them later. Saved profiles appear in the sidebar; nothing is stored by default.

## Admissions URL map

`fetchers.py` includes a curated `ADM_URL_MAP` with 63 verified admissions-page
URLs (Ivy League, UC system, state flagships, privates). The `discover_admissions_url()`
function falls back to pattern probing against the college's domain when no
curated URL exists.

## CI

`.github/workflows/refresh_store.yml` runs weekly (Monday 06:00 UTC) to rebuild
the local store from the Scorecard CSV, regenerate deadlines, run tests, and
commit any changes. Also available via the GitHub UI (workflow_dispatch).

## Agent (Phase 2)

`agents/agent.py` wraps the Ollama-compatible LLM endpoint and provides:

- **`detect_gaps()`** — deterministic gap detection (GPA, budget, majors, residency)
  with optional LLM wording enhancement, used after the interview to ask
  targeted follow-ups
- **`summarize_profile()`** — one-paragraph counselor-style profile summary
- **`generate_rationale()`** — 2-3 sentence per-college fit explanation shown on
  each results card
- **`generate_list_summary()`** — overall list summary at the top of the results

The configured model is a reasoning model (dots3) that thinks out loud before
answering — `chat()` reads the `reasoning` field and `_clean_rationale()`
strips the thinking preamble so the displayed text is clean.

## Configuration

Credentials live in `.env` (gitignored):

```
OLLAMA_API_KEY=...
ollama_url=https://zenmux.ai/api/v1
model=dots-studio/dots3-note-prev
```

## Tests

```bash
pytest tests/
```

21 tests: classification matrix, GPA shift, SAT-absent path, in-state tuition,
reciprocity, and criteria-fit scoring.

## Deployment

Streamlit Community Cloud — point your repo at `app.py` and it deploys free."# college" 
