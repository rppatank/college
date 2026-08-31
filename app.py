"""app.py — Streamlit entry point.

Phase 1: conversational interview (one question at a time) -> local ranking +
reach classification -> triaged results view (2 stretch / 5 reach / 3 safety,
user-adjustable). Curated application deadlines + EA/ED info merged in.
In-state vs out-of-state tuition with multi-state residency support.

Run:  streamlit run app.py
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

from scoring import StudentProfile, _effective_cost, aid_estimate, triage, final_counts
from agents.agent import detect_gaps, generate_rationale, generate_list_summary

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "colleges.db"

# CIP-2 family labels for the majors picker.
# Labels MUST match the actual Scorecard pcip_XX columns (standard CIP-2 codes).
CIP_LABELS = {
    "01": "Agriculture & Related",
    "03": "Architecture",
    "04": "Communications",
    "05": "Education",
    "09": "Health Professions",
    "10": "Liberal Arts",
    "11": "Computer Science",
    "12": "Law",
    "13": "Education (other)",
    "14": "Engineering",
    "15": "Engineering Tech",
    "16": "Foreign Languages",
    "19": "Family/Consumer Sci",
    "22": "Military Tech",
    "23": "English",
    "24": "Liberal Arts (other)",
    "25": "Library Science",
    "26": "Biological Sciences",
    "27": "Mathematics",
    "29": "American Indian Studies",
    "30": "Multi/Interdisciplinary",
    "31": "Parks, Rec, Leisure",
    "38": "Philosophy",
    "39": "Theology & Religion",
    "40": "Physical Sciences",
    "41": "Science Tech",
    "42": "Psychology",
    "43": "Security & Protective (Forensic Science)",
    "44": "Public Administration",
    "45": "Social Sciences",
    "51": "Nursing",
}

REGION_NAMES = {
    1: "New England",
    2: "Mid East",
    3: "Great Lakes",
    4: "Plains",
    5: "Southeast",
    6: "Southwest",
    7: "Rocky Mountains",
    8: "Far West",
    9: "Outlying Areas",
}

US_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
]

LOCALE_NAMES = {
    11: "City - Large",
    12: "City - Midsize",
    13: "City - Small",
    21: "Suburb - Large",
    22: "Suburb - Midsize",
    23: "Suburb - Small",
    31: "Town - Fringe",
    32: "Town - Distant",
    33: "Town - Remote",
    41: "Rural - Fringe",
    42: "Rural - Distant",
    43: "Rural - Remote",
}


# ---------------------------------------------------------------- data

@st.cache_data(show_spinner=False)
def load_colleges() -> pd.DataFrame:
    """Load the local college store from SQLite."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query("SELECT * FROM colleges", conn)
    finally:
        conn.close()
    return df


def load_colleges_filtered() -> pd.DataFrame:
    """Load colleges, dropping rows missing acceptance_rate (hard filter)."""
    df = load_colleges()
    if df.empty:
        return df
    return df.dropna(subset=["acceptance_rate"]).copy()


def load_deadlines() -> pd.DataFrame:
    """Load curated application deadlines and merge into the college store."""
    path = ROOT / "data" / "college_deadlines.csv"
    if not path.exists():
        return pd.DataFrame()
    dl = pd.read_csv(path)
    # Drop the redundant name column; the college store already has 'name'.
    if "name" in dl.columns:
        dl = dl.drop(columns=["name"])
    return dl


# ---------------------------------------------------------------- saved profiles

STUDENTS_PATH = ROOT / "data" / "students.db"


