"""对局实验室的公开历史摘要与可操作 BP 样例。"""
from datetime import date, datetime, time, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from analysis.profile_service import ProfileServiceError
from ingest.order_families import SPEC_FAMILY, family_for
from shared.draft_template import TEMPLATE


def connection(dsn):
    return psycopg.connect(dsn.replace("postgresql+psycopg://", "postgresql://"), connect_timeout=5, row_factory=dict_row)


def load_analysis_catalog(dsn):
    with connection(dsn) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '15s'")
        rows = conn.execute("""
            SELECT m.match_id, p.version_name AS patch, m.started_at, m.first_pick_team,
                   m.radiant_team_id, m.dire_team_id, r.name AS radiant_name, d.name AS dire_name,
                   jsonb_agg(jsonb_build_object('ord', a.ord, 'team', a.team,
                       'is_pick', a.is_pick, 'hero_id', a.hero_id) ORDER BY a.ord) AS draft
            FROM (SELECT * FROM opponent_profile_matches
                  WHERE started_at <= CURRENT_TIMESTAMP AND started_at > CURRENT_TIMESTAMP - interval '90 days'
                    AND draft_state = 'complete' AND NOT anomaly AND n_draft_actions = 24
                    AND radiant_team_id IS NOT NULL AND dire_team_id IS NOT NULL
                    AND radiant_team_id != dire_team_id AND (game_mode = 2 OR game_mode IS NULL)
                  ORDER BY started_at DESC LIMIT 150) m
            JOIN patches p USING (patch_id)
            JOIN teams r ON r.team_id = m.radiant_team_id
            JOIN teams d ON d.team_id = m.dire_team_id
            JOIN draft_actions a USING (match_id)
            GROUP BY m.match_id, p.version_name, m.started_at, m.first_pick_team,
                     m.radiant_team_id, m.dire_team_id, r.name, d.name
            ORDER BY m.started_at DESC, m.match_id DESC
        """).fetchall()
    examples = []
    per_patch = {}
    for row in rows:
        if family_for(len(row["draft"]), row["first_pick_team"], row["draft"]) != SPEC_FAMILY:
            continue
        patch = row["patch"]
        if per_patch.get(patch, 0) >= 6:
            continue
        per_patch[patch] = per_patch.get(patch, 0) + 1
        row["started_at"] = row["started_at"].isoformat()
        # 只提供前缀，样例中不暴露结果标签或下一手答案。
        row["draft"] = row["draft"][:12]
        examples.append(row)
    return {
        "draft_template": [{"ord": i, "is_pick": pick, "actor": actor} for i, (pick, actor) in enumerate(TEMPLATE)],
        "examples": examples,
        "note": "历史 BP 仅作为交互输入。按所选截止日期分析，不是对该场比赛的无泄漏回测。",
    }


def load_matchup_summary(dsn, request):
    as_of = date.fromisoformat(request["as_of"])
    cutoff = min(datetime.combine(as_of + timedelta(days=1), time.min, timezone.utc), datetime.now(timezone.utc))
    start = datetime.combine(as_of - timedelta(days=89), time.min, timezone.utc)
    ids = [request["us"], request["them"]]
    with connection(dsn) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '15s'")
        refs = conn.execute("SELECT team_id, name, tag FROM teams WHERE team_id = ANY(%s)", (ids,)).fetchall()
        if len(refs) != 2:
            raise ProfileServiceError("not_found", "请求的战队不存在")
        if not conn.execute("SELECT patch_id FROM patches WHERE version_name = %s", (request["patch"],)).fetchone():
            raise ProfileServiceError("not_found", "请求的版本不存在")
        rows = conn.execute("""
            SELECT m.match_id, m.started_at, m.radiant_team_id, m.dire_team_id, m.radiant_win,
                   m.duration_s, m.draft_state, m.anomaly,
                   EXISTS (SELECT 1 FROM match_players mp WHERE mp.match_id = m.match_id) AS has_detail
            FROM opponent_profile_matches_with_pub m JOIN patches p USING (patch_id)
            WHERE p.version_name = %s AND m.data_source = ANY(%s)
              AND m.started_at >= %s AND m.started_at < %s
              AND (m.radiant_team_id = ANY(%s) OR m.dire_team_id = ANY(%s))
              AND (m.game_mode = 2 OR m.game_mode IS NULL)
            ORDER BY m.started_at DESC, m.match_id DESC
        """, (request["patch"], request["sources"], start, cutoff, ids, ids)).fetchall()
    teams = {}
    for who, team_id in zip(("us", "them"), ids):
        ref = next(row for row in refs if row["team_id"] == team_id)
        games = [row for row in rows if team_id in (row["radiant_team_id"], row["dire_team_id"])]
        finished = [row for row in games if row["duration_s"] is not None and row["duration_s"] > 0
                    and row["started_at"] + timedelta(seconds=row["duration_s"]) <= cutoff]
        outcomes = [row for row in finished if row["radiant_win"] is not None]
        wins = sum(row["radiant_win"] == (row["radiant_team_id"] == team_id) for row in outcomes)
        durations = [row["duration_s"] for row in finished]
        teams[who] = {**ref, "n_matches": len(games), "n_outcomes": len(outcomes), "wins": wins,
                      "win_rate": wins / len(outcomes) if outcomes else None,
                      "n_detailed": sum(row["has_detail"] for row in games),
                      "mean_duration_s": sum(durations) / len(durations) if durations else None}
    meetings = [row for row in rows if {row["radiant_team_id"], row["dire_team_id"]} == set(ids)]
    return {
        "teams": teams, "head_to_head": {
            "n_matches": len(meetings),
            "games": [{"match_id": row["match_id"], "started_at": row["started_at"].isoformat(),
                       "winner": None if row["radiant_win"] is None or not row["duration_s"] or row["started_at"] + timedelta(seconds=row["duration_s"]) > cutoff else ("us" if row["radiant_win"] == (row["radiant_team_id"] == ids[0]) else "them")} for row in meetings[:10]],
        },
        "window": {"from": start.date().isoformat(), "as_of": as_of.isoformat(), "patch": request["patch"]},
        "note": "历史战绩描述样本本身，不等于本次对局的预测胜率。",
    }
