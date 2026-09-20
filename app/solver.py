"""Difference-constraints engine for distributed clock diagnostics.

Observation semantics
---------------------
An observation ``(id, a, b, min, max)`` states that the clock difference
``clock(b) - clock(a)`` lies in the inclusive interval ``[min, max]``
milliseconds.  This is equivalent to the two difference constraints

    clock(b) - clock(a) <= max
    clock(a) - clock(b) <= -min

which become directed edges in a constraint graph:

    a -> b, weight = max        (shortest path relaxes on a -> b)
    b -> a, weight = -min

A system of difference constraints is feasible iff this graph has no
negative-weight cycle.  A negative cycle's total weight is strictly
negative: walking the chain of inequalities around it yields
``0 <= total < 0``, a contradiction.

Tightest reference-relative bounds
----------------------------------
For a reference node ``r`` fixed at clock offset 0,

    tightest upper bound of clock(v) = dist(r, v)
    tightest lower bound of clock(v) = -dist(v, r)

when the corresponding paths exist.  A node that is reachable from r in
only one direction is constrained on one side only; a node reachable in
neither direction is completely free relative to the reference.

All numeric inputs are in milliseconds and handled as floats.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

# Tolerance (milliseconds) used both for relaxations and for deciding that a
# cycle is genuinely contradictory.  Smaller residual differences are treated
# as numeric noise.
EPS = 1e-9


class ConstraintError(ValueError):
    """Raised for malformed diagnostic requests (HTTP 400)."""


@dataclass(frozen=True)
class Edge:
    u: str
    v: str
    w: float
    obs_id: str
    side: str  # "upper": x_v - x_u <= w ; "lower": counterpart of a lower bound
    orig_a: str = ""  # original observation direction and interval, for reports
    orig_b: str = ""
    lo: float = 0.0
    hi: float = 0.0

    def inequality(self) -> str:
        if self.side == "upper":
            return f"x({self.v}) - x({self.u}) <= {fmt_num(self.w)}"
        return f"x({self.u}) - x({self.v}) <= {fmt_num(self.w)}"


def fmt_num(value: float) -> float:
    """Round for JSON output and normalize negative zero."""
    r = round(float(value), 9)
    if r == 0:
        r = 0.0
    return r


def _check_finite(name: str, value: float) -> None:
    if not math.isfinite(value):
        raise ConstraintError(f"observation '{name}': min/max must be finite numbers")


def build_edges(observations: Iterable[dict]) -> tuple[list[Edge], list[str]]:
    edges: list[Edge] = []
    seen_ids: set[str] = set()
    for obs in observations:
        obs_id = obs["id"]
        if obs_id in seen_ids:
            raise ConstraintError(f"duplicate observation id: '{obs_id}'")
        seen_ids.add(obs_id)

        a, b = obs["a"], obs["b"]
        lo, hi = float(obs["min"]), float(obs["max"])
        _check_finite(obs_id, lo)
        _check_finite(obs_id, hi)
        if lo > hi + EPS:
            raise ConstraintError(
                f"observation '{obs_id}': min ({lo}) must be <= max ({hi})"
            )

        # x_b - x_a <= max
        edges.append(Edge(a, b, hi, obs_id, "upper", a, b, lo, hi))
        # x_a - x_b <= -min
        edges.append(Edge(b, a, -lo, obs_id, "lower", a, b, lo, hi))

    # Stable edge order: same observation id keeps its two edges together.
    edges.sort(key=lambda e: (e.obs_id, 0 if e.side == "upper" else 1, e.u, e.v))
    return edges, list(seen_ids)


def _extract_cycle(edges: list[Edge], nodes: list[str]) -> list[Edge]:
    """Locate a negative cycle via a full Bellman-Ford run.

    Runs ``n`` relaxation passes and follows predecessor pointers from the
    last relaxing edge for ``n`` steps, which is guaranteed to land on a
    vertex of the offending cycle.
    """
    n = len(nodes)
    index = {name: i for i, name in enumerate(nodes)}
    dist = [0.0] * n
    pred: list[int | None] = [None] * n

    last_relax = -1
    for _ in range(n):
        last_relax = -1
        for ei, e in enumerate(edges):
            nd = dist[index[e.u]] + e.w
            if nd < dist[index[e.v]] - EPS:
                dist[index[e.v]] = nd
                pred[index[e.v]] = ei
                last_relax = ei

    if last_relax < 0:
        return []

    # Walk n steps back to guarantee being on the cycle.
    v = edges[last_relax].v
    for _ in range(n):
        ei = pred[index[v]]
        if ei is None:
            break
        v = edges[ei].u

    # Collect the cycle edges in predecessor (backward) order.
    cycle_ei: list[int] = []
    start = v
    for _ in range(n + 1):
        ei = pred[index[v]]
        if ei is None:  # defensive: predecessor must stay on the cycle
            break
        cycle_ei.append(ei)
        v = edges[ei].u
        if v == start:
            break

    # Backward collection -> reverse to get forward constraint order.
    cycle_edges = [edges[i] for i in reversed(cycle_ei)]

    # Verify the chain closes head-to-tail; the n-step argument guarantees a
    # cycle, this only guards against numeric or bookkeeping noise.
    if len(cycle_edges) < 1:
        return []
    closed = all(
        cycle_edges[i].v == cycle_edges[i + 1].u
        for i in range(len(cycle_edges) - 1)
    ) and cycle_edges[-1].v == cycle_edges[0].u
    if not closed:
        return []
    return cycle_edges


def _shortest_from(source: str, edges: list[Edge], nodes: list[str]) -> list[float]:
    """Single-source shortest distances; ``inf`` where unreachable."""
    n = len(nodes)
    index = {name: i for i, name in enumerate(nodes)}
    inf = math.inf
    dist = [inf] * n
    dist[index[source]] = 0.0

    for _ in range(n - 1):
        changed = False
        for e in edges:
            du = dist[index[e.u]]
            if du == inf:
                continue
            nd = du + e.w
            if nd < dist[index[e.v]] - EPS:
                dist[index[e.v]] = nd
                changed = True
        if not changed:
            break
    return dist


def diagnose(
    observations: Iterable[dict],
    reference: str,
    nodes: Iterable[str] | None = None,
) -> dict:
    """Run a complete diagnostic.

    :param observations: dicts with keys id, a, b, min, max (milliseconds).
    :param reference: reference node; its clock offset is fixed at 0.
    :param nodes: optional explicit node universe.  Endpoints that appear in
        observations are always included automatically.
    :returns: a result dict.  ``status`` is ``"feasible"`` or
        ``"infeasible"``.  Raises :class:`ConstraintError` on bad input.
    """
    observations = list(observations)

    node_set: set[str] = set()
    if nodes is not None:
        nodes = list(nodes)
        if any(name == "" for name in nodes):
            raise ConstraintError("node ids must be non-empty strings")
        if len(nodes) != len(set(nodes)):
            raise ConstraintError("duplicate node id in 'nodes'")
        node_set.update(nodes)

    edges, _ids = build_edges(observations)
    for e in edges:
        node_set.add(e.u)
        node_set.add(e.v)

    if not isinstance(reference, str) or reference == "":
        raise ConstraintError("reference must be a non-empty node id")
    if reference not in node_set:
        raise ConstraintError(
            f"reference node '{reference}' does not appear in nodes or observations"
        )

    all_nodes = sorted(node_set)

    # --- Feasibility: negative cycle detection -----------------------------
    cycle = _extract_cycle(edges, all_nodes)
    if cycle:
        # Canonical start: smallest (observation id, side) for determinism.
        rot_keys = [(e.obs_id, 0 if e.side == "upper" else 1) for e in cycle]
        start_idx = min(range(len(cycle)), key=lambda i: rot_keys[i])
        cycle = cycle[start_idx:] + cycle[:start_idx]
        total = sum(e.w for e in cycle)

        entries = []
        running = 0.0
        for e in cycle:
            running += e.w
            entries.append(
                {
                    "observation_id": e.obs_id,
                    "observation": {
                        "from": e.orig_a,
                        "to": e.orig_b,
                        "min": fmt_num(e.lo),
                        "max": fmt_num(e.hi),
                    },
                    "traversed_edge": f"{e.u} -> {e.v}",
                    "weight": fmt_num(e.w),
                    "inequality": e.inequality(),
                    "running_sum": fmt_num(running),
                }
            )

        return {
            "status": "infeasible",
            "reference": reference,
            "feasible": False,
            "cycle": entries,
            "cycle_observation_ids": [e["observation_id"] for e in entries],
            "accumulated_bound": fmt_num(total),
            "contradiction": f"0 <= {fmt_num(total)} < 0",
            "interpretation": (
                "Chaining the observations around this directed cycle forces "
                "0 ms <= accumulated bound < 0 ms, so no consistent clock "
                "offsets exist."
            ),
            "node_count": len(all_nodes),
            "observation_count": len(observations),
        }

    # --- Feasible: tightest bounds relative to the reference ----------------
    fwd = _shortest_from(reference, edges, all_nodes)  # dist(r, v)
    rev_edges = [Edge(e.v, e.u, e.w, e.obs_id, e.side) for e in edges]
    rev = _shortest_from(reference, rev_edges, all_nodes)  # dist(v, r)

    index = {name: i for i, name in enumerate(all_nodes)}
    node_results = []
    unconstrained = []
    for name in all_nodes:
        i = index[name]
        d_ru = fwd[i]
        d_vr = rev[i]
        reachable_out = math.isfinite(d_ru)
        reachable_in = math.isfinite(d_vr)

        if reachable_out and reachable_in:
            upper = d_ru
            lower = -d_vr
            relation = "bounded"
        elif reachable_out:  # x_v <= dist(r, v), no lower side
            upper = d_ru
            lower = -math.inf
            relation = "upper_only"
        elif reachable_in:  # x_v >= -dist(v, r), no upper side
            upper = math.inf
            lower = -d_vr
            relation = "lower_only"
        else:
            upper = math.inf
            lower = -math.inf
            relation = "unconstrained"

        item = {
            "node": name,
            "relation": relation,
            "reachable_from_reference": reachable_out,
            "can_reach_reference": reachable_in,
            "lower_bound": None if lower == -math.inf else fmt_num(lower),
            "upper_bound": None if upper == math.inf else fmt_num(upper),
            "is_reference": name == reference,
        }
        node_results.append(item)
        if relation == "unconstrained":
            unconstrained.append(name)

    return {
        "status": "feasible",
        "reference": reference,
        "feasible": True,
        "nodes": node_results,
        "unconstrained_nodes": unconstrained,
        "node_count": len(all_nodes),
        "observation_count": len(observations),
    }
