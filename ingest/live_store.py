"""OpenDota 公共比赛的事务写入边界。"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from ingest.load_bootstrap import detect_anomaly, normalize_actions
from shared.draft_template import first_pick_team_from_actions


class SourceConflict(RuntimeError):
    pass


PARSED_MARKERS = (
    "damage_taken",
    "lane_role",
    "obs_placed",
)

PLAYER_REFRESH_FIELDS = (
    ("kills", "kills"),
    ("deaths", "deaths"),
    ("assists", "assists"),
    ("gpm", "gold_per_min"),
    ("xpm", "xp_per_min"),
    ("last_hits", "last_hits"),
    ("denies", "denies"),
    ("hero_damage", "hero_damage"),
    ("hero_healing", "hero_healing"),
    ("tower_damage", "tower_damage"),
    ("damage_taken", "damage_taken"),
    ("net_worth", "net_worth"),
    ("obs_placed", "obs_placed"),
    ("sen_placed", "sen_placed"),
    ("observer_kills", "observer_kills"),
    ("sentry_kills", "sentry_kills"),
    ("camps_stacked", "camps_stacked"),
    ("rune_pickups", "rune_pickups"),
    ("lane_role", "lane_role"),
    ("is_roaming", "is_roaming"),
    ("firstblood_claimed", "firstblood_claimed"),
)


def parsed_player_stats(player: Mapping[str, object]) -> bool:
    return any(key in player and player[key] is not None for key in PARSED_MARKERS)


def normalize_slot(raw_slot: int) -> int:
    slot = int(raw_slot)
    return slot if slot < 128 else slot - 123


def _positive_id(value):
    if value is None:
        return None
    value = int(value)
    return value if value > 0 else None


def _optional_bool(value):
    if value is None or isinstance(value, bool):
        return value
    if value in (0, 1, "0", "1"):
        return bool(int(value))
    raise ValueError(f"布尔字段只接受 0/1/true/false，收到 {value!r}")


def _upsert_entities(conn, payload: Mapping[str, object]) -> tuple[int | None, int | None, int | None]:
    team_values = []
    for side in ("radiant", "dire"):
        nested = payload.get(f"{side}_team")
        nested = nested if isinstance(nested, Mapping) else {}
        team_id = _positive_id(payload.get(f"{side}_team_id") or nested.get("team_id"))
        name = (
            payload.get(f"{side}_name")
            or payload.get(f"{side}_team_name")
            or nested.get("name")
        )
        tag = payload.get(f"{side}_tag") or nested.get("tag")
        if team_id is not None and name:
            team_values.append((team_id, str(name), str(tag) if tag else None))
    if team_values:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO teams(team_id,name,tag) VALUES (%s,%s,%s)
                   ON CONFLICT(team_id) DO UPDATE SET name=EXCLUDED.name,
                     tag=COALESCE(EXCLUDED.tag,teams.tag)""",
                team_values,
            )
    radiant_nested = payload.get("radiant_team")
    dire_nested = payload.get("dire_team")
    radiant = _positive_id(
        payload.get("radiant_team_id")
        or (radiant_nested.get("team_id") if isinstance(radiant_nested, Mapping) else None)
    )
    dire = _positive_id(
        payload.get("dire_team_id")
        or (dire_nested.get("team_id") if isinstance(dire_nested, Mapping) else None)
    )
    known = {row[0] for row in conn.execute(
        "SELECT team_id FROM teams WHERE team_id=ANY(%s)",
        ([x for x in (radiant, dire) if x is not None],),
    )} if radiant or dire else set()
    radiant = radiant if radiant in known else None
    dire = dire if dire in known else None

    nested_league = payload.get("league")
    nested_league = nested_league if isinstance(nested_league, Mapping) else {}
    league_id = _positive_id(
        payload.get("leagueid")
        or payload.get("league_id")
        or nested_league.get("leagueid")
        or nested_league.get("league_id")
    )
    league_name = payload.get("league_name") or nested_league.get("name")
    if league_id is not None and league_name:
        conn.execute(
            """INSERT INTO leagues(league_id,name,source) VALUES (%s,%s,'opendota')
               ON CONFLICT(league_id) DO UPDATE SET name=EXCLUDED.name""",
            (league_id, str(league_name)),
        )
    elif league_id is not None:
        known_league = conn.execute(
            "SELECT 1 FROM leagues WHERE league_id=%s", (league_id,)
        ).fetchone()
        league_id = league_id if known_league else None
    return radiant, dire, league_id


