"""提供 validator_for() —— 让子 schema 的 $ref 能正确解析。

**两种常见写法都不通，不要用**：
  ① validate(instance, subschema) —— #/components/... 的 $ref 在子 schema
     内无根可依，抛 referencing 的 PointerToNowhere，而它**不是**
     jsonschema.ValidationError，常规 except 与 pytest.raises 都抓不到。
  ② Draft202012Validator({"$ref": "#/..."}, registry=Registry().with_resource("", doc))
     —— referencing 的 resolver_with_root 会用 resource.id() or "" 重新注册，
     把包装器盖在文档之上，仍然 PointerToNowhere。且 Resource.from_contents(openapi_doc)
     会直接抛 CannotDetermineSpecification（OpenAPI 文档没有 $schema 字段）。

**可用写法**：把整个文档交给校验器，再用 evolve() 换 schema。
"""
from __future__ import annotations
import pathlib
import pytest, yaml
from jsonschema import Draft202012Validator

ROOT = pathlib.Path(__file__).parents[2] / "contracts"

@pytest.fixture(scope="session")
def contract_doc() -> dict:
    return yaml.safe_load((ROOT / "openapi.yaml").read_text(encoding="utf-8"))

@pytest.fixture(scope="session")
def validator_for(contract_doc):
    root = Draft202012Validator(contract_doc)
    def _make(name: str) -> Draft202012Validator:
        return root.evolve(schema=contract_doc["components"]["schemas"][name])
    return _make

@pytest.fixture(scope="session")
def common(contract_doc) -> dict:
    return contract_doc["components"]["schemas"]