def _init_students_db() -> None:
    """Create the students table if it doesn't exist."""
    import sqlite3
    STUDENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(STUDENTS_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS students (
            name TEXT PRIMARY KEY,
            answers TEXT NOT NULL,   -- JSON-encoded interview answers
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.commit()
    conn.close()


def _list_saved_profiles() -> list[str]:
    """Return the names of all saved student profiles."""
    import sqlite3
    if not STUDENTS_PATH.exists():
        return []
    _init_students_db()
    conn = sqlite3.connect(STUDENTS_PATH)
    rows = [r[0] for r in conn.execute("SELECT name FROM students ORDER BY name").fetchall()]
    conn.close()
    return rows


def _load_saved_profile(name: str) -> Optional[dict]:
    """Load a saved student profile by name. Returns the answers dict or None."""
    import sqlite3
    if not STUDENTS_PATH.exists():
        return None
    _init_students_db()
    conn = sqlite3.connect(STUDENTS_PATH)
    row = conn.execute(
        "SELECT answers FROM students WHERE name = ?", (name,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return None


def _save_profile(name: str, answers: dict) -> None:
    """Save a student profile (upsert by name)."""
    import sqlite3
    _init_students_db()
    conn = sqlite3.connect(STUDENTS_PATH)
    conn.execute(
        """INSERT INTO students (name, answers) VALUES (?, ?)
           ON CONFLICT(name) DO UPDATE SET answers = excluded.answers,
           updated_at = CURRENT_TIMESTAMP""",
        (name, json.dumps(answers, default=str)),
    )
    conn.commit()
    conn.close()


def _clear_saved_profiles() -> None:
    """Delete all saved student profiles."""
    import sqlite3
    if not STUDENTS_PATH.exists():
        return
    conn = sqlite3.connect(STUDENTS_PATH)
    conn.execute("DELETE FROM students")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------- UI

INTERVIEW_QUESTIONS = [
    {
        "id": "gpa",
        "text": "What's your unweighted high school GPA? (0–4.0)",
        "type": "number",
        "required": True,
        "min": 0.0, "max": 4.0, "step": 0.01, "default": 3.8,
    },
    {
        "id": "home_state",
        "text": "What state do you live in and go to high school?",
        "type": "state",
        "required": True,
    },
    {
        "id": "residency",
        "text": "Do you have residency in any other states? (e.g. where your parents or grandparents live — this can qualify you for in-state tuition at those states' public universities, even if you don't live there)",
        "type": "residency",
    },
    {
        "id": "test",
        "text": "Do you have an SAT score, an ACT score, or no test score?",
        "type": "choice",
        "options": ["SAT", "ACT", "No test score"],
    },
    {
        "id": "sat_score",
        "text": "What's your SAT total score? (400–1600)",
        "type": "number",
        "min": 400, "max": 1600, "step": 10,
        "default": 1350,
        "condition": "test == 'SAT'",
    },
    {
        "id": "act_score",
        "text": "What's your ACT composite score? (1–36)",
        "type": "number",
        "min": 1, "max": 36,
        "step": 1,
        "default": 28,
        "condition": "test == 'ACT'",
    },
    {
        "id": "in_state_focus",
        "text": "Are you focusing on in-state colleges, out-of-state, or both?",
        "type": "choice",
        "options": ["In-state only", "Out-of-state only", "Both"],
    },
    {
        "id": "preferred_states",
        "text": "Which states are you willing to consider for college? (comma-separated abbreviations, e.g. MN, WI, CA — or leave blank for anywhere)",
        "type": "text",
    },
    {
        "id": "distance",
        "text": "How far from home are you willing to go?",
        "type": "choice",
        "options": ["Stay in-state", "Within 300 miles", "Anywhere in the US", "Open to international"],
    },
    {
        "id": "budget",
        "text": "What's the maximum annual cost you can afford? (before aid, in dollars)",
        "type": "number",
        "min": 0,
        "step": 5000,
        "default": 60000,
    },
    {
        "id": "majors",
        "text": "What majors are you interested in? (e.g. computer science, biology, business, nursing — comma-separated)",
        "type": "text",
    },
    {
        "id": "high_school",
        "text": "What high school do you attend? (name and city, e.g. 'North HS, Minneapolis')",
        "type": "text",
    },
]


def _default_for(q: dict, answers: dict):
    """Return the default value for a question, using prior answers."""
    if q["id"] == "home_state":
        return "MN"
    return q.get("default")


def _render_question_input(q: dict, answers: dict):
    """Render the input widget for the current question. Returns the answer."""
    qid = q["id"]
    default = _default_for(q, answers)

    if q["type"] == "number":
        return st.number_input(
            q["text"],
            min_value=q.get("min", 0),
            max_value=q.get("max", 1000000),
            step=q.get("step", 1),
            value=default if default is not None else q.get("min", 0),
            key=f"intv_{qid}",
        )
    elif q["type"] == "choice":
        return st.radio(q["text"], options=q["options"], index=0, key=f"intv_{qid}")
    elif q["type"] == "state":
        return st.selectbox(
            q["text"],
            options=US_STATES,
            index=US_STATES.index(default) if default in US_STATES else 0,
            key=f"intv_{qid}",
        )
    elif q["type"] == "residency":
        st.write(q["text"])
        # Single text input — simpler than 3 separate fields.
        raw = st.text_input(
            "Additional residency states (comma-separated, optional)",
            key="intv_res",
            placeholder="e.g. FL, GA",
            help="States where your parents or grandparents live — some states grant "
                 "in-state tuition to residents' grandchildren even if you don't live there.",
        )
        states = []
        for part in str(raw).split(","):
            part = part.strip().upper()
            if part and part in US_STATES and part not in states:
                states.append(part)
        return tuple(states)
    else:  # text
        return st.text_input(q["text"], value=str(default) if default else "", key=f"intv_{qid}")


def _should_ask(q: dict, answers: dict) -> bool:
    """Check if a conditional question should be asked."""
    cond = q.get("condition")
    if not cond:
        return True
    # Simple condition: "test == 'SAT'"
    for key, val in answers.items():
        if cond.startswith(key):
            return str(val) == cond.split("==")[1].strip().strip("'")
    return True


def _analyze_answers(answers: dict) -> list[dict]:
    """Review interview answers and return follow-up questions for unclear ones.

    Delegates to the LLM-powered agent (Phase 2), which uses deterministic
    gap detection + optional LLM wording enhancement.
    """
    return detect_gaps(answers)


def main() -> None:
    st.set_page_config(
        page_title="College App",
        page_icon="🎓",
        layout="wide",
    )

    # Logo in the top-left corner with matching navy background.
    # Look for the logo in several locations (works locally and on Streamlit Cloud).
    _logo_candidates = [
        ROOT / "logo.png",
        ROOT / "assets" / "logo.png",
        ROOT / "www" / "logo.png",
        Path("C:/Users/tapor/Desktop/Deploy/open/www/logo.png"),  # local dev
    ]
    logo_path = next((p for p in _logo_candidates if p.exists()), None)
    logo_b64 = ""
    if logo_path:
        import base64

        with open(logo_path, "rb") as f:
            logo_b64 = base64.b64encode(f.read()).decode("utf-8")

    # Logo in the sidebar (left panel) + centered title in main content.
    # Look for the logo in several locations (works locally and on Streamlit Cloud).
    _logo_candidates = [
        ROOT / "logo.png",
        ROOT / "assets" / "logo.png",
        ROOT / "www" / "logo.png",
        Path("C:/Users/tapor/Desktop/Deploy/open/www/logo.png"),  # local dev
    ]
    logo_path = next((p for p in _logo_candidates if p.exists()), None)
    logo_b64 = ""
    if logo_path:
        import base64

        with open(logo_path, "rb") as f:
            logo_b64 = base64.b64encode(f.read()).decode("utf-8")

    # Centered title in the main content area.
    st.markdown(
        """
        <style>
        /* Navy background matching the logo */
        .stApp {
            background: #0f2740;
            color: #ffffff;
        }
        .title-center {
            color: #ffffff;
            font-size: 2.2rem;
            font-weight: 700;
            text-align: center;
            margin: 4px 0 8px 0;
        }
        /* Light text on navy */
        .stApp h1, .stApp h2, .stApp h3, .stApp p, .stApp span {
            color: #ffffff !important;
        }
        </style>
        <div class="title-center">🎓 College App</div>
        """,
        unsafe_allow_html=True,
    )

    st.write(
        "Build your triaged college list (stretch / reach / safety) from a local "
        "store of ~4,000 US colleges. I'll ask you a few questions, then show "
        "your personalized list with deadlines, costs, and fit scores."
    )

    df = load_colleges_filtered()
    if df.empty:
        st.error(
            "Local college store not found. Run `python scripts/build_db.py` first "
            "(downloads the Dept. of Education College Scorecard)."
        )
        st.stop()

    # Merge curated deadlines (Phase 1).
    dl = load_deadlines()
    if not dl.empty:
        df = df.merge(dl, on="unitid", how="left")

    # ---- Initialize interview session state ----
    if "interview_step" not in st.session_state:
        st.session_state.interview_step = "intro"
        st.session_state.answers = {}
        st.session_state.chat = []

    # ---- Sidebar: logo + criteria + list size (always visible) ----
    with st.sidebar:
        if logo_b64:
            st.markdown(
                f'<img src="data:image/png;base64,{logo_b64}" alt="Logo" '
                f'style="height:48px; width:auto; display:block; margin:8px 0 4px 0;" />',
                unsafe_allow_html=True,
            )
        st.divider()
        st.subheader("Criteria — importance (0–10)")
        w_cost = st.slider("Cost + scholarship likelihood", 0, 10, 9)
        w_majors = st.slider("Majors / academic fit", 0, 10, 9)
        w_rankings = st.slider("Rankings / selectivity", 0, 10, 7)
        w_climate = st.slider("Climate / region", 0, 10, 7)
        w_safety = st.slider("Campus safety", 0, 10, 8)
        w_size = st.slider("Size", 0, 10, 5)
        w_location = st.slider("Location", 0, 10, 7)
        w_culture = st.slider("Campus culture", 0, 10, 6)

        st.divider()
        st.subheader("Preferences")
        majors = st.multiselect(
            "Majors of interest (CIP-2 families)",
            options=sorted(CIP_LABELS.keys()),
            format_func=lambda c: f"{c} — {CIP_LABELS[c]}",
            default=["11", "14", "25"],
        )
        regions = st.multiselect(
            "Preferred regions",
            options=sorted(REGION_NAMES.keys()),
            format_func=lambda r: REGION_NAMES[r],
            default=[2, 5, 8],
        )
        locales = st.multiselect(
            "Preferred locale (urban/suburban/rural)",
            options=sorted(LOCALE_NAMES.keys()),
            format_func=lambda l: LOCALE_NAMES[l],
            default=[11, 12, 13, 21, 22, 23],
        )
        controls = st.multiselect(
            "Preferred control",
            options=[1, 2],
            format_func=lambda c: {1: "Public", 2: "Private nonprofit"}[c],
            default=[1, 2],
        )
        size_pref = st.selectbox(
            "Size preference",
            options=["Any", "Small", "Medium", "Large"],
            index=0,
        )

        st.divider()
        st.subheader("List size")
        n_stretch = st.slider("Stretch", 0, 5, 2)
        n_reach = st.slider("Reach", 0, 10, 5)
        n_safety = st.slider("Safety", 0, 10, 3)

        st.divider()
        st.subheader("Saved profiles")
        saved = _list_saved_profiles()
        if saved:
            load_name = st.selectbox("Load a saved profile", options=["-- none --"] + saved, key="load_profile")
            if st.button("Load selected", use_container_width=True):
                loaded = _load_saved_profile(load_name)
                if loaded:
                    st.session_state.answers = loaded
                    st.session_state.interview_step = "clarify"
                    st.rerun()
        else:
            st.write("_No saved profiles_")

    # ---- Main area: conversational interview ----
    step = st.session_state.interview_step

    if step == "intro":
        st.info("👋 Hi! I'll ask you a few questions to build your personalized college list. "
                "At the end, I'll review your answers and follow up if anything is unclear.")
        if st.button("Start interview", type="primary", use_container_width=True):
            st.session_state.interview_step = "questions"
            st.session_state.chat = [
                {"role": "assistant", "content": "Great! Let's start with your academic profile."},
            ]
            st.rerun()

    elif step == "questions":
        # Determine which questions to ask (skip conditionals that don't apply)
        answers = st.session_state.answers
        active = [q for q in INTERVIEW_QUESTIONS if _should_ask(q, answers)]

        # Find the next unanswered question.
        # A question counts as unanswered only if it hasn't been answered at all,
        # OR it's required and the answer is blank. Optional questions with blank
        # answers (e.g. "preferred_states" left empty for "anywhere") are valid.
        next_q = None
        for q in active:
            if q["id"] not in answers:
                next_q = q
                break
            if q.get("required") and answers[q["id"]] in (None, "", (), []):
                next_q = q
                break

        if next_q is None:
            # All questions answered — move to clarification
            st.session_state.interview_step = "clarify"
            st.rerun()
            return

        # Show chat history
        for msg in st.session_state.chat:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])

        # Show current question
        with st.chat_message("assistant"):
            st.write(next_q["text"])
            answer = _render_question_input(next_q, answers)

        # Next button
        col1, col2 = st.columns([1, 4])
        with col1:
            if st.button("Next →", key=f"next_{next_q['id']}", type="primary"):
                # Validate
                if next_q.get("required") and (answer is None or answer == "" or answer == ()):
                    st.error("This question is required. Please answer before continuing.")
                    st.stop()
                # Store answer under the question ID so the "next unanswered"
                # check below finds it on the next rerun.
                st.session_state.answers[next_q["id"]] = answer
                # Add to chat
                st.session_state.chat.append({"role": "assistant", "content": next_q["text"]})
                st.session_state.chat.append({"role": "user", "content": str(answer)})
                st.rerun()

    elif step == "clarify":
        answers = st.session_state.answers
        followups = _analyze_answers(answers)

        # Show chat history
        for msg in st.session_state.chat:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])

        if not followups:
            # All clear — generate the list
            st.session_state.interview_step = "results"
            st.rerun()
            return

        # Show agent analysis message
        with st.chat_message("assistant"):
            st.write("Thanks! I've reviewed your answers. Let me follow up on a few things:")

        # Ask each follow-up
        for fq in followups:
            with st.chat_message("assistant"):
                st.write(fq["text"])
            if fq["id"] == "gpa":
                val = st.number_input("GPA", min_value=0.0, max_value=4.0, value=3.8, step=0.01, key=f"follow_gpa_{fq['id']}")
            elif fq["id"] == "budget":
                val = st.number_input("Budget ($)", min_value=0, value=60000, step=5000, key=f"follow_budget_{fq['id']}")
            elif fq["id"] == "majors":
                val = st.text_input("Majors", key=f"follow_majors_{fq['id']}")
            elif fq["id"] == "residency":
                val = st.text_input("Residency states (comma-separated)", key=f"follow_residency_{fq['id']}")
            else:
                val = None

            col1, col2 = st.columns([1, 4])
            with col1:
                if st.button("Answer →", key=f"follow_{fq['id']}"):
                    if fq["id"] == "residency":
                        states = []
                        for part in str(val).split(","):
                            part = part.strip().upper()
                            if part and part in US_STATES and part not in states:
                                states.append(part)
                        st.session_state.answers["residency"] = tuple(states)
                    else:
                        st.session_state.answers[fq["id"]] = val
                    st.session_state.chat.append({"role": "assistant", "content": fq["text"]})
                    st.session_state.chat.append({"role": "user", "content": str(val)})
                    st.rerun()

    elif step == "results":
        answers = st.session_state.answers

        # Build profile from interview answers + sidebar criteria
        residency_states = answers.get("residency", ())
        home_state = answers.get("home_state", "MN")
        all_residency = tuple(sorted(set([home_state]) | set(residency_states)))

        profile = StudentProfile(
            gpa=float(answers.get("gpa", 3.8)),
            sat_total=int(answers["sat_score"]) if answers.get("test") == "SAT" and answers.get("sat_score") else None,
            act_total=int(answers["act_score"]) if answers.get("test") == "ACT" and answers.get("act_score") else None,
            home_state=home_state,
            residency_states=all_residency,
            high_school=str(answers.get("high_school", "")),
            w_cost=w_cost / 10.0,
            w_majors=w_majors / 10.0,
            w_rankings=w_rankings / 10.0,
            w_climate=w_climate / 10.0,
            w_safety=w_safety / 10.0,
            w_size=w_size / 10.0,
            w_location=w_location / 10.0,
            w_culture=w_culture / 10.0,
            max_annual_cost=float(answers.get("budget", 60000)) if answers.get("budget") else None,
            interested_cips=tuple(majors),
            preferred_regions=tuple(regions),
            preferred_locales=tuple(locales),
            preferred_states=tuple(s for s in str(answers.get("preferred_states", "")).split(",") if s.strip() in US_STATES),
            preferred_controls=tuple(controls),
            size_pref=size_pref.lower() if size_pref != "Any" else None,
        )

        # Apply in-state / out-of-state filter
        focus = answers.get("in_state_focus", "Both")
        if focus == "In-state only":
            df = df[df["state"] == home_state]
        elif focus == "Out-of-state only":
            df = df[df["state"] != home_state]

        # Map free-text majors to CIP codes
        cip_map = _majors_to_cip(answers.get("majors", ""))
        if cip_map:
            profile.interested_cips = tuple(cip_map)

        with st.spinner("Ranking colleges..."):
            ranked = triage(df, profile)
            final = final_counts(ranked, n_stretch, n_reach, n_safety)

        if final.empty:
            st.warning("No colleges matched. Try widening your preferences.")
            return

        actual_stretch = int((final["tier"] == "stretch").sum())
        actual_reach = int((final["tier"] == "reach").sum())
        actual_safety = int((final["tier"] == "safety").sum())
        st.success(
            f"Showing {len(final)} colleges: "
            f"{actual_stretch} stretch / {actual_reach} reach / {actual_safety} safety"
        )

        # ---- Export (Phase 3) ----
        # Compute effective cost and aid probability on the FULL row set first,
        # so the helpers can see control / tuition columns. Then select the
        # columns that go into the downloadable CSV.
        export_df = final.copy()
        export_df["effective_cost"] = export_df.apply(
            lambda r: _effective_cost(r, profile), axis=1
        )
        export_df["aid_prob"] = export_df.apply(
            lambda r: aid_estimate(r, profile)["prob"], axis=1
        )
        export_df = export_df[[
            "name", "tier", "acceptance_rate", "fit_score", "state",
            "cost_attendance", "rd_deadline", "early_deadline", "ea_ed_type",
            "effective_cost", "aid_prob",
        ]].copy()
        export_df = export_df.rename(columns={
            "name": "College", "tier": "Tier", "acceptance_rate": "Acceptance Rate",
            "fit_score": "Fit Score", "state": "State", "cost_attendance": "Sticker Cost",
            "effective_cost": "Estimated Cost", "aid_prob": "Aid Probability",
            "rd_deadline": "RD Deadline", "early_deadline": "Early Deadline",
            "ea_ed_type": "Early Type",
        })
        csv_buf = export_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Download list as CSV",
            data=csv_buf,
            file_name="college_list.csv",
            mime="text/csv",
            use_container_width=True,
        )

        st.divider()
        st.write("##### 💾 Save this profile for later")
        save_name = st.text_input("Profile name", placeholder="e.g. Alex - Class of 2026", key="save_name")
        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button("Save profile", use_container_width=True):
                if save_name and save_name.strip():
                    _save_profile(save_name.strip(), st.session_state.answers)
                    st.success(f"Saved '{save_name.strip()}'")
                else:
                    st.warning("Enter a profile name first.")
        with col2:
            if st.button("Clear all saved", use_container_width=True):
                _clear_saved_profiles()
                st.success("Cleared.")

        _render_results(final, profile, ranked)


