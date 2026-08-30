# Plan: College-Admissions Agentic App (Streamlit)

## Context / Goal

Build a **deployable Streamlit app** that helps US high school students build a triaged
college-application list (stretch / reach / safety). The student supplies:

1. **Their criteria** — weighted importance of cost (incl. scholarship likelihood), majors,
   rankings, climate, safety, size, location, campus culture, etc.
2. **Their circumstances** — SAT/ACT, HS GPA/transcript, AP courses + scores, activities,
   sports, recommendations, essays, and any free-text application assets.

The app then:

- Keeps a **local, lightweight attribute store** of all US colleges (sourced from the
  Department of Education College Scorecard) used to shortlist.
- For the shortlist only, **pulls the full Common Data Set (CDS) profile** and other
  college webpages from the internet to enrich each candidate.
- Runs an **agentic layer** that asks final clarifying questions, reasons over the pulled
  data, and outputs a triaged list defaulting to **2 stretch / 5 reach / 3 safety**
  (user-adjustable).

GPA is required; SAT is optional (defaults to using college averages when absent).

---

## Architecture

```
                     ┌────────────────────────────────────────────┐
                     │            Streamlit Frontend (app.py)     │
                     │  Student profile │ criteria │ results │ chat  │
                     └──────┬──────────────┬────────────────┬────────┘
                            │              │                │
              ┌─────────────▼──┐   ┌───────▼────────┐  ┌────▼──────────┐
              │  Intake Agent  │   │  College Agent │  │  Clarify Qs  │
              │  (parse + gap  │   │  (fetch+score) │  │  (agentic)   │
              │   analysis)    │   │                │  │               │
              └───────┬────────┘   └───────┬────────┘  └──────┬────────┘
                      │                    │                  │
              ┌───────▼────────────────────▼──────────────────▼────────┐
              │                   Agent Runtime (LLM + tools)          │
              │   tools: query_local_db │ fetch_cds │ fetch_page │ websearch │
              └───────┬────────────────────────────────┬───────────────┘
                      │                                │
              ┌───────▼──────────────┐     ┌──────────────▼──────────┐
              │  Local college store │     │  Internet (live fetch)  │
              │  SQLite + Parquet    │     │  CDS pages + webpages   │
              │  (Scorecard-derived) │     │  (shortlist only)       │
              └──────────────────────┘     └─────────────────────────┘
```

**Two data tiers** (matches the spec):
1. **Local store** — one row per college with ~30 key attributes (acceptance rate, SAT/ACT
   25–75, cost, tuition, major shares, region, locale, size, control, coords, graduation
   proxy). Used for shortlisting. Built once from Scorecard, refreshed on a schedule.
2. **Live fetch** — full CDS + college webpages fetched **only for the shortlist**
   (~15–25 colleges), cached on disk so repeat runs are cheap.
   - **Scope is all ~4,000 colleges in the local store**, but CDS URLs are not curatable at
     that scale (no official index exists). So CDS enrichment is **best-effort per
     shortlisted college**: at runtime, discover the CDS URL by (a) trying a small set of
     common URL patterns against the college's domain, (b) the `websearch` tool, and
     (c) a growing curated map for the most common schools. Unreachable/missing CDS →
     gracefully fall back to Scorecard data + the college's admissions page.

---

## Data Layer

### Local store (build-time)

Source: Dept. of Education **College Scorecard** "Most-Recent-Cohorts-Institution" CSV
(`https://ed-public-download.scorecard.network/downloads/Most-Recent-Cohorts-Institution_*.zip`).
Refresh script: `scripts/build_db.py`.

Schema (`data/colleges.db` SQLite + `data/colleges.parquet`):

