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
_TRUTHY = {"1", "true", "yes"}

def _refresh() -> bool:
    """严格的刷新开关：只有 1/true/yes（忽略大小写与空白）才算开启。

    **不能**用 os.environ.get(...) 的真值判断：那样 REFRESH_NETWORK=0 与
    =false 都会被当成「开启刷新」——把「关」写成 0 是最自然的写法，后果却是
    每次跑测试都打网络，且覆盖掉已提交的缓存字节。故只有 1/true/yes 算刷新，
    空串与 0/false/no 一律是关闭。
    """
    return os.environ.get("REFRESH_NETWORK", "").strip().lower() in _TRUTHY

def _cache_dir() -> pathlib.Path:
    """缓存根目录。可用 MCNDOTAGA_CACHE_DIR 覆盖（指向只读介质时也能命中缓存）。"""
    override = os.environ.get("MCNDOTAGA_CACHE_DIR")
    return pathlib.Path(override) if override else CACHE

def fetch_json(url: str, cache_name: str) -> dict | list:
    """拉取并解析 JSON；命中缓存则不碰网络。

    `follow_redirects=True` 是**必须**的：上游若把 canonical URL 302 到别处（raw → CDN 镜像这一类），
    跟随才拿得到 JSON；不跟随时 `raise_for_status()` 会抛一个只说「302 Found」的 HTTPStatusError，
    把"这个 URL 只是转发"伪装成一次真实的上游故障。
    副作用：跟随重定向后**原始来源不可辨** —— raw 被 302 到镜像时，落盘的字节来自镜像而不是 `url`
    参数，故缓存文件记录的是「谁最终返回了 JSON」，不能当作「canonical URL 可达」的证据。
    """
    path = _cache_dir() / cache_name
    if path.exists() and not _refresh():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"缓存文件已损坏，无法解析：{path}\n"
                f"  恢复方式二选一：REFRESH_NETWORK=1 重新拉取，或删除该文件。\n"
                f"  解析错误：{exc}") from exc
    resp = httpx.get(url, headers={"User-Agent": UA}, timeout=60, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()          # 必须先于写盘：非 JSON 响应绝不落进缓存
    path.parent.mkdir(parents=True, exist_ok=True)   # 只在写路径上建目录
    path.write_bytes(resp.content)   # 落**原始字节**，缓存与上游可逐字节比对
    return data
