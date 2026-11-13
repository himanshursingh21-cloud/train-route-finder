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
import time
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
    max_layovers: Optional[int] = None,
) -> Tuple[float, float, List[Edge]]:
    """
    Run multi-criteria Dijkstra's algorithm on `graph`.

    Uses a min-heap (heapq) for O(E log V) performance.
    Path history is NOT stored in the PQ — instead a predecessor map
    is maintained for efficient path reconstruction at the end.

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
    max_layovers  : Optional[int]. Maximum number of TRANSFER edges allowed
                    in the path. Paths exceeding this limit are pruned.
                    Set to None (default) for no limit.

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

    # ── Search circuit breaker ──────────────────────────────────────────
    # Set a start time and check every 1000 iterations to prevent infinite loops
    start_time = time.time()
    iter_count = 0

    # Priority queue: only (accumulated_weight, tie_break, current_node)
    # NO path history in the PQ — keeps heap operations O(log V).
    pq: List[Tuple] = []

    # Seed the queue with every departure node at the start station
    for node in graph.adjacency_list.keys():
        if node.station_code == start_station:
            heapq.heappush(pq, (0.0, next(tiebreaker), node))

    # best known accumulated_weight to reach each (StateNode, transfers_used) state
    best_weight_to_state: Dict[Tuple[StateNode, int], float] = {}

    # Predecessor map: (StateNode, transfers_used) → the Edge that led to this state
    predecessor: Dict[Tuple[StateNode, int], Optional[Edge]] = {}

    # accumulated_cost[node] and accumulated_time[node] store the totals
    # at the node that is the *target* of the predecessor edge.
    # For the start node, initialise to 0.
    # We locate the StateNode object whose code matches start_station.
    start_node: Optional[StateNode] = None
    for node in graph.adjacency_list.keys():
        if node.station_code == start_station:
            start_node = node
            break
    assert start_node is not None, f"Start station {start_station} not found in graph"
    accumulated_cost: Dict[StateNode, float] = {start_node: 0.0}
    accumulated_time: Dict[StateNode, int] = {start_node: 0}

    # Layover count: for each start node, initialise with 0 transfers.
    # We track layover_count per start node since different starts are independent.
    layover_count: Dict[StateNode, int] = {}
    for node in graph.adjacency_list.keys():
        if node.station_code == start_station:
            layover_count[node] = 0

    while pq:
        current_weight, _, current_node = heapq.heappop(pq)
        transfers = layover_count.get(current_node, 0)

        # ── Search circuit breaker ──────────────────────────────────────
        iter_count += 1
        if iter_count % 1000 == 0 and time.time() - start_time > 8.0:
            raise TimeoutError("Route calculation exceeded maximum search time.")

        # ── Skip stale heap entries for (node, transfers) ──
        if current_weight > best_weight_to_state.get((current_node, transfers), float("inf")):
            continue

        best_weight_to_state[(current_node, transfers)] = current_weight

        # ── Goal check ────────────────────────────────────────────────────
        if current_node.station_code == end_station:
            # Reconstruct path by walking backwards via predecessor map
            path: List[Edge] = []
            node: Optional[StateNode] = current_node
            while node is not None and node in predecessor:
                edge = predecessor[(node, layover_count.get(node, 0))]
                if edge is None:
                    break
                path.insert(0, edge)
                # Move to the source of this edge to continue walking backwards
                node = edge.source  # type: ignore[union-attr]
            return accumulated_cost[current_node], accumulated_time[current_node] / 60.0, path

        # ── Relax outgoing edges ──────────────────────────────────────────
        for edge in graph.adjacency_list.get(current_node, []):
            # Skip IN_TRAIN edges that don't match the requested travel class
            if class_filter is None and edge.edge_type == "IN_TRAIN":
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
                edge_weight = edge.time_taken  # integer minutes — simple addition
            else:  # balanced: value 1 hour of travel at ₹150
                edge_weight = edge.cost + (150 / 60.0) * edge.time_taken

            new_weight = current_weight + edge_weight
            new_cost   = accumulated_cost[current_node] + edge.cost
            new_time   = accumulated_time[current_node] + edge.time_taken  # integer minute accumulation
            new_layovers = layover_count.get(current_node, 0) + (1 if edge.edge_type == "TRANSFER" else 0)

            # ── Strict Layover Counting ────────────────────────────────────
            # Enforce max_layovers inside Dijkstra state traversal using state = (node, transfers_used).
            # Count only real train changes on TRANSFER edges. Skip if exceeded.
            if max_layovers is not None and new_layovers > max_layovers:
                continue

            new_key = (edge.target, new_layovers)
            if new_weight < best_weight_to_state.get(new_key, float("inf")):
                heapq.heappush(
                    pq,
                    (
                        new_weight,
                        next(tiebreaker),
                        edge.target,
                    ),
                )
                # Store predecessor and accumulated values for path reconstruction
                predecessor[(edge.target, new_layovers)] = edge
                accumulated_cost[edge.target] = new_cost
                accumulated_time[edge.target] = new_time
                layover_count[edge.target] = new_layovers

    # No path found
    return float("inf"), float("inf"), []