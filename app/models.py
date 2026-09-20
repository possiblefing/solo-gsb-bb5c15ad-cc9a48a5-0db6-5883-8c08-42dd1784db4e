"""请求 / 响应数据模型。

请求中观测区间字段按需求命名为 ``min`` / ``max``（Python 内建名冲突通过
字段别名安全处理），输出统一使用 camelCase 风格之外的 snake_case 直白命名。
"""

from __future__ import annotations

import math
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator


class ObservationIn(BaseModel):
    """一条有向观测：target 时钟 - source 时钟 ∈ [min, max] 毫秒。"""

    id: str = Field(..., min_length=1, description="观测的唯一 ID")
    source: str = Field(..., min_length=1, description="被减时钟节点 A")
    target: str = Field(..., min_length=1, description="被减时钟节点 B")
    min: float = Field(..., alias="min", description="区间下界（毫秒）")
    max: float = Field(..., alias="max", description="区间上界（毫秒）")

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @model_validator(mode="after")
    def _check(self) -> "ObservationIn":
        if not math.isfinite(self.min) or not math.isfinite(self.max):
            raise ValueError("min/max 必须为有限数值")
        if self.min > self.max:
            raise ValueError(
                f"观测 {self.id} 的区间非法：min({self.min}) > max({self.max})"
            )
        return self


class DiagnosisRequest(BaseModel):
    """单条诊断请求。"""

    reference: str = Field(..., min_length=1, description="参考时钟节点")
    observations: List[ObservationIn] = Field(
        default_factory=list, description="有向时钟观测列表"
    )
    nodes: Optional[List[str]] = Field(
        default=None,
        description="显式声明的全部节点；缺省时取观测端点与参考节点的并集",
    )
    name: Optional[str] = Field(
        default=None, description="可选名称，诊断与批量接口会原样回显"
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _check(self) -> "DiagnosisRequest":
        if self.nodes is not None:
            if len(self.nodes) != len(set(self.nodes)):
                raise ValueError("nodes 中存在重复节点")
            if self.reference not in self.nodes:
                raise ValueError("reference 必须包含在 nodes 中")

        ids = [o.id for o in self.observations]
        if len(ids) != len(set(ids)):
            raise ValueError("observations 中存在重复的观测 ID")
        return self

    def effective_nodes(self) -> List[str]:
        """合并显式节点、观测端点与参考节点。"""
        nodes = set(self.nodes or [])
        nodes.add(self.reference)
        for o in self.observations:
            nodes.add(o.source)
            nodes.add(o.target)
        return sorted(nodes)


class NodeBounds(BaseModel):
    node: str
    lower: Optional[float] = Field(
        ..., description="x_node - x_reference 的最紧下界（毫秒），不可达时为 null"
    )
    upper: Optional[float] = Field(
        ..., description="x_node - x_reference 的最紧上界（毫秒），不可达时为 null"
    )
    reachable_from_reference: bool = Field(
        ..., description="参考节点能否沿约束边到达该节点（决定上界）"
    )
    can_reach_reference: bool = Field(
        ..., description="该节点能否沿约束边到达参考节点（决定下界）"
    )
    constrained: bool = Field(
        ..., description="是否可从参考节点在两个方向上同时约束（双向可达）"
    )


class CycleStep(BaseModel):
    observation_id: str
    from_: str = Field(..., serialization_alias="from")
    to: str
    constraint: Literal["upper", "lower"] = Field(
        ..., description="upper=对应 max 约束边，lower=对应 -min 约束边"
    )
    weight: float = Field(..., description="该边在差分约束图中的权重（毫秒）")
    bound: float = Field(..., description="观测区间对应的原始端点值（毫秒）")


class ContradictionCycle(BaseModel):
    steps: List[CycleStep] = Field(..., description="沿矛盾环方向排列的边")
    accumulated_bound: float = Field(
        ..., description="环上权重之和（负数），即推出的 0 <= 该负数"
    )
    contradiction: str = Field(..., description="人类可读的矛盾式，例如 '0 <= -2.0'")


class FeasibleDiagnosis(BaseModel):
    feasible: Literal[True] = True
    reference: str
    name: Optional[str] = None
    node_bounds: List[NodeBounds]
    unconstrained_nodes: List[str] = Field(
        ..., description="无法从参考节点双向约束（界不完整）的节点"
    )


class InfeasibleDiagnosis(BaseModel):
    feasible: Literal[False] = False
    name: Optional[str] = None
    cycle: ContradictionCycle


DiagnosisResponse = Union[FeasibleDiagnosis, InfeasibleDiagnosis]


class BatchDiagnosisRequest(BaseModel):
    """批量诊断请求：顺序提交，结果按下标稳定返回。"""

    diagnoses: List[DiagnosisRequest] = Field(..., min_length=1)

    model_config = {"extra": "forbid"}


class BatchItemResponse(BaseModel):
    index: int
    name: Optional[str] = None
    result: Union[FeasibleDiagnosis, InfeasibleDiagnosis] = Field(
        ..., discriminator="feasible"
    )


class BatchDiagnosisResponse(BaseModel):
    count: int
    results: List[BatchItemResponse]
