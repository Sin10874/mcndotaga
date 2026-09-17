import pathlib
import yaml, pytest

ROOT = pathlib.Path(__file__).parents[2] / "contracts"
RESOURCES = ["Value", "Policy", "Playbook", "Profile", "Advise"]

def test_all_five_resources_are_defined(common):
    missing = [r for r in RESOURCES if r not in common]
    assert missing == [], f"契约缺少资源 schema: {missing}"

@pytest.mark.parametrize("name", RESOURCES)
def test_resource_schema_is_not_empty(common, name):
    schema = common[name]
    assert schema.get("required"), f"{name} 没有 required 字段——空 schema 会让任何 fixture 通过"
    assert schema.get("properties"), f"{name} 没有 properties"

def test_every_degraded_field_refs_the_Degraded_component(contract_doc):
    """规格 §15 的降级契约依赖此约束：降级字段必须 $ref，不得内联同形对象。"""
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
    for name in RESOURCES:
        walk(schemas[name], name)
    assert offenders == [], f"以下位置内联了降级对象而非 $ref Degraded: {offenders}"
