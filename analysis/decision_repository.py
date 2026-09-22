"""Playbook 与 Advise 的只读元数据和公开样本统计。"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from constants.archetypes import archetypes_for
from db.sources import resolve_sources
from ingest.order_families import SPEC_FAMILY, family_for


DEGRADED = {"value": None, "reason": "insufficient_samples"}
UTC = timezone.utc
_PUBLIC_VIEW = "opponent_profile_matches_with_pub"


def _sample(items: list[dict]) -> dict:
    return {"wr": sum(bool(item["won"]) for item in items) / len(items), "n": len(items)}


def _recommendation(option: dict, min_delta: float) -> str:
    values = []
    for key, field in (("if_we_pick", "wr"), ("if_we_ban", "wr"), ("if_we_leave", "our_wr")):
        row = option[key]
        if field not in row:
            return "insufficient_data"
        values.append((key, float(row[field])))
    values.sort(key=lambda item: item[1], reverse=True)
    if values[0][1] - values[1][1] < min_delta:
        return "insufficient_data"
    if values[0][0] == "if_we_pick":
        return "pick"
    if values[0][0] == "if_we_ban":
        return "ban"
    counters = option["if_we_leave"].get("our_counter_options") or []
    if counters and max(row["wr"] for row in counters) > values[0][1]:
        return "leave_and_counter"
    return "ban"


def build_op_hero_decisions(rows: list[dict], *, min_n: int = 30,
                            min_delta: float = .02) -> list[dict]:
    """从已标明 pick、ban、leave、counter 的公开样本构建 OP 三路径。"""
    grouped: dict[int, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    seen_rows = set()
    for row in rows:
        hero_id = row.get("hero_id")
        mode = row.get("mode")
        identity = ((row.get("match_id"), hero_id, mode, row.get("counter_hero_id"))
                    if row.get("match_id") is not None else None)
        if identity is not None and identity in seen_rows:
            continue
        if identity is not None:
            seen_rows.add(identity)
        if isinstance(hero_id, int) and hero_id > 0 and mode in {"pick", "ban", "leave", "counter"}:
            grouped[hero_id][mode].append(row)
    result = []
    for hero_id in sorted(grouped):
        modes = grouped[hero_id]
        pick = _sample(modes["pick"]) if len(modes["pick"]) >= min_n else dict(DEGRADED)
        ban = _sample(modes["ban"]) if len(modes["ban"]) >= min_n else dict(DEGRADED)
        leave_rows = modes["leave"]
        if len(leave_rows) >= min_n:
            counters = []
            counter_groups: dict[int, list[dict]] = defaultdict(list)
            for row in modes["counter"]:
                counter = row.get("counter_hero_id")
                if isinstance(counter, int) and counter > 0:
                    counter_groups[counter].append(row)
            for counter_id, items in sorted(counter_groups.items()):
                if len(items) >= min_n:
                    sample = _sample(items)
                    counters.append({"hero_id": counter_id, **sample})
            counters.sort(key=lambda item: (-item["wr"], -item["n"], item["hero_id"]))
            our_wr = sum(bool(item["won"]) for item in leave_rows) / len(leave_rows)
            leave = {"our_wr": our_wr, "their_wr": 1 - our_wr,
                     "n": len(leave_rows), "our_counter_options": counters}
        else:
            leave = dict(DEGRADED)
        option = {"hero_id": hero_id, "if_we_pick": pick, "if_we_ban": ban,
                  "if_we_leave": leave, "recommendation": "insufficient_data"}
        option["recommendation"] = _recommendation(option, min_delta)
        result.append(option)
    return result


def _snapshot_hero_ids(raw: Any) -> list[int]:
    if isinstance(raw, list):
        return sorted({int(value) for value in raw if isinstance(value, int) and value > 0})
    if isinstance(raw, dict):
        return sorted(int(key) for key, enabled in raw.items()
                      if enabled and str(key).isdigit() and int(key) > 0)
    return []


def _utc_window(raw_as_of: str | None, now: datetime | None = None) -> tuple[date, datetime, datetime]:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("now 必须含时区")
    current = current.astimezone(UTC)
    as_of = date.fromisoformat(raw_as_of or current.date().isoformat())
    lower = datetime.combine(as_of - timedelta(days=89), time.min, UTC)
    day_end = datetime.combine(as_of + timedelta(days=1), time.min, UTC)
    return as_of, lower, min(current, day_end)


def _ended_by(match: dict, cutoff: datetime) -> bool:
    duration = match.get("duration_s")
    started = match.get("started_at")
    return (isinstance(started, datetime) and started.tzinfo is not None
            and isinstance(duration, int) and not isinstance(duration, bool) and duration > 0
            and started.astimezone(UTC) + timedelta(seconds=duration) <= cutoff)


def _series_first_pick_team(match: dict, request: dict) -> int | None:
    historical_side = match.get("first_pick_team")
    if historical_side not in (0, 1):
        return None
    team_id = match["radiant_team_id"] if historical_side == 0 else match["dire_team_id"]
    if team_id == request["us"]:
        return request["us_side"]
    if team_id == request["them"]:
        return 1 - request["us_side"]
    return None


def load_decision_metadata(dsn: str, request: dict) -> dict:
    """在只读快照中装配决策元数据，开发库不会被写入。"""
    sources = resolve_sources(list(request.get("sources") or ["pro_match"]))
    us_side = request.get("us_side")
    if us_side not in (0, 1):
        raise ValueError("us_side 必须明确为 0 或 1")
    as_of, lower, cutoff = _utc_window(request.get("as_of"))
    with psycopg.connect(dsn.replace("postgresql+psycopg://", "postgresql://"),
                         connect_timeout=5, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '15s'")
        teams = list(conn.execute(
            "SELECT team_id, name, tag FROM teams WHERE team_id = ANY(%s)",
            ([request["us"], request["them"]],),
        ))
        refs = {row["team_id"]: {"team_id": row["team_id"],
                                 "name": row["name"] or "",
                                 "tag": row["tag"] or ""} for row in teams}
        if set(refs) != {request["us"], request["them"]}:
            raise ValueError("未知的 us 或 them 战队")
        patch = conn.execute(
            "SELECT patch_id, is_cm_pool_snapshot FROM patches WHERE version_name = %s",
            (request["patch"],),
        ).fetchone()
        if patch is None:
            raise ValueError("未知 patch")
        hero_ids = _snapshot_hero_ids(patch["is_cm_pool_snapshot"])
        pool_basis = "historical_snapshot"
        if not hero_ids:
            hero_ids = [row["hero_id"] for row in conn.execute(
                "SELECT hero_id FROM heroes WHERE cm_enabled IS TRUE ORDER BY hero_id"
            )]
            pool_basis = "current_constants"
        role_rows = list(conn.execute(
            "SELECT hero_id, roles FROM heroes WHERE hero_id = ANY(%s) ORDER BY hero_id",
            (hero_ids,),
        ))
        configs = {row["key"]: row["value"] for row in conn.execute(
            "SELECT key, value FROM app_config_kv WHERE key = ANY(%s)",
            (["robustness_lambda", "min_sample_n", "op_decision_min_delta"],),
        )}
        matches = list(conn.execute(
            f"""SELECT m.match_id, m.radiant_team_id, m.dire_team_id, m.radiant_win,
                      m.draft_state, m.anomaly, m.parse_state, m.started_at, m.data_source,
                      m.first_pick_team, m.n_draft_actions, m.game_mode, m.duration_s, m.series_id
               FROM {_PUBLIC_VIEW} m JOIN patches p USING (patch_id)
               WHERE p.version_name = %s AND m.data_source = ANY(%s)
                 AND m.started_at >= %s AND m.started_at < %s
                 AND m.game_mode = 2
                 AND (m.radiant_team_id = ANY(%s) OR m.dire_team_id = ANY(%s))""",
            (request["patch"], sources, lower, cutoff,
             [request["us"], request["them"]], [request["us"], request["them"]]),
        ))
        match_ids = [row["match_id"] for row in matches]
        actions = list(conn.execute(
            "SELECT match_id, ord, is_pick, team, hero_id FROM draft_actions WHERE match_id = ANY(%s)",
            (match_ids or [0],),
        ))
    by_match: dict[int, list[dict]] = defaultdict(list)
    for action in actions:
        by_match[action["match_id"]].append(action)
    ended_matches = [match for match in matches if _ended_by(match, cutoff)]
    eligible_ids = set()
    for match in ended_matches:
        draft = sorted(by_match[match["match_id"]], key=lambda row: row["ord"])
        if (match["draft_state"] == "complete" and not match["anomaly"]
                and family_for(match["n_draft_actions"], match["first_pick_team"], draft) == SPEC_FAMILY):
            eligible_ids.add(match["match_id"])
    op_rows = []
    for match in ended_matches:
        if match["radiant_win"] is None or match["match_id"] not in eligible_ids:
            continue
        us_team = 0 if match["radiant_team_id"] == request["us"] else 1 if match["dire_team_id"] == request["us"] else None
        them_team = 0 if match["radiant_team_id"] == request["them"] else 1 if match["dire_team_id"] == request["them"] else None
        won = bool(match["radiant_win"]) == (us_team == 0) if us_team is not None else False
        draft = by_match[match["match_id"]]
        if us_team is not None:
            for action in draft:
                if action["team"] == us_team:
                    op_rows.append({"match_id": match["match_id"], "hero_id": action["hero_id"],
                                    "mode": "pick" if action["is_pick"] else "ban", "won": won})
        if us_team is not None and them_team is not None:
            their_picks = [a["hero_id"] for a in draft if a["team"] == them_team and a["is_pick"]]
            our_picks = [a["hero_id"] for a in draft if a["team"] == us_team and a["is_pick"]]
            for hero_id in their_picks:
                op_rows.append({"match_id": match["match_id"], "hero_id": hero_id,
                                "mode": "leave", "won": won})
                for counter in our_picks:
                    op_rows.append({"match_id": match["match_id"], "hero_id": hero_id,
                                    "mode": "counter", "won": won,
                                    "counter_hero_id": counter})
    target_matches = [row for row in matches if request["them"] in (
        row["radiant_team_id"], row["dire_team_id"]
    )]
    coverage = {}
    for source in ("pro_match", "pub_match"):
        scoped = [row for row in target_matches if row["data_source"] == source]
        coverage[source] = {"n_matches": len(scoped),
                            "n_stat_available": sum(row["parse_state"] == "full" for row in scoped)}
    pending = [row for row in target_matches if row["draft_state"] == "pending"]
    oldest = None
    if pending:
        oldest_start = min(row["started_at"] for row in pending)
        end = datetime.combine(as_of + timedelta(days=1), datetime.min.time(), timezone.utc)
        oldest = max(0, int((end - oldest_start).total_seconds() // 3600))
    data_quality = {
        "n_pending_draft": len(pending),
        "n_unavailable_draft": sum(row["draft_state"] == "unavailable" for row in target_matches),
        "n_anomalous_draft": sum(bool(row["anomaly"]) for row in target_matches),
        "oldest_pending_hours": oldest,
        "note": (
            "仅统计指定版本、截止日期与公开来源内的对手比赛。"
            + ("候选英雄池使用该版本历史快照。" if pool_basis == "historical_snapshot"
               else "该版本缺少历史 CM 池快照，候选仅使用当前常量，不宣称历史合法池。")
        ),
    }
    min_n = max(30, int(configs.get("min_sample_n", 30)))
    opening_hero_ids = {name: [] for name in (
        "teamfight", "push", "pickoff", "splitpush", "protect", "initiate"
    )}
    for row in role_rows:
        for name in archetypes_for(list(row["roles"] or [])):
            opening_hero_ids[name].append(row["hero_id"])
    positions = [
        {"side": side, "role": role, "notes": [{
            "kind": "ward", "text": "眼位坐标需要回放解析，Phase A 不提供坐标结论。",
            "evidence": [], "value": None, "reason": "needs_replay", "needs": "Phase B",
        }]}
        for side in ("us", "them") for role in range(1, 6)
    ]
    series = None
    if request.get("series_id") is not None:
        series_id = request["series_id"]
        series_matches = sorted(
            [row for row in ended_matches if row["series_id"] == series_id
             and request["us"] in (row["radiant_team_id"], row["dire_team_id"])
             and request["them"] in (row["radiant_team_id"], row["dire_team_id"])],
            key=lambda row: (row["started_at"], row["match_id"]),
        )
        if series_matches:
            series = {
                "series_id": series_id,
                "games": [{"game_no": index,
                           "first_pick_team": _series_first_pick_team(row, request)}
                          for index, row in enumerate(series_matches, 1)],
                "game1_plan": "按公开画像与第一局已知先手权执行，赛后重新计算。",
                "adjustment_rules": [{"if": "对手实际开局偏离赛前最高概率体系",
                                      "then": "使用新 BP 前缀重新调用 Advise，不沿用旧分支。"}],
            }
    return {
        "side_map": {"us": us_side, "them": 1 - us_side},
        "hero_ids": hero_ids, "pool_basis": pool_basis,
        "opening_hero_ids": opening_hero_ids,
        "robustness_lambda": float(configs.get("robustness_lambda", 1.0)),
        "as_of": as_of.isoformat(),
        "team_refs": {"us": refs[request["us"]], "them": refs[request["them"]]},
        "coverage": coverage, "data_quality": data_quality,
        "op_hero_decision": build_op_hero_decisions(
            op_rows, min_n=min_n, min_delta=float(configs.get("op_decision_min_delta", .02))
        ),
        "positions": positions, "series": series,
    }
