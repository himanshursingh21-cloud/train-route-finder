"""
backend/app.py
──────────────
Flask REST API for the Cheapest Train App.

Endpoints
---------
GET /api/route
    Find the optimal train route between two stations across all preferences.
    Query params: source, destination, preference (cheapest|fastest|balanced)
    Returns: total_cost, total_time, route_legs (with per-leg coordinates)
    Also returns direct-train baseline and "savings vs direct" calculations.

GET /api/stations
    Return all stations that are reachable within the loaded graph.
    Returns: list of {code, name, lat, lon}

GET /health
    Return {"status": "ok"}
"""

from __future__ import annotations

from pathlib import Path

import functools
import pandas as pd
import time
from flask import Flask, jsonify, request
from flask_cors import CORS

from .data_loader import build_graph_from_parquet

# ── Flask setup ───────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# ── Project paths ─────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).parent.parent
_STATIONS_PATH = _PROJECT_ROOT / "data" / "stations.parquet"
_TRAINS_PATH = _PROJECT_ROOT / "data" / "trains.csv"

# ── One-time startup: build graph and load station metadata ───────────────────
print("Loading graph from parquet files … (this may take a few seconds)")
graph = build_graph_from_parquet()

in_train = sum(1 for es in graph.adjacency_list.values() for e in es if e.edge_type == "IN_TRAIN")
transfers = sum(1 for es in graph.adjacency_list.values() for e in es if e.edge_type == "TRANSFER")
print(f"  Graph ready — {len(graph.adjacency_list):,} source nodes, "
      f"{in_train:,} IN_TRAIN, {transfers:,} TRANSFER edges")

# ── Station metadata from graph nodes only (not raw parquet) ──────────────────
# Build coordinate lookup from the graph's StateNode attributes.
# StateNode stores station_code, time, and train_id — coordinates are derived
# from the original parquet data but only for graph-routable stations.
# This ensures /api/stations only shows active stations from the loaded graph.

# Re-load parquet but filter to graph-routable codes only (same as original behavior,
# but the filtering logic is explicit and graph-centered)
from .data_loader import build_graph_from_parquet as _build_graph_from_parquet
_raw_stations = pd.read_parquet(_STATIONS_PATH)
_raw_stations = _raw_stations.dropna(subset=["lat", "lon"])
_routable_codes_set: set[str] = {
    node.station_code for node in graph.adjacency_list.keys()
}
# Keep only stations that exist in the graph
_routable_stations = _raw_stations[_raw_stations["station_code"].isin(_routable_codes_set)].reset_index(drop=True)

# Fast coordinate lookup: station_code → {lat, lon, name}
_coord_lookup: dict[str, dict] = {
    row.station_code: {"lat": row.lat, "lon": row.lon, "name": row.name}
    for row in _routable_stations.itertuples(index=False)
}

# Train lookup from CSV
_trains_df = pd.read_csv(_TRAINS_PATH, dtype={"number": str, "name": str})
_trains_df = _trains_df.dropna(subset=["number", "name"])
_train_lookup: dict[str, dict] = {
    row.number: {
        "name": row.name,
        "from_station_code": row.from_station_code,
        "from_station_name": row.from_station_name,
        "to_station_code": row.to_station_code,
        "to_station_name": row.to_station_name,
    }
    for row in _trains_df.itertuples(index=False)
}


# ── Helper ────────────────────────────────────────────────────────────────────
def _get_coords(station_code: str) -> dict:
    """Return {lat, lon, name} for a station from the graph's coordinate lookup,
    or empty dict if unavailable. This uses the graph-derived lookup."""
    return _coord_lookup.get(station_code, {})


