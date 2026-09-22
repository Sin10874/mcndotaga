"""战队与选手画像的纯计算引擎。"""
from __future__ import annotations

import datetime as dt
import math
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from constants.archetypes import archetypes_for
from ingest.order_families import SPEC_FAMILY, family_for


_ARCHETYPES = ("initiate", "protect", "push", "teamfight", "pickoff", "splitpush")
_POSITION_DIMENSIONS = ("hero_pool", "laning", "combat", "map_vision", "tempo")
_KNOWN_TIERS = {"tier1", "tier2", "qualifier", "other"}
_INSUFFICIENT = {"value": None, "reason": "insufficient_samples"}
_STAT_UNAVAILABLE = {"value": None, "reason": "stat_unavailable"}
_MAP_FIELDS = ("obs_placed", "sen_placed", "observer_kills", "sentry_kills",
               "camps_stacked", "rune_pickups")


class ProfileInsufficientData(ValueError):
    """画像的必需观测分母不存在。"""


def _round(value: float) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _as_date(value: object) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.astimezone(dt.timezone.utc).date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        try:
            parsed = dt.datetime.fromisoformat(text)
        except ValueError:
            return dt.date.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc).date()
    raise ValueError(f"无法解析日期：{value!r}")


def _started_at(match: dict) -> dt.datetime:
    value = match["started_at"]
    if isinstance(value, dt.datetime):
        parsed = value
    elif isinstance(value, dt.date):
        parsed = dt.datetime.combine(value, dt.time.min, tzinfo=dt.timezone.utc)
    else:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _team_side(match: dict, team_id: int) -> int | None:
    if match.get("radiant_team_id") == team_id:
        return 0
    if match.get("dire_team_id") == team_id:
        return 1
    return None


def _won(match: dict, row: dict) -> bool | None:
    if match.get("radiant_win") is None:
        return None
    return bool(match["radiant_win"]) == (int(row["team"]) == 0)


def _records(matches: Iterable[dict], account_id: int, *, stats: bool = False) -> list[tuple[dict, dict]]:
    result = []
    for match in matches:
        for row in match.get("players") or []:
            if row.get("account_id") != account_id:
                continue
            if stats and not row.get("stats_available"):
                continue
            result.append((match, row))
    return result


def _mode(values: Iterable[int | None]) -> int | None:
    counts = Counter(value for value in values if value in (1, 2, 3, 4, 5))
    if not counts:
        return None
    largest = max(counts.values())
    winners = [value for value, count in counts.items() if count == largest]
    return winners[0] if len(winners) == 1 else None


def _mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return sum(items) / len(items) if items else None


def _source_weight(dataset: dict, metric: str, source: str) -> float:
    return float((dataset.get("weights") or {}).get(metric, {}).get(source, 0.0))


def _weighted_source_value(grouped: dict[str, list[float]], dataset: dict,
                           metric: str) -> tuple[float | None, float]:
    numerator = 0.0
    denominator = 0.0
    for source, values in grouped.items():
        weight = _source_weight(dataset, metric, source)
        numerator += weight * sum(values)
        denominator += weight * len(values)
    return (numerator / denominator if denominator else None, denominator)


def _percentile(target: float, peers: list[float]) -> int:
    lower = 0
    equal = 0
    for value in peers:
        if math.isclose(value, target, rel_tol=1e-12, abs_tol=1e-12):
            equal += 1
        elif value < target:
            lower += 1
    return _round(100.0 * (lower + 0.5 * equal) / len(peers))


def _dimension(targets: list[float | None], peers: list[list[float]], minimum: int) -> dict:
    if any(value is None for value in targets):
        return dict(_STAT_UNAVAILABLE)
    counts = [len(values) for values in peers]
    if not counts or min(counts) < minimum:
        return dict(_INSUFFICIENT)
    values = [_percentile(float(target), peer_values)
              for target, peer_values in zip(targets, peers)]
    return {"percentile": _round(sum(values) / len(values)), "n": min(counts)}