def _ensure_public_target(conn, match_id: int) -> tuple | None:
    row = conn.execute(
        """SELECT data_source,draft_state,n_draft_actions,anomaly,first_pick_team,
                  parse_state,first_blood_time,first_tower_time,first_roshan_time,game_mode """
        "FROM matches WHERE match_id=%s",
        (match_id,),
    ).fetchone()
    if row is not None and row[0] == "scrim":
        raise SourceConflict(f"match_id={match_id} 已属于 scrim，公共采集器拒绝覆盖")
    return row


def _patch_id(conn, start_time: int) -> int | None:
    row = conn.execute(
        "SELECT patch_id FROM patches WHERE released_at <= to_timestamp(%s) "
        "ORDER BY released_at DESC LIMIT 1",
        (start_time,),
    ).fetchone()
    return row[0] if row else None


def discover_public_match(conn, summary: Mapping[str, object]) -> bool:
    match_id = int(summary["match_id"])
    existing = _ensure_public_target(conn, match_id)
    radiant, dire, league = _upsert_entities(conn, summary)
    start = int(summary["start_time"])
    written = conn.execute(
        """INSERT INTO matches
           (match_id,data_source,patch_id,started_at,duration_s,league_id,series_id,
            series_type,radiant_team_id,dire_team_id,radiant_win,draft_state)
           VALUES (%s,'pro_match',%s,to_timestamp(%s),%s,%s,%s,%s,%s,%s,%s,'pending')
           ON CONFLICT(match_id) DO UPDATE SET
             patch_id=COALESCE(EXCLUDED.patch_id,matches.patch_id),
             started_at=EXCLUDED.started_at,
             duration_s=COALESCE(EXCLUDED.duration_s,matches.duration_s),
             league_id=COALESCE(EXCLUDED.league_id,matches.league_id),
             series_id=COALESCE(EXCLUDED.series_id,matches.series_id),
             series_type=COALESCE(EXCLUDED.series_type,matches.series_type),
             radiant_team_id=COALESCE(EXCLUDED.radiant_team_id,matches.radiant_team_id),
             dire_team_id=COALESCE(EXCLUDED.dire_team_id,matches.dire_team_id),
             radiant_win=COALESCE(EXCLUDED.radiant_win,matches.radiant_win)
           WHERE matches.data_source='pro_match'
           RETURNING data_source""",
        (
            match_id,
            _patch_id(conn, start),
            start,
            summary.get("duration") or summary.get("duration_s"),
            league,
            summary.get("series_id"),
            summary.get("series_type"),
            radiant,
            dire,
            summary.get("radiant_win"),
        ),
    ).fetchone()
    if written is None or written[0] != "pro_match":
        raise SourceConflict(f"match_id={match_id} 已属于 scrim，公共采集器拒绝覆盖")
    return existing is None


def public_match_needs_detail(conn, match_id: int) -> bool:
    row = conn.execute(
        """SELECT m.draft_state,m.game_mode,
                  EXISTS(SELECT 1 FROM match_players p WHERE p.match_id=m.match_id)
           FROM matches m WHERE m.match_id=%s AND m.data_source='pro_match'""",
        (match_id,),
    ).fetchone()
    return bool(row and (row[0] != "complete" or row[1] is None or not row[2]))


def _objective_times(payload: Mapping[str, object]) -> tuple[int | None, int | None]:
    towers: list[int] = []
    roshans: list[int] = []
    for event in payload.get("objectives") or []:
        kind = str(event.get("type") or "").lower()
        key = str(event.get("key") or "").lower()
        when = event.get("time")
        if when is None:
            continue
        if "building" in kind and "tower" in key and "rax" not in key and "barrack" not in key:
            towers.append(int(when))
        if "roshan" in kind or "roshan" in key:
            roshans.append(int(when))
    return (min(towers) if towers else None, min(roshans) if roshans else None)


