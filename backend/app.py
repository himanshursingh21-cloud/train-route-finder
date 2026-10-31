"""
backend/app.py
──────────────
Flask REST API for the Cheapest Train App.

Endpoints
---------
GET /api/route
    Find the optimal train route between two stations.
    Query params: source, destination, preference (cheapest|fastest|balanced)
    Returns: total_cost, total_time, route_legs (with per-leg coordinates)

GET /api/stations
    Return all stations that are reachable within the loaded graph.
    Returns: list of {code, name, lat, lon}
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, request
from flask_cors import CORS

from .data_loader import build_graph_from_parquet
from .dijkstra_engine import find_optimal_route

# ── Flask setup ───────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# ── Project paths ─────────────────────────────────────────────────────────────
_PROJECT_ROOT  = Path(__file__).parent.parent
_STATIONS_PATH = _PROJECT_ROOT / "data" / "stations.parquet"

# ── One-time startup: build graph and load station metadata ───────────────────
print("Loading graph from parquet files … (this may take a few seconds)")
graph = build_graph_from_parquet()

in_train  = sum(1 for es in graph.adjacency_list.values() for e in es if e.edge_type == "IN_TRAIN")
transfers = sum(1 for es in graph.adjacency_list.values() for e in es if e.edge_type == "TRANSFER")
print(f"  Graph ready — {len(graph.adjacency_list):,} source nodes, "
      f"{in_train:,} IN_TRAIN, {transfers:,} TRANSFER edges")

# ── Station metadata ──────────────────────────────────────────────────────────
_stations_df: pd.DataFrame = pd.DataFrame(
    columns=["station_code", "name", "lat", "lon"]
)

if _STATIONS_PATH.exists():
    _raw = pd.read_parquet(_STATIONS_PATH)

    # Gracefully handle missing geometry: drop rows where lat/lon are absent
    _raw = _raw.dropna(subset=["lat", "lon"])

    # Keep only stations that exist in the graph (routable stations)
    _routable_codes: set[str] = {
        node.station_code for node in graph.adjacency_list.keys()
    }
    _stations_df = _raw[_raw["station_code"].isin(_routable_codes)].reset_index(drop=True)
    print(f"  Stations loaded — {len(_stations_df):,} routable stations with coordinates")
else:
    print("  WARNING: stations.parquet not found — coordinate lookups will be empty")

# Fast coordinate lookup: station_code → {lat, lon, name}
_coord_lookup: dict[str, dict] = {
    row.station_code: {"lat": row.lat, "lon": row.lon, "name": row.name}
    for row in _stations_df.itertuples(index=False)
}


# ── Helper ────────────────────────────────────────────────────────────────────

def _get_coords(station_code: str) -> dict:
    """Return {lat, lon} for a station, or empty dict if unavailable."""
    info = _coord_lookup.get(station_code, {})
    return {"lat": info.get("lat"), "lon": info.get("lon")} if info else {}


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/api/stations', methods=['GET'])
def get_stations():
    """Return all routable stations with their coordinates.

    Response schema
    ---------------
    [
      { "code": "NDLS", "name": "New Delhi", "lat": 28.64, "lon": 77.22 },
      ...
    ]
    """
    station_list = [
        {
            "code": row.station_code,
            "name": row.name,
            "lat":  row.lat,
            "lon":  row.lon,
        }
        for row in _stations_df.itertuples(index=False)
    ]
    return jsonify(station_list)


@app.route('/api/route', methods=['GET'])
def get_route():
    """Find the optimal train route between two stations.

    Query Parameters
    ----------------
    source      : str  – departure station code (e.g. 'NDLS')
    destination : str  – arrival station code   (e.g. 'BCT')
    preference  : str  – 'cheapest' | 'fastest' | 'balanced'  (default: 'cheapest')

    Response schema
    ---------------
    {
      "total_cost": 1000.0,
      "total_time": 4.5,
      "route_legs": [
        {
          "from_station": "NDLS",
          "to_station":   "BCT",
          "depart_time":  9.0,
          "arrive_time":  13.5,
          "cost":         1000.0,
          "edge_type":    "IN_TRAIN",
          "from_lat":     28.64,   // null when coordinates unavailable
          "from_lon":     77.22,
          "to_lat":       18.97,
          "to_lon":       72.82
        },
        ...
      ]
    }
    """
    source      = request.args.get('source', '').strip()
    destination = request.args.get('destination', '').strip()
    preference  = request.args.get('preference', 'cheapest').strip().lower()
    travel_class = request.args.get('travel_class', 'SL').strip().upper()
    travel_date = request.args.get('travel_date', '').strip()

    # ── Validation ────────────────────────────────────────────────────────────
    if not source or not destination:
        return jsonify({"error": "Missing required query parameters: source and destination"}), 400

    if source == destination:
        return jsonify({"error": "Source and destination must be different"}), 400

    if preference not in {"cheapest", "fastest", "balanced"}:
        return jsonify({"error": "preference must be one of: cheapest, fastest, balanced"}), 400

    # ── Route search ──────────────────────────────────────────────────────────
    total_cost, total_time, path = find_optimal_route(
        graph, source, destination, preference, class_filter=travel_class,
        travel_date=travel_date or None
    )

    if total_cost == float('inf'):
        return jsonify({"error": f"No route found from '{source}' to '{destination}'"}), 404

    # ── Serialise route legs with coordinates, fare breakdown, availability ────
    route_legs = []
    for edge in path:
        from_coords = _get_coords(edge.source.station_code)
        to_coords   = _get_coords(edge.target.station_code)

        leg = {
            "from_station": edge.source.station_code,
            "to_station":   edge.target.station_code,
            "depart_time":  edge.source.time,
            "arrive_time":  edge.target.time,
            "cost":         edge.cost,
            "edge_type":    edge.edge_type,
            "classCode":    edge.classCode,
            # Fare breakdown (IN_TRAIN legs only)
            "fare_breakdown": edge.fare_breakdown,
            # Historical availability sample (IN_TRAIN legs only)
            "availability": edge.availability,
            # Coordinates — null when station is not in stations.parquet
            "from_lat": from_coords.get("lat"),
            "from_lon": from_coords.get("lon"),
            "to_lat":   to_coords.get("lat"),
            "to_lon":   to_coords.get("lon"),
        }
        route_legs.append(leg)

    return jsonify({
        "total_cost": total_cost,
        "total_time": total_time,
        "travel_date": travel_date or None,
        "route_legs": route_legs,
    })


# ── Dev server ────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