def _majors_to_cip(free_text: str) -> list[str]:
    """Map free-text major names to CIP-2 codes using keyword matching."""
    if not free_text or len(free_text.strip()) < 2:
        return []
    text = free_text.lower()
    keywords = {
        "11": ["computer science", "cs ", "software", "it ", "information tech"],
        "14": ["engineering", "engineer"],
        "25": ["business", "finance", "marketing", "management", "economics"],
        "09": ["health", "nursing", "medicine", "pre-med"],
        "05": ["education", "teaching", "teacher"],
        "19": ["psychology", "psych"],
        "45": ["sociology", "political science", "poli sci", "government"],
        "27": ["math", "statistics", "data science"],
        "40": ["physics", "chemistry", "biology", "bio ", "science"],
        "24": ["english", "literature", "writing"],
        "50": ["art", "music", "theater", "dance", "film"],
        "26": ["history"],
        "15": ["philosophy", "religion"],
        "10": ["liberal arts"],
        "42": ["psychology"],
        "44": ["public admin", "public policy"],
        "51": ["business"],
    }
    matched = []
    for cip, kws in keywords.items():
        for kw in kws:
            if kw in text:
                if cip not in matched:
                    matched.append(cip)
                break
    return matched


def _render_results(
    final: pd.DataFrame,
    profile: StudentProfile,
    ranked: pd.DataFrame,
) -> None:
    """Three-column card layout: stretch / reach / safety."""
    # LLM-generated list summary (Phase 2).
    summary = generate_list_summary(final, {
        "gpa": profile.gpa,
        "sat_total": profile.sat_total,
        "act_total": profile.act_total,
        "home_state": profile.home_state,
        "max_annual_cost": profile.max_annual_cost,
        "interested_cips": profile.interested_cips,
    })
    if summary:
        st.markdown(f"#### 🤔 Counselor's summary")
        st.write(summary)
        st.divider()

    cols = st.columns(3)
    titles = ["🚀 Stretch", "✅ Reach", "🛡️ Safety"]
    keys = ["stretch", "reach", "safety"]

    for col, title, key in zip(cols, titles, keys):
        with col:
            st.subheader(title)
            subset = final[final["tier"] == key]
            if subset.empty:
                st.write("_None_")
                continue
            for _, row in subset.iterrows():
                _college_card(row, profile)


