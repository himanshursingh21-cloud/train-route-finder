"""
prepare_data.py
───────────────
Combines Steps 2, 3, and 4 of the guide, adapted for CSV input files.

Column mapping (actual CSV → guide convention):
  schedules.csv : train_number  → trainNumber
                  arrival       → arr
                  departure     → dep
                  station_code  → station_code   (already matches)
  stations.csv  : code          → station_code
                  latitude      → lat
                  longitude     → lon
  price_data.csv: trainNumber, fromStnCode, toStnCode  (already match)

Outputs (written to data/):
  schedule_flat.parquet  – one row per (train, stop) with times in minutes
  stations.parquet       – station coordinates keyed on station_code
  edges.parquet          – fare + travel_min for every valid O-D pair
"""

import ast
import hashlib
import pandas as pd
import numpy as np

# ── Paths ────────────────────────────────────────────────────────────────────

SCHEDULE_CSV   = "data/schedules.csv"
STATIONS_CSV   = "data/stations.csv"
PRICE_CSV      = "data/price_data.csv"

OUT_SCHED      = "data/schedule_flat.parquet"
OUT_STATIONS   = "data/stations.parquet"
OUT_EDGES      = "data/edges.parquet"


def compute_runs_on(train_number) -> frozenset:
    """Deterministic set of weekdays (0=Mon … 6=Sun) a train runs on.

    Based on a hash of the train number so the same train always gets the
    same running days. Each train runs on 4–6 days of the week.
    """
    h = int(hashlib.md5(str(train_number).encode()).hexdigest(), 16)
    num_days = 4 + (h % 3)  # 4, 5, or 6 running days
    days = set()
    for i in range(num_days):
        days.add((h // (7 ** i)) % 7)
    return frozenset(days)


def parse_availability(avail_str) -> str | None:
    """Extract the first availability status from the price_data JSON-like string.

    Example input: "[{'date': '2-12-2023', 'status': 'AVAILABLE-0008'}, ...]"
    Returns: 'AVAILABLE-0008' or None if unavailable / unparseable.
    """
    if pd.isna(avail_str) or str(avail_str).strip() in ("", "-", "None", "nan"):
        return None
    try:
        entries = ast.literal_eval(str(avail_str))
        if entries and isinstance(entries, list):
            first = entries[0]
            if isinstance(first, dict):
                return first.get("status")
    except (ValueError, SyntaxError):
        pass
    return None

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 – Build schedule_flat.parquet
# ─────────────────────────────────────────────────────────────────────────────

def to_min(time_str) -> float | None:
    """Convert 'HH:MM:SS' or 'HH:MM' string to total minutes since midnight.

    Returns NaN for missing / sentinel values ('-', NaN, etc.).
    """
    if pd.isna(time_str) or str(time_str).strip() in ("", "-", "None"):
        return np.nan
    s = str(time_str).strip()
    parts = s.split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        sec = int(parts[2]) if len(parts) > 2 else 0
        return h * 60 + m + sec / 60
    except (ValueError, IndexError):
        return np.nan


def build_schedule_flat() -> pd.DataFrame:
    """Read schedules.csv, normalise column names, convert times to minutes.

    Returns schedule_flat DataFrame and saves it as a Parquet file.
    """
    print("► [Step 2] Reading schedules.csv …")
    sched = pd.read_csv(SCHEDULE_CSV, low_memory=False)

    # ── Rename to guide convention ───────────────────────────────────────────
    sched = sched.rename(columns={
        "train_number": "trainNumber",
        "arrival":      "arr",
        "departure":    "dep",
        # station_code, station_name, train_name, day stay as-is
    })

    # ── Convert times ────────────────────────────────────────────────────────
    sched["arr_min"] = sched["arr"].apply(to_min)
    sched["dep_min"] = sched["dep"].apply(to_min)

    # ── Effective time: prefer departure, fall back to arrival ───────────────
    sched["time_min"] = sched["dep_min"].combine_first(sched["arr_min"])

    # ── Drop rows where we cannot determine any time at all ──────────────────
    before = len(sched)
    sched = sched.dropna(subset=["time_min"]).reset_index(drop=True)
    print(f"   Dropped {before - len(sched)} rows with no usable time.")

    # ── Ensure trainNumber is a string (consistent with price_data) ──────────
    sched["trainNumber"] = sched["trainNumber"].astype(str).str.strip()

    sched.to_parquet(OUT_SCHED, index=False)
    print(f"   Saved → {OUT_SCHED}  shape={sched.shape}")
    return sched


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 – Build stations.parquet
# ─────────────────────────────────────────────────────────────────────────────

def build_stations() -> pd.DataFrame:
    """Read stations.csv, rename columns, and save as Parquet.

    Guide convention: station_code, lat, lon.
    Actual columns   : code, latitude, longitude.
    """
    print("► [Step 2] Reading stations.csv …")
    stations = pd.read_csv(STATIONS_CSV, low_memory=False)

    stations = stations.rename(columns={
        "code":      "station_code",
        "latitude":  "lon",
        "longitude": "lat",
    })

    # Keep only the columns we need
    keep = ["station_code", "name", "lat", "lon", "zone", "state"]
    keep = [c for c in keep if c in stations.columns]
    stations = stations[keep].drop_duplicates(subset=["station_code"]).reset_index(drop=True)

    stations.to_parquet(OUT_STATIONS, index=False)
    print(f"   Saved → {OUT_STATIONS}  shape={stations.shape}")
    return stations


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 – Build edges.parquet
# ─────────────────────────────────────────────────────────────────────────────

def build_edges(sched: pd.DataFrame) -> pd.DataFrame:
    """Join price_data.csv with schedule_flat to produce validated edges.

    An edge (fromStnCode → toStnCode on trainNumber) is valid only when:
      1. The train actually stops at fromStnCode  (dep_min is known).
      2. The train actually stops at toStnCode    (arr_min is known).
      3. The departure time at 'from' is strictly before the arrival at 'to'.

    Computes travel_min = arr_min(to) − dep_min(from).
    """
    print("► [Step 3] Reading price_data.csv …")
    prices = pd.read_csv(PRICE_CSV, low_memory=False)

    # Ensure join keys are strings
    prices["trainNumber"] = prices["trainNumber"].astype(str).str.strip()
    prices["fromStnCode"] = prices["fromStnCode"].astype(str).str.strip()
    prices["toStnCode"]   = prices["toStnCode"].astype(str).str.strip()
    sched["station_code"] = sched["station_code"].astype(str).str.strip()

    # ── Lookup tables: (trainNumber, station_code) → dep_min / arr_min ──────
    # For the departure station we need dep_min (when the train leaves).
    dep_lookup = (
        sched[sched["dep_min"].notna()][["trainNumber", "station_code", "dep_min"]]
        .drop_duplicates(subset=["trainNumber", "station_code"])
        .rename(columns={"station_code": "fromStnCode", "dep_min": "from_dep_min"})
    )

    # For the arrival station we need arr_min (when the train arrives).
    arr_lookup = (
        sched[sched["arr_min"].notna()][["trainNumber", "station_code", "arr_min"]]
        .drop_duplicates(subset=["trainNumber", "station_code"])
        .rename(columns={"station_code": "toStnCode", "arr_min": "to_arr_min"})
    )

    print("   Merging prices ← dep_lookup …")
    edges = prices.merge(dep_lookup, on=["trainNumber", "fromStnCode"], how="inner")
    print(f"   After dep merge: {len(edges):,} rows")

    print("   Merging prices ← arr_lookup …")
    edges = edges.merge(arr_lookup, on=["trainNumber", "toStnCode"], how="inner")
    print(f"   After arr merge: {len(edges):,} rows")

    # ── Validate ordering: from must depart before to arrives ────────────────
    edges = edges[edges["from_dep_min"] < edges["to_arr_min"]].copy()
    print(f"   After ordering filter: {len(edges):,} rows")

    # ── Compute travel_min ────────────────────────────────────────────────────
    edges["travel_min"] = edges["to_arr_min"] - edges["from_dep_min"]

    # ── Parse availability from price_data.csv ────────────────────────────────
    if "availability" in edges.columns:
        edges["availability"] = edges["availability"].apply(parse_availability)
    else:
        edges["availability"] = None

    # ── Deterministic running days per train ──────────────────────────────────
    edges["runs_on"] = edges["trainNumber"].apply(compute_runs_on)
    # Store as list (parquet-compatible); data_loader.py converts back to frozenset
    edges["runs_on"] = edges["runs_on"].apply(lambda s: sorted(list(s)) if s is not None else None)

    # ── Select and order final columns ────────────────────────────────────────
    out_cols = [
        "trainNumber", "fromStnCode", "toStnCode", "classCode",
        "from_dep_min", "to_arr_min", "travel_min",
        "distance", "duration",
        "totalFare", "baseFare", "tatkalFare",
        "reservationCharge", "superfastCharge", "serviceTax",
        "availability", "runs_on",
    ]
    # Only keep columns that actually exist (guard against schema changes)
    out_cols = [c for c in out_cols if c in edges.columns]
    edges = edges[out_cols].reset_index(drop=True)

    edges.to_parquet(OUT_EDGES, index=False)
    print(f"   Saved → {OUT_EDGES}  shape={edges.shape}")
    return edges


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 – Transfer utility functions
# ─────────────────────────────────────────────────────────────────────────────

def wait_minutes(arr_min: float, dep_min: float) -> float:
    """Return waiting time in minutes between arrival and next departure.

    Handles overnight crossings: if dep_min < arr_min the next departure
    is assumed to be on the following day (add 24 × 60 = 1440 minutes).

    Parameters
    ----------
    arr_min : float
        Arrival time at the transfer station in minutes since midnight.
    dep_min : float
        Departure time of the connecting train in minutes since midnight.

    Returns
    -------
    float
        Non-negative wait duration in minutes.
    """
    wait = dep_min - arr_min
    if wait < 0:
        wait += 1440  # overnight crossing
    return wait


def valid_transfer(arr_min: float, dep_min: float,
                   min_buffer: float = 20.0,
                   max_buffer: float = 360.0) -> bool:
    """Return True if the layover time is within the allowed transfer window.

    Parameters
    ----------
    arr_min    : float  – arrival time at transfer station (minutes since midnight)
    dep_min    : float  – departure time of connecting train (minutes since midnight)
    min_buffer : float  – minimum connection time in minutes (default 20 min)
    max_buffer : float  – maximum wait before the connection is impractical
                          (default 360 min = 6 hours)

    Returns
    -------
    bool
        True  → transfer is feasible.
        False → too tight or too long a wait.
    """
    wait = wait_minutes(arr_min, dep_min)
    return min_buffer <= wait <= max_buffer


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # ── Step 2 ────────────────────────────────────────────────────────────────
    sched    = build_schedule_flat()
    stations = build_stations()

    # ── Step 3 ────────────────────────────────────────────────────────────────
    edges = build_edges(sched)

    # ── Required print statements (as specified by the guide) ─────────────────
    print()
    print(sched.shape, "schedule_flat rows × cols")
    print(len(edges), "edges after merge & validation")
    print("Stations sample:")
    print(stations.head())
    print()
    print("✅  All parquet files written to data/")
