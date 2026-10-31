from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List, Dict


@dataclass(frozen=True, eq=True)
class StateNode:
    """A node in the time‑expanded train routing graph.

    Attributes
    ----------
    station_code: str
        Identifier of the station (e.g., "STN_A").
    time: float
        Time expressed as decimal hours (e.g., 9.5 for 09:30).
    train_id: Optional[str]
        ``None`` when the node represents a platform (standing still).
        Otherwise the identifier of the train the passenger is on.
    """
    station_code: str
    time: float
    train_id: Optional[str] = None

    def __hash__(self) -> int:
        # Frozen dataclass already provides a hash, but we keep this explicit for clarity.
        return hash((self.station_code, self.time, self.train_id))


@dataclass(frozen=True, eq=True)
class Edge:
    """An edge connecting two ``StateNode`` instances.

    edge_type is either ``'IN_TRAIN'`` for regular travel legs or ``'TRANSFER'`` for a
    platform transfer/layover.
    """
    source: StateNode
    target: StateNode
    cost: float
    time_taken: float
    edge_type: str  # Expected values: 'IN_TRAIN' or 'TRANSFER'
    classCode: Optional[str] = None  # Fare class for IN_TRAIN edges; None for transfers
    fare_breakdown: Optional[dict] = None  # {baseFare, reservationCharge, superfastCharge, serviceTax, totalFare}
    availability: Optional[str] = None  # e.g. 'AVAILABLE-0008' (historical sample data)
    runs_on: Optional[frozenset] = None  # Weekdays (0=Mon … 6=Sun) the train runs on

    def __post_init__(self):
        if self.edge_type not in {"IN_TRAIN", "TRANSFER"}:
            raise ValueError(f"Invalid edge_type: {self.edge_type}. Must be 'IN_TRAIN' or 'TRANSFER'.")


class RouteGraph:
    """Container for the time‑expanded graph.

    The graph is stored as an adjacency list mapping a ``StateNode`` to a list of
    outgoing ``Edge`` objects.
    """

    def __init__(self) -> None:
        self.adjacency_list: Dict[StateNode, List[Edge]] = {}

    def add_edge(self, edge: Edge) -> None:
        """Add *edge* to the adjacency list.

        Parameters
        ----------
        edge: Edge
            The edge to insert. The source node is used as the key.
        """
        self.adjacency_list.setdefault(edge.source, []).append(edge)

    def add_transfer_edge(
        self,
        source_node: StateNode,
        target_node: StateNode,
        min_buffer: float = 0.33,
    ) -> None:
        """Create a transfer edge between two platform nodes.

        The method validates that both nodes belong to the same station and that the
        time difference respects the *min_buffer* (default 0.33 h ≈ 20 min). If the
        validation fails a ``ValueError`` is raised.
        """
        if source_node.station_code != target_node.station_code:
            raise ValueError(
                "Transfer nodes must share the same station_code."
            )
        time_gap = target_node.time - source_node.time
        if time_gap < min_buffer:
            raise ValueError(
                f"Insufficient buffer for transfer: {time_gap:.2f}h < {min_buffer:.2f}h"
            )
        transfer_edge = Edge(
            source=source_node,
            target=target_node,
            cost=0.0,
            time_taken=time_gap,
            edge_type="TRANSFER",
        )
        self.add_edge(transfer_edge)