def _hero_summary(records: list[tuple[dict, dict]]) -> tuple[int, list[dict], list[dict], int]:
    by_hero: dict[int, list[bool]] = defaultdict(list)
    for match, row in records:
        outcome = _won(match, row)
        if outcome is not None:
            by_hero[int(row["hero_id"])].append(outcome)
    window_games = len(records)
    signature = []
    comfortable = []
    effective_count = 0
    for hero_id, outcomes in by_hero.items():
        games = len(outcomes)
        wr = sum(outcomes) / games
        pct = _round(100 * games / window_games) if window_games else 0
        entry = {"hero_id": hero_id, "games": games, "wr": wr, "pct": pct}
        effective = games >= 3 and wr >= 0.5
        is_signature = games >= 5 and wr >= 0.6 and games / window_games >= 0.1
        if effective:
            effective_count += 1
            (signature if is_signature else comfortable).append(entry)
    key = lambda entry: (-entry["games"], entry["hero_id"])
    return window_games, sorted(signature, key=key), sorted(comfortable, key=key), effective_count


def _hero_pool_metric(records: list[tuple[dict, dict]], dataset: dict) -> tuple[float | None, float]:
    grouped_records: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    for match, row in records:
        grouped_records[match["data_source"]].append((match, row))
    grouped_values = {}
    for source, source_records in grouped_records.items():
        value = float(_hero_summary(source_records)[3])
        grouped_values[source] = [value] * len(source_records)
    return _weighted_source_value(grouped_values, dataset, "hero_pool")


def _presence_pick_rate(records: list[tuple[dict, dict]], hero_ids: set[int]) -> float:
    numerator = 0
    denominator = 0
    for match, row in records:
        draft = match.get("draft") or []
        n_actions = match.get("n_draft_actions")
        if (match.get("draft_state") != "complete" or match.get("anomaly")
                or match.get("game_mode") not in (None, 2)
                or not isinstance(n_actions, int)
                or family_for(n_actions, match.get("first_pick_team"), draft) is None):
            continue
        opponent_bans = {int(action["hero_id"]) for action in draft
                         if not action["is_pick"] and int(action["team"]) != int(row["team"])}
        if not opponent_bans:
            continue
        if hero_ids - opponent_bans:
            denominator += 1
            numerator += int(int(row["hero_id"]) in hero_ids)
    if denominator == 0:
        raise ProfileInsufficientData("presence_pick_rate 没有可观测的合法 BP 分母")
    return numerator / denominator


def _hero_archetype(records: list[tuple[dict, dict]], hero_roles: dict) -> dict[str, float]:
    raw = {name: 0.0 for name in _ARCHETYPES}
    for hero_id, count in Counter(int(row["hero_id"]) for _, row in records).items():
        roles = hero_roles.get(hero_id, hero_roles.get(str(hero_id), []))
        for archetype in archetypes_for(roles):
            raw[archetype] += count
    total = sum(raw.values())
    if total <= 0:
        raise ProfileInsufficientData("hero_archetype 没有可归一化的英雄角色数据")
    return {name: raw[name] / total for name in _ARCHETYPES}


def _team_rows(match: dict, team: int) -> list[dict]:
    return [row for row in match.get("players") or [] if row.get("team") == team]


def _team_total(match: dict, row: dict, field: str) -> float | None:
    rows = _team_rows(match, int(row["team"]))
    if len(rows) != 5 or any(member.get(field) is None for member in rows):
        return None
    total = sum(float(member[field]) for member in rows)
    return total if total else None


def _combat_values(match: dict, row: dict, role: int) -> list[float | None]:
    kills, deaths, assists = row.get("kills"), row.get("deaths"), row.get("assists")
    hero_damage, net_worth = row.get("hero_damage"), row.get("net_worth")
    team_kills = _team_total(match, row, "kills")
    team_damage = _team_total(match, row, "hero_damage")
    values = [
        float(kills + assists) / max(1, deaths) if None not in (kills, deaths, assists) else None,
        float(kills + assists) / team_kills
        if None not in (kills, assists) and team_kills is not None else None,
        float(hero_damage) / team_damage
        if hero_damage is not None and team_damage is not None else None,
        float(hero_damage) / float(net_worth)
        if hero_damage is not None and net_worth not in (None, 0) else None,
    ]
    if role == 3:
        team_taken = _team_total(match, row, "damage_taken_total")
        taken = row.get("damage_taken_total")
        values.append(float(taken) / team_taken
                      if taken is not None and team_taken is not None else None)
    if role in (4, 5):
        healing = row.get("hero_healing")
        values.append(float(healing) if healing is not None else None)
    return values


