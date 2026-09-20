"""FastAPI application for distributed clock-constraint diagnostics.

Endpoints
---------
GET  /health           liveness probe
POST /api/v1/diagnose  diagnose one node/observation system
POST /api/v1/batch     diagnose many systems in one request (order preserved)

All time values are milliseconds.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .solver import ConstraintError, diagnose

app = FastAPI(
    title="Distributed Clock Constraint Diagnostics API",
    version="1.0.0",
    description=(
        "Submit directed observations 'clock(B) - clock(A) in [min, max] ms'. "
        "The service converts them to difference constraints, checks whether "
        "consistent clock offsets exist, and returns the tightest bounds "
        "relative to a reference node, or a contradictory observation cycle."
    ),
)


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #
class ObservationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, description="Stable observation identifier.")
    a: str = Field(..., min_length=1, description="Source node (clock A).")
    b: str = Field(..., min_length=1, description="Target node (clock B).")
    min: float = Field(..., description="Lower bound of clock(B)-clock(A), ms.")
    max: float = Field(..., description="Upper bound of clock(B)-clock(A), ms.")

    @field_validator("min", "max")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("min/max must be finite numbers")
        return v

    @field_validator("max")
    @classmethod
    def _ordered(cls, v: float, info) -> float:
        lo = info.data.get("min")
        if lo is not None and v < lo:
            raise ValueError(f"max ({v}) must be >= min ({lo})")
        return v


class DiagnosisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference: str = Field(..., min_length=1, description="Reference node id.")
    observations: list[ObservationIn] = Field(
        ..., description="Directed clock-difference observations."
    )
    nodes: list[str] | None = Field(
        default=None,
        description=(
            "Optional explicit node universe. Observation endpoints are always "
            "included automatically. Nodes with no connecting observations are "
            "reported as unconstrained relative to the reference."
        ),
    )

    @field_validator("nodes")
    @classmethod
    def _non_empty_nodes(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            if any(name == "" for name in v):
                raise ValueError("node ids must be non-empty strings")
            if len(v) != len(set(v)):
                raise ValueError("duplicate node id in 'nodes'")
        return v


class BatchDiagnosisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests: list[DiagnosisRequest] = Field(
        ..., min_length=1, description="Diagnosis jobs, processed in given order."
    )


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #
class CycleEntry(BaseModel):
    observation_id: str
    observation: dict[str, Any]
    traversed_edge: str
    weight: float
    inequality: str
    running_sum: float


class InfeasibleResult(BaseModel):
    status: Literal["infeasible"]
    reference: str
    feasible: Literal[False]
    cycle: list[CycleEntry]
    cycle_observation_ids: list[str]
    accumulated_bound: float
    contradiction: str
    interpretation: str
    node_count: int
    observation_count: int


class NodeBound(BaseModel):
    node: str
    relation: Literal["bounded", "upper_only", "lower_only", "unconstrained"]
    reachable_from_reference: bool
    can_reach_reference: bool
    lower_bound: float | None
    upper_bound: float | None
    is_reference: bool


class FeasibleResult(BaseModel):
    status: Literal["feasible"]
    reference: str
    feasible: Literal[True]
    nodes: list[NodeBound]
    unconstrained_nodes: list[str]
    node_count: int
    observation_count: int


class BatchResponse(BaseModel):
    count: int
    results: list[dict[str, Any]]


class ErrorResponse(BaseModel):
    error: str
    detail: str


# --------------------------------------------------------------------------- #
# Exception handling
# --------------------------------------------------------------------------- #
@app.exception_handler(ConstraintError)
async def constraint_error_handler(_request: Request, exc: ConstraintError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"error": "invalid_constraint_request", "detail": str(exc)},
    )


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    return {
        "service": "clock-constraint-diagnostics",
        "docs": "/docs",
        "diagnose": "/api/v1/diagnose",
        "batch": "/api/v1/batch",
    }


def _run(req: DiagnosisRequest) -> dict[str, Any]:
    return diagnose(
        observations=[o.model_dump() for o in req.observations],
        reference=req.reference,
        nodes=req.nodes,
    )


@app.post(
    "/api/v1/diagnose",
    response_model=FeasibleResult | InfeasibleResult,
    tags=["diagnostics"],
    responses={400: {"model": ErrorResponse}},
    summary="Diagnose one clock-constraint system",
)
async def diagnose_one(req: DiagnosisRequest) -> dict[str, Any]:
    return _run(req)


@app.post(
    "/api/v1/batch",
    response_model=BatchResponse,
    tags=["diagnostics"],
    summary="Diagnose many systems; input order is preserved in results",
)
async def diagnose_batch(req: BatchDiagnosisRequest) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for job in req.requests:
        try:
            results.append(_run(job))
        except ConstraintError as exc:
            # One bad job never invalidates the batch.
            results.append(
                {
                    "status": "error",
                    "reference": job.reference,
                    "error": "invalid_constraint_request",
                    "detail": str(exc),
                }
            )
    return {"count": len(results), "results": results}
