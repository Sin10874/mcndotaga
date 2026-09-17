# contracts/fixtures —— M0 的 17 个边界响应样例

这些文件是**纯响应体**（mock 数据）。前端（第三条线）在真接口上线前对着它们开发；
线 A/线 C 也用它们作契约桩。每个文件都必须同时满足：

1. `contracts/openapi.yaml` 里对应资源的 schema（含 `additionalProperties: false`
   的封闭性与 §6.0 的降级形态）；
2. `contracts/tools/invariants.py` 里该资源的 `check_*`（求和/等式/排序/§9.1 复算等
   JSON Schema 表达不了的约束）。

校验命令（venv 不在 PATH 上时 `make contract` 会报 `python: No such file or directory`，
直接用模块入口即可）：

```bash
.venv/bin/python -m contracts.tools.validate_fixtures   # → 17 fixtures, 0 failures
.venv/bin/pytest tests/contracts/test_fixtures.py -q    # 合法 + 边界覆盖两层
```

`tests/contracts/test_fixtures.py` 除了跑上面的工具，还逐文件断言**边界真的被覆盖**
（防止 fixture 被"漂白"成合法但平庸的响应）。

## HTTP 状态码映射

fixture 文件体只承载**响应体**——JSON 里没有地方表达状态码，故在此冻结。
机器校验状态码需要请求上下文，上移到 Plan 2+ 的接口集成测试。

| fixture 前缀 | HTTP 状态 |
|---|---|
| `value__*` / `policy__*` / `playbook__*` / `profile__*` / `advise__*` | 200 |
| `error__insufficient_data` | **200**（业务不足，不是传输错误——规格 §6.0 第二层） |
| `error__source_not_allowed` | 403（规格 §4.1：请求了未授权的来源） |

其余错误码（`invalid_request` 400、`not_found` 404、`upstream_unavailable` 503）
未单独出 fixture：它们不携带资源形状，前端只需渲染统一错误信封。

## 清单（一行一条边界）

| 文件 | 覆盖的边界 |
|---|---|
| `value__spec_example.json` | 规格 §6.1 示例（真实比赛 8996973546）：`n_samples=412`、四因子分解 |
| `value__low_confidence.json` | `confidence:"low"` + `n_samples=12 < 30`（§6.0 枚举判定） |
| `policy__spec_example.json` | 规格 §6.2 示例：真实比赛前 12 手 BP ⇒ `next_ord=12`、`team=1` |
| `policy__no_model.json` | 模型未就绪：`model=null`、`baseline.model_top1=null`（字段存在） |
| `playbook__spec_example.json` | 规格 §6.3 示例：series 1141522，两分支各带 `applies_to_game` |
| `playbook__draft_incomplete.json` | 单局剧本 + `n_pending_draft>0` 且 `n_unavailable_draft>0`（§12 R1） |
| `playbook__anomalous.json` | `n_anomalous_draft>0`；第 2 局先手权未定（`first_pick_team=null`） |
| `playbook__positions_phase_b.json` | 眼位 note 降级 `needs_replay`+`needs:"Phase B"`；非 `needs_replay` 的降级 note 不带 `needs` |
| `playbook__op_insufficient.json` | `if_we_ban` 降级（`insufficient_samples`，无 `needs`）⇒ `recommendation:"insufficient_data"` |
| `profile__spec_example.json` | 规格 §6.4 示例：`window_games=48`、signature `12/48=25%` |
| `profile__map_vision_counts_only.json` | `n_position_unknown>0` 与**非降级**的 `map_vision` 百分位并存（§6.5：不得误降级数量口径指标） |
| `profile__position_unknown.json` | `role:null` ⇒ 五个位置维度降级，`hero_archetype` 仍返回六项且和为 1.0 |
| `advise__realtime.json` | 规格 §6.6 示例：`options[]` 形状 + `assumptions` |
| `advise__offline.json` | offline 形状：`branches[].plans[]`（带 `branch_id`/`condition`），无 `options[]` |
| `advise__robustness_penalized.json` | `robustness_delta=0.35` 的项：带 `risk_note`、`penalized_score(0.350) < expected_wr(0.600)`，且原始胜率最高却被排到最后 |
| `error__insufficient_data.json` | §6.0 第二层错误信封：`error.code="insufficient_data"`，`detail` 给出 `n_samples`/`required` |
| `error__source_not_allowed.json` | §6.0 错误码表：`error.code="source_not_allowed"`，`detail.source="scrim"` |

## 两条使用约定

- **响应体里没有 `mode` 字段**：`mode` 是 `/v1/advise` 的**请求**参数，`Advise` schema
  是封闭的（`additionalProperties: false`）且没有该属性——realtime 与 offline 靠
  **形状**判别（`options[]` vs `branches[]`，见 schema 的 `oneOf`）。前端不得读取
  响应里的 `mode`。
- **fixture 只证明"契约定成什么样"**：它证明不了实现不写 Phase A 禁止的字段。
  规格 §6.5「必须不存在」清单的机器校验归 Plan 3 的接口集成测试（那里能对真实
  响应做全字段扫描）——这是一处**显式延迟，不是遗漏**。
