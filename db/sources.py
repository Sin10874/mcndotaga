"""规格 §4.1 的隔离规则。

对手侧模块（playbook / value 的对手侧 / profile / policy / advise）
调用时必须 allow_scrim=False（默认值）。
"""
from __future__ import annotations

ALLOWED = {"pro_match", "pub_match", "scrim"}

class SourceNotAllowed(ValueError):
    pass

def resolve_sources(requested: list[str], *, allow_scrim: bool = False) -> list[str]:
    unknown = set(requested) - ALLOWED
    if unknown:
        raise SourceNotAllowed(f"unknown sources: {sorted(unknown)}")
    if "scrim" in requested and not allow_scrim:
        raise SourceNotAllowed("scrim data may never reach opponent-side paths (spec §4.1)")
    return sorted(set(requested))
