"""合并 contracts/schemas/*.yaml 为单一 openapi.yaml。重名冲突直接报错。"""
from __future__ import annotations
import copy, pathlib, sys
import yaml

HERE = pathlib.Path(__file__).parents[1]
SCHEMAS = HERE / "schemas"
OUT = HERE / "openapi.yaml"

def build() -> dict:
    merged = {"openapi": "3.1.0",
              "info": {"title": "MCNDOTAGA Contract", "version": "1.0.0"},
              "paths": {},
              "components": {"schemas": {}}}
    for path in sorted(SCHEMAS.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, schema in (doc.get("components", {}).get("schemas") or {}).items():
            if name in merged["components"]["schemas"]:
                raise ValueError(
                    f"schema {name!r} 在 {path.name} 与其它文件重复定义——"
                    "同名不同义会让前端与后端产生分歧")
            merged["components"]["schemas"][name] = copy.deepcopy(schema)
    return merged

def main() -> int:
    doc = build()
    OUT.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"wrote {OUT} with {len(doc['components']['schemas'])} schemas")
    return 0

# 说明：本 chunk 只冻结**响应体**（components.schemas），paths 刻意留空。
# 请求体 schema 与路径定义属 Plan 2+ 的接口实现范围——冻结它们需要先确定
# 服务端框架与鉴权方式，现在冻结会产生返工。M0 的验收条款只要求
# components/schemas 通过校验，故本 chunk 的产物已满足。

if __name__ == "__main__":
    sys.exit(main())
