#!/usr/bin/env python
"""scripts/fetch_cds.py — Best-effort CDS (Common Data Set) feeder-school scraper.

The CDS Section C2 lists "feeder high schools" for a handful of universities.
This is the ONLY public source of per-highschool admission pipeline data, but:
  - Only ~5-10% of colleges publish a CDS, and fewer still list C2 feeder data.
  - Coverage is concentrated at elite private universities.
  - We can't curate URLs for all ~4,000 colleges.

This script:
  1. Uses a curated map of CDS URLs for known publishers.
  2. Tries common URL patterns against each college's domain as a fallback.
  3. Fetches + parses Section C2 for feeder high school names.
  4. Caches results to data/cds_cache/ for reuse.

Output: data/cds_feeder.csv  (unitid, high_school, source_url)

Run:  python scripts/fetch_cds.py [--limit N]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "cache" / "cds"
FEEDER_OUT = ROOT / "data" / "cds_feeder.csv"

# Curated CDS URLs for universities known to publish Section C2.
# Format: unitid -> CDS URL. Grown over time.
CDS_URL_MAP: dict[int, str] = {
    166027: "https://www.caltech.edu/academics/common-data-set",
    243744: "https://www.stanford.edu/academics/common-data-set/",
    166683: "https://www.harvard.edu/college-counselors/common-data-set/",
    129021: "https://www.yale.edu/academics/common-data-set",
    190150: "https://www.columbia.edu/content/common-data-set",
    144050: "https://www.uchicago.edu/college/commondata/",
    164924: "https://mit.edu/commondata/cds",
    215062: "https://www.curtis.edu/admissions/common-data-set/",
    217156: "https://www.princeton.edu/academics/common-data-set",
    168148: "https://www.brown.edu/admission/common-data-set",
    182670: "https://www.dartmouth.edu/office/admissions/common-data-set",
    213261: "https://www.upenn.edu/academics/common-data-set",
    198419: "https://www.duke.edu/admissions/common-data-set/",
    222075: "https://www.vanderbilt.edu/admissions/common-data-set/",
    162928: "https://www.jhu.edu/admissions/common-data-set/",
    115460: "https://www.pomona.edu/admission/common-data-set",
    165508: "https://www.colby.edu/admissions/common-data-set",
    163604: "https://www.bowdoin.edu/admissions/common-data-set/",
    167445: "https://www.swarthmore.edu/common-data-set",
    144460: "https://www.northwestern.edu/admissions/common-data-set/",
}

# Common CDS URL patterns to try against a college domain.
CDS_PATTERNS = [
    "/academics/common-data-set",
    "/common-data-set",
    "/about/common-data-set",
    "/admissions/common-data-set",
    "/registrar/common-data-set",
    "/college-counselors/common-data-set",
    "/ipeds/common-data-set",
    "/_files/data/common-data-set",
]


def _fetch_url(url: str, timeout: float = 15.0) -> str | None:
    """Fetch a URL and return text, or None on failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "college-triage/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _extract_feeder_schools(html: str) -> list[str]:
    """Parse CDS Section C2 feeder high school names from HTML.

    C2 asks "Percent of first-time, first-year students who enrolled in
    fall 2023 whose high school is: (n/a if less than 10)" and lists school
    names. We look for a list of school names near a 'C2' heading.
    """
    if not html:
        return []
    # Find C2 section: look for "C2" heading followed by school names.
    # CDS C2 typically lists high school names in a table or list.
    # Strategy: find text between "C2" and "C3" or next section heading.
    c2_match = re.search(r"C2\b.*?(?=<h[2-4]|C3\b|<div[^>]*class=\"page\")", html, re.DOTALL | re.IGNORECASE)
    if not c2_match:
        # Broader fallback: look for any high school names near "feeder"
        c2_match = re.search(r"(feeder|high school).*?(?=<h[2-4]|C3\b)", html, re.DOTALL | re.IGNORECASE)
    if not c2_match:
        return []
    section = c2_match.group(0)
    # Strip tags for name extraction.
    text = re.sub(r"<[^>]+>", " ", section)
    text = re.sub(r"\s+", " ", text).strip()
    # Extract plausible high school names (lines with known patterns).
    schools = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or len(line) < 5:
            continue
        # Skip header/generic lines.
        if re.match(r"^(C2|C3|Percent|Total|n/a|Name|High School|School)", line, re.IGNORECASE):
            continue
        # Keep lines that look like school names.
        if re.search(r"(High School|Academy|College|Prep|School|Institute|University|HS)", line, re.IGNORECASE):
            schools.append(line.strip(" ,;"))
    return schools


def fetch_feeder_for_unit(unitid: int, name: str, domain: str | None) -> list[str]:
    """Try curated URL then common patterns; return feeder school names."""
    # 1. Try curated URL.
    curated = CDS_URL_MAP.get(unitid)
    if curated:
        html = _fetch_url(curated)
        schools = _extract_feeder_schools(html) if html else []
        if schools:
            return schools

    # 2. Try common patterns against the college domain.
    if domain:
        domain = domain.rstrip("/")
        for pattern in CDS_PATTERNS:
            url = f"https://{domain}{pattern}"
            html = _fetch_url(url)
            if html:
                schools = _extract_feeder_schools(html) if html else []
                if schools:
                    return schools

    return []


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch CDS feeder high school data")
    parser.add_argument("--limit", type=int, default=50, help="Max colleges to check")
    args = parser.parse_args()

    import sqlite3
    conn = sqlite3.connect(ROOT / "data" / "colleges.db")
    df = pd.read_sql_query(
        "SELECT unitid, name, url FROM colleges WHERE acceptance_rate IS NOT NULL "
        "ORDER BY acceptance_rate ASC LIMIT ?",
        conn, params=[args.limit],
    )
    conn.close()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    records = []

    for _, row in df.iterrows():
        unitid = int(row["unitid"])
        name = str(row["name"])
        url = str(row["url"]) if pd.notna(row["url"]) else ""
        # Extract domain from URL.
        domain = None
        if url:
            m = re.search(r"https?://([^/]+)", url)
            if m:
                domain = m.group(1)

        schools = fetch_feeder_for_unit(unitid, name, domain)
        for school in schools:
            records.append({"unitid": unitid, "name": name, "high_school": school})
        if schools:
            print(f"  {name[:50]:50s} -> {len(schools)} feeder schools")

    if records:
        out = pd.DataFrame(records)
        out.to_csv(FEEDER_OUT, index=False)
        print(f"\nWrote {len(out)} feeder records -> {FEEDER_OUT}")
    else:
        print("\nNo feeder school data found (expected — coverage is very sparse).")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"fetch_cds FAILED: {e}", file=sys.stderr)
        raise