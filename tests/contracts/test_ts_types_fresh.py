"""TS 生成物的新鲜度与完整性。

路径一律由 ``__file__`` 推出仓库根，**不依赖 cwd**：从 ``tests/`` 里跑
pytest 与从仓库根跑必须同结果（仓库内其余测试同样 cwd 无关）。
"""
import pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).parents[2]
TS = ROOT / "web" / "src" / "types" / "contract.ts"


def test_generated_ts_is_up_to_date():
    before = TS.read_text(encoding="utf-8")
    subprocess.run([sys.executable, "-m", "contracts.tools.gen_ts_types"],
                   check=True, cwd=ROOT)
    assert before == TS.read_text(encoding="utf-8"), "contract.ts 已过期，运行 make contract-ts 并提交"


def test_ts_contains_all_required_enums():
    """生成器漏掉 §6.0 的枚举时失败。"""
    ts = TS.read_text(encoding="utf-8")
    for name in ["Confidence", "Recommendation", "TheirOpening", "NoteKind",
                 "UnavailableReason", "ErrorCode", "Side"]:
        assert f"export type {name} =" in ts, f"{name} 未生成"
