"""FastAPI 入口：单条诊断、批量诊断与健康检查。"""

from __future__ import annotations

from typing import List

from fastapi import FastAPI

from .models import (
    BatchDiagnosisRequest,
    BatchDiagnosisResponse,
    BatchItemResponse,
    ContradictionCycle,
    CycleStep,
    DiagnosisRequest,
    DiagnosisResponse,
    FeasibleDiagnosis,
    InfeasibleDiagnosis,
)
from .solver import Observation, diagnose

app = FastAPI(
    title="分布式时钟约束诊断 API",
    description=(
        "提交形如「B 时钟 - A 时钟 ∈ [min, max] 毫秒」的有向观测，"
        "服务将其转换为差分约束，判断时钟偏移是否一致；"
        "可行时给出相对参考节点的最紧上下界，不可行时返回稳定的矛盾环。"
    ),
    version="1.0.0",
)


def _to_observation(raw) -> Observation:
    return Observation(
        id=raw.id,
        source=raw.source,
        target=raw.target,
        min=raw.min,
        max=raw.max,
    )


def _run_diagnosis(request: DiagnosisRequest):
    result = diagnose(
        nodes=request.effective_nodes(),
        reference=request.reference,
        observations=[_to_observation(o) for o in request.observations],
    )
    if result["feasible"]:
        return FeasibleDiagnosis(name=request.name, **result)

    cycle = result["cycle"]
    steps: List[CycleStep] = [
        CycleStep(
            observation_id=s["observation_id"],
            from_=s["from"],
            to=s["to"],
            constraint=s["constraint"],
            weight=s["weight"],
            bound=s["bound"],
        )
        for s in cycle["steps"]
    ]
    return InfeasibleDiagnosis(
        name=request.name,
        cycle=ContradictionCycle(
            steps=steps,
            accumulated_bound=cycle["accumulated_bound"],
            contradiction=cycle["contradiction"],
        ),
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/v1/diagnose", response_model=DiagnosisResponse, tags=["diagnosis"])
def diagnose_once(request: DiagnosisRequest) -> DiagnosisResponse:
    """提交一组观测与参考节点，返回单次诊断结果。"""
    return _run_diagnosis(request)


@app.post(
    "/api/v1/diagnose/batch",
    response_model=BatchDiagnosisResponse,
    tags=["diagnosis"],
)
def diagnose_batch(request: BatchDiagnosisRequest) -> BatchDiagnosisResponse:
    """批量诊断；结果按提交顺序（index）稳定返回。"""
    items = [
        BatchItemResponse(index=i, name=req.name, result=_run_diagnosis(req))
        for i, req in enumerate(request.diagnoses)
    ]
    return BatchDiagnosisResponse(count=len(items), results=items)
