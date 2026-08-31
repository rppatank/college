"""scoring.py — Reach/safety classification and criteria-fit score.

Classification compares the student's academic profile against each college's
middle 50% (25th–75th percentile) of first-year GPA and SAT/ACT.

Tiers:
  Safety  — student GPA >= 75th AND SAT >= 75th (or comfortably above median),
            AND acceptance rate >= ~40%
  Reach   — student GPA < 25th OR SAT < 25th, AND acceptance rate <= ~25%
  Match   — in the middle 50% on both, or borderline selectivity

Rules:
  - SAT absent -> use the college's SAT average / middle-50 as the bar.
  - GPA required -> the app blocks generation without it.
  - Criteria-fit score modulates the final list ordering.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------- thresholds

SAFETY_AR_MIN = 0.40     # acceptance rate floor for "safety"
REACH_AR_MAX = 0.25     # acceptance rate ceiling for "reach"


# State-level tuition reciprocity agreements (neighbouring-state discounts).
# Sources: NCES / state higher-ed compacts. Best-effort; not exhaustive.
RECIPROCITY_MAP = {
    "MN": {"WI", "ND", "SD", "MT", "IA", "IL", "IN", "MI", "OH", "PA"},
    "WI": {"MN", "MI", "IL", "IA", "IN", "OH", "PA"},
    "ND": {"MN", "SD", "MT"},
    "SD": {"MN", "ND", "MT", "WY", "NE", "IA"},
    "MI": {"OH", "IN", "IL", "WI", "MN"},
    "IA": {"MN", "WI", "IL", "MO", "NE", "SD"},
    "IL": {"WI", "IA", "IN", "MI", "MO", "KY"},
    "IN": {"IL", "MI", "OH", "KY", "WI"},
    "OH": {"MI", "IN", "KY", "WV", "PA"},
    "NY": {"VT", "MA", "CT", "RI", "NJ", "PA"},
    "VT": {"NY", "NH", "MA", "CT"},
    "MA": {"NY", "VT", "NH", "RI", "CT"},
    "CT": {"NY", "RI", "MA"},
    "NJ": {"NY", "PA", "DE"},
    "PA": {"NY", "NJ", "OH", "WV", "MD", "DE"},
    "CA": {"OR", "NV", "AZ", "HI"},
    "TX": {"OK", "LA", "AR", "NM"},
    "FL": {"GA", "AL"},
    "GA": {"FL", "AL", "TN", "NC", "SC"},
    "NC": {"SC", "GA", "TN", "VA"},
    "VA": {"NC", "TN", "KY", "WV", "MD", "DC"},
}


@dataclass
class StudentProfile:
    gpa: float                      # required, 0-4.0 (or unweighted equivalent)
    sat_total: Optional[int] = None # 400-1600, or None
    act_total: Optional[int] = None # 32, or None
    home_state: Optional[str] = None  # 2-letter state code, e.g. "MN"
    residency_states: tuple = ()     # additional states with legal residency (grandparent, parent, etc.)
    high_school: str = ""            # free-text high school name

    # Weighted criteria importance, each 0-1. Sum need not be 1.
    w_cost: float = 1.0
    w_majors: float = 1.0
    w_rankings: float = 0.8
    w_climate: float = 0.7
    w_safety: float = 0.9
    w_size: float = 0.5
    w_location: float = 0.7
    w_culture: float = 0.6

    # Preferences
    max_annual_cost: Optional[float] = None   # before aid
    interested_cips: tuple = ()                # CIP-2 codes, e.g. ("11", "14")
    preferred_regions: tuple = ()
    preferred_locales: tuple = ()
    preferred_states: tuple = ()               # 2-letter state codes
    preferred_controls: tuple = ()
    size_pref: Optional[str] = None            # "small" | "medium" | "large"


# ---------------------------------------------------------------- helpers

def _sat_from_act(act: float) -> float:
    """Rough concordance: ACT composite -> SAT total (2018 concordance)."""
    if act is None or pd.isna(act):
        return float("nan")
    return float(act) * 50.0  # coarse but adequate for tiering


def student_sat(profile: StudentProfile) -> float:
    """Return a single SAT-equivalent score, or NaN if neither test is present."""
    if profile.sat_total is not None:
        return float(profile.sat_total)
    if profile.act_total is not None:
        return _sat_from_act(profile.act_total)
    return float("nan")


def college_sat_bar(college: pd.Series) -> float:
    """Middle of the college's SAT middle-50 (average of 25th and 75th).

    Uses the best available SAT section. Falls back to SAT_AVG.
    Returns NaN if no SAT data at all.
    """
    vr_25 = college.get("sat_vr_25")
    vr_75 = college.get("sat_vr_75")
    mt_25 = college.get("sat_mt_25")
    mt_75 = college.get("sat_mt_75")
    wr_25 = college.get("sat_wr_25")
    wr_75 = college.get("sat_wr_75")
    sat_avg = college.get("sat_avg")

    pairs = []
    for lo, hi in ((vr_25, vr_75), (mt_25, mt_75), (wr_25, wr_75)):
        lo_f = _to_float(lo)
        hi_f = _to_float(hi)
        if not pd.isna(lo_f) and not pd.isna(hi_f):
            pairs.append((lo_f + hi_f) / 2.0)
    if pairs:
        return sum(pairs) / len(pairs)
    sat_avg_f = _to_float(sat_avg)
    if not pd.isna(sat_avg_f):
        return sat_avg_f
    return float("nan")


def college_sat_percentile(college: pd.Series, q: float) -> float:
    """Estimate a SAT total at the q-th percentile (0-1) from section 25/75.

    Uses the SAT middle-50 as a rough proxy: 25th ~= 25th pct, 75th ~= 75th pct.
    Linear interpolation between them.
    """
    vr_25 = college.get("sat_vr_25")
    vr_75 = college.get("sat_vr_75")
    mt_25 = college.get("sat_mt_25")
    mt_75 = college.get("sat_mt_75")
    wr_25 = college.get("sat_wr_25")
    wr_75 = college.get("sat_wr_75")

    lo_list, hi_list = [], []
    for a, b in ((vr_25, vr_75), (mt_25, mt_75), (wr_25, wr_75)):
        a_f = _to_float(a)
        b_f = _to_float(b)
        if not pd.isna(a_f) and not pd.isna(b_f):
            lo_list.append(a_f)
            hi_list.append(b_f)
    if not lo_list:
        return float("nan")

    lo = sum(lo_list) / len(lo_list)
    hi = sum(hi_list) / len(hi_list)
    if hi == lo:
        return lo
    # Map q (0-1) onto the [25th, 75th] span.
    span = hi - lo
    return lo + span * (q - 0.25) / 0.50


# ---------------------------------------------------------------- classification

def classify_tier(
    student_gpa: float,
    student_sat: float,
    college: pd.Series,
) -> str:
    """Return 'stretch' | 'reach' | 'safety' | 'match'.

    Classifies by combining the student's SAT position relative to the
    college's middle 50% with the college's acceptance rate.

    SAT absent -> use acceptance rate as the primary signal.
    """
    ar = _to_float(college.get("acceptance_rate"))
    if pd.isna(ar):
        return "match"  # unknown selectivity -> can't classify

    sat_25 = college_sat_percentile(college, 0.25)
    sat_75 = college_sat_percentile(college, 0.75)

    sat_absent = pd.isna(student_sat)

    if sat_absent:
        # No SAT: tier driven by selectivity alone.
        if ar >= 0.45:
            return "safety"
        if ar <= 0.18:
            return "stretch"
        if ar <= 0.32:
            return "reach"
        return "match"

    sat = float(student_sat)

    # SAT position relative to the college's middle 50%.
    has_sat_data = (not pd.isna(sat_25)) and (not pd.isna(sat_75))
    if has_sat_data:
        if sat >= sat_75:
            sat_pos = "above"   # at or above 75th percentile
        elif sat >= sat_25:
            sat_pos = "middle"  # inside the middle 50%
        else:
            sat_pos = "below"   # below 25th percentile
    else:
        sat_pos = "middle"  # no SAT data -> assume middle

    # GPA modifier: a very strong student shifts one tier toward safety;
    # a weaker student shifts one tier toward stretch.
    shift = 0
    if student_gpa >= 3.7:
        shift = 1
    elif student_gpa <= 3.2:
        shift = -1

    # SAT position x selectivity matrix.
    if sat_pos == "above":
        if ar >= 0.40:
            tier = "safety"
        elif ar >= 0.25:
            tier = "safety" if ar >= 0.35 else "match"
        elif ar <= 0.10:
            # Extremely selective (<=10% AR): a dream school for any student,
            # even one above the 75th percentile. Classify as stretch so the
            # student sees these as aspirational targets, not likely admits.
            tier = "stretch"
        else:
            tier = "reach"
    elif sat_pos == "middle":
        if ar <= 0.22:
            tier = "stretch"
        elif ar <= 0.35:
            tier = "reach"
        else:
            tier = "match"
    else:  # below
        if ar <= 0.25:
            tier = "stretch"
        elif ar <= 0.45:
            tier = "reach"
        else:
            tier = "match"

    # Apply GPA shift only to borderline "match" cases when SAT data is
    # present. Stretch/reach/safety are already set by SAT position +
    # selectivity, which are stronger signals than GPA alone.
    if has_sat_data and tier == "match":
        order = ["stretch", "reach", "match", "safety"]
        idx = order.index(tier) + shift
        return order[min(3, max(0, idx))]
    return tier


# ---------------------------------------------------------------- fit score

def _effective_cost(college: pd.Series, profile: StudentProfile) -> float:
    """Return the annual cost the student would actually pay.

    Public universities charge different tuition for in-state vs out-of-state.
    We use tuition_in_state when the student has residency in the college's
    state (home state, additional residency states, or a reciprocity agreement)
    and tuition_out_state otherwise. Private universities charge the same rate
    regardless. Falls back to cost_attendance when tuition columns are missing.
    """
    control = _to_float(college.get("control"))
    is_public = not pd.isna(control) and control == 1.0
    college_state = str(college.get("state", "")).strip().upper()

    if is_public and profile.home_state:
        home = profile.home_state.strip().upper()
        # 1. Direct residency: home state or additional residency states.
        residency = {home} | {s.strip().upper() for s in profile.residency_states}
        in_state = college_state in residency
        # 2. Reciprocity: neighbouring-state agreements.
        if not in_state and home in RECIPROCITY_MAP:
            in_state = college_state in RECIPROCITY_MAP[home]

        tu = _to_float(college.get("tuition_in_state")) if in_state else _to_float(
            college.get("tuition_out_state")
        )
        if not pd.isna(tu):
            # Tuition + fees only; add ~15k living costs for a total estimate.
            return tu + 15000.0

    # Private school or no home state: use the published cost of attendance.
    cost = _to_float(college.get("cost_attendance"))
    if not pd.isna(cost):
        return cost
    tu = _to_float(college.get("tuition_out_state"))
    if not pd.isna(tu):
        return tu + 15000.0
    return float("nan")


def aid_estimate(college: pd.Series, profile: StudentProfile) -> dict:
    """Estimate the probability of receiving academic/need-based aid and the
    expected aid amount, for display in the results card.

    The Scorecard data we store does not include scholarship/aid columns, and
    live CDS fetching has near-zero coverage (modern pages are JS-rendered).
    This is a transparent heuristic based on control type and selectivity —
    real aid outcomes vary widely by individual circumstance.

    Returns {prob, expected_aid, net_cost, note}.
    """
    control = _to_float(college.get("control"))
    is_private = not pd.isna(control) and control == 2.0
    ar = _to_float(college.get("acceptance_rate"))
    cost = _effective_cost(college, profile)

    if pd.isna(cost):
        return {"prob": float("nan"), "expected_aid": float("nan"),
                "net_cost": float("nan"), "note": "unknown"}

    # Detect tuition-free schools (e.g. Curtis Institute of Music, which is
    # tuition-free for all students). When tuition is $0 the "aid" is the
    # waived tuition itself, so the probability is effectively 100%.
    tuition = _to_float(college.get("tuition_out_state"))
    tuition_in = _to_float(college.get("tuition_in_state"))
    is_tuition_free = (
        (not pd.isna(tuition) and tuition <= 0)
        or (not pd.isna(tuition_in) and tuition_in <= 0)
    )

    # Base probability of receiving any grant aid.
    if is_tuition_free:
        prob = 0.95   # tuition already free -> aid prob ~100%
    elif is_private:
        prob = 0.60   # privates are more likely to give grant aid
    else:
        prob = 0.30   # publics rely more on in-state tuition discount

    # More selective colleges tend to be more generous.
    if not pd.isna(ar):
        if ar < 0.20:
            prob += 0.10
        elif ar < 0.40:
            prob += 0.05

    prob = min(0.95, max(0.05, prob))

    # Expected aid as a share of the effective cost.
    if is_tuition_free:
        # Tuition is already $0; aid here covers living expenses only.
        aid_pct = 0.30
    elif is_private:
        aid_pct = 0.45
    else:
        aid_pct = 0.25

    expected_aid = cost * aid_pct * prob
    net_cost = cost - expected_aid

    return {
        "prob": prob,
        "expected_aid": expected_aid,
        "net_cost": net_cost,
        "note": "estimated",
    }


def _norm(v: float, lo: float, hi: float) -> float:
    """Normalize v into [0,1] against [lo, hi]; clamp."""
    if hi == lo:
        return 0.5
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


def _to_float(v) -> float:
    """Safely convert a value to float; non-numeric strings (e.g. 'PS', 'N') -> NaN."""
    if v is None:
        return float("nan")
    try:
        f = pd.to_numeric(v, errors="coerce")
        if pd.isna(f):
            return float("nan")
        return float(f)
    except (ValueError, TypeError):
        return float("nan")


def criteria_fit(college: pd.Series, profile: StudentProfile) -> float:
    """Return a 0-1 weighted fit score for one college vs the student's criteria."""
    score = 0.0
    total = 0.0

    # --- cost (in-state vs out-of-state aware) ---
    if profile.max_annual_cost is not None:
        w = profile.w_cost
        total += w
        cost = _effective_cost(college, profile)
        if not pd.isna(cost):
            # Lower cost -> higher fit; allow up to 2x budget before zeroing.
            score += w * _norm(profile.max_annual_cost - cost, -profile.max_annual_cost, profile.max_annual_cost)
        else:
            score += w * 0.3  # unknown cost, mild penalty

    # --- majors ---
    if profile.interested_cips:
        w = profile.w_majors
        total += w
        overlap = 0.0
        for cip in profile.interested_cips:
            col = _to_float(college.get(f"pcip_{cip}"))
            if not pd.isna(col) and col > 0:
                overlap += col
        # Scale major share into [0,1]. Factor 2.0 (not 5.0) so that colleges
        # with a genuine majority of degrees in the student's fields — e.g. MIT
        # at 62% CS+Eng — outrank those with a token presence, which previously
        # all scored a flat 1.0 and made the major filter non-discriminating.
        score += w * min(1.0, overlap * 2.0)

    # --- rankings (proxy: selectivity + completion) ---
    w = profile.w_rankings
    total += w
    ar = _to_float(college.get("acceptance_rate"))
    comp = _to_float(college.get("completion_rate"))
    # Selectivity: LOWER acceptance rate = MORE selective = BETTER ranking.
    # Invert so 0% AR -> 1.0 (best) and 100% AR -> 0.0 (worst).
    sel = _norm(1.0 - (ar if not pd.isna(ar) else 0.5), 0.0, 1.0)
    comp_n = _norm(comp if not pd.isna(comp) else 0.5, 0.0, 1.0)
    score += w * 0.5 * (sel + comp_n)

    # --- climate / region ---
    if profile.preferred_regions:
        w = profile.w_climate
        total += w
        reg = college.get("region")
        score += w * (1.0 if reg in profile.preferred_regions else 0.0)

    # --- safety (locale + size prior) ---
    w = profile.w_safety
    total += w
    loc = college.get("locale")
    if profile.preferred_locales:
        score += w * 0.7 * (1.0 if loc in profile.preferred_locales else 0.0)
    else:
        score += w * 0.5
    # Urban/suburban campuses are a mild safety prior.
    if loc in (11, 12, 13, 21, 22, 23):
        score += w * 0.3

    # --- size ---
    w = profile.w_size
    total += w
    ugds = _to_float(college.get("ugds"))
    if not pd.isna(ugds):
        u = ugds
        if profile.size_pref == "small":
            score += w * _norm(3000 - u, 0, 3000)
        elif profile.size_pref == "large":
            score += w * _norm(u - 3000, 0, 15000)
        else:
            score += w * 0.5
    else:
        score += w * 0.4

    # --- location (state + region) ---
    w = profile.w_location
    total += w
    loc_score = 0.0
    college_state = str(college.get("state", "")).strip().upper()
    if profile.preferred_states:
        if college_state in profile.preferred_states:
            loc_score += 0.7
        elif profile.home_state and college_state == profile.home_state.strip().upper():
            loc_score += 0.5  # home state is a mild fallback
    if profile.preferred_regions:
        loc_score += 0.3 * (1.0 if college.get("region") in profile.preferred_regions else 0.0)
    else:
        loc_score += 0.3 * 0.5  # no region pref -> neutral
    score += w * min(1.0, loc_score)

    # --- culture (control) ---
    w = profile.w_culture
    total += w
    if profile.preferred_controls:
        score += w * (1.0 if college.get("control") in profile.preferred_controls else 0.0)
    else:
        score += w * 0.5

    # --- residency bonus (in-state public universities are a strong fit) ---
    if profile.home_state:
        residency = {profile.home_state.strip().upper()} | \
                     {s.strip().upper() for s in profile.residency_states}
        if college_state in residency:
            control = _to_float(college.get("control"))
            is_public = not pd.isna(control) and control == 1.0
            if is_public:
                w = profile.w_cost
                total += w
                score += w * 0.8  # in-state public = significant cost advantage

    if total == 0:
        return 0.5
    return score / total


