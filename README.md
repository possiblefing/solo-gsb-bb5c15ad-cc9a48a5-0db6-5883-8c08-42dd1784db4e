# 分布式时钟约束诊断 API

提交形如 **“B 时钟 − A 时钟位于 `[min, max]` 毫秒”** 的有向观测，服务将其转换为
[差分约束系统](https://en.wikipedia.org/wiki/Difference_constraints) 并完成：

1. **可行性判定**：是否存在一组自洽的节点时钟偏移；
2. **可行时**：给出每个节点相对指定 **参考节点** 的最紧上下界，并标出无法从参考节点
   双向约束的节点（孤岛 / 单向可达）；
3. **不可行时**：返回一条由观测 ID 组成的 **矛盾环**、环上每条约束边及累计界限
   （形如 `0 <= -3.0`），环的起点与顺序经过规范化，多次调用结果稳定；
4. 提供 **批量诊断接口**，结果按提交下标稳定返回。

算法：差分约束图 + Bellman-Ford（超源初始化检测负环、前驱指针回卷提取矛盾环），
纯 Python 标准实现，无外部服务依赖。

---

## 一键启动（单条命令）

需要 Docker 与 Docker Compose v2：

```bash
docker compose up --build -d
```

服务监听并对外暴露 **8000 端口**：

- 健康检查：<http://localhost:8000/health>
- 交互式文档（Swagger UI）：<http://localhost:8000/docs>
- OpenAPI Schema：<http://localhost:8000/openapi.json>

停止：

```bash
docker compose down
```

不用 Compose 时：

```bash
docker build -t clock-constraint-diagnosis .
docker run --rm -p 8000:8000 clock-constraint-diagnosis
```

---

## 配置

配置全部通过环境变量完成（见 `docker-compose.yml`）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `HOST` | `0.0.0.0` | 容器内 uvicorn 监听地址 |
| `PORT` | `8000` | 容器内监听端口；compose 同时用它映射宿主机端口 |

例如改用宿主机 18000 端口：

```bash
PORT=18000 docker compose up --build -d   # 访问 http://localhost:18000
```

---

## 本地开发

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
pytest -q
```

---

## 接口

### `POST /api/v1/diagnose` — 单条诊断

请求体：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `reference` | string | 是 | 参考时钟节点（计算界的基准） |
| `observations` | array | 是 | 观测列表，可为空 |
| `observations[].id` | string | 是 | 观测唯一 ID（矛盾环按它回指） |
| `observations[].source` | string | 是 | 被减时钟节点 A |
| `observations[].target` | string | 是 | 被减时钟节点 B |
| `observations[].min` | number | 是 | 区间下界（毫秒），须 ≤ `max` |
| `observations[].max` | number | 是 | 区间上界（毫秒） |
| `nodes` | string[] | 否 | 显式节点列表；缺省取观测端点与 `reference` 的并集 |
| `name` | string | 否 | 可选名称，响应原样回显 |

语义：每条观测表示 `x_target - x_source ∈ [min, max]`（`x_v` 为节点 v 的时钟偏移，
单位毫秒）。

#### 可行响应（示例）

```bash
curl -s -X POST http://localhost:8000/api/v1/diagnose \
  -H 'Content-Type: application/json' \
  -d '{
    "reference": "A",
    "observations": [
      {"id": "o1", "source": "A", "target": "B", "min": 1, "max": 5},
      {"id": "o2", "source": "B", "target": "C", "min": 2, "max": 4}
    ]
  }'
