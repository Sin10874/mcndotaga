"""从 openapi.yaml 生成 web/src/types/contract.ts。覆盖全部枚举与资源名。"""
from __future__ import annotations
import pathlib, sys
import yaml

ROOT = pathlib.Path(__file__).parents[1]
OUT = ROOT.parent / "web" / "src" / "types" / "contract.ts"

HEADER = ("// AUTO-GENERATED from contracts/openapi.yaml — do not edit.\n"
          "// Regenerate: make contract-ts\n\n")

# 契约里必须存在、且前端需要引用的枚举；缺失即报错
# （防止契约删字段而前端静默退化）
REQUIRED_ENUMS = ["Confidence", "Recommendation", "TheirOpening", "NoteKind",
                  "UnavailableReason", "ErrorCode", "Side", "Factor"]

def ts_type(name: str, schema: dict) -> str | None:
    enum = schema.get("enum")
    if not enum:
        return None
    if schema.get("type") == "string":
        body = " | ".join(f'"{v}"' for v in enum)
        return f"export type {name} = {body};\n"
    if schema.get("type") == "integer":
        return f"export type {name} = {' | '.join(str(v) for v in enum)};\n"
    return None

def main() -> int:
    doc = yaml.safe_load((ROOT / "openapi.yaml").read_text(encoding="utf-8"))
    schemas = doc["components"]["schemas"]
    missing = [n for n in REQUIRED_ENUMS if n not in schemas]
    if missing:
        raise SystemExit(f"契约缺少必需枚举: {missing}")
    parts = [HEADER]
    for name in sorted(schemas):
        t = ts_type(name, schemas[name])
        if t:
            parts.append(t)
    parts.append("\n/** 资源名 → 契约组件名 */\n"
                 "export type Resource = 'Value' | 'Policy' | 'Playbook' | 'Profile' | 'Advise';\n")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(parts), encoding="utf-8")
    print(f"wrote {OUT}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
