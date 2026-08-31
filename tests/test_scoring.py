"""tests/test_scoring.py — Unit tests for scoring.py."""

import pandas as pd
import pytest

from scoring import (
    StudentProfile,
    classify_tier,
    college_sat_bar,
    student_sat,
    SAFETY_AR_MIN,
    REACH_AR_MAX,
    _effective_cost,
    criteria_fit,
)


def _row(**kw):
    base = dict(
        acceptance_rate=0.5,
        sat_vr_25=1200, sat_vr_75=1400,
        sat_mt_25=1200, sat_mt_75=1400,
        sat_wr_25=1200, sat_wr_75=1400,
        sat_avg=1300,
        cost_attendance=60000,
        region=2, locale=22, control=1, ugds=10000,
    )
    base.update(kw)
    return pd.Series(base)


def test_student_sat_from_sat():
    p = StudentProfile(gpa=3.8, sat_total=1450)
    assert student_sat(p) == 1450


def test_student_sat_from_act():
    p = StudentProfile(gpa=3.8, act_total=32)
    assert student_sat(p) == 1600.0


def test_student_sat_absent():
    p = StudentProfile(gpa=3.8)
    assert pd.isna(student_sat(p))


def test_college_sat_bar_uses_sections():
    row = _row()
    bar = college_sat_bar(row)
    assert bar == 1300.0  # midpoint of 1200/1400


def test_college_sat_bar_falls_back_to_avg():
    row = _row(sat_vr_25=None, sat_vr_75=None, sat_mt_25=None, sat_mt_75=None,
               sat_wr_25=None, sat_wr_75=None, sat_avg=1250)
    assert college_sat_bar(row) == 1250.0


def test_safety_tier():
    # GPA >= 75th, SAT >= 75th, acceptance rate high.
    p = StudentProfile(gpa=4.0, sat_total=1550)
    row = _row(acceptance_rate=0.7, sat_vr_25=1200, sat_vr_75=1400)
    assert classify_tier(p.gpa, student_sat(p), row) == "safety"


def test_stretch_tier():
    # GPA < 25th proxy, SAT < 25th, acceptance rate low.
    p = StudentProfile(gpa=3.0, sat_total=1000)
    row = _row(acceptance_rate=0.1, sat_vr_25=1300, sat_vr_75=1500)
    assert classify_tier(p.gpa, student_sat(p), row) == "stretch"


def test_reach_tier():
    p = StudentProfile(gpa=3.5, sat_total=1250)
    row = _row(acceptance_rate=0.3, sat_vr_25=1200, sat_vr_75=1400)
    # SAT 1250 in middle 50%, AR 0.30 <= 0.35 -> reach.
    assert classify_tier(p.gpa, student_sat(p), row) == "reach"


def test_stretch_middle_high_selectivity():
    # Student in middle 50% but school is very selective -> stretch.
    p = StudentProfile(gpa=3.8, sat_total=1300)
    row = _row(acceptance_rate=0.10, sat_vr_25=1200, sat_vr_75=1400)
    assert classify_tier(p.gpa, student_sat(p), row) == "stretch"


def test_reach_below_moderate_selectivity():
    # Student below 25th, school moderately selective -> reach (not stretch).
    p = StudentProfile(gpa=3.5, sat_total=1100)
    row = _row(acceptance_rate=0.35, sat_vr_25=1200, sat_vr_75=1400)
    assert classify_tier(p.gpa, student_sat(p), row) == "reach"


def test_gpa_shift_strong_student():
    # Strong GPA shifts a "match" toward "safety".
    p = StudentProfile(gpa=3.9, sat_total=1300)
    row = _row(acceptance_rate=0.50, sat_vr_25=1200, sat_vr_75=1400)
    # SAT in middle 50%, AR 0.50 -> match, but GPA 3.9 shifts to safety.
    assert classify_tier(p.gpa, student_sat(p), row) == "safety"


def test_gpa_shift_weak_student():
    # Weak GPA shifts a "match" toward "reach".
    p = StudentProfile(gpa=3.0, sat_total=1300)
    row = _row(acceptance_rate=0.50, sat_vr_25=1200, sat_vr_75=1400)
    # SAT in middle 50%, AR 0.50 -> match, but GPA 3.0 shifts to reach.
    assert classify_tier(p.gpa, student_sat(p), row) == "reach"


def test_sat_absent_uses_bar():
    # SAT absent, high acceptance rate -> safety.
    p = StudentProfile(gpa=3.9)
    row = _row(acceptance_rate=0.6)
    assert classify_tier(p.gpa, float("nan"), row) == "safety"


def test_sat_absent_reach():
    # SAT absent, low acceptance rate -> stretch.
    p = StudentProfile(gpa=3.9)
    row = _row(acceptance_rate=0.1)
    assert classify_tier(p.gpa, float("nan"), row) == "stretch"