```

```json
{
  "feasible": true,
  "reference": "A",
  "name": null,
  "node_bounds": [
    {"node": "A", "lower": 0.0, "upper": 0.0,
     "reachable_from_reference": true, "can_reach_reference": true, "constrained": true},
    {"node": "B", "lower": 1.0, "upper": 5.0,
     "reachable_from_reference": true, "can_reach_reference": true, "constrained": true},
    {"node": "C", "lower": 3.0, "upper": 9.0,
     "reachable_from_reference": true, "can_reach_reference": true, "constrained": true}
  ],
  "unconstrained_nodes": []
}
```

`lower` / `upper` 即 `x_node - x_reference` 的最紧上下界（毫秒）。
无法从参考节点方向到达（上界缺失）或无法回到参考节点（下界缺失）的节点，对应界为
`null`，并被收入 `unconstrained_nodes`。

#### 不可行响应（矛盾环，示例）

```bash
curl -s -X POST http://localhost:8000/api/v1/diagnose \
  -H 'Content-Type: application/json' \
  -d '{
    "reference": "A",
    "observations": [
      {"id": "o1", "source": "A", "target": "B", "min": 0, "max": 10},
      {"id": "o2", "source": "B", "target": "A", "min": 3, "max": 4}
    ]
  }'
```

```json
{
  "feasible": false,
  "name": null,
  "cycle": {
    "steps": [
      {"observation_id": "o2", "from": "A", "to": "B",
       "constraint": "lower", "weight": -3.0, "bound": 3.0},
      {"observation_id": "o1", "from": "B", "to": "A",
       "constraint": "lower", "weight": 0.0, "bound": 0.0}
    ],
    "accumulated_bound": -3.0,
    "contradiction": "0 <= -3.0"
  }
}
```

- `steps` 沿环方向闭合排列：上一步的 `to` 等于下一步的 `from`；
- `constraint`：`upper` 表示该边对应观测的 `max`（边 `source -> target`，权 `max`），
  `lower` 表示对应 `-min`（边 `target -> source`，权 `-min`）；
- `weight` 为差分约束图中的边权，`accumulated_bound` 为环上权值之和（负数），
  沿环累加推出 `0 <= accumulated_bound`，即矛盾式。

### `POST /api/v1/diagnose/batch` — 批量诊断

请求体 `{"diagnoses": [ <单条请求>, ... ]}`（至少 1 条）。每条可带 `name`，
响应按提交顺序返回 `index`，互不影响：

```bash
curl -s -X POST http://localhost:8000/api/v1/diagnose/batch \
  -H 'Content-Type: application/json' \
  -d '{"diagnoses":[
    {"name":"ok", "reference":"A",
     "observations":[{"id":"x","source":"A","target":"B","min":0,"max":2}]},
    {"name":"bad", "reference":"A",
     "observations":[
       {"id":"o1","source":"A","target":"B","min":0,"max":10},
       {"id":"o2","source":"B","target":"A","min":3,"max":4}
     ]}
  ]}'
```

### `GET /health`

返回 `{"status": "ok"}`，compose 已配置健康检查。

### 校验错误

非法区间（`min > max`）、重复观测 ID、`reference` 不在 `nodes` 中等请求问题返回
HTTP `422` 及详细错误信息。

---

## 建模与稳定性说明

每条观测 `x_B - x_A ∈ [min, max]` 展开为两条边：

- `A -> B`，权 `max`（上界约束 `x_B - x_A <= max`）
- `B -> A`，权 `-min`（下界约束 `x_A - x_B <= -min`）

性质：

- 参考节点 `r` 到 `v` 的**最短路** = `x_v - x_r` 的最紧上界；
- 反图中 `r` 到 `v` 的最短路取负 = 最紧下界；
- **负环 ⇔ 不可行**；负环即矛盾环，环上权值之和恒为负。

结果顺序的确定性保证：

- 节点一律按字典序输出；
- Bellman-Ford 的边按 `(观测 ID, 约束类型 upper<lower)` 排序后松弛，先到先松弛；
- 矛盾环先沿前驱回卷，再旋转到 `(起点节点, 观测 ID, 约束类型)` 最小的边作为唯一起点；
- 因此相同的观测集合无论提交顺序如何，矛盾环与累计界限完全一致。

## 项目结构

```
app/
  main.py      # FastAPI 路由：/api/v1/diagnose、/api/v1/diagnose/batch、/health
  models.py    # 请求/响应 Pydantic 模型与入参校验
  solver.py    # 差分约束建模、Bellman-Ford、负环提取与规范化
tests/         # 算法层与 HTTP 接口测试（20 个用例）
Dockerfile
docker-compose.yml
```