| Field | Source col | Use |
|---|---|---|
| `unitid`, `name`, `city`, `state`, `url` | INSTNM, CITY, STABBR, INSTURL | identity |
| `main`, `num_branch` | MAIN, NUMBRANCH | main-vs-branch flag |
| `control` | CONTROL | public / private-nonprofit / for-profit |
| `region`, `locale` | REGION, LOCALE | region + urban-suburban-rural |
| `lat`, `lon` | LATITUDE, LONGITUDE | distance & climate proxy |
| `acceptance_rate` | ADM_RATE | selectivity |
| `sat_25/75` (vr/mt/wr), `sat_avg` | SATVR25..SATWRMID, SAT_AVG | admissions bar |
| `act_25/75`, `act_mid` | ACTCM25..ACTWRMID | admissions bar |
| `cost_attendance`, `tuition_in/out` | COSTT4_A, TUITIONFEE_IN/OUT | cost |
| `ugds`, `ugds_*` | UGDS, UGDS_* | size + diversity |
| `pcip_*` | PCIP01..PCIP54 | **major offerings** (share of degrees by CIP) |
| `completion_rate` | COMP_4YR_TRANS_YR2_RT | graduation/outcome proxy |
| `cds_url` | curated map | where to fetch the full CDS |

Filter at build: 4-year, degree-granting; **keep branch campuses** (flag them via the
`MAIN` / `NUMBRANCH` columns so the UI can distinguish a main campus from a branch);
drop for-profit programs.

### Live fetch (runtime, shortlist only)

- **CDS page**: discovered at runtime for shortlisted colleges via common-URL-pattern
  probing + `websearch` + a curated `data/cds_urls.csv` for frequently-requested schools.
  No universal index exists, so this is best-effort; missing CDS → Scorecard + admissions
  page only.
- **College webpages** (admissions, scholarships, safety, housing): fetched via a
  `fetch_page` tool; parse key sections (tuition, **application deadlines + EA/ED
  availability**, safety stats, housing, first-year GPA distribution).
- **Web search** tool (e.g. DuckDuckGo HTML) used by the agent to locate pages when no
  curated URL exists.
- Cache fetched content to `data/cache/` (JSON, keyed by URL) so re-runs are free.

---

## Shortlisting

1. **Hard filter**: 4-year, undergraduate-focused; drop for-profit; drop if
   `acceptance_rate` missing.
