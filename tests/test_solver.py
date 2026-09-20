"""Solver-level tests for the difference-constraints engine."""

import pytest

from app.solver import ConstraintError, diagnose


def test_feasible_chain_tightest_bounds():
    # B-A in [1,3], C-B in [2,5] => C-A in [3,8] (tightest).
    res = diagnose(
        observations=[
            {"id": "o1", "a": "A", "b": "B", "min": 1, "max": 3},
            {"id": "o2", "a": "B", "b": "C", "min": 2, "max": 5},
        ],
        reference="A",
    )
    assert res["status"] == "feasible"
    by_node = {n["node"]: n for n in res["nodes"]}

    assert by_node["A"]["lower_bound"] == 0.0
    assert by_node["A"]["upper_bound"] == 0.0
    assert by_node["B"]["lower_bound"] == 1.0
    assert by_node["B"]["upper_bound"] == 3.0
    assert by_node["C"]["lower_bound"] == 3.0
    assert by_node["C"]["upper_bound"] == 8.0
    assert all(n["relation"] == "bounded" for n in res["nodes"])
    assert res["unconstrained_nodes"] == []
    # stable node ordering
    assert [n["node"] for n in res["nodes"]] == ["A", "B", "C"]


def test_connected_node_is_bounded_on_both_sides():
    # Each observation adds two opposite edges, so any node connected to the
    # reference is reachable in both directions and bounded on both sides.
    res = diagnose(
        observations=[{"id": "o1", "a": "A", "b": "B", "min": 0, "max": 5}],
        reference="A",
        nodes=["A", "B", "C"],
    )
    by_node = {n["node"]: n for n in res["nodes"]}

    assert by_node["B"]["relation"] == "bounded"
    assert by_node["B"]["upper_bound"] == 5.0
    assert by_node["B"]["lower_bound"] == 0.0

    assert by_node["C"]["relation"] == "unconstrained"
    assert by_node["C"]["lower_bound"] is None
    assert by_node["C"]["upper_bound"] is None
    assert res["unconstrained_nodes"] == ["C"]


def test_reverse_direction_bounds():
    # Observation stored B->A: A-B in [1,4] => B-A in [-4,-1].
    res = diagnose(
        observations=[{"id": "o1", "a": "B", "b": "A", "min": 1, "max": 4}],
        reference="A",
    )
    by_node = {n["node"]: n for n in res["nodes"]}
    assert by_node["B"]["relation"] == "bounded"
    assert by_node["B"]["lower_bound"] == -4.0
    assert by_node["B"]["upper_bound"] == -1.0


def test_negative_self_loop_is_infeasible():
    res = diagnose(
        observations=[{"id": "s1", "a": "A", "b": "A", "min": 1, "max": 2}],
        reference="A",
    )
    assert res["status"] == "infeasible"
    assert res["cycle_observation_ids"] == ["s1"]
    # 0 <= x_A - x_A, and min=1 forces 0 <= ... <= -1: weight -1
    assert res["accumulated_bound"] == -1.0
    assert res["cycle"][0]["running_sum"] == -1.0


def test_contradictory_cycle_ids_and_running_sums():
    res = diagnose(
        observations=[
            {"id": "o3", "a": "C", "b": "A", "min": 0, "max": 5},
            {"id": "o1", "a": "A", "b": "B", "min": 10, "max": 20},
            {"id": "o2", "a": "B", "b": "C", "min": 10, "max": 20},
        ],
        reference="A",
    )
    assert res["status"] == "infeasible"
    # Canonical rotation starts at the smallest (observation id, side).
    # The closed constraint-edge chain is B->A (o1 lower), A->C (o3 lower),
    # C->B (o2 lower); weights -10, 0, -10, total -20.
    assert res["cycle_observation_ids"] == ["o1", "o3", "o2"]
    assert [e["running_sum"] for e in res["cycle"]] == [-10.0, -10.0, -20.0]
    assert res["accumulated_bound"] == -20.0
    # The traversed edges form a closed head-to-tail chain.
    for prev, nxt in zip(res["cycle"], res["cycle"][1:]):
        assert prev["traversed_edge"].split(" -> ")[1] == nxt[
            "traversed_edge"
        ].split(" -> ")[0]
    last = res["cycle"][-1]["traversed_edge"].split(" -> ")[1]
    first = res["cycle"][0]["traversed_edge"].split(" -> ")[0]
    assert last == first


def test_zero_width_feasible_cycle():
    # Exact clock sync around a triangle: B-A=1, C-B=1, A-C=-2 sums to zero.
    res = diagnose(
        observations=[
            {"id": "p", "a": "A", "b": "B", "min": 1, "max": 1},
            {"id": "q", "a": "B", "b": "C", "min": 1, "max": 1},
            {"id": "r", "a": "C", "b": "A", "min": -2, "max": -2},
        ],
        reference="A",
    )
    assert res["status"] == "feasible"
    by_node = {n["node"]: n for n in res["nodes"]}
    assert by_node["B"]["lower_bound"] == by_node["B"]["upper_bound"] == 1.0
    assert by_node["C"]["lower_bound"] == by_node["C"]["upper_bound"] == 2.0


def test_missing_reference_rejected():
    with pytest.raises(ConstraintError, match="reference node"):
        diagnose(
            observations=[{"id": "o", "a": "A", "b": "B", "min": 0, "max": 1}],
            reference="Z",
        )


def test_duplicate_observation_id_rejected():
    with pytest.raises(ConstraintError, match="duplicate observation id"):
        diagnose(
            observations=[
                {"id": "x", "a": "A", "b": "B", "min": 0, "max": 1},
                {"id": "x", "a": "B", "b": "C", "min": 0, "max": 1},
            ],
            reference="A",
        )


def test_inverted_interval_rejected():
    with pytest.raises(ConstraintError, match="min"):
        diagnose(
            observations=[{"id": "x", "a": "A", "b": "B", "min": 5, "max": 1}],
            reference="A",
        )


def test_result_stability_across_input_permutations():
    obs_a = [
        {"id": "o3", "a": "C", "b": "A", "min": 0, "max": 5},
        {"id": "o1", "a": "A", "b": "B", "min": 10, "max": 20},
        {"id": "o2", "a": "B", "b": "C", "min": 10, "max": 20},
    ]
    obs_b = list(reversed(obs_a))
    r1 = diagnose(observations=obs_a, reference="A")
    r2 = diagnose(observations=obs_b, reference="A")
    assert r1["cycle_observation_ids"] == r2["cycle_observation_ids"]
    assert r1["accumulated_bound"] == r2["accumulated_bound"]
