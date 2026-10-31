"""
dijkstra_engine.py
──────────────────
Multi-criteria Dijkstra's algorithm for the Time-Expanded Train Routing Graph.

Supports three routing preferences:
  cheapest  – minimise total fare
  fastest   – minimise total travel time
  balanced  – minimise fare + (time × ₹150/hr) combined weight

The engine also honours train running days (weekday filter) when a
travel_date is supplied.
"""

import heapq
from datetime import date
from itertools import count
from typing import Dict, List, Optional, Tuple

from .graph_models import Edge, RouteGraph, StateNode


def find_optimal_route(
    graph: RouteGraph,
    start_station: str,
    end_station: str,
    preference: str = "cheapest",
    class_filter: Optional[str] = None,
    travel_date: Optional[str] = None,
) -> Tuple[float, float, List[Edge]]:
    """
    Run multi-criteria Dijkstra's algorithm on `graph`.

    Parameters
    ──────────
    graph         : A populated RouteGraph instance.
    start_station : Station code of the origin  (e.g. 'STN_A').
    end_station   : Station code of destination (e.g. 'STN_C').
    preference    : 'cheapest' | 'fastest' | 'balanced'
    class_filter  : Optional IRCTC class code (e.g. 'SL', '3A'). Only IN_TRAIN
                    edges matching this class are considered.
    travel_date   : ISO date string ('YYYY-MM-DD'). IN_TRAIN edges whose train
                    does not run on that weekday are skipped.

    Returns
    ───────
    (total_cost, total_time, path) where:
        total_cost – actual fare accumulated along the chosen path (₹).
        total_time – actual travel time accumulated along the chosen path (hrs).
        path       – ordered list of Edge objects traversed.

    Returns (float('inf'), float('inf'), []) if no route exists.
    """

    # Resolve the weekday (0=Mon … 6=Sun) once, if a date was supplied.
    target_weekday: Optional[int] = None
    if travel_date:
        try:
            target_weekday = date.fromisoformat(travel_date).weekday()
        except ValueError:
            target_weekday = None

    tiebreaker = count()

    # Priority queue:
    # (accumulated_weight, tie_break, current_node, path_history, total_cost, total_time)
    pq: List[Tuple] = []

    # Seed the queue with every departure node at the start station
    for node in graph.adjacency_list.keys():
        if node.station_code == start_station:
            heapq.heappush(pq, (0.0, next(tiebreaker), node, [], 0.0, 0.0))

    # best known accumulated_weight to reach each StateNode
    best_weight_to_node: Dict[StateNode, float] = {}

    while pq:
        current_weight, _, current_node, path_history, total_cost, total_time = heapq.heappop(pq)

        # ── Goal check ────────────────────────────────────────────────────
        if current_node.station_code == end_station:
            return total_cost, total_time, path_history

        # ── Stale entry (already found a lower-weight path to this node) ──
        if current_weight > best_weight_to_node.get(current_node, float("inf")):
            continue

        best_weight_to_node[current_node] = current_weight

        # ── Relax outgoing edges ──────────────────────────────────────────
        for edge in graph.adjacency_list.get(current_node, []):
            # Skip IN_TRAIN edges that don't match the requested travel class
            if class_filter is not None and edge.edge_type == "IN_TRAIN":
                if edge.classCode != class_filter:
                    continue

            # Skip IN_TRAIN edges whose train does not run on the selected weekday
            if target_weekday is not None and edge.edge_type == "IN_TRAIN":
                if edge.runs_on is not None and target_weekday not in edge.runs_on:
                    continue

            # Compute edge_weight based on user preference
            if preference == "cheapest":
                edge_weight = edge.cost
            elif preference == "fastest":
                edge_weight = edge.time_taken
            else:  # balanced: value 1 hour of travel at ₹150
                edge_weight = edge.cost + (edge.time_taken * 150)

            new_weight = current_weight + edge_weight
            new_cost   = total_cost   + edge.cost
            new_time   = total_time   + edge.time_taken

            if new_weight < best_weight_to_node.get(edge.target, float("inf")):
                heapq.heappush(
                    pq,
                    (
                        new_weight,
                        next(tiebreaker),
                        edge.target,
                        path_history + [edge],
                        new_cost,
                        new_time,
                    ),
                )

    # No path found
    return float("inf"), float("inf"), []


# ── Smoke-test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import os

    # Allow running directly from the backend/ directory
    sys.path.insert(0, os.path.dirname(__file__))

    from mock_data import build_test_graph

    graph = build_test_graph()

    for pref in ("cheapest", "fastest", "balanced"):
        total_cost, total_time, path = find_optimal_route(graph, "STN_A", "STN_C", preference=pref)

        print(f"\n{'─' * 50}")
        print(f"Preference : {pref.upper()}   STN_A → STN_C")

        if total_cost == float("inf"):
            print("  No route found.")
            continue

        print(f"Total cost : ₹{total_cost:.2f}")
        print(f"Total time : {total_time:.2f}h")
        print(f"Edges      : {len(path)}")
        print()
        for i, edge in enumerate(path, 1):
            print(
                f"  Step {i}: [{edge.edge_type}]  "
                f"{edge.source.station_code}@{edge.source.time:.2f}h "
                f"({edge.source.train_id}) → "
                f"{edge.target.station_code}@{edge.target.time:.2f}h "
                f"({edge.target.train_id})  "
                f"cost=₹{edge.cost:.2f}  time={edge.time_taken:.2f}h"
            )
