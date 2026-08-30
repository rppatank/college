"""fetchers.py — Live webpage fetching for the shortlist (Phase 1).

Strategy
--------
CDS pages are rarely at predictable URLs and many colleges don't publish one.
Admissions pages, on the other hand, are universal and always contain the
information we need: application deadlines, EA/ED availability, and sometimes
scholarship/aid hints.

This module:
  1. Discovers the admissions page URL for a college (curated map + pattern
     probing against the college's domain).
  2. Fetches + caches the page to data/cache/pages/.
  3. Extracts application deadlines, EA/ED dates, and aid hints from the HTML.
  4. Falls back gracefully when a page can't be fetched.

Run as a standalone script to pre-warm the cache:
    python fetchers.py --limit 20
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "data" / "cache" / "pages"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Curated admissions-page URLs for colleges where the default pattern fails.
# Format: unitid -> admissions page URL. Grown over time.
# Unitids verified against data/colleges.db.
ADM_URL_MAP: dict[int, str] = {
    # Ivy League
    130794: "https://www.yale.edu/college-admissions",
    166027: "https://college.harvard.edu/admissions",
    186131: "https://www.princeton.edu/admissions",
    190150: "https://www.columbia.edu/content/admissions",
    166683: "https://mit.edu/admissions",
    217156: "https://www.brown.edu/admission",
    215062: "https://www.upenn.edu/admissions",
    182670: "https://www.dartmouth.edu/office/admissions",
    190415: "https://admissions.cornell.edu/",

    # Top National
    243744: "https://www.stanford.edu/admissions/",
    144050: "https://www.uchicago.edu/college/admissions/",
    147767: "https://admissions.northwestern.edu/",
    198419: "https://admissions.duke.edu/",
    221999: "https://www.vanderbilt.edu/admissions/",
    162928: "https://apply.jhu.edu/",
    121345: "https://www.pomona.edu/admission",
    161086: "https://www.colby.edu/admissions/",
    161004: "https://www.bowdoin.edu/admissions/",
    216287: "https://www.swarthmore.edu/admissions",
    211893: "https://www.curtis.edu/admissions",
    110404: "https://www.caltech.edu/admissions",

    # UC System
    110635: "https://admissions.berkeley.edu/",
    110662: "https://admission.ucla.edu/",
    110680: "https://admissions.ucsd.edu/",
    110644: "https://admission.ucdavis.edu/",
    110653: "https://admissions.uci.edu/",
    110705: "https://admissions.ucsb.edu/",
    110714: "https://admissions.ucsc.edu/",
    110671: "https://admissions.ucr.edu/",
    445188: "https://admissions.ucmerced.edu/",

    # Top Public
    170976: "https://admissions.umich.edu/",
    234076: "https://admissions.virginia.edu/",
    199120: "https://admissions.unc.edu/",
    139755: "https://admission.gatech.edu/",
    145637: "https://admissions.illinois.edu/",
    240444: "https://admissions.wisc.edu/",
    174066: "https://admission.umn.edu/",
    228778: "https://admissions.utexas.edu/",
    204796: "https://undergrad.osu.edu/admissions",
    134130: "https://admissions.ufl.edu/",
    236948: "https://admission.washington.edu/",

    # Common State Flagships
    214777: "https://admissions.psu.edu/",
    243780: "https://admission.purdue.edu/",
    186380: "https://admissions.rutgers.edu/",
    163286: "https://admissions.umd.edu/",
    100858: "https://admissions.auburn.edu/",
    217882: "https://admission.clemson.edu/",
    153658: "https://admissions.uiowa.edu/",
    151351: "https://admissions.iu.edu/",
    155317: "https://admissions.ku.edu/",
    157085: "https://admissions.uky.edu/",
    178396: "https://admiss.missouri.edu/",
    181464: "https://admissions.unl.edu/",
    209551: "https://admissions.uoregon.edu/",
    218663: "https://sc.edu/admissions/",
    221759: "https://admissions.utk.edu/",
    230764: "https://admissions.utah.edu/",
    238032: "https://admissions.wvu.edu/",
    240727: "https://admissions.uwyo.edu/",

    # Private Universities
    164988: "https://www.bu.edu/admissions/",
    227757: "https://admission.rice.edu/",
    160755: "https://admission.tulane.edu/",
    139658: "https://admission.emory.edu/",
}

# Common admissions-page URL patterns to probe.
ADM_PATTERNS = [
    "/admissions",
    "/admission",
    "/apply",
    "/applying",
    "/undergraduate/admissions",
    "/admissions-and-aid",
    "/future-students/admissions",
    "/admissions financial-aid",
]


def _cache_path(url: str) -> Path:
    """Return the on-disk cache path for a URL."""
    h = hashlib.sha256(url.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{h}.json"


def fetch_url(url: str, timeout: float = 12.0) -> Optional[str]:
    """Fetch a URL with on-disk caching. Returns HTML text or None."""
    if not url:
        return None
    cp = _cache_path(url)
    if cp.exists():
        try:
            data = json.loads(cp.read_text(encoding="utf-8"))
            if data.get("ok") and data.get("body"):
                return data["body"]
        except Exception:
            pass

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; CollegeTriage/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace")
        cp.write_text(json.dumps({"ok": True, "body": body}), encoding="utf-8")
        return body
    except Exception as e:
        cp.write_text(json.dumps({"ok": False, "error": str(e)}), encoding="utf-8")
        return None


def _extract_domain(url: str) -> Optional[str]:
    """Extract the domain from a URL."""
    m = re.search(r"https?://([^/]+)", url or "")
    return m.group(1) if m else None


def discover_admissions_url(college) -> Optional[str]:
    """Find the admissions page URL for a college.

    Priority: curated map -> pattern probing against the college domain.
    """
    unitid = college.get("unitid")
    if unitid is not None and int(unitid) in ADM_URL_MAP:
        return ADM_URL_MAP[int(unitid)]

    url = str(college.get("url", "") or "")
    domain = _extract_domain(url)
    if not domain:
        return None

    for pattern in ADM_PATTERNS:
        candidate = f"https://{domain}{pattern}"
        body = fetch_url(candidate, timeout=8.0)
        if body and len(body) > 2000:  # real page, not a redirect/404
            return candidate
    return None


# ---------------------------------------------------------------- extraction

def _strip_tags(html: str) -> str:
    """Crude HTML tag stripping for text search."""
    return re.sub(r"<[^>]+>", " ", html or "")


def _find_dates(text: str) -> list[str]:
    """Find date-like strings in text."""
    patterns = [
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}",
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4}",
        r"\d{1,2}/\d{1,2}/\d{2,4}",
        r"\d{4}-\d{2}-\d{2}",
    ]
    found = []
    for pat in patterns:
        found.extend(re.findall(pat, text, re.IGNORECASE))
    return found


def extract_deadlines(html: str) -> list[dict]:
    """Extract application deadlines from an admissions page.

    Returns a list of {date, context} dicts.
    """
    if not html:
        return []
    text = _strip_tags(html)
    # Normalize whitespace.
    text = re.sub(r"\s+", " ", text)

    keywords = [
        "application deadline", "deadline", "priority deadline",
        "regular decision", "early action", "early decision",
        "EA deadline", "ED deadline", "RD deadline",
        "apply by", "applications due", "final deadline",
        "priority filing", "priority date",
    ]
    results = []
    seen = set()
    for kw in keywords:
        for m in re.finditer(re.escape(kw), text, re.IGNORECASE):
            start = max(0, m.start() - 5)
            end = min(len(text), m.end() + 120)
            snippet = text[start:end].strip()
            # Find dates in the snippet.
            dates = _find_dates(snippet)
            for d in dates:
                key = (kw.lower(), d)
                if key not in seen:
                    seen.add(key)
                    results.append({"keyword": kw, "date": d, "context": snippet[:200]})
    return results


def extract_ea_ed(html: str) -> dict:
    """Extract Early Action / Early Decision availability from an admissions page."""
    if not html:
        return {"early_action": False, "early_decision": False, "notes": ""}
    text = _strip_tags(html).lower()
    notes = []
    ea = False
    ed = False

    ea_patterns = [
        r"early action", r"\bea\b", r"early admission",
        r"restrictive early action", r"single-choice early action",
    ]
    ed_patterns = [
        r"early decision", r"\bed\b", r"early decision plan",
    ]

    for pat in ea_patterns:
        if re.search(pat, text):
            ea = True
            break
    for pat in ed_patterns:
        if re.search(pat, text):
            ed = True
            break

    # Extract any dates near EA/ED mentions.
    for pat in ea_patterns + ed_patterns:
        for m in re.finditer(pat, text):
            start = max(0, m.start() - 5)
            end = min(len(text), m.end() + 100)
            snippet = text[start:end].strip()
            dates = _find_dates(snippet)
            if dates:
                notes.append(f"{m.group()}: {dates[0]}")

    return {
        "early_action": ea,
        "early_decision": ed,
        "notes": "; ".join(notes[:5]),
    }


def extract_aid_hints(html: str) -> dict:
    """Extract scholarship/aid hints from an admissions page."""
    if not html:
        return {"merit_aid": False, "need_aid": False, "notes": ""}
    text = _strip_tags(html).lower()
    notes = []
    merit = False
    need = False

    if re.search(r"merit scholarship|merit aid|academic scholarship|presidential scholarship", text):
        merit = True
    if re.search(r"need-based|need blind|financial aid|FAFSA|CSS", text):
        need = True

    for kw in ["merit scholarship", "need-based", "need blind", "FAFSA"]:
        for m in re.finditer(re.escape(kw), text):
            start = max(0, m.start() - 10)
            end = min(len(text), m.end() + 80)
            notes.append(text[start:end].strip())
            break

    return {
        "merit_aid": merit,
        "need_aid": need,
        "notes": " | ".join(notes[:3]),
    }


def fetch_college_info(college) -> dict:
    """Fetch + parse a college's admissions page. Returns a structured dict."""
    url = discover_admissions_url(college)
    if not url:
        return {"url": None, "deadlines": [], "ea": False, "ed": False, "aid": {}, "error": "no_url"}

    body = fetch_url(url)
    if not body:
        return {"url": url, "deadlines": [], "ea": False, "ed": False, "aid": {}, "error": "fetch_failed"}

    return {
        "url": url,
        "deadlines": extract_deadlines(body),
        "ea": extract_ea_ed(body),
        "ed": extract_ea_ed(body),
        "aid": extract_aid_hints(body),
        "error": None,
    }


