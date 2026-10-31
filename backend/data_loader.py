"""
backend/data_loader.py
──────────────────────
Builds a time-expanded RouteGraph from pre-processed Parquet files.

Expected files (relative to project root):
  data/edges.parquet    – one row per valid O-D fare pair with travel times
  data/stations.parquet – station metadata with coordinates

Parquet column reference:
  edges    : trainNumber, fromStnCode, toStnCode, classCode,
             from_dep_min, to_arr_min, travel_min, totalFare
  stations : station_code, name, lat, lon
"""

from __future__ import annotations

import bisect
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from .graph_models import Edge, RouteGraph, StateNode

# ── Path resolution ──────────────────────────────────────────────────────────
_PROJECT_ROOT  = Path(__file__).parent.parent
_EDGES_PATH    = _PROJECT_ROOT / "data" / "edges.parquet"
_STATIONS_PATH = _PROJECT_ROOT / "data" / "stations.parquet"


# ─────────────────────────────────────────────────────────────────────────────
# Transfer utility functions  (mirrors prepare_data.py Step 4)
# ─────────────────────────────────────────────────────────────────────────────

def wait_minutes(arr_min: float, dep_min: float) -> float:
    """Return the wait time (minutes) between arrival and next departure.

    Handles overnight crossings: if dep_min < arr_min the connection is assumed
    to be the following day, so 1440 minutes are added.
    """
    wait = dep_min - arr_min
    if wait < 0:
        wait += 1440
    return wait


def valid_transfer(arr_min: float, dep_min: float,
                   min_buffer: float = 20.0,
                   max_buffer: float = 360.0) -> bool:
    """Return True when the layover falls within the allowed transfer window.

    Parameters
    ----------
    arr_min    : Arrival time at the transfer station (minutes since midnight).
    dep_min    : Departure time of the connecting train (minutes since midnight).
    min_buffer : Minimum connection time in minutes (default 20 min).
    max_buffer : Maximum wait before the connection is impractical
                 (default 360 min = 6 hours).
    """
    wait = wait_minutes(arr_min, dep_min)
    return min_buffer <= wait <= max_buffer


# ─────────────────────────────────────────────────────────────────────────────
# Main builder
# ─────────────────────────────────────────────────────────────────────────────

