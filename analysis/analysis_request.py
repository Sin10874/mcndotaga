"""对局分析的公开请求边界。"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import re

from db.sources import resolve_sources
from shared.draft_template import resolve


def positive_id(value, name):
    if type(value) is not int or not 0 < value < 2**63:
        raise ValueError(f"{name} 必须是正整数")
    return value


def side(value, name):
    if type(value) is not int or value not in (0, 1):
        raise ValueError(f"{name} 必须明确指定 0（天辉）或 1（夜魇）")
    return value


def validate_draft(draft, first_pick_team, *, allow_complete=False):
    if not isinstance(draft, list) or len(draft) > (24 if allow_complete else 23):
        raise ValueError("draft 必须是合法的未结束 BP 前缀")
    seen = set()
    for index, action in enumerate(draft):
        if not isinstance(action, dict) or set(action) != {"ord", "team", "is_pick", "hero_id"}:
            raise ValueError("BP 每手必须包含 ord、team、is_pick、hero_id")
        expected_pick, expected_team = resolve(index, first_pick_team)
        if (type(action["ord"]) is not int or action["ord"] != index
                or type(action["team"]) is not int or action["team"] != expected_team
                or type(action["is_pick"]) is not bool or action["is_pick"] != expected_pick):
            raise ValueError(f"第 {index + 1} 手与当前 CM 规则不一致")
        hero = positive_id(action["hero_id"], "hero_id")
        if hero in seen:
            raise ValueError("已选或已禁的英雄不能重复出现")
        seen.add(hero)


def parse_analysis_request(body, resource, *, today=None):
    if not isinstance(body, dict):
        raise ValueError("请求必须是 JSON 对象")
    today = today or datetime.now(timezone.utc).date()
    common = {"patch", "sources", "as_of", "first_pick_team"}
    allowed = {
        "value": common | {"radiant", "dire"},
        "policy": common | {"radiant_team_id", "dire_team_id", "draft", "top_n"},
        "analysis": common | {"us", "them", "us_side", "draft", "top_n"},
        "advise": common | {"us", "them", "us_side", "draft", "mode", "branches", "top_n"},
        "playbook": common | {"us", "them", "us_side", "draft", "series_id", "top_n"},
    }
    if resource not in allowed or set(body) - allowed[resource]:
        raise ValueError("请求包含不支持的参数")
    patch = body.get("patch")
    if not isinstance(patch, str) or not re.fullmatch(r"[0-9]{1,2}\.[0-9]{1,3}[a-z]?", patch):
        raise ValueError("patch 必须是明确的精确版本")
    sources = body.get("sources", ["pro_match"])
    if not isinstance(sources, list) or not sources or any(not isinstance(x, str) or x not in {"pro_match", "pub_match", "scrim"} for x in sources):
        raise ValueError("sources 必须是明确的数据来源数组")
    sources = resolve_sources(sources)
    as_of = body.get("as_of", today.isoformat())
    try:
        if not isinstance(as_of, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
            raise ValueError()
        if not date.min + timedelta(days=89) <= date.fromisoformat(as_of) <= today:
            raise ValueError()
    except ValueError:
        raise ValueError("as_of 必须是有效 UTC 日期且不能晚于今天") from None
    result = {**body, "sources": list(sources), "as_of": as_of}
    side(body.get("first_pick_team"), "first_pick_team")
    if resource == "value":
        heroes = set()
        team_ids = []
        for name in ("radiant", "dire"):
            entry = body.get(name)
            if not isinstance(entry, dict) or set(entry) != {"team_id", "heroes"}:
                raise ValueError(f"{name} 必须包含 team_id 与 heroes")
            team_ids.append(positive_id(entry["team_id"], "team_id"))
            if not isinstance(entry["heroes"], list) or len(entry["heroes"]) > 5:
                raise ValueError("每方最多选择五名英雄")
            positions = set()
            for hero in entry["heroes"]:
                if not isinstance(hero, dict) or set(hero) - {"hero_id", "position"}:
                    raise ValueError("英雄项只允许 hero_id 和可选 position")
                hero_id = positive_id(hero.get("hero_id"), "hero_id")
                if hero_id in heroes:
                    raise ValueError("双方英雄不能重复")
                heroes.add(hero_id)
                if "position" in hero:
                    position = hero["position"]
                    if type(position) is not int or position not in range(1, 6) or position in positions:
                        raise ValueError("位置必须为 1 至 5 且同侧不能重复")
                    positions.add(position)
    else:
        keys = ("radiant_team_id", "dire_team_id") if resource == "policy" else ("us", "them")
        team_ids = [positive_id(body.get(key), key) for key in keys]
        if resource != "policy":
            side(body.get("us_side"), "us_side")
        result.setdefault("draft", [])
        validate_draft(result["draft"], result["first_pick_team"], allow_complete=resource == "analysis")
        result.setdefault("top_n", 10)
        if type(result["top_n"]) is not int or not 1 <= result["top_n"] <= 127:
            raise ValueError("top_n 必须为 1 至 127")
        if resource == "advise":
            result.setdefault("mode", "realtime")
            if result["mode"] not in ("realtime", "offline"):
                raise ValueError("mode 必须为 realtime 或 offline")
            if "branches" in result:
                choices = {f"{who}:{opening}" for who in ("us", "them") for opening in ("initiate", "protect", "push", "teamfight", "pickoff", "splitpush", "unknown")}
                branches = result["branches"]
                if not isinstance(branches, list) or not branches or any(not isinstance(item, str) or item not in choices for item in branches) or len(set(branches)) != len(branches):
                    raise ValueError("branches 必须是无重复的先手与体系组合")
        if "series_id" in result:
            positive_id(result["series_id"], "series_id")
    if team_ids[0] == team_ids[1]:
        raise ValueError("双方必须是不同战队")
    return result