def _tempo_source(records: list[tuple[dict, dict]]) -> tuple[list[float | None], int]:
    ordered = sorted(records, key=lambda item: _started_at(item[0]), reverse=True)[:20]
    if not ordered:
        return [None, None, None], 0
    outcomes = [_won(match, row) for match, row in ordered]
    if any(outcome is None for outcome in outcomes):
        weighted_win = stability = None
    else:
        weights = [0.5 ** (index / 7.0) for index in range(len(ordered))]
        weighted_win = sum(weight * bool(outcome)
                           for weight, outcome in zip(weights, outcomes)) / sum(weights)
        stability = 1.0 - weighted_win * (1.0 - weighted_win)
    firstblood = [row.get("firstblood_claimed") for _, row in ordered]
    firstblood_rate = (sum(bool(value) for value in firstblood) / len(firstblood)
                       if firstblood and all(value is not None for value in firstblood) else None)
    return [weighted_win, stability, firstblood_rate], len(records)


def _weighted_tempo(records: list[tuple[dict, dict]], dataset: dict) -> tuple[list[float | None], float]:
    by_source: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    for match, row in records:
        by_source[match["data_source"]].append((match, row))
    source_values = {source: _tempo_source(items) for source, items in by_source.items()}
    total_weight = sum(_source_weight(dataset, "tempo", source) * count
                       for source, (_, count) in source_values.items())
    result = []
    for index in range(3):
        numerator = denominator = 0.0
        for source, (values, count) in source_values.items():
            if values[index] is None:
                continue
            weight = _source_weight(dataset, "tempo", source)
            numerator += weight * count * float(values[index])
            denominator += weight * count
        result.append(numerator / denominator if denominator else None)
    return result, total_weight


def _eligible_peers(matches: list[dict], role: int,
                    tiers: set[str]) -> list[tuple[dict, dict]]:
    result = []
    for match in matches:
        if match.get("tier") not in tiers or match.get("parse_state") != "full":
            continue
        for row in match.get("players") or []:
            if row.get("position") == role and row.get("stats_available") is True:
                result.append((match, row))
    return result


