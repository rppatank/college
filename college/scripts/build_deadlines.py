#!/usr/bin/env python
"""scripts/build_deadlines.py — Generate data/college_deadlines.csv with
curated application deadlines and EA/ED info for common colleges.

Deadlines are for the 2025–2026 application cycle. Sources: college admissions
websites and the Common Data Set. This is a manually-curated core set — it
covers the colleges most likely to appear on a student's shortlist. The
fetchers.py module supplements this with live fetching where possible.

Run:  python scripts/build_deadlines.py
"""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "college_deadlines.csv"

# unitid: (name, regular_deadline, early_deadline, ea_ed_type, notes)
# unitids verified against data/colleges.db (exact name match).
# ea_ed_type: "EA" | "ED" | "REA" | "SCEA" | "both" | "none"
# Dates are "MM-DD" format; "rolling" for rolling admissions.
DEADLINES: dict[int, tuple[str, str, str, str, str]] = {
    # --- Ivy League ---
    130794: ("Yale", "01-02", "11-01", "ED", "Single-choice ED"),
    166027: ("Harvard", "01-01", "11-01", "REA", "Restrictive Early Action"),
    110404: ("Caltech", "01-03", "11-01", "REA", "Restrictive Early Action"),
    186131: ("Princeton", "01-01", "11-01", "SCEA", "Single-choice Early Action"),
    190150: ("Columbia", "01-01", "11-01", "ED", "Early Decision"),
    166683: ("MIT", "01-06", "11-01", "EA", "Early Action"),
    217156: ("Brown", "01-03", "11-01", "ED", "Early Decision"),
    215062: ("UPenn", "01-05", "11-01", "ED", "Early Decision"),
    182670: ("Dartmouth", "01-02", "11-01", "ED", "Early Decision"),
    190415: ("Cornell", "01-02", "11-01", "ED", "Early Decision"),

    # --- Top National ---
    243744: ("Stanford", "01-05", "11-01", "REA", "Restrictive Early Action"),
    144050: ("UChicago", "01-02", "11-01", "EA", "Early Action (non-binding)"),
    147767: ("Northwestern", "01-03", "11-01", "ED", "Early Decision"),
    198419: ("Duke", "01-02", "11-01", "ED", "Early Decision"),
    221999: ("Vanderbilt", "01-01", "11-01", "ED", "Early Decision (binding)"),
    162928: ("Johns Hopkins", "01-02", "11-01", "ED", "Early Decision"),
    121345: ("Pomona", "01-08", "11-01", "ED", "Early Decision"),
    161086: ("Colby", "01-01", "11-01", "ED", "Early Decision"),
    161004: ("Bowdoin", "01-05", "11-01", "ED", "Early Decision"),
    216287: ("Swarthmore", "01-04", "11-01", "ED", "Early Decision"),
    211893: ("Curtis Institute", "12-15", "12-01", "none", "Rolling after Dec 1"),

    # --- UC System (all same deadlines) ---
    110635: ("UC Berkeley", "11-30", "none", "none", "UC application: Nov 1–30; no EA/ED"),
    110662: ("UCLA", "11-30", "none", "none", "UC application: Nov 1–30; no EA/ED"),
    110680: ("UC San Diego", "11-30", "none", "none", "UC application: Nov 1–30"),
    110644: ("UC Davis", "11-30", "none", "none", "UC application: Nov 1–30"),
    110653: ("UC Irvine", "11-30", "none", "none", "UC application: Nov 1–30"),
    110705: ("UC Santa Barbara", "11-30", "none", "none", "UC application: Nov 1–30"),
    110714: ("UC Santa Cruz", "11-30", "none", "none", "UC application: Nov 1–30"),
    110671: ("UC Riverside", "11-30", "none", "none", "UC application: Nov 1–30"),
    445188: ("UC Merced", "11-30", "none", "none", "UC application: Nov 1–30"),

    # --- Top Public ---
    170976: ("Michigan", "02-01", "11-01", "EA", "Early Action (optional)"),
    234076: ("Virginia", "01-05", "11-01", "EA", "Early Action (optional)"),
    199120: ("UNC Chapel Hill", "01-15", "10-15", "EA", "Early Action (optional)"),
    139755: ("Georgia Tech", "01-06", "10-15", "EA", "Early Action"),
    145637: ("UIUC", "01-05", "11-01", "EA", "Early Action"),
    240444: ("Wisconsin", "01-15", "11-01", "EA", "Early Action (optional)"),
    174066: ("Minnesota", "01-01", "11-01", "EA", "Early Action"),
    228778: ("Texas", "12-01", "10-15", "EA", "Early Action"),
    204796: ("Ohio State", "02-01", "11-01", "EA", "Early Action"),
    134130: ("Florida", "11-01", "none", "none", "Priority: Nov 1"),
    236948: ("Washington", "11-15", "none", "none", "Autumn quarter; no EA/ED"),

    # --- Common State Flagships ---
    214777: ("Penn State", "12-01", "11-01", "EA", "Early Action (optional)"),
    243780: ("Purdue", "01-15", "11-01", "EA", "Early Action"),
    186380: ("Rutgers", "12-01", "11-01", "EA", "Early Action"),
    163286: ("Maryland", "01-20", "11-01", "EA", "Early Action"),
    100858: ("Auburn", "02-01", "12-01", "EA", "Early Action"),
    217882: ("Clemson", "05-01", "12-01", "EA", "Early Action"),
    153658: ("Iowa", "05-01", "11-01", "EA", "Early Action"),
    151351: ("Indiana", "02-01", "11-01", "EA", "Early Action"),
    155317: ("Kansas", "12-01", "11-01", "EA", "Early Action"),
    157085: ("Kentucky", "02-15", "12-01", "EA", "Early Action"),
    178396: ("Missouri", "01-15", "12-01", "EA", "Early Action"),
    181464: ("Nebraska", "05-01", "12-01", "EA", "Early Action"),
    209551: ("Oregon", "01-15", "11-01", "EA", "Early Action"),
    218663: ("South Carolina", "12-01", "10-15", "EA", "Early Action"),
    221759: ("Tennessee", "12-15", "11-01", "EA", "Early Action"),
    230764: ("Utah", "04-01", "12-01", "EA", "Early Action"),
    238032: ("West Virginia", "06-01", "12-01", "EA", "Early Action"),
    240727: ("Wyoming", "06-01", "12-01", "EA", "Early Action"),

    # --- Private Universities ---
    164988: ("Boston U", "01-04", "11-01", "ED", "Early Decision"),
    227757: ("Rice", "01-04", "11-01", "ED", "Early Decision"),
    160755: ("Tulane", "01-15", "11-01", "ED", "Early Decision"),
    139658: ("Emory", "01-01", "11-01", "ED", "Early Decision"),
}


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["unitid", "name", "rd_deadline", "early_deadline", "ea_ed_type", "notes"])
        for unitid, (name, rd, early, typ, notes) in sorted(DEADLINES.items()):
            w.writerow([unitid, name, rd, early, typ, notes])
    print(f"Wrote {len(DEADLINES)} deadline records -> {OUT}")


if __name__ == "__main__":
    main()