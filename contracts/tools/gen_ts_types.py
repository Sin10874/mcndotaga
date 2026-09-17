"""从 openapi.yaml 生成 web/src/types/contract.ts。**只生成枚举联合与资源名联合。**

用法：
    python -m contracts.tools.gen_ts_types            # 写入生成物（默认，给 make contract-ts）
    python -m contracts.tools.gen_ts_types --check    # 只读：只比对，不写盘（新鲜度测试用）

**范围（重要，别误以为对象类型也在里面）**：本生成器只输出顶层枚举联合
（string / integer 枚举）与 ``Resource`` 联合。契约里 53 个 schema 的对象图
（``oneOf`` + ``unevaluatedProperties``、``allOf`` 条件、``type: [X, "null"]``、
``$ref`` 链、``required`` / ``x-optional``）**不生成**——前端在 Plan 4 之前
没有可用的对象接口，需要时按 Task 8 的范围说明扩展本文件。

为什么不直接写盘：``--check`` 模式存在的理由是新鲜度测试。
旧实现是「跑一遍生成器 → 比较前后文本」，那会把**受版本控制的生成物当草稿纸**：
本地手改过 contract.ts 时，测试报 FAILED 的同时顺手把手改抹掉，
失败信息里的 diff 也随之消失。只读比对把「检测」和「写入」分开。
"""
from __future__ import annotations
import argparse, difflib, json, pathlib, re, sys
import yaml

ROOT = pathlib.Path(__file__).parents[1]
DEFAULT_SPEC = ROOT / "openapi.yaml"
DEFAULT_OUT = ROOT.parent / "web" / "src" / "types" / "contract.ts"

HEADER = ("// AUTO-GENERATED from contracts/openapi.yaml — do not edit.\n"
          "// Regenerate: make contract-ts\n\n")

# 契约里必须存在、且前端需要引用的枚举；缺失即报错
# （防止契约删字段而前端静默退化）
REQUIRED_ENUMS = ["Confidence", "Recommendation", "TheirOpening", "NoteKind",
                  "UnavailableReason", "ErrorCode", "Side", "Factor"]

# 资源名 = 五个顶层资源组件，**冻结**在生成器里而不从 components.schemas 推导：
# 推导会让「契约里冒出一个新组件」自动变成 Resource 的新成员，
# 值得看的改动会悄悄溜进生成物。冻结之后，任何资源集变化都必须改这一行，
# 且 tests/contracts/test_ts_types_fresh.py 的 RESOURCE_MEMBERS 基线会同时变红。
RESOURCE_MEMBERS = ["Value", "Policy", "Playbook", "Profile", "Advise"]

# 枚举 schema 的 type 允许形态：单值或列表（OpenAPI 3.1 用 type: [X, "null"] 表达可空）
_ENUM_TYPES = {"string", "integer", "null"}
_TS_DECL = re.compile(r"^export type (\w+) = ", re.M)
_JSDOC_CLOSE = re.compile(r"\*/")


class UnrenderableEnum(Exception):
    """schema 带 enum，但本生成器无法渲染成 TS 联合。"""


def _die(msg: str) -> None:
    raise SystemExit(f"gen_ts_types: {msg}")


def js_member(value: object) -> str:
    """枚举成员 → TS 字面量。

    字符串一律走 ``json.dumps``：``f'"{v}"'`` 对含 ``"`` / 反斜杠 / 换行的值
    会生成非法 TS（``back\\slash`` 还会**静默编译成** ``backslash``——不报错，
    只是值变了）。``ensure_ascii=False`` 保持中文等可读，转义交给 JSON 规则。
    """
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return "null"
    if isinstance(value, bool):                 # bool 是 int 的子类，先拦
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    raise UnrenderableEnum(f"枚举成员类型不支持: {value!r} ({type(value).__name__})")


def jsdoc(description: str) -> str:
    """schema.description → JSDoc。

    ``Team = 0 | 1`` 不带说明时，前端极易把 Radiant/Dire 弄反——描述是契约里
    唯一写清语义的地方，必须一起进生成物。``*/`` 提前转义，否则描述能提前
    结束注释、把后面的文字变成代码。
    """
    text = " ".join(str(description).split())      # 压平换行与多余空白
    text = _JSDOC_CLOSE.sub("*\\/", text)          # 不产生注释结束符
    return f"/** {text} */\n"