def _college_card(row: pd.Series, profile: StudentProfile) -> None:
    """Render one college as a card."""
    name = row.get("name", "?")
    ar = row.get("acceptance_rate")
    ar_str = f"{ar*100:.0f}%" if ar is not None and not pd.isna(ar) else "—"
    sat_bar = row.get("sat_bar")
    sat_str = f"~{sat_bar:.0f}" if sat_bar is not None and not pd.isna(sat_bar) else "—"

    # Effective cost: in-state vs out-of-state aware.
    effective = _effective_cost(row, profile)
    sticker = row.get("cost_attendance")
    cost_str = f"${effective:,.0f}" if not pd.isna(effective) else "—"
    if not pd.isna(sticker) and not pd.isna(effective) and abs(effective - sticker) > 1000:
        cost_str += f" (sticker ${sticker:,.0f})"

    # Aid probability estimate (Phase 1).
    aid = aid_estimate(row, profile)
    aid_str = ""
    if not pd.isna(aid["prob"]):
        aid_str = f"  |  💰 aid prob: {aid['prob']*100:.0f}% (~${aid['expected_aid']:,.0f} est.)"

    college_state = str(row.get("state", "")).strip().upper()
    state_tag = ""
    if profile.home_state and college_state == profile.home_state.strip().upper():
        state_tag = " 🏠 in-state"
    fit = row.get("fit_score", 0)

    with st.container():
        st.markdown(f"**{name}**{state_tag}")
        st.write(f"Acceptance: {ar_str}  |  SAT bar: {sat_str}")
        st.write(f"Cost: {cost_str}{aid_str}  |  Fit: {fit:.2f}")

        # Application deadlines + EA/ED (Phase 1).
        rd = row.get("rd_deadline")
        ed = row.get("early_deadline")
        et = row.get("ea_ed_type")
        if pd.notna(rd) or pd.notna(ed) or pd.notna(et):
            parts = []
            if pd.notna(rd) and str(rd) != "nan":
                parts.append(f"RD: {rd}")
            if pd.notna(ed) and str(ed) != "nan":
                ea_label = ""
                if pd.notna(et) and str(et) != "none" and str(et) != "nan":
                    ea_label = f" ({et})"
                parts.append(f"Early: {ed}{ea_label}")
            elif pd.notna(et) and str(et) != "none" and str(et) != "nan":
                parts.append(f"Early: {et}")
            if parts:
                st.write("📅 " + "  |  ".join(parts))
            elif pd.notna(et) and str(et) == "none":
                st.write("📅 No early option")

        # GPA/SAT bar vs student.
        sat = profile.sat_total if profile.sat_total else profile.act_total
        if sat and not pd.isna(sat_bar):
            student_sat = float(sat)
            pct = (student_sat / sat_bar * 100) if sat_bar else 100
            st.progress(min(1.0, max(0.0, pct / 100)), text=f"Your SAT {student_sat:.0f} vs bar {sat_bar:.0f}")

        city = row.get("city")
        state = row.get("state")
        if city and state:
            st.caption(f"{city}, {state}")

        # LLM-generated rationale (Phase 2).
        rationale = generate_rationale({
            "name": name,
            "acceptance_rate": float(ar) if ar is not None and not pd.isna(ar) else None,
            "sat_bar": float(sat_bar) if sat_bar is not None and not pd.isna(sat_bar) else None,
            "effective_cost": float(effective) if not pd.isna(effective) else None,
            "tier": row.get("tier", ""),
            "fit_score": float(fit),
            "rd_deadline": str(rd) if pd.notna(rd) else None,
            "early_deadline": str(ed) if pd.notna(ed) else None,
            "ea_ed_type": str(et) if pd.notna(et) else None,
        }, {
            "gpa": profile.gpa,
            "sat_total": profile.sat_total,
            "act_total": profile.act_total,
            "home_state": profile.home_state,
            "max_annual_cost": profile.max_annual_cost,
            "interested_cips": profile.interested_cips,
        })
        if rationale:
            st.caption(f"💡 {rationale}")

        # Links
        url = row.get("url")
        if url and not pd.isna(url):
            st.markdown(f"[Admissions page]({url})", unsafe_allow_html=True)

        st.divider()


if __name__ == "__main__":
    main()