"""校验每个 fixture：① 过其资源 schema（根锚定，$ref 可解析）；② 过该资源不变式。"""
from __future__ import annotations
import json, pathlib, sys
from jsonschema import Draft202012Validator
import yaml
from . import invariants as inv

ROOT = pathlib.Path(__file__).parents[1]
FIX = ROOT / "fixtures"
MIN_FIXTURES = 17   # 目录为空时不得静默通过

ROUTES = {
    "value":    inv.check_value,
    "policy":   inv.check_policy,
    "playbook": inv.check_playbook,
    "profile":  inv.check_profile,
    "advise":   inv.check_advise,
}

def main() -> int:
    doc = yaml.safe_load((ROOT / "openapi.yaml").read_text(encoding="utf-8"))
    root = Draft202012Validator(doc)

    def validator(name: str) -> Draft202012Validator:
        return root.evolve(schema=doc["components"]["schemas"][name])

    failures, files = [], sorted(FIX.glob("*.json"))
    if len(files) < MIN_FIXTURES:
        print(f"FAIL 只找到 {len(files)} 个 fixture，至少应有 {MIN_FIXTURES} 个")
        return 1
    for f in files:
        try:
            body = json.loads(f.read_text(encoding="utf-8"))
            resource = f.name.split("__")[0]
            if resource == "error":
                validator("ErrorEnvelope").validate(body)
                continue
            validator(resource.capitalize()).validate(body)
            for err in ROUTES[resource](body):
                failures.append(f"{f.name}: invariant — {err}")
        except Exception as e:                      # schema 错误计入失败而非让工具崩溃
            msg = getattr(e, "message", None) or str(e)
            failures.append(f"{f.name}: schema — {type(e).__name__}: {msg}")
    for m in failures:
        print("FAIL", m)
    print(f"{len(files)} fixtures, {len(failures)} failures")
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main())
