# 分布式时钟约束诊断 API

提交分布式节点间的有向观测 **“B 时钟 − A 时钟位于 `[min, max]` 毫秒”**，服务将其转换为
[差分约束系统](https://en.wikipedia.org/wiki/Difference_constraints)，判断是否存在一组一致的
时钟偏移：

- **可行**：返回每个节点相对指定参考节点（偏移固定为 0）的**最紧上界 / 下界**，并标出无法从
  参考节点建立约束的节点；
- **不可行**：返回一条由**观测 ID 组成的矛盾环**、环上逐边累计界限与矛盾说明；
- 结果顺序稳定（节点按 ID 排序；矛盾环以最小观测 ID 为起点定向旋转，逐边求和）；
- 提供**批量诊断接口**，结果顺序与请求顺序一致。

技术栈：Python 3.11 · FastAPI · Pydantic v2 · Uvicorn · Docker / Compose。

---

## 一键启动

```bash
docker compose up --build -d
```

服务监听 **8000** 端口：

- API 文档（Swagger UI）：<http://localhost:8000/docs>
- 交互式文档（ReDoc）：<http://localhost:8000/redoc>
- 健康检查：<http://localhost:8000/health>

停止：`docker compose down`；查看日志：`docker compose logs -f`。

### 不用 Docker 时

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 配置

| 项目 | 值 |
| --- | --- |
| 监听端口 | **8000**（容器内固定，Compose 映射 `8000:8000`） |
| 监听地址 | `0.0.0.0` |
| 启动命令 | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| 单位 | 所有 `min` / `max` / 返回界限均为**毫秒**（支持小数） |
| 数值容差 | 松弛与判环使用 `1e-9 ms` 容差，输出四舍五入到 9 位小数 |

如需改映射端口，修改 `docker-compose.yml` 中的 `"8000:8000"`（左为主机端口）即可。

---

## API

### `POST /api/v1/diagnose` — 单组诊断

请求体：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `reference` | string，必填 | 参考节点 ID，其偏移固定为 0 |
| `observations` | 数组，必填 | 观测列表，字段见下 |
| `nodes` | string[]，可选 | 显式节点集合；观测端点会自动并入。仅在此声明且无任何关联观测的节点会被标记为 `unconstrained` |

观测对象：

| 字段 | 说明 |
| --- | --- |
| `id` | 观测 ID（同组内唯一），矛盾环即由这些 ID 构成 |
| `a` / `b` | 节点 A / 节点 B |
| `min` / `max` | `clock(B) − clock(A) ∈ [min, max]` 毫秒，要求 `min ≤ max` |

#### 示例 1：可行系统（最紧界限）

观测：`B−A ∈ [1,3]`、`C−B ∈ [2,5]`，以 `A` 为参考。

```bash
curl -s -X POST http://localhost:8000/api/v1/diagnose \
  -H 'Content-Type: application/json' \
  -d '{
    "reference": "A",
    "observations": [
      {"id": "obs1", "a": "A", "b": "B", "min": 1, "max": 3},
      {"id": "obs2", "a": "B", "b": "C", "min": 2, "max": 5}
    ]
  }'
```

响应（可行时）：

```json
{
  "status": "feasible",
  "reference": "A",
  "feasible": true,
  "nodes": [
    {"node": "A", "relation": "bounded", "reachable_from_reference": true,
     "can_reach_reference": true, "lower_bound": 0.0, "upper_bound": 0.0,
     "is_reference": true},
    {"node": "B", "relation": "bounded", "reachable_from_reference": true,
     "can_reach_reference": true, "lower_bound": 1.0, "upper_bound": 3.0,
     "is_reference": false},
    {"node": "C", "relation": "bounded", "reachable_from_reference": true,
     "can_reach_reference": true, "lower_bound": 3.0, "upper_bound": 8.0,
     "is_reference": false}
  ],
  "unconstrained_nodes": [],
  "node_count": 3,
  "observation_count": 2
}
```

`relation` 取值：

- `bounded`：参考可正向、反向到达，上下界都存在（`lower_bound ≤ clock(node) ≤ upper_bound`）；
- `upper_only`：只能从参考到达该节点，仅有上界（下界返回 `null`）；
- `lower_only`：该节点只能到达参考，仅有下界（上界返回 `null`）；
- `unconstrained`：与参考之间两个方向都不连通，**无法从参考节点约束**，上下界均为 `null`，
  同时列入 `unconstrained_nodes`。

#### 示例 2：不可行系统（矛盾环）

`B−A ∈ [10,20]`、`C−B ∈ [10,20]`、`A−C ∈ [0,5]` 三条观测迫使
`B−A≥10、C−B≥10、A−C≥0`，三者相加得到 `0 ≥ 20`，矛盾。

```bash
curl -s -X POST http://localhost:8000/api/v1/diagnose \
  -H 'Content-Type: application/json' \
  -d '{
    "reference": "A",
    "observations": [
      {"id": "o1", "a": "A", "b": "B", "min": 10, "max": 20},
      {"id": "o2", "a": "B", "b": "C", "min": 10, "max": 20},
      {"id": "o3", "a": "C", "b": "A", "min": 0,  "max": 5}
    ]
  }'
```

响应（环按有向约束边首尾相接排列；注意某条观测实际经过的边可能与其原始
`a→b` 方向相反）：

```json
{
  "status": "infeasible",
  "reference": "A",
  "feasible": false,
  "cycle": [
    {
      "observation_id": "o1",
      "observation": {"from": "A", "to": "B", "min": 10.0, "max": 20.0},
      "traversed_edge": "B -> A",
      "weight": -10.0,
      "inequality": "x(B) - x(A) <= -10.0",
      "running_sum": -10.0
    },
    {
      "observation_id": "o3",
      "observation": {"from": "C", "to": "A", "min": 0.0, "max": 5.0},
      "traversed_edge": "A -> C",
      "weight": 0.0,
      "inequality": "x(A) - x(C) <= 0.0",
      "running_sum": -10.0
    },
    {
      "observation_id": "o2",
      "observation": {"from": "B", "to": "C", "min": 10.0, "max": 20.0},
      "traversed_edge": "C -> B",
      "weight": -10.0,
      "inequality": "x(C) - x(B) <= -10.0",
      "running_sum": -20.0
    }
  ],
  "cycle_observation_ids": ["o1", "o3", "o2"],
  "accumulated_bound": -20.0,
  "contradiction": "0 <= -20.0 < 0",
  "interpretation": "Chaining the observations around this directed cycle forces 0 ms <= accumulated bound < 0 ms, so no consistent clock offsets exist.",
  "node_count": 3,
  "observation_count": 3
}
```

每个环元素给出：观测 ID、原始观测（`from/to/min/max`）、实际经过的有向边
（`traversed_edge`，上边 `a→b` 权 `max` 或反向边 `b→a` 权 `-min`）、对应不等式、以及沿环的
逐边累计和 `running_sum`；`accumulated_bound` 为环总权重（严格小于 0 即矛盾）。
`cycle_observation_ids` 即按环顺序排列的观测 ID 列表。

### `POST /api/v1/batch` — 批量诊断

```bash
curl -s -X POST http://localhost:8000/api/v1/batch \
  -H 'Content-Type: application/json' \
  -d '{
    "requests": [
      {"reference": "A", "observations": [{"id": "m", "a": "A", "b": "B", "min": -2, "max": 2}]},
      {"reference": "A", "observations": [{"id": "n", "a": "A", "b": "A", "min": 1, "max": 2}]}
    ]
  }'
```

响应 `{"count": 2, "results": [ ...可行/不可行结果... ]}`，**`results[i]` 严格对应
`requests[i]`**。单个作业的语义错误（如参考节点不存在）不会使整批失败，该位置返回
`{"status": "error", "error": ..., "detail": ...}`。

### `GET /health`

返回 `{"status": "ok"}`，Docker 健康检查使用该端点。

### 错误约定

- `400`：语义错误，如观测 ID 重复、`min > max`、参考节点不存在（批量中对应条目返回
  `status=error`）；
- `422`：请求结构 / 类型校验失败（Pydantic，响应内含字段级定位）。

---

## 算法说明

观测 `B−A ∈ [min,max]` 等价于两条差分约束：

```
x_B - x_A ≤ max      → 有向边  A → B，权 max
x_A - x_B ≤ -min     → 有向边  B → A，权 -min
```

1. **可行性 / 矛盾环**：差分约束系统可行 ⇔ 约束图无负权环。以连接所有节点、权为 0 的虚拟源
   运行 Bellman–Ford，第 `|V|` 轮仍可松弛时沿前驱指针回溯，提取一个负权环；环总权重 < 0，
   沿环链式相加得到 `0 ≤ 总权重 < 0` 的矛盾。
2. **最紧界限**：固定参考节点 `r` 的偏移 `x_r = 0` 后，
   - 最紧上界 `x_v ≤ dist(r, v)`（从 r 出发的最短路），
   - 最紧下界 `x_v ≥ -dist(v, r)`（在反图上从 r 求最短路）。
   某方向不可达即该侧无界；两侧均不可达即无法从参考节点约束。
3. **稳定性**：节点输出按 ID 排序；边按观测 ID 确定序松弛；负环以最小
   `(观测 ID, 边类型)` 为起点旋转后再输出；批量保持请求顺序。

## 测试

```bash
pip install -r requirements.txt
pip install pytest
pytest -q
```

测试覆盖：可行系统的链式最紧界限、单向有界 / 无界节点、自环与多观测矛盾环（含环顺序与累计
和）、参考节点缺失、重复观测 ID、`min > max`、以及批量接口顺序与单条错误隔离。