# Cached routing helper
@functools.lru_cache(maxsize=128)
def _find_optimal_route_cached(
    start_station: str,
    end_station: str,
    preference: str,
    travel_class: str | None,
    travel_date: str | None,
    max_layovers: int | None,
) -> tuple[float, float, list]:
    """Run Dijkstra with caching for identical searches.

    Note: In this implementation the graph is static (built once at startup),
    so lru_cache keys based on parameters effectively cache repeated queries.
    The cache key includes all variables that affect the routing result.
    """
    from .dijkstra_engine import find_optimal_route
    return find_optimal_route(
        graph, start_station, end_station, preference,
        class_filter=travel_class,
        travel_date=travel_date or None,
        max_layovers=max_layovers,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/api/health', methods=['GET'])
def get_health():
    """Return health status.

    Response schema
    ---------------
    { "status": "ok" }
    """
    return jsonify({"status": "ok"})


@app.route('/api/stations', methods=['GET'])
def get_stations():
    """Return all routable stations sourced from loaded graph nodes.

    Response schema
    ---------------
    [
      { "code": "NDLS", "name": "New Delhi", "lat": 28.64, "lon": 77.22 },
      ...
    ]
    Only stations that exist in the graph are included — no stations from
    the raw parquet file that are not routable.
    """
    station_list = []
    for node in graph.adjacency_list.keys():
        code = node.station_code
        # Use any coordinate info we have from the graph
        # Since StateNode may not always have lat/lon stored directly,
        # we derive from _coord_lookup which was built from graph-provenance data
        coord_info = _coord_lookup.get(code, {})
        station_list.append({
            "code": code,
            "name": coord_info.get("name", code),
            "lat": coord_info.get("lat"),
            "lon": coord_info.get("lon"),
        })
    # Filter out any entries with missing essential data
    station_list = [s for s in station_list if s["code"] and s["lat"] is not None and s["lon"] is not None]
    return jsonify(station_list)


@app.route('/api/route', methods=['GET'])
def get_route():
    """Find optimal train routes between two stations across all preferences.

    Query Parameters
    ----------------
    source      : str  – departure station code (e.g. 'NDLS')
    destination : str  – arrival station code   (e.g. 'BCT')

    Response schema
    ---------------
    {
      "cheapest": {total_cost, total_time, route_legs, direct_baseline, savings_vs_direct},
      "fastest":  {total_cost, total_time, route_legs, direct_baseline, savings_vs_direct},
      "balanced": {total_cost, total_time, route_legs, direct_baseline, savings_vs_direct},
      "direct_baseline": {...} | null
    }

    The endpoint runs Dijkstra independently for 'cheapest', 'fastest', and 'balanced'
    preferences. If a direct train exists between source and destination, the direct
    baseline is included along with "savings vs direct" calculations for each preference.
    """
    source = request.args.get('source', '').strip()
    destination = request.args.get('destination', '').strip()

    # ── Validation ────────────────────────────────────────────────────────────
    if not source or not destination:
        return jsonify({"error": "Missing required query parameters: source and destination"}), 400

    if source == destination:
        return jsonify({"error": "Source and destination must be different"}), 400

    # Validate that source and destination exist in the graph
    graph_node_codes = {node.station_code for node in graph.adjacency_list.keys()}
    if source not in graph_node_codes:
        return jsonify({"error": f"Source station '{source}' not found in graph"}), 400
    if destination not in graph_node_codes:
        return jsonify({"error": f"Destination station '{destination}' not found in graph"}), 400

    # ── Find direct train baseline (once, shared across all preferences) ──────
    direct_edge = None
    for node, edges in graph.adjacency_list.items():
        if node.station_code != source:
            continue
        for edge in edges:
            if edge.edge_type != "IN_TRAIN":
                continue
            if edge.target.station_code != destination:
                continue
            direct_edge = edge
            break
        if direct_edge:
            break

    # ── Read optional max_layovers from request ─────────────────────────────
    max_layovers = request.args.get('max_layovers', None)

    # ── Route computation ───────────────────────────────────────────────────────
    try:
        total_cost, total_time, path = _find_optimal_route_cached(
            source, destination, pref, travel_class=None, travel_date=None,
            max_layovers=max_layovers
        )
    except Exception as e:
        import logging
        logging.exception("Route computation failed")
        return jsonify({"error": "Internal error while searching"}), 500

    if total_cost == float('inf'):
        results[pref] = {"error": f"No route found from '{source}' to '{destination}'"}
        continue

        # Compute savings vs direct train if a direct exists
        savings_vs_direct = None
        if direct_edge is not None:
            # Determine the direct train weight for the same preference
            if pref == "cheapest":
                direct_weight = direct_edge.cost
            elif pref == "fastest":
                direct_weight = direct_edge.time_taken  # minutes
            else:  # balanced
                direct_weight = direct_edge.cost + (150 / 60.0) * direct_edge.time_taken

            # Savings = direct weight - routed weight
            if direct_weight is not None:
                if pref == "cheapest":
                    savings = direct_weight - total_cost
                elif pref == "fastest":
                    # direct_weight is in minutes, total_time is in hours
                    savings = direct_weight - (total_time * 60.0)
                else:  # balanced
                    savings = direct_weight - total_cost

                savings_vs_direct = round(savings, 2) if savings > 0 else 0

        # Serialize route legs
        route_legs = []
        for edge in path:
            from_coords = _get_coords(edge.source.station_code)
            to_coords = _get_coords(edge.target.station_code)

            train_id = edge.source.train_id or ""
            train_info = _train_lookup.get(train_id, {})
            train_number = train_info.get("name", train_id) if train_info else train_id
            train_name = train_info.get("name", train_id) if train_info else train_id

            leg = {
                "from_station": edge.source.station_code,
                "to_station":   edge.target.station_code,
                "from_station_name": from_coords.get("name", edge.source.station_code),
                "to_station_name":   to_coords.get("name", edge.target.station_code),
                "depart_time": edge.source.time,
                "arrive_time": edge.target.time,
                "cost": edge.cost,
                "edge_type": edge.edge_type,
                "classCode": edge.classCode,
                "train_number": train_number,
                "train_name": train_name,
                "fare_breakdown": edge.fare_breakdown,
                "availability": edge.availability,
                "from_lat": from_coords.get("lat"),
                "from_lon": from_coords.get("lon"),
                "to_lat": to_coords.get("lat"),
                "to_lon": to_coords.get("lon"),
            }
            route_legs.append(leg)

        results[pref] = {
            "total_cost": total_cost,
            "total_time": total_time,
            "route_legs": route_legs,
            "direct_baseline": direct_edge is not None,
            "savings_vs_direct": savings_vs_direct,
        }

    # Build direct baseline object (shared across all preferences)
    direct_baseline = None
    if direct_edge is not None:
        from_coords = _get_coords(direct_edge.source.station_code)
        to_coords = _get_coords(direct_edge.target.station_code)

        train_id = direct_edge.source.train_id or ""
        train_info = _train_lookup.get(train_id, {})
        train_number = train_info.get("name", train_id) if train_info else train_id
        train_name = train_info.get("name", train_id) if train_info else train_id

        direct_baseline = {
            "from_station": direct_edge.source.station_code,
            "to_station":   direct_edge.target.station_code,
            "from_station_name": from_coords.get("name", direct_edge.source.station_code),
            "to_station_name":   to_coords.get("name", direct_edge.target.station_code),
            "depart_time": direct_edge.source.time,
            "arrive_time": direct_edge.target.time,
            "cost": direct_edge.cost,
            "edge_type": direct_edge.edge_type,
            "classCode": direct_edge.classCode,
            "train_number": train_number,
            "train_name": train_name,
            "fare_breakdown": direct_edge.fare_breakdown,
            "availability": direct_edge.availability,
            "from_lat": from_coords.get("lat"),
            "from_lon": from_coords.get("lon"),
            "to_lat": to_coords.get("lat"),
            "to_lon": to_coords.get("lon"),
            "direct_baseline": True,
        }

    return jsonify({
        "cheapest": results.get("cheapest", {}),
        "fastest": results.get("fastest", {}),
        "balanced": results.get("balanced", {}),
        "direct_baseline": direct_baseline,
    })


# ── Dev server ────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)