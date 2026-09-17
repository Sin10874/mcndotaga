from __future__ import annotations
from ._http import fetch_json

OPENDOTA = "https://api.opendota.com/api"

def fetch_heroes() -> list[dict]:
    raw = fetch_json(f"{OPENDOTA}/constants/heroes", "opendota_heroes.json")
    return list(raw.values())          # OpenDota 以 hero_id 为键返回

def fetch_items() -> list[dict]:
    raw = fetch_json(f"{OPENDOTA}/constants/items", "opendota_items.json")
    return list(raw.values())

def derive_token_index(heroes: list[dict]) -> dict[int, int]:
    """规格 §5.1：dense_index = 按 hero_id 升序排序后的 0-based 下标。

    hero_id 稀疏（1..155，含 9 个 > 126），不能直接当 token 用。
    """
    return {h["id"]: i for i, h in enumerate(sorted(heroes, key=lambda h: h["id"]))}