def ts_type(name: str, schema: dict, where: str) -> str | None:
    """枚举 schema → TS 联合声明；不是枚举返回 None。

    schema 带 ``enum`` 却渲染不出来时**抛错**而不是返回 None：返回 None 会让
    契约里真实存在的枚举从生成物里静默消失，而 main() 只按名字查在不在、
    退出码仍是 0（例如把 Confidence 改成 type: object）。
    """
    if "enum" not in schema:
        return None
    members = schema["enum"]
    if not isinstance(members, list) or not members:
        raise UnrenderableEnum(f"{where}: enum 不是非空列表: {members!r}")
    raw = schema.get("type")
    types = [raw] if isinstance(raw, str) else list(raw or [])
    if not types or set(types) - _ENUM_TYPES:
        raise UnrenderableEnum(
            f"{where}: enum 的 type={raw!r} 不在 {sorted(_ENUM_TYPES)} 内，"
            f"无法渲染为 TS 联合（成员 {members!r}）")
    body = " | ".join(js_member(v) for v in members)
    if not body:
        raise UnrenderableEnum(f"{where}: 枚举渲染结果为空")
    head = jsdoc(schema["description"]) if schema.get("description") else ""
    return f"{head}export type {name} = {body};\n"


def resource_union(members: list[str] | None = None) -> str:
    """Resource 联合声明（单引号，与枚举的 JSON 双引号风格区分开）。"""
    names = RESOURCE_MEMBERS if members is None else members
    body = " | ".join(f"'{n}'" for n in names)
    return ("\n/** 资源名 → 契约组件名 */\n"
            f"export type Resource = {body};\n")


def render(spec: pathlib.Path) -> str:
    """渲染生成物文本。**纯函数**：不读旧生成物、不写盘，校验失败即抛错。

    所有校验都在返回之前完成——调用方拿到文本时它已经是可信的，
    因此失败路径永远不会覆盖已提交的生成物。
    """
    schemas = yaml.safe_load(spec.read_text(encoding="utf-8"))["components"]["schemas"]
    missing = [n for n in REQUIRED_ENUMS if n not in schemas]
    if missing:
        _die(f"契约缺少必需枚举: {missing}")
    absent = [n for n in RESOURCE_MEMBERS if n not in schemas]
    if absent:
        _die(f"契约缺少资源组件: {absent}")

    parts = [HEADER]
    emitted: set[str] = set()
    for name in sorted(schemas):
        try:
            decl = ts_type(name, schemas[name], f"components.schemas.{name}")
        except UnrenderableEnum as exc:
            _die(f"{exc}——枚举必须能渲染，不能静默丢字段")
        if decl:
            parts.append(decl)
            emitted.add(name)
    parts.append(resource_union())

    # 发射校验：查的是**生成物里真的有这个类型**，不是「schema 里有这个名字」。
    # 名字在契约里存在但渲染器不认（type: object / 只有 $ref）时，
    # 旧实现照样 exit 0，而前端拿不到这个类型。
    text = "".join(parts)
    declared = set(_TS_DECL.findall(text))
    for name in REQUIRED_ENUMS:
        if name not in emitted or name not in declared:
            _die(f"必需枚举 {name} 未出现在生成物中（schema 存在但渲染器没有发射它）")
    if "Resource" not in declared:
        _die("生成物缺少 Resource 联合")
    return text


def unified_diff(old: str, new: str, n: int = 2) -> str:
    """期望值 vs 盘上值的统一 diff，给 --check 的失败信息用。"""
    lines = difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile="contract.ts (on disk)", tofile="contract.ts (regenerated)", n=n)
    return "".join(lines) or "(无行级差异——可能只差末尾换行)"


def check(spec: pathlib.Path, out: pathlib.Path) -> int:
    """只读新鲜度检查：不写盘，不一致时返回 1 并打印 diff。"""
    want = render(spec)
    if not out.exists():
        _die(f"{out} 不存在——运行 python -m contracts.tools.gen_ts_types 生成它")
    got = out.read_text(encoding="utf-8")
    if got == want:
        print(f"up to date: {out}")
        return 0
    print(f"out of date: {out}\n"
          f"运行 python -m contracts.tools.gen_ts_types（或 make contract-ts）并提交；"
          f"本模式不写盘。\n"
          f"{unified_diff(got, want)}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m contracts.tools.gen_ts_types",
        description="从 openapi.yaml 生成 TS 枚举联合（--check 为只读比对）")
    ap.add_argument("--check", action="store_true",
                    help="只读：比对生成物是否最新，不写盘，不一致时 exit 1")
    ap.add_argument("--spec", type=pathlib.Path, default=DEFAULT_SPEC)
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    if args.check:
        return check(args.spec, args.out)
    text = render(args.spec)                 # 校验全部在这里面完成
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
