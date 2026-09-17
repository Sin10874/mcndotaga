"""契约形态的机器可检断言：空 schema / 降级字段必须 $ref / required 锚定 / 降级位封闭。

这些是**结构性**约束——它们不校验某个 fixture，而是防止契约本身退化
（例如把 required 删空、把降级对象内联、让降级位夹带绝对值）。
"""
import jsonschema
import pytest

RESOURCES = ["Value", "Policy", "Playbook", "Profile", "Advise"]


def test_all_five_resources_are_defined(common):
    missing = [r for r in RESOURCES if r not in common]
    assert missing == [], f"契约缺少资源 schema: {missing}"

@pytest.mark.parametrize("name", RESOURCES)
def test_resource_schema_is_not_empty(common, name):
    schema = common[name]
    assert schema.get("required"), f"{name} 没有 required 字段——空 schema 会让任何 fixture 通过"
    assert schema.get("properties"), f"{name} 没有 properties"

@pytest.mark.parametrize("name", RESOURCES)
def test_resource_rejects_empty_body(validator_for, name):
    """F8 反向：五个资源都不得接受 `{}`（证明 schema 不是空壳、required 真在生效）。"""
    with pytest.raises(jsonschema.ValidationError):
        validator_for(name).validate({})

def test_every_degraded_field_refs_the_Degraded_component(contract_doc):
    """规格 §15 的降级契约依赖此约束：降级字段必须 $ref，不得内联同形对象。

    **遍历 components.schemas 的每一个组件**（跳过 `Degraded` 自身——它是合法定义）。
    只从五个资源节点出发的旧写法走不进被 `$ref` 的组件内部：在
    `LeaveEvaluation` 之类的组件里内联一个 `{value, reason}` 可以完全逃逸（F7）。
    """
    schemas = contract_doc["components"]["schemas"]
    offenders = []
    def walk(node, path):
        if isinstance(node, dict):
            # 形如 {"value": ..., "reason": ...} 的内联降级对象
            if "reason" in (node.get("properties") or {}) and "value" in (node.get("properties") or {}):
                if "$ref" not in node:
                    offenders.append(path)
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
    for name, schema in schemas.items():
        if name == "Degraded":
            continue
        walk(schema, name)
    assert offenders == [], f"以下位置内联了降级对象而非 $ref Degraded: {offenders}"

def test_required_matches_declared_optionality(common):
    """F8：`required` 必须恰好等于「未标 `x-optional` 的属性集」（约定见 common.yaml 头部）。

    删掉 `Playbook.required` 里的一项、或让某个属性悄悄变成必填，都会在此变红。
    注：本断言按**组件**锚定（约定所在层级）；内联子对象的 required 不在本断言范围内。
    """
    offenders = []
    for name, schema in common.items():
        props = schema.get("properties")
        if not isinstance(props, dict) or not props:
            continue
        expected = {p for p, sub in props.items()
                    if not (isinstance(sub, dict) and sub.get("x-optional"))}
        actual = set(schema.get("required") or [])
        if actual != expected:
            offenders.append(f"{name}: required={sorted(actual)} 应为 {sorted(expected)}")
    assert offenders == [], ("required 与 x-optional 标注不一致（新增可选字段请标 x-optional）: "
                             + "；".join(offenders))

@pytest.mark.parametrize("name, body", [
    ("ProfileRef", {"team_id": 1, "name": "A", "tag": "A"}),                       # logo_url
    ("SeriesGame", {"game_no": 1, "first_pick_team": None}),                       # note
    ("PolicyBaseline", {"frequency_top1": None}),                                  # model_top1
    ("CoverageEntry", {"n_matches": 0, "n_stat_available": 0}),                    # n_position_unknown
    ("AdviseOption", {"hero_id": 1, "expected_wr": 0.5, "robustness_delta": 0.04,
                      "penalized_score": 0.5, "why": "x", "fallback": [2],
                      "counterparty_plan": "y"}),                                  # risk_note
])
def test_x_optional_properties_may_be_omitted(validator_for, name, body):
    """`x-optional` 不只是标注：省略这些属性必须仍然合法（否则标注是假的）。"""
    validator_for(name).validate(body)

# ── I4：四个 oneOf 降级位必须封闭 ─────────────────────────────────────────
# 规格 §6.4/§6.5：降级形态只含 value/reason(/needs)。若 oneOf 不封闭，
# `{"value": null, "reason": ..., "percentile": 72}` 这类"降级但仍带绝对值"
# 的响应会通过——Phase A 边界（§6.5）与 §7.3 的门槛同时失效。

@pytest.mark.parametrize("name, body", [
    ("PercentileDimension", {"value": None, "reason": "insufficient_samples", "percentile": 72}),
    ("PercentileDimension", {"value": None, "reason": "needs_replay", "needs": "Phase B",
                             "percentile": 72, "n": 120}),
    ("WinRateSample", {"value": None, "reason": "stat_unavailable", "wr": 0.44, "n": 4}),
    ("LeaveEvaluation", {"value": None, "reason": "insufficient_samples", "our_wr": 0.55}),
    ("CounterOption", {"value": None, "reason": "insufficient_samples",
                       "hero_id": 36, "wr": 0.61, "n": 31}),
    ("PositionNote", {"kind": "ward", "text": "x", "evidence": [], "value": None,
                      "reason": "needs_replay", "needs": "Phase B", "percentile": 72}),
])
def test_degraded_branches_are_closed(validator_for, name, body):
    with pytest.raises(jsonschema.ValidationError):
        validator_for(name).validate(body)

@pytest.mark.parametrize("name, body", [
    ("PercentileDimension", {"value": None, "reason": "insufficient_samples"}),
    ("PercentileDimension", {"percentile": 72, "n": 120}),
    ("WinRateSample", {"value": None, "reason": "stat_unavailable"}),
    ("LeaveEvaluation", {"value": None, "reason": "insufficient_samples"}),
    ("CounterOption", {"value": None, "reason": "insufficient_samples"}),
    ("PositionNote", {"kind": "ward", "text": "x", "evidence": [], "value": None,
                      "reason": "needs_replay", "needs": "Phase B"}),
])
def test_degraded_branches_accept_the_legal_forms(validator_for, name, body):
    """封闭不等于误杀：合法的降级形态与合法有值形态都必须继续通过。"""
    validator_for(name).validate(body)

def test_position_note_keeps_needs_conditionality(validator_for):
    """PositionNote 改成 `$ref Degraded` + `unevaluatedProperties:false` 后，
    §6.0 的 needs 条件规则（仅 needs_replay 必填、其余必须省略）必须原样生效。"""
    v = validator_for("PositionNote")
    base = {"kind": "ward", "text": "x", "evidence": []}
    v.validate({**base, "value": None, "reason": "needs_replay", "needs": "Phase B"})
    v.validate({**base, "value": None, "reason": "insufficient_samples"})
    with pytest.raises(jsonschema.ValidationError):
        v.validate({**base, "value": None, "reason": "needs_replay"})
    with pytest.raises(jsonschema.ValidationError):
        v.validate({**base, "value": None, "reason": "insufficient_samples", "needs": "Phase B"})
    with pytest.raises(jsonschema.ValidationError):
        v.validate({**base, "hero_id": 1, "value": None, "reason": "insufficient_samples"})