# ---------------------------------------------------------------- batch API

def triage(
    df: pd.DataFrame,
    profile: StudentProfile,
    n_stretch: int = 2,
    n_reach: int = 5,
    n_safety: int = 3,
) -> pd.DataFrame:
    """Classify every row in df and return it with tier + fit_score columns.

    The returned DataFrame has extra columns: 'tier', 'fit_score', 'sat_bar'.

    When the student has selected specific majors (interested_cips), colleges
    that offer NONE of those majors are excluded entirely — major fit is an
    exclusionary filter, not just a scoring factor.
    """
    out = df.copy()

    # --- Exclusionary major filter ---
    if profile.interested_cips:
        def _has_major(row: pd.Series) -> bool:
            for cip in profile.interested_cips:
                col = _to_float(row.get(f"pcip_{cip}"))
                if not pd.isna(col) and col > 0:
                    return True
            return False
        mask = out.apply(_has_major, axis=1)
        excluded = (~mask).sum()
        out = out[mask].copy()
        if excluded:
            print(f"[triage] excluded {excluded} colleges with no overlap for majors {profile.interested_cips}")

    sat = student_sat(profile)

    tiers = []
    fit_scores = []
    sat_bars = []
    for _, row in out.iterrows():
        tiers.append(classify_tier(profile.gpa, sat, row))
        fit_scores.append(criteria_fit(row, profile))
        sat_bars.append(college_sat_bar(row))

    out["tier"] = tiers
    out["fit_score"] = fit_scores
    out["sat_bar"] = sat_bars

    # Sort within each tier by fit score (best fit first).
    tier_order = {"stretch": 0, "reach": 1, "match": 2, "safety": 3}
    out["_tier_rank"] = out["tier"].map(tier_order).fillna(4)
    out = out.sort_values(["_tier_rank", "fit_score"], ascending=[True, False])
    out = out.drop(columns=["_tier_rank"])

    return out


def final_counts(
    df: pd.DataFrame,
    n_stretch: int = 2,
    n_reach: int = 5,
    n_safety: int = 3,
) -> pd.DataFrame:
    """Pick the final list: top N of each tier by fit_score.

    'stretch' tier gets the first n_stretch, 'reach' the next n_reach,
    'safety' the next n_safety. Remaining rows are dropped.
    """
    keep = []
    counts = {"stretch": n_stretch, "reach": n_reach, "safety": n_safety}
    for tier, n in counts.items():
        rows = df[df["tier"] == tier].head(n)
        keep.append(rows)
    if not keep:
        return df.iloc[0:0]
    result = pd.concat(keep)
    return result.sort_values(
        by=["fit_score"], ascending=False
    )