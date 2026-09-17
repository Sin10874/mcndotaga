"""规格 §7 维度 6 的 roles → 原型 映射。

roles 词表实测只有 8 项（Carry/Disabler/Durable/Escape/Initiator/
Nuker/Pusher/Support）——没有 Jungler，也没有任何一项等于六类原型名。
teamfight 必须是**析取式**：上一版的合取式会让 8 个英雄零命中。
"""
from __future__ import annotations
from collections.abc import Callable

RULES: list[tuple[str, Callable[[set[str]], bool]]] = [
    ("initiate",   lambda r: "Initiator" in r),
    ("push",       lambda r: "Pusher" in r),
    ("pickoff",    lambda r: "Escape" in r and "Nuker" in r),
    ("splitpush",  lambda r: "Carry" in r and "Escape" in r and "Pusher" not in r),
    ("protect",    lambda r: "Support" in r and ("Nuker" in r or "Durable" in r)),
    ("teamfight",  lambda r: "Durable" in r or "Disabler" in r
                             or ("Carry" in r and "Nuker" in r)),
]

def archetypes_for(roles: list[str]) -> list[str]:
    r = set(roles or [])
    return [name for name, pred in RULES if pred(r)]
