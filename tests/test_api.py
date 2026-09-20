"""HTTP-level tests for the FastAPI application."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_diagnose_feasible():
    r = client.post(
        "/api/v1/diagnose",
        json={
            "reference": "A",
            "observations": [
                {"id": "o1", "a": "A", "b": "B", "min": 1, "max": 3},
                {"id": "o2", "a": "B", "b": "C", "min": 2, "max": 5},
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "feasible"
    assert body["node_count"] == 3


def test_diagnose_infeasible():
    r = client.post(
        "/api/v1/diagnose",
        json={
            "reference": "A",
            "observations": [
                {"id": "o1", "a": "A", "b": "B", "min": 10, "max": 20},
                {"id": "o2", "a": "B", "b": "C", "min": 10, "max": 20},
                {"id": "o3", "a": "C", "b": "A", "min": 0, "max": 5},
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "infeasible"
    assert body["cycle_observation_ids"] == ["o1", "o3", "o2"]
    assert body["accumulated_bound"] == -20.0


def test_diagnose_bad_reference_is_400():
    r = client.post(
        "/api/v1/diagnose",
        json={
            "reference": "Z",
            "observations": [{"id": "o", "a": "A", "b": "B", "min": 0, "max": 1}],
        },
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_constraint_request"


def test_validation_error_is_422():
    r = client.post(
        "/api/v1/diagnose",
        json={
            "reference": "A",
            "observations": [{"id": "o", "a": "A", "b": "B", "min": 9, "max": 1}],
        },
    )
    assert r.status_code == 422


def test_batch_preserves_order_and_isolates_errors():
    r = client.post(
        "/api/v1/batch",
        json={
            "requests": [
                {
                    "reference": "A",
                    "observations": [
                        {"id": "m", "a": "A", "b": "B", "min": -2, "max": 2}
                    ],
                },
                {
                    "reference": "Z",
                    "observations": [
                        {"id": "n", "a": "A", "b": "B", "min": 0, "max": 1}
                    ],
                },
                {
                    "reference": "A",
                    "observations": [
                        {"id": "p", "a": "A", "b": "A", "min": 1, "max": 2}
                    ],
                },
            ]
        },
    )
    assert r.status_code == 200
    results = r.json()["results"]
    assert r.json()["count"] == 3
    assert results[0]["status"] == "feasible"
    assert results[1]["status"] == "error"
    assert results[2]["status"] == "infeasible"
    assert results[2]["cycle_observation_ids"] == ["p"]


def test_batch_empty_rejected():
    r = client.post("/api/v1/batch", json={"requests": []})
    assert r.status_code == 422


def test_openapi_available():
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert r.json()["info"]["title"]