def _player_dimensions(role: int | None, player_records: list[tuple[dict, dict]],
                       peer_matches: list[dict], tiers: set[str], dataset: dict,
                       minimum: int) -> dict:
    if role is None:
        result = {name: dict(_INSUFFICIENT) for name in _POSITION_DIMENSIONS}
        result["hero_archetype"] = _hero_archetype(player_records, dataset["hero_roles"])
        return result

    peers = _eligible_peers(peer_matches, role, tiers)
    target_stats = [item for item in player_records
                    if item[0].get("parse_state") == "full"
                    and item[1].get("stats_available") is True]
    peer_accounts: dict[int, list[tuple[dict, dict]]] = defaultdict(list)
    for match, row in peers:
        account_id = row.get("account_id")
        if isinstance(account_id, int) and account_id > 0:
            peer_accounts[account_id].append((match, row))

    target_pool, target_pool_weight = _hero_pool_metric(target_stats, dataset)
    peer_pool_by_account = {peer_id: _hero_pool_metric(records, dataset)[0]
                            for peer_id, records in peer_accounts.items()}
    pool_peers = [float(peer_pool_by_account[row["account_id"]])
                  for match, row in peers
                  if row.get("account_id") in peer_pool_by_account
                  and peer_pool_by_account[row["account_id"]] is not None
                  and _source_weight(dataset, "hero_pool", match["data_source"]) > 0]
    pool_peer_weight = sum(_source_weight(dataset, "hero_pool", match["data_source"])
                           for match, row in peers
                           if row.get("account_id") in peer_pool_by_account
                           and peer_pool_by_account[row["account_id"]] is not None)
    if not target_stats:
        hero_pool = dict(_STAT_UNAVAILABLE)
    elif target_pool is None or target_pool_weight < minimum or pool_peer_weight < minimum:
        hero_pool = dict(_INSUFFICIENT)
    else:
        hero_pool = _dimension([target_pool], [pool_peers], minimum)

    laning_targets = [_mean(float(row[field]) for _, row in target_stats
                            if row.get(field) is not None) for field in ("gpm", "xpm")]
    laning_peers = [[float(row[field]) for _, row in peers if row.get(field) is not None]
                    for field in ("gpm", "xpm")]
    laning = _dimension(laning_targets, laning_peers, minimum)

    target_combat_rows = [_combat_values(match, row, role) for match, row in target_stats]
    peer_combat_rows = [_combat_values(match, row, role) for match, row in peers]
    combat_count = 4 + int(role in (3, 4, 5))
    combat_targets = [_mean(values[index] for values in target_combat_rows
                            if values[index] is not None) for index in range(combat_count)]
    combat_peers = [[values[index] for values in peer_combat_rows
                     if values[index] is not None] for index in range(combat_count)]
    combat = _dimension(combat_targets, combat_peers, minimum)

    map_targets = []
    map_target_weights = []
    map_peers = []
    map_peer_weights = []
    for field in _MAP_FIELDS:
        grouped: dict[str, list[float]] = defaultdict(list)
        for match, row in target_stats:
            if row.get(field) is not None:
                grouped[match["data_source"]].append(float(row[field]))
        target, target_weight = _weighted_source_value(grouped, dataset, "map_vision")
        map_targets.append(target)
        map_target_weights.append(target_weight)
        map_peers.append([float(row[field]) for match, row in peers
                          if row.get(field) is not None
                          and _source_weight(dataset, "map_vision", match["data_source"]) > 0])
        map_peer_weights.append(sum(_source_weight(dataset, "map_vision", match["data_source"])
                                    for match, row in peers if row.get(field) is not None))
    if not target_stats or any(value is None for value in map_targets):
        map_vision = dict(_STAT_UNAVAILABLE)
    elif min(map_target_weights) < minimum or min(map_peer_weights) < minimum:
        map_vision = dict(_INSUFFICIENT)
    else:
        map_vision = _dimension(map_targets, map_peers, minimum)

    target_tempo, target_tempo_weight = _weighted_tempo(target_stats, dataset)
    peer_tempo_by_account = {peer_id: _weighted_tempo(records, dataset)[0]
                             for peer_id, records in peer_accounts.items()}
    tempo_peers = [[], [], []]
    tempo_peer_weights = [0.0, 0.0, 0.0]
    for match, row in peers:
        source_weight = _source_weight(dataset, "tempo", match["data_source"])
        if source_weight <= 0:
            continue
        account_id = row.get("account_id")
        if account_id not in peer_tempo_by_account:
            continue
        for index, value in enumerate(peer_tempo_by_account[account_id]):
            if value is not None:
                tempo_peers[index].append(value)
                tempo_peer_weights[index] += source_weight
    if not target_stats or any(value is None for value in target_tempo):
        tempo = dict(_STAT_UNAVAILABLE)
    elif target_tempo_weight < minimum or min(tempo_peer_weights) < minimum:
        tempo = dict(_INSUFFICIENT)
    else:
        tempo = _dimension(target_tempo, tempo_peers, minimum)

    return {"hero_pool": hero_pool, "laning": laning, "combat": combat,
            "map_vision": map_vision, "tempo": tempo,
            "hero_archetype": _hero_archetype(player_records, dataset["hero_roles"])}


def _bp_tendency(target_matches: list[dict], team_id: int) -> dict:
    eligible = []
    for match in target_matches:
        draft = match.get("draft") or []
        n_actions = match.get("n_draft_actions")
        if (match.get("draft_state") != "complete" or match.get("anomaly")
                or match.get("game_mode") not in (None, 2)
                or not isinstance(n_actions, int)
                or family_for(n_actions, match.get("first_pick_team"), draft) != SPEC_FAMILY):
            continue
        side = _team_side(match, team_id)
        if side is not None:
            eligible.append((match, side))

    first_phase_games = first_pick_games = 0
    first_phase_counts: Counter[int] = Counter()
    first_pick_counts: Counter[int] = Counter()
    by_ord: dict[int, Counter[int]] = defaultdict(Counter)
    ord_games: Counter[int] = Counter()
    for match, side in eligible:
        actions = sorted(match["draft"], key=lambda action: action["ord"])
        bans = {int(action["hero_id"]) for action in actions
                if int(action["ord"]) <= 6 and not action["is_pick"]
                and int(action["team"]) == side}
        if bans:
            first_phase_games += 1
            first_phase_counts.update(bans)
        picks = [action for action in actions if action["is_pick"]
                 and int(action["team"]) == side]
        if picks:
            first_pick_games += 1
            first_pick_counts[int(picks[0]["hero_id"])] += 1
        for action in actions:
            if action["is_pick"] or int(action["team"]) != side:
                continue
            ord_ = int(action["ord"])
            ord_games[ord_] += 1
            by_ord[ord_][int(action["hero_id"])] += 1
    first_phase = ([{"hero_id": hero_id, "freq": count / first_phase_games,
                     "n": first_phase_games} for hero_id, count in first_phase_counts.items()]
                   if first_phase_games else [])
    first_pick = ([{"hero_id": hero_id, "freq": count / first_pick_games,
                    "n": first_pick_games} for hero_id, count in first_pick_counts.items()]
                  if first_pick_games else [])
    ban_by_phase = [{"ord": ord_, "hero_id": hero_id,
                     "freq": count / ord_games[ord_], "n": ord_games[ord_]}
                    for ord_, counts in by_ord.items() for hero_id, count in counts.items()]
    first_phase.sort(key=lambda item: (-item["freq"], item["hero_id"]))
    first_pick.sort(key=lambda item: (-item["freq"], item["hero_id"]))
    ban_by_phase.sort(key=lambda item: (item["ord"], -item["freq"], item["hero_id"]))
    return {"first_phase_ban_freq": first_phase, "first_pick_freq": first_pick,
            "ban_by_phase": ban_by_phase}