def test_gpa_required_blocks_generation():
    # An assertion in app.py handles this, but scoring should still return a tier.
    p = StudentProfile(gpa=0)
    row = _row()
    tier = classify_tier(p.gpa, student_sat(p), row)
    assert tier in ("stretch", "reach", "safety", "match")


# ---------------------------------------------------------------- in-state tuition

def test_effective_cost_in_state_public():
    # In-state public: tuition_in_state + living allowance.
    p = StudentProfile(gpa=3.8, home_state="MN")
    row = _row(control=1, state="MN", tuition_in_state=10000, tuition_out_state=18000,
               cost_attendance=25000)
    ec = _effective_cost(row, p)
    assert ec == 25000.0  # 10000 + 15000 living


def test_effective_cost_out_of_state_public():
    # Out-of-state public: tuition_out_state + living allowance.
    p = StudentProfile(gpa=3.8, home_state="MN")
    row = _row(control=1, state="CA", tuition_in_state=10000, tuition_out_state=18000,
               cost_attendance=25000)
    ec = _effective_cost(row, p)
    assert ec == 33000.0  # 18000 + 15000 living


def test_effective_cost_reciprocity():
    # MN-WI reciprocity: MN student at WI public pays in-state rate.
    p = StudentProfile(gpa=3.8, home_state="MN")
    row = _row(control=1, state="WI", tuition_in_state=9000, tuition_out_state=16000,
               cost_attendance=24000)
    ec = _effective_cost(row, p)
    assert ec == 24000.0  # 9000 + 15000 (reciprocity applies)


def test_effective_cost_private():
    # Private: same rate regardless of state.
    p = StudentProfile(gpa=3.8, home_state="MN")
    row = _row(control=2, state="CA", tuition_in_state=10000, tuition_out_state=18000,
               cost_attendance=55000)
    ec = _effective_cost(row, p)
    assert ec == 55000.0  # uses cost_attendance


def test_effective_cost_no_home_state():
    # No home state -> use published cost_attendance.
    p = StudentProfile(gpa=3.8)
    row = _row(control=1, state="MN", tuition_in_state=10000, tuition_out_state=18000,
               cost_attendance=25000)
    ec = _effective_cost(row, p)
    assert ec == 25000.0


def test_criteria_fit_in_state_cheaper():
    # Same college, in-state vs out-of-state: in-state should score higher on cost.
    row = _row(control=1, state="MN", tuition_in_state=10000, tuition_out_state=18000,
               cost_attendance=25000)
    p_in = StudentProfile(gpa=3.8, home_state="MN", max_annual_cost=20000, w_cost=1.0)
    p_out = StudentProfile(gpa=3.8, home_state="CA", max_annual_cost=20000, w_cost=1.0)
    fit_in = criteria_fit(row, p_in)
    fit_out = criteria_fit(row, p_out)
    assert fit_in > fit_out


# ---------------------------------------------------------------- new behaviour


def test_stretch_above_extremely_selective():
    # Student SAT is above the 75th percentile, but the college's acceptance
    # rate is <= 10% — a dream school for anyone. Should classify as "stretch"
    # (not "reach"), so high-achieving students see aspirational targets.
    p = StudentProfile(gpa=4.0, sat_total=1600)
    row = _row(acceptance_rate=0.05, sat_vr_25=1200, sat_vr_75=1400)
    assert classify_tier(p.gpa, student_sat(p), row) == "stretch"


def test_reach_above_moderate_selectivity():
    # Student SAT above 75th, AR between 10% and 25% -> "reach" (not stretch).
    p = StudentProfile(gpa=4.0, sat_total=1600)
    row = _row(acceptance_rate=0.15, sat_vr_25=1200, sat_vr_75=1400)
    assert classify_tier(p.gpa, student_sat(p), row) == "reach"


def test_criteria_fit_selectivity_inverted():
    # More selective (lower AR) college should score HIGHER on the rankings
    # component than a less selective one, all else equal.
    selective = _row(acceptance_rate=0.05, cost_attendance=50000)
    less_selective = _row(acceptance_rate=0.50, cost_attendance=50000)
    p = StudentProfile(gpa=3.8, max_annual_cost=60000)
    assert criteria_fit(selective, p) > criteria_fit(less_selective, p)


def test_major_fit_scaling_discriminates():
    # The major-fit scaling (factor 2.0, not 5.0) should differentiate between
    # colleges with different major shares instead of giving all a flat 1.0.
    high = _row(acceptance_rate=0.5, pcip_11=0.5, pcip_14=0.5)
    low = _row(acceptance_rate=0.5, pcip_11=0.05, pcip_14=0.0)
    p = StudentProfile(gpa=3.8, max_annual_cost=60000,
                       interested_cips=("11", "14"))
    assert criteria_fit(high, p) > criteria_fit(low, p)