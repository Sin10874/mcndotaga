"""统一的 HTTP 拉取：带自定义 User-Agent + 本地缓存。

实测：OpenDota 对 `Python-urllib/3.12` 返回 403，但对
python-httpx / python-requests / 无 UA 均返回 200。
仍然锚定一个明确的 UA —— 这是对公共 API 的基本礼貌，也让流量可追溯。
"""
from __future__ import annotations
import json, os, pathlib
import httpx

UA = "Dota2DraftAnalysis/0.1 (https://github.com/yourname/mcndotaga)"
CACHE = pathlib.Path(__file__).parents[1] / "tests" / "fixtures" / "network"

def fetch_json(url: str, cache_name: str) -> dict | list:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / cache_name
    if path.exists() and not os.environ.get("REFRESH_NETWORK"):
        return json.loads(path.read_text(encoding="utf-8"))
    resp = httpx.get(url, headers={"User-Agent": UA}, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data