def _replace_players(conn, match_id: int, players: Sequence[Mapping[str, object]]) -> None:
    if not players:
        return
    conn.execute("DELETE FROM match_players WHERE match_id=%s", (match_id,))
    rows = []
    accounts = []
    for player in players:
        slot = normalize_slot(int(player["player_slot"]))
        account_id = _positive_id(player.get("account_id"))
        if account_id is not None:
            accounts.append((account_id, player.get("name")))
        damage = player.get("damage_taken")
        total = None
        if isinstance(damage, Mapping):
            total = sum(int(value or 0) for value in damage.values())
        rows.append((
            match_id, slot, account_id, 0 if slot < 5 else 1, int(player["hero_id"]), None,
            player.get("kills"), player.get("deaths"), player.get("assists"),
            player.get("gold_per_min"), player.get("xp_per_min"), player.get("last_hits"),
            player.get("denies"), player.get("hero_damage"), player.get("hero_healing"),
            player.get("tower_damage"), json.dumps(damage) if isinstance(damage, Mapping) else None,
            total, player.get("net_worth"), player.get("obs_placed"), player.get("sen_placed"),
            player.get("observer_kills"), player.get("sentry_kills"), player.get("camps_stacked"),
            player.get("rune_pickups"), player.get("lane_role"),
            _optional_bool(player.get("is_roaming")),
            _optional_bool(player.get("firstblood_claimed")), parsed_player_stats(player),
        ))
    if accounts:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO players(account_id,name,is_pro) VALUES (%s,%s,true)
                   ON CONFLICT(account_id) DO UPDATE SET
                     name=COALESCE(EXCLUDED.name,players.name),is_pro=true""",
                accounts,
            )
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO match_players
               (match_id,player_slot,account_id,team,hero_id,position,kills,deaths,assists,gpm,xpm,
                last_hits,denies,hero_damage,hero_healing,tower_damage,damage_taken,
                damage_taken_total,net_worth,obs_placed,sen_placed,observer_kills,sentry_kills,
                camps_stacked,rune_pickups,lane_role,is_roaming,firstblood_claimed,stats_available)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                       %s,%s,%s,%s,%s,%s,%s,%s)""",
            rows,
        )


def _full_player_refresh_is_safe(
    conn, match_id: int, players: Sequence[Mapping[str, object]]
) -> bool:
    """既有 full 只接受身份一致且不丢任何既有非空字段的整组刷新。"""
    columns = [name for name, _ in PLAYER_REFRESH_FIELDS]
    existing = conn.execute(
        "SELECT player_slot,account_id,hero_id," + ",".join(columns)
        + " FROM match_players WHERE match_id=%s ORDER BY player_slot",
        (match_id,),
    ).fetchall()
    if not existing:
        return True
    incoming = {}
    for player in players:
        slot = normalize_slot(int(player["player_slot"]))
        if slot in incoming:
            raise ValueError(f"match_id={match_id} 重复 player_slot={slot}")
        incoming[slot] = player
    existing_slots = {int(row[0]) for row in existing}
    # 身份冲突必须先完整扫描全部 slot。若与字段完整性混在同一轮，前面 slot 的字段缺失
    # 会提前 return False，掩盖后面 slot 的身份变化，让本场其它元数据部分提交。
    for row in existing:
        slot, old_account, old_hero, *_old_values = row
        if int(slot) not in incoming:
            continue
        player = incoming[int(slot)]
        new_account = _positive_id(player.get("account_id"))
        new_hero = int(player["hero_id"])
        if new_account != old_account or new_hero != old_hero:
            raise ValueError(
                f"match_id={match_id} slot={slot} 身份变化，拒绝把旧解析统计拼给新选手或英雄"
            )
    if set(incoming) != existing_slots:
        missing = sorted(existing_slots - set(incoming))
        extra = sorted(set(incoming) - existing_slots)
        raise ValueError(
            f"match_id={match_id} 玩家 slot 集合不完整，missing={missing}, extra={extra}"
        )
    for row in existing:
        slot, _old_account, _old_hero, *old_values = row
        player = incoming[int(slot)]
        for old_value, (_db_name, payload_name) in zip(old_values, PLAYER_REFRESH_FIELDS):
            if old_value is None:
                continue
            new_value = player.get(payload_name)
            if new_value is None:
                return False
            if isinstance(old_value, Mapping) and old_value and (
                not isinstance(new_value, Mapping) or not new_value
            ):
                return False
    return True


def upsert_public_match(conn, summary: Mapping[str, object], detail: Mapping[str, object]) -> str:
    """整场详情原子写入，任一 FK 或结构错误会回滚本场全部改动。"""
    with conn.transaction():
        return _upsert_public_match(conn, summary, detail)