def build_graph_from_parquet(
    edges_path: str | Path = _EDGES_PATH,
    class_filter: str | None = None,
) -> RouteGraph:
    """Build a RouteGraph from edges.parquet.

    Parameters
    ----------
    edges_path   : Path to edges.parquet (defaults to data/edges.parquet
                   relative to the project root).
    class_filter : Optional IRCTC class code to restrict edges
                   (e.g. 'SL', '2A', '3A'). When None all classes are used.

    Returns
    -------
    RouteGraph
        Fully populated graph with IN_TRAIN edges and feasible TRANSFER edges.
    """
    # ── Load and sanitise the edge table ─────────────────────────────────────
    df = pd.read_parquet(edges_path)

    if class_filter:
        df = df[df["classCode"] == class_filter].copy()

    # Drop rows with missing time or fare information
    required_cols = ["from_dep_min", "to_arr_min", "travel_min", "totalFare"]
    df = df.dropna(subset=required_cols).reset_index(drop=True)
    df["trainNumber"] = df["trainNumber"].astype(str)

    graph = RouteGraph()

    # Accumulate arrival / departure nodes per station for transfer linking.
    # Layout: station_code → sorted list of (time_in_minutes, StateNode)
    arrivals_by_station:   Dict[str, List[Tuple[float, StateNode]]] = {}
    departures_by_station: Dict[str, List[Tuple[float, StateNode]]] = {}

    # ── IN_TRAIN edges ────────────────────────────────────────────────────────
    for row in df.itertuples(index=False):
        # Times stored as minutes since midnight; StateNode expects decimal hours
        src = StateNode(
            station_code=row.fromStnCode,
            time=row.from_dep_min / 60.0,
            train_id=row.trainNumber,
        )
        tgt = StateNode(
            station_code=row.toStnCode,
            time=row.to_arr_min / 60.0,
            train_id=row.trainNumber,
        )

        # Normalise runs_on (stored in parquet as list/array → frozenset of ints)
        runs_on = getattr(row, "runs_on", None)
        if runs_on is not None and not isinstance(runs_on, frozenset):
            try:
                runs_on = frozenset(int(d) for d in runs_on)
            except (TypeError, ValueError):
                runs_on = None

        # Fare component breakdown
        fare_breakdown = {
            "baseFare": float(row.baseFare) if pd.notna(row.baseFare) else 0.0,
            "reservationCharge": float(row.reservationCharge) if pd.notna(row.reservationCharge) else 0.0,
            "superfastCharge": float(row.superfastCharge) if pd.notna(row.superfastCharge) else 0.0,
            "serviceTax": float(row.serviceTax) if pd.notna(row.serviceTax) else 0.0,
            "totalFare": float(row.totalFare),
        }

        graph.add_edge(Edge(
            source=src,
            target=tgt,
            cost=float(row.totalFare),
            time_taken=float(row.travel_min) / 60.0,  # minutes → hours
            edge_type="IN_TRAIN",
            classCode=row.classCode,
            fare_breakdown=fare_breakdown,
            availability=getattr(row, "availability", None),
            runs_on=runs_on,
        ))

        # Register endpoints for later transfer discovery
        arrivals_by_station.setdefault(row.toStnCode, []).append(
            (row.to_arr_min, tgt)
        )
        departures_by_station.setdefault(row.fromStnCode, []).append(
            (row.from_dep_min, src)
        )

    # ── TRANSFER edges ────────────────────────────────────────────────────────
    # Strategy: for every station that appears as both an arrival and a
    # departure point, pair each arrival node with every departure node from a
    # *different* train whose layover falls within [min_buffer, max_buffer].
    # Bisect is used on the sorted departure list to bound the inner loop.
    seen_transfers: set[tuple[StateNode, StateNode]] = set()

    common_stations = set(arrivals_by_station) & set(departures_by_station)
    MIN_BUF, MAX_BUF = 20.0, 360.0  # minutes

    for station in common_stations:
        # Sort arrivals and departures by time
        arr_list = sorted(arrivals_by_station[station],   key=lambda x: x[0])
        dep_list = sorted(departures_by_station[station], key=lambda x: x[0])
        dep_times = [d[0] for d in dep_list]  # sorted float list for bisect

        for arr_min, arr_node in arr_list:
            # Binary-search window: departures in [arr_min+MIN_BUF, arr_min+MAX_BUF]
            lo = bisect.bisect_left(dep_times,  arr_min + MIN_BUF)
            hi = bisect.bisect_right(dep_times, arr_min + MAX_BUF)

            for dep_min, dep_node in dep_list[lo:hi]:
                if arr_node.train_id == dep_node.train_id:
                    continue  # same train – already linked by IN_TRAIN edge

                key = (arr_node, dep_node)
                if key in seen_transfers:
                    continue  # deduplicate across class variants

                seen_transfers.add(key)
                wait_h = (dep_min - arr_min) / 60.0
                graph.add_edge(Edge(
                    source=arr_node,
                    target=dep_node,
                    cost=0.0,
                    time_taken=wait_h,
                    edge_type="TRANSFER",
                ))

    return graph


# ─────────────────────────────────────────────────────────────────────────────
# Standalone smoke-test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import os
    # Allow running directly: python -m backend.data_loader
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

    from backend.graph_models import RouteGraph  # noqa: F811 (re-import for __main__)

    print("Building graph from parquet files …")
    g = build_graph_from_parquet()
    in_train  = sum(
        1 for edges in g.adjacency_list.values()
        for e in edges if e.edge_type == "IN_TRAIN"
    )
    transfers = sum(
        1 for edges in g.adjacency_list.values()
        for e in edges if e.edge_type == "TRANSFER"
    )
    print(f"  Source nodes : {len(g.adjacency_list):,}")
    print(f"  IN_TRAIN     : {in_train:,}")
    print(f"  TRANSFER     : {transfers:,}")
