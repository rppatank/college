#!/usr/bin/env python
"""scripts/build_db.py — Build the local college store from the Dept. of Education
College Scorecard "Most-Recent-Cohorts-Institution" CSV.

Produces:
  data/colleges.db     (SQLite)
  data/colleges.parquet

Filter: 4-year, degree-granting, undergraduate-focused. Branch campuses are KEPT
(flagged via MAIN / NUMBRANCH). For-profit institutions are dropped.

Run:  python scripts/build_db.py
"""

from __future__ import annotations

import sqlite3
import sys
import zipfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "colleges.db"
PARQUET_PATH = DATA_DIR / "colleges.parquet"
RAW_DIR = DATA_DIR / "raw"
SCORECARD_URL = (
    "https://ed-public-download.scorecard.network/downloads/"
    "Most-Recent-Cohorts-Institution_06102026.zip"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS colleges (
    unitid              INTEGER PRIMARY KEY,
    name                TEXT,
    city                TEXT,
    state               TEXT,
    url                 TEXT,
    main                INTEGER,
    num_branch          INTEGER,
    control             INTEGER,
    region              INTEGER,
    locale              INTEGER,
    lat                 REAL,
    lon                 REAL,
    acceptance_rate     REAL,
    sat_vr_25           REAL,
    sat_vr_75           REAL,
    sat_mt_25           REAL,
    sat_mt_75           REAL,
    sat_wr_25           REAL,
    sat_wr_75           REAL,
    sat_avg             REAL,
    act_cm_25           REAL,
    act_cm_75           REAL,
    act_mt_25           REAL,
    act_mt_75           REAL,
    act_wr_25           REAL,
    act_wr_75           REAL,
    act_mid             REAL,
    cost_attendance     REAL,
    tuition_in_state     REAL,
    tuition_out_state    REAL,
    ugds                 REAL,
    completion_rate      REAL,
    pcip_01             REAL,
    pcip_02             REAL,
    pcip_03             REAL,
    pcip_04             REAL,
    pcip_05             REAL,
    pcip_06             REAL,
    pcip_07             REAL,
    pcip_08             REAL,
    pcip_09             REAL,
    pcip_10             REAL,
    pcip_11             REAL,
    pcip_12             REAL,
    pcip_13             REAL,
    pcip_14             REAL,
    pcip_15             REAL,
    pcip_16             REAL,
    pcip_17             REAL,
    pcip_18             REAL,
    pcip_19             REAL,
    pcip_20             REAL,
    pcip_21             REAL,
    pcip_22             REAL,
    pcip_23             REAL,
    pcip_24             REAL,
    pcip_25             REAL,
    pcip_26             REAL,
    pcip_27             REAL,
    pcip_28             REAL,
    pcip_29             REAL,
    pcip_30             REAL,
    pcip_31             REAL,
    pcip_32             REAL,
    pcip_33             REAL,
    pcip_34             REAL,
    pcip_35             REAL,
    pcip_36             REAL,
    pcip_37             REAL,
    pcip_38             REAL,
    pcip_39             REAL,
    pcip_40             REAL,
    pcip_41             REAL,
    pcip_42             REAL,
    pcip_43             REAL,
    pcip_44             REAL,
    pcip_45             REAL,
    pcip_46             REAL,
    pcip_47             REAL,
    pcip_48             REAL,
    pcip_49             REAL,
    pcip_50             REAL,
    pcip_51             REAL,
    pcip_52             REAL,
    pcip_53             REAL,
    pcip_54             REAL
)
"""

PCIP_FIELDS = [f"PCIP{i:02d}" for i in range(1, 55)]

KEEP_COLS = [
    "UNITID", "INSTNM", "CITY", "STABBR", "INSTURL",
    "MAIN", "NUMBRANCH", "CONTROL", "REGION", "LOCALE",
    "LATITUDE", "LONGITUDE",
    "ADM_RATE",
    "SATVR25", "SATVR75", "SATMT25", "SATMT75", "SATWR25", "SATWR75", "SAT_AVG",
    "ACTCM25", "ACTCM75", "ACTMT25", "ACTMT75", "ACTWR25", "ACTWR75", "ACTMID",
    "COSTT4_A", "TUITIONFEE_IN", "TUITIONFEE_OUT",
    "UGDS",
    "COMP_4YR_TRANS_YR2_RT",
] + PCIP_FIELDS


def download_scorecard() -> Path:
    """Download + unzip the Scorecard CSV if not already present."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = RAW_DIR / "Most-Recent-Cohorts-Institution.csv"
    if csv_path.exists():
        print(f"[build_db] Using cached {csv_path}")
        return csv_path

    print(f"[build_db] Downloading {SCORECARD_URL}")
    import urllib.request

    zip_path = RAW_DIR / "scorecard.zip"
    req = urllib.request.Request(SCORECARD_URL, headers={"User-Agent": "college-triage/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(zip_path, "wb") as f:
        while True:
            chunk = r.read(1 << 20)  # 1MB
            if not chunk:
                break
            f.write(chunk)

    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if n.endswith(".csv")]
        if not names:
            raise RuntimeError(f"No CSV found in {zip_path}")
        zf.extract(names[0], RAW_DIR)
        extracted = RAW_DIR / names[0]
        extracted.rename(csv_path)

    zip_path.unlink(missing_ok=True)
    return csv_path


def build() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = download_scorecard()

    print("[build_db] Reading CSV (this may take a moment)...")
    header = pd.read_csv(csv_path, nrows=0).columns.tolist()
    use_cols = [c for c in KEEP_COLS if c in header]

    df = pd.read_csv(csv_path, usecols=use_cols, low_memory=False)
    print(f"[build_db] Raw rows: {len(df)}")

    # ---------------------------------------------------------------- filters
    # 4-year, degree-granting, undergraduate-focused.
    # CONTROL: 1=public, 2=private-nonprofit, 3=for-profit  -> drop 3.
    if "CONTROL" in df:
        df = df[df["CONTROL"].isin([1, 2])]
    if "UGDS" in df:
        df = df[df["UGDS"].notna() & (df["UGDS"] > 0)]

    # ---------------------------------------------------------------- rename
    rename = {
        "UNITID": "unitid",
        "INSTNM": "name",
        "CITY": "city",
        "STABBR": "state",
        "INSTURL": "url",
        "MAIN": "main",
        "NUMBRANCH": "num_branch",
        "CONTROL": "control",
        "REGION": "region",
        "LOCALE": "locale",
        "LATITUDE": "lat",
        "LONGITUDE": "lon",
        "ADM_RATE": "acceptance_rate",
        "SATVR25": "sat_vr_25",
        "SATVR75": "sat_vr_75",
        "SATMT25": "sat_mt_25",
        "SATMT75": "sat_mt_75",
        "SATWR25": "sat_wr_25",
        "SATWR75": "sat_wr_75",
        "SAT_AVG": "sat_avg",
        "ACTCM25": "act_cm_25",
        "ACTCM75": "act_cm_75",
        "ACTMT25": "act_mt_25",
        "ACTMT75": "act_mt_75",
        "ACTWR25": "act_wr_25",
        "ACTWR75": "act_wr_75",
        "ACTMID": "act_mid",
        "COSTT4_A": "cost_attendance",
        "TUITIONFEE_IN": "tuition_in_state",
        "TUITIONFEE_OUT": "tuition_out_state",
        "UGDS": "ugds",
        "COMP_4YR_TRANS_YR2_RT": "completion_rate",
    }
    for i in range(1, 55):
        rename[f"PCIP{i:02d}"] = f"pcip_{i:02d}"

    df = df.rename(columns=rename)

    # Drop rows without an identity.
    df = df.dropna(subset=["unitid", "name"])
    df["unitid"] = df["unitid"].astype("int64")

    # ---------------------------------------------------------------- persist
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    df.to_sql("colleges", conn, if_exists="append", index=False)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_name ON colleges(name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_state ON colleges(state)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_control ON colleges(control)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_acceptance ON colleges(acceptance_rate)")
    conn.commit()
    conn.close()

    df.to_parquet(PARQUET_PATH, index=False)

    n = len(df)
    print(f"[build_db] Wrote {n} colleges -> {DB_PATH}, {PARQUET_PATH}")

    # ---------------------------------------------------------------- verify
    with sqlite3.connect(DB_PATH) as conn:
        cnt = conn.execute("SELECT COUNT(*) FROM colleges").fetchone()[0]
        null_rate = conn.execute(
            "SELECT COUNT(*) FROM colleges WHERE acceptance_rate IS NULL"
        ).fetchone()[0]
    print(f"[build_db] Verify: {cnt} rows in SQLite, {null_rate} missing acceptance_rate")
    assert cnt == n, "SQLite row count mismatch"
    assert n > 3000, f"Expected >3000 colleges, got {n}"
    print("[build_db] OK")


if __name__ == "__main__":
    try:
        build()
    except Exception as e:
        print(f"[build_db] FAILED: {e}", file=sys.stderr)
        raise