def build_profile(dataset: dict) -> dict:
    """从 repository DTO 构建冻结的 Profile 响应。"""
    team_id = int(dataset["team_id"])
    patch = str(dataset["patch"])
    base_version = str(dataset["base_version"])
    as_of = _as_date(dataset["as_of"])
    left = as_of - dt.timedelta(days=89)
    requested_sources = list(dict.fromkeys(dataset["sources"]))
    allowed_sources = set(requested_sources)
    minimum = max(30, int(dataset.get("min_sample_n", 30)))
    peer_matches = [match for match in dataset.get("matches") or []
                    if match.get("data_source") in allowed_sources
                    and match.get("base_version") == base_version
                    and left <= _started_at(match).date() <= as_of]
    patch_matches = [match for match in peer_matches if match.get("patch") == patch]
    target_matches = [match for match in patch_matches if _team_side(match, team_id) is not None]

    coverage = {}
    for source in ("pro_match", "pub_match"):
        matches = [match for match in target_matches if match.get("data_source") == source]
        stat_count = unknown_count = 0
        for match in matches:
            rows = _team_rows(match, int(_team_side(match, team_id)))
            stat_count += (match.get("parse_state") == "full"
                           and any(row.get("stats_available") is True for row in rows))
            unknown_count += any(row.get("position") is None for row in rows)
        coverage[source] = {"n_matches": len({match["match_id"] for match in matches}),
                            "n_stat_available": stat_count,
                            "n_position_unknown": unknown_count}

    roster_accounts = sorted({int(row["account_id"]) for match in target_matches
                              for row in _team_rows(match, int(_team_side(match, team_id)))
                              if isinstance(row.get("account_id"), int) and row["account_id"] > 0})
    known_tiers = {str(match["tier"]) for match in target_matches
                   if match.get("tier") in _KNOWN_TIERS}
    tiers = known_tiers or {"tier1", "tier2"}
    players = []
    contributing_sources = {match["data_source"] for match in target_matches}
    for account_id in roster_accounts:
        player_records = _records(patch_matches, account_id)
        contributing_sources.update(match["data_source"] for match, _ in player_records)
        role = _mode(row.get("position") for _, row in player_records)
        window_games, signature, comfortable, effective_count = _hero_summary(player_records)
        effective_hero_ids = {
            entry["hero_id"] for entry in (*signature, *comfortable)
        }
        if not effective_hero_ids:
            raise ProfileInsufficientData("presence_pick_rate 没有有效英雄池")
        names = [row.get("name") for _, row in sorted(
            player_records, key=lambda item: _started_at(item[0]), reverse=True) if row.get("name")]
        players.append({
            "account_id": account_id,
            "name": str(names[0]) if names else "",
            "role": role,
            "hero_pool": {"window_games": window_games, "signature": signature,
                          "comfortable": comfortable, "effective_count": effective_count,
                          "presence_pick_rate": _presence_pick_rate(
                              player_records, effective_hero_ids
                          )},
            "dimensions": _player_dimensions(role, player_records, peer_matches, tiers,
                                             dataset, minimum),
        })
    sources_used = [source for source in requested_sources if source in contributing_sources]
    return {"team_id": team_id, "patch": patch, "as_of": as_of.isoformat(),
            "sources_used": sources_used, "coverage": coverage, "players": players,
            "team_bp_tendency": _bp_tendency(target_matches, team_id)}