2. **Criteria match** (student's weighted criteria vs local attributes):
   - *Majors* → colleges whose `pcip_*` share overlaps the student's interested CIP families.
   - *Cost* → within the student's stated annual budget (before aid).
   - *Climate/region* → `region` + `lat/lon` (e.g. Northeast/mild, Southeast/warm).
   - *Safety* → campus-crime stats come from the live CDS; use `locale` + size as a prior.
   - *Rankings* → proxy from selectivity/completion; refine with live data.
   - *Size, location, culture* → `locale`, `ugds`, `control`.
3. **Rank** by weighted fit score → top ~20 for the shortlist.
4. **Fetch full CDS + pages** for the shortlist, then re-rank with the enriched data.

---

## Scoring & reach classification

**Reach tier** = comparison of the student's academic profile against each college's
middle 50% (25th–75th percentile) of first-year GPA and SAT/ACT.

Local prior (from Scorecard SAT/ACT 25–75; refined by live CDS C-section GPA when available):

| Tier | Condition |
|---|---|
| **Safety** | Student GPA ≥ 75th **and** SAT ≥ 75th (or comfortably above median), **and** acceptance rate ≥ ~40% |
| **Reach** | Student GPA < 25th **or** SAT < 25th, **and** acceptance rate ≤ ~25% |
| **Match** | In the middle 50% on both, or borderline selectivity |

Rules:
- If SAT is **absent**, use the college's SAT average/middle-50 as the bar instead of the
  student's score (per spec) — the tier is then driven by GPA + selectivity.
- GPA is **required**; the app blocks generation without it.
- The student's **criteria fit score** modulates the final list: a school that is a
  "reach" by stats but a poor fit on cost/major is flagged; a "safety" that is a strong
  fit is prioritized.

**Default output**: 2 stretch / 5 reach / 3 safety. The student can change these counts.

---

## Agentic layer

An LLM-driven agent (recommended: **Claude via Anthropic API**) with a tool set, running
a conversational loop:

- `query_local_db(sql/lookup)` — read the local college store.
- `fetch_cds(college)` — pull + parse the full CDS page.
- `fetch_page(url)` — pull a college webpage.
- `websearch(query)` — locate pages/aggregators not in the curated map.

Phases:
1. **Intake** — parse the student's free-text inputs into a structured profile; detect
   gaps (no SAT, no budget, no major, ambiguous region).
2. **Clarify** — emit 3–6 targeted questions (e.g. "You listed AP CS but no SAT — do you
   plan to retake it, or should I use college averages?", "What annual cost is acceptable
   before aid?", "Which of these majors is primary: CS, biology, business?").
3. **Retrieve** — run the shortlist + live fetch tools.
4. **Reason + recommend** — produce the triaged list with per-college rationale: stats
   comparison, fit on each criterion, cost/aid notes, **application deadlines and whether
   Early Decision / Early Action is available**, and a suggested application-order.

Tool-calling loop is hand-rolled for the MVP (fully controllable, no heavy framework);
LangGraph can be adopted if the multi-step reasoning gets complex.

---

## UI / UX flow (Streamlit)

1. **Onboarding** — multi-section form: criteria (with weights), student stats (GPA
   required; SAT/AP/activities optional), budget, region/climate preferences, major
   interests.
2. **Clarifying questions** — rendered as an in-app chat/step where the agent asks
   follow-ups; user answers, then "Generate list".
3. **Results** — three columns (Stretch / Reach / Safety), each card showing: name,
   acceptance rate, GPA/SAT bar vs the student's stats, cost, fit score, **application
   deadline + EA/ED availability**, key rationale, and links (CDS, admissions page).
4. **Adjust** — sliders to change the 2/5/3 counts; re-runs classification without a new
   fetch (cached).
5. **Save** — optional local save of the student's profile for reuse (explicit save/
   clear controls; nothing stored by default).
6. **Export** — CSV / shareable link.

---

## Tech Stack

- **Framework**: Streamlit (Python 3.10+)
- **Data**: pandas, pyarrow (Parquet), sqlite3 (stdlib)
- **Fetching**: `httpx`/`requests`, `beautifulsoup4`, `lxml`
- **Agent**: Anthropic SDK (Claude), hand-rolled tool loop
- **Store**: SQLite + Parquet in `data/`
- **Deployment**: Streamlit Community Cloud — **free, ad-free, no payments** (no Google
  ads, no checkout). If monetization/payments or own ads are ever wanted, switch to
  self-hosting via the provided Dockerfile. Uses `.streamlit/secrets.toml` for API keys.
- **LLM**: Ollama-compatible HTTP API against `https://zenmux.ai/api/v1`, model
  `dots-studio/dots3-note-prev`, keyed via env vars from `.env` (`ollama_url`,
  `OLLAMA_API_KEY`, `model`). **Never commit the token** — put it in `.env` (gitignored)
  only.
- **Tests**: pytest (scoring unit tests + a small smoke test of the full pipeline)

---

## File Layout

```
college-app/
├── app.py                    # Streamlit entry + session state
├── pages/                    # optional multi-page nav
├── agents/
│   ├── intake_agent.py       # parse inputs → structured profile + gap detection
│   └── college_agent.py      # LLM tool loop: clarify → retrieve → recommend
├── scoring.py                # reach/safety classification + fit score
├── fetchers.py               # CDS + webpage fetching, cache
├── data/
│   ├── colleges.db           # local store (SQLite)
│   ├── colleges.parquet      # local store (Parquet)
│   ├── students.db           # optional saved student profiles (SQLite)
│   ├── cds_urls.csv          # curated CDS URL map (core set, grown over time)
│   ├── cache/                # live-fetch cache
│   └── scripts/
│       └── build_db.py       # refresh local store from Scorecard
├── tests/
│   └── test_scoring.py
├── requirements.txt
├── Dockerfile
├── .streamlit/secrets.toml.example   # ANTHROPIC_BASE_URL / AUTH_TOKEN / MODEL
└── README.md
```

---

## Implementation Phases

**Phase 0 — MVP (week 1–2):** local store build from Scorecard (all ~4,000 colleges);
intake form; criteria weights; local-only ranking + reach classification (no live CDS
fetch yet); basic Streamlit results view; deploy to Community Cloud. *Deliverable: a
working triaged list from Scorecard data alone.*

**Phase 1 — Live enrichment (week 2–3):** runtime CDS URL discovery (common-URL-pattern
probing + websearch + curated core map); CDS parser (GPA distribution, scholarships,
safety/C2–C4, housing, deadlines); `fetch_cds` + `fetch_page` tools with caching;
re-rank shortlist on enriched data.

**Phase 2 — Agentic layer (week 3–4):** intake agent (parse + gap detection), clarifying
questions in chat, LLM tool loop (configured for the zenmux endpoint), final
recommendation with rationale; websearch tool for missing URLs.

**Phase 3 — Hardening (week 4+):** grow the curated CDS URL map, error handling for
missing/unreachable pages, performance (caching, pagination), optional student-profile
save, export/share, CI to refresh the local store.

---

## Verification

- `scripts/build_db.py` runs clean and reproduces the local store; assert key columns
  non-null and row count ≈ expected institutions.
- `pytest tests/`: scoring unit tests — e.g. given a student GPA/SAT and a college's
  25/75/mid, the tier is correct; SAT-absent path uses college average; GPA-absent
  blocks generation.
- End-to-end smoke run in the deployed app: submit a sample profile → confirm a 2/5/3
  list renders with sensible tiers and no crashes; verify live fetch cache is created.
- Manual check: a known highly-selective school (e.g. MIT, Stanford) lands in "stretch"
  for an average student; a large public university lands in "safety" for a strong
  student.

---

## Assumptions & Open Questions (to confirm)

1. **Deployment** — Streamlit Community Cloud (free, ad-free, no payments). Self-host via
   Docker if payments/own ads are ever wanted.
2. **LLM** — Ollama-compatible endpoint `https://zenmux.ai/api/v1`, model
   `dots-studio/dots3-note-prev`, via env/secrets (token never committed).
3. **College scope** — all ~4,000 in the local store; CDS enrichment is best-effort for the
   shortlist only (URLs discovered at runtime), not full CDS for every college.
4. **Student data** — **optional local save for reuse** (a lightweight local profile store
   with explicit save/clear controls; nothing stored by default).
5. **CDS URL map** is best-effort at runtime (patterns + web search + curated core set),
   not a universal index.

## Risks

- **CDS availability/URLs** — no canonical index; many colleges don't publish CDS or use
  non-standard URLs, and the scope is all ~4,000. Mitigation: runtime URL discovery
  (patterns + websearch + curated core set) for the shortlist only; graceful fallback to
  Scorecard + admissions page. Full CDS for every college is explicitly out of scope.
- **CDS parsing quality** — CDS is PDF or HTML with inconsistent markup; parse defensively,
  prefer HTML versions, fall back to key-phrase extraction via the LLM.
- **SAT score fields are increasingly sparse** (test-optional era) — rely on GPA +
  acceptance rate + the SAT-absent rule.
- **Live fetch at scale** — rate limits; cache aggressively and cap concurrent fetches.
- **LLM endpoint availability** — the zenmux endpoint must be reachable from the deploy
  environment; provide a fallback env var for a direct Anthropic/OpenAI key.

---

## Sources

- [College Scorecard data page](https://collegescorecard.ed.gov/data/) — institution-level
  admissions, cost, majors, outcomes; refreshed 2026-06-10.
- [Most-Recent-Cohorts-Institution CSV](https://ed-public-download.scorecard.network/downloads/Most-Recent-Cohorts-Institution_06102026.zip) — verified local base
  (ADM_RATE, SATVR/MT/WR 25–75, ACT 25–75, COSTT4_A, PCIP majors, REGION, LOCALE, CONTROL).