# ---------------------------------------------------------------- CLI

def main() -> None:
    import argparse
    import sqlite3

    parser = argparse.ArgumentParser(description="Fetch admissions pages for shortlisted colleges")
    parser.add_argument("--limit", type=int, default=20, help="Max colleges to check")
    args = parser.parse_args()

    conn = sqlite3.connect(ROOT / "data" / "colleges.db")
    df = pd.read_sql_query(
        "SELECT unitid, name, url FROM colleges WHERE acceptance_rate IS NOT NULL "
        "ORDER BY acceptance_rate ASC LIMIT ?",
        conn, params=[args.limit],
    )
    conn.close()

    for _, row in df.iterrows():
        info = fetch_college_info(row)
        name = str(row["name"])[:45]
        if info["error"]:
            print(f"  {name:45s} -> {info['error']}")
            continue
        dl = info["deadlines"]
        dl_str = ", ".join(f"{d['date']} ({d['keyword']})" for d in dl[:3]) or "none found"
        ea = "EA" if info["ea"].get("early_action") else ""
        ed = "ED" if info["ea"].get("early_decision") else ""
        print(f"  {name:45s} url={info['url'][:55]}")
        print(f"    deadlines: {dl_str}")
        print(f"    EA/ED: {ea} {ed}")


if __name__ == "__main__":
    import pandas as pd
    try:
        main()
    except Exception as e:
        print(f"fetchers FAILED: {e}", file=sys.stderr)
        raise