def _upsert_public_match(conn, summary: Mapping[str, object], detail: Mapping[str, object]) -> str:
    match_id = int(detail.get("match_id") or summary["match_id"])
    existing = _ensure_public_target(conn, match_id)
    merged = dict(summary)
    merged.update(detail)
    discover_public_match(conn, merged)

    raw_value = detail.get("picks_bans")
    if raw_value is not None and not isinstance(raw_value, list):
        raise ValueError("picks_bans 必须是数组或 null")
    raw_actions = raw_value or []
    raw_nonempty = bool(raw_actions)
    actions, problems = normalize_actions(raw_actions, ord_origin=0) if raw_actions else ([], {
        "ord_out_of_range": 0,
        "duplicate_ord": 0,
    })
    first_pick = None
    if actions:
        try:
            first_pick = first_pick_team_from_actions(actions)
        except ValueError:
            pass
    anomaly = detect_anomaly(actions, first_pick, **problems)
    game_mode = int(detail.get("game_mode") or 0) or None
    non_cm = game_mode is not None and game_mode != 2
    preserve_draft = not actions and existing is not None and existing[1] == "complete"
    if preserve_draft:
        draft_state, n_actions, anomaly_flag, first_pick = existing[1:5]
    else:
        draft_state = "complete" if raw_nonempty else ("unavailable" if non_cm else "pending")
        n_actions = len(actions) if raw_nonempty else None
        anomaly_flag = anomaly is not None

    first_tower, first_roshan = _objective_times(detail)
    players = detail.get("players") or []
    incoming_full = len(players) == 10 and all(parsed_player_stats(p) for p in players)
    existing_parse = existing[5] if existing is not None else "unparsed"
    safe_full_refresh = True
    if existing_parse == "full" and players:
        safe_full_refresh = _full_player_refresh_is_safe(conn, match_id, players)
    if incoming_full and safe_full_refresh:
        parse_state = "full"
        replace_players = True
    elif existing_parse == "full":
        parse_state = "full"
        replace_players = False
    elif players:
        parse_state = "header_only"
        replace_players = True
    else:
        parse_state = existing_parse
        replace_players = False
    first_blood = detail.get("first_blood_time")
    if existing is not None:
        first_blood = first_blood if first_blood is not None else existing[6]
        first_tower = first_tower if first_tower is not None else existing[7]
        first_roshan = first_roshan if first_roshan is not None else existing[8]
        game_mode = game_mode if game_mode is not None else existing[9]
    conn.execute(
        """UPDATE matches SET first_pick_team=%s,lobby_type=%s,first_blood_time=%s,
           first_tower_time=%s,first_roshan_time=%s,draft_state=%s,n_draft_actions=%s,
           anomaly=%s,parse_state=%s,game_mode=%s WHERE match_id=%s AND data_source='pro_match'""",
        (
            first_pick,
            detail.get("lobby_type"),
            first_blood,
            first_tower,
            first_roshan,
            draft_state,
            n_actions,
            anomaly_flag,
            parse_state,
            game_mode,
            match_id,
        ),
    )
    if raw_nonempty and not preserve_draft:
        conn.execute("DELETE FROM draft_actions WHERE match_id=%s", (match_id,))
        if actions:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO draft_actions(match_id,ord,is_pick,team,hero_id) VALUES (%s,%s,%s,%s,%s)",
                    [(match_id, a["ord"], a["is_pick"], a["team"], a["hero_id"]) for a in actions],
                )
        if anomaly:
            conn.execute(
                """INSERT INTO draft_anomalies(match_id,n_actions,kinds,detail)
                   VALUES (%s,%s,%s,%s) ON CONFLICT(match_id) DO UPDATE SET
                   n_actions=EXCLUDED.n_actions,kinds=EXCLUDED.kinds,detail=EXCLUDED.detail,
                   detected_at=now()""",
                (match_id, anomaly["n_actions"], anomaly["kinds"], json.dumps(anomaly["detail"])),
            )
        else:
            conn.execute("DELETE FROM draft_anomalies WHERE match_id=%s", (match_id,))
    if replace_players:
        _replace_players(conn, match_id, players)
    conn.execute(
        """UPDATE matches SET
             attempt_count=(SELECT count(*) FROM collector_attempts a WHERE a.match_id=matches.match_id),
             last_attempt_at=(SELECT max(at) FROM collector_attempts a WHERE a.match_id=matches.match_id)
           WHERE match_id=%s AND data_source='pro_match'""",
        (match_id,),
    )
    return "complete" if raw_nonempty or preserve_draft else ("non_cm" if non_cm else "not_ready")
