"""只读公开赛事，生成可直接打开的中文采集报告。"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from html import escape
import json
import pathlib

import psycopg

from db.migrate import dsn_from_env


def _text(value) -> str:
    return escape(str(value if value is not None else "未知"), quote=True)


def _number(value) -> str:
    return f"{value:,}" if isinstance(value, int) else _text(value)


def render_report(snapshot: dict) -> str:
    counts, metrics = snapshot["counts"], snapshot["metrics"]
    lag = metrics["latest_bp_lag_hours"]
    retry = metrics["retry_success_rate"]
    unavailable = metrics["unavailable_rate"]
    alert = ('<p class="alert">需检查：不可用 BP 的占比超过 5%。请核对上游延迟与采集错误。</p>'
             if unavailable is not None and unavailable > .05 else "")
    cards = [
        ("公开比赛", _number(counts["matches"]), "报告时间窗内"),
        ("BP 抓取完成", _number(counts["complete"]), "结构异常会单独标注"),
        ("已入库 BP 手数", _number(counts["draft_actions"]), "原始逐手记录"),
        ("已补选手详情", _number(counts["detailed_matches"]), "至少有一名选手的场次"),
    ]
    tiles = "".join(f'<div class="card"><span>{_text(a)}</span><strong>{b}</strong><small>{_text(c)}</small></div>'
                    for a, b, c in cards)
    measures = [
        ("最新正常 BP 滞后", f"{lag:.1f} 小时" if lag is not None else "尚无完整 BP"),
        ("待补 BP", _number(counts["pending"])),
        ("重试成功率", f"{retry:.1%}" if retry is not None else "样本不足"),
        ("今日请求 / 日预算", f'{metrics["requests_today"]:,} / {metrics["daily_limit"]:,}'),
        ("不可用 BP 占比", f"{unavailable:.1%}" if unavailable is not None else "样本不足"),
        ("非 CM 场次", _number(counts["non_cm"])),
        ("BP 结构异常", _number(counts["anomalies"])),
        ("位置未知记录", _number(counts["position_unknown"])),
        ("待调度（含补抓）", _number(metrics.get("pending_queue", 0))),
    ]
    measure_html = "".join(f'<div><dt>{_text(k)}</dt><dd>{v}</dd></div>' for k, v in measures)
    rows = []
    state_names = {"complete": "已取得 BP", "pending": "等待 BP", "unavailable": "BP 不可用"}
    for match in snapshot["recent_matches"]:
        non_cm = match.get("game_mode") not in (None, 2)
        state = "非 CM" if non_cm else state_names.get(match["draft_state"], "未知")
        if match["anomaly"]:
            state += " · 结构异常"
        draft_html = "".join(
            f'<li><b>{action["ord"] + 1:02d}</b> '
            f'{"天辉" if action["team"] == 0 else "夜魇"} '
            f'{"选" if action["is_pick"] else "禁"} {_text(action["hero"])}</li>'
            for action in match["draft"])
        details = (f'<details><summary>查看 {len(match["draft"])} 手 BP</summary><ol class="draft">{draft_html}</ol></details>'
                   if draft_html else '<span class="muted">暂无 BP 记录</span>')
        label = f'{match["radiant"]} {match["dire"]} {match["league"]} {match["match_id"]}'
        rows.append(f'<tr data-search="{_text(label.lower())}" data-state="{_text("non_cm" if non_cm else match["draft_state"])}">'
                    f'<td><span class="mono">{match["match_id"]}</span><small>{_text(match["started_at"][:16].replace("T", " "))} UTC</small></td>'
                    f'<td><b>{_text(match["radiant"])}</b><span class="versus">对</span><b>{_text(match["dire"])}</b>'
                    f'<small>{_text(match["league"])}</small></td><td>{_text(match["patch"])}</td>'
                    f'<td><span class="tag">{_text(state)}</span><small>{match["n_players"]} 名选手</small></td>'
                    f'<td>{details}</td></tr>')
    rows_html = "".join(rows) or '<tr><td colspan="5" class="muted">尚未发现公开比赛。先运行一次采集命令。</td></tr>'
    cursor_html = "".join(f'<tr><td class="mono">{_text(c["source"])}</td><td>{_text(c["last_seen_id"])}</td>'
                          f'<td>{_text(c["updated_at"])}</td></tr>' for c in snapshot["cursors"])
    cursor_html = cursor_html or '<tr><td colspan="3">尚无已保存的采集位点</td></tr>'
    scan_html = "".join(
        '<p class="muted">历史列表回填已完成。下一轮从最新列表开始，扫描到已保存位点后结束。</p>'
        if scan["mode"] == "incremental" and scan["next_less_than_match_id"] is None
        else f'<p class="muted">{"历史回填" if scan["mode"] == "backfill" else "增量扫描"}：'
        f'下一页从比赛 {_text(scan["next_less_than_match_id"])} 之前继续。'
        f'本轮已见最高编号 {_text(scan["cycle_high_match_id"])}，完整扫描后才推进上表位点。</p>'
        for scan in snapshot.get("scans", []))
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>职业比赛采集状态</title><style>
:root{{color-scheme:light;--ink:#202720;--muted:#69716a;--line:#dce1da;--paper:#f6f7f3;--green:#285f46}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}}main{{max-width:1240px;margin:0 auto;padding:48px 36px 72px}}header{{border-bottom:1px solid var(--line);padding-bottom:26px}}.eyebrow{{font-size:12px;letter-spacing:.15em;color:var(--green);font-weight:650}}h1{{font-size:34px;letter-spacing:-.035em;margin:10px 0}}h2{{font-size:20px;margin:0 0 16px}}p{{margin:8px 0}}.muted,small{{color:var(--muted)}}small{{display:block;font-size:12px;margin-top:5px}}.status{{display:inline-block;background:#e6eee4;color:var(--green);padding:4px 10px;font-size:12px;margin-top:12px}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line);border:1px solid var(--line);margin:28px 0}}.card{{padding:20px;background:#fff}}.card span{{color:var(--muted);font-size:13px}}.card strong{{display:block;font-size:32px;line-height:1.4;letter-spacing:-.04em;font-variant-numeric:tabular-nums}}section{{margin-top:34px}}.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:20px 28px;margin:0}}dt{{font-size:13px;color:var(--muted)}}dd{{margin:4px 0 0;font-weight:600;font-variant-numeric:tabular-nums}}.alert{{background:#f4ead8;color:#715120;padding:12px 16px;border-left:3px solid #b68d45;margin-top:20px}}.toolbar{{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:16px}}input,select{{font:inherit;background:white;border:1px solid var(--line);border-radius:2px;padding:9px 12px;color:var(--ink)}}input{{flex:1;min-width:180px}}.table-wrap{{overflow:auto;border:1px solid var(--line);background:#fff}}table{{width:100%;border-collapse:collapse;text-align:left}}th{{font-size:12px;color:var(--muted);font-weight:500;background:#eef1ea;white-space:nowrap}}th,td{{padding:14px 16px;vertical-align:top;border-bottom:1px solid var(--line)}}tbody tr:last-child td{{border-bottom:0}}td{{font-size:13px}}.mono{{font-family:ui-monospace,monospace;font-size:12px}}.versus{{padding:0 8px;color:var(--muted)}}.tag{{display:inline-block;background:#eef1ea;padding:2px 7px;white-space:nowrap;font-size:12px}}details{{min-width:145px}}summary{{cursor:pointer;color:var(--green);white-space:nowrap}}.draft{{list-style:none;margin:12px 0 0;padding:0;min-width:215px;font-size:12px}}.draft b{{color:var(--muted);display:inline-block;width:24px}}.boundary{{padding:20px 24px;background:#e9ede4;border:1px solid var(--line)}}.boundary p{{font-size:13px}}footer{{margin-top:36px;padding-top:16px;border-top:1px solid var(--line);font-size:12px;color:var(--muted)}}@media(max-width:700px){{main{{padding:28px 18px 40px}}h1{{font-size:27px}}.cards,.metrics{{grid-template-columns:repeat(2,1fr)}}.card{{padding:16px}}.card strong{{font-size:28px}}th,td{{padding:12px}}.toolbar input{{width:100%}}}}
</style></head><body><main><header><div class="eyebrow">DOTA2 / 数据采集</div><h1>职业比赛采集状态</h1>
<p class="muted">从公开赛事到可追溯的 BP 记录。统计窗口：近 {_text(snapshot["window_days"])} 天。</p>
<span class="status">静态快照 · {_text(snapshot["generated_at"])} · 不会自动刷新</span></header>
<div class="cards">{tiles}</div><section><h2>采集健康度</h2><dl class="metrics">{measure_html}</dl>{alert}
<p class="muted"><small>BP 状态统计排除已确认的非 CM 场次。重试成功率只计首次尝试之后的 BP 尝试；请求配额覆盖列表页、详情、错误与限流响应，按 UTC 日累计。</small></p></section>
<section><h2>最近比赛</h2><div class="toolbar"><input id="search" aria-label="搜索队伍、联赛或比赛编号" placeholder="搜索队伍、联赛或比赛编号"><select id="state" aria-label="筛选 BP 状态"><option value="">全部状态</option><option value="complete">已取得 BP</option><option value="pending">等待 BP</option><option value="unavailable">BP 不可用</option><option value="non_cm">非 CM</option></select><span id="visible-count" class="muted"></span></div>
<div class="table-wrap"><table id="matches"><thead><tr><th>比赛 / 时间</th><th>对阵 / 联赛</th><th>版本</th><th>数据状态</th><th>逐手记录</th></tr></thead><tbody>{rows_html}</tbody></table></div><p id="no-results" hidden class="muted">没有符合筛选条件的比赛。</p></section>
<section><h2>断点与恢复</h2><div class="table-wrap"><table><thead><tr><th>数据流</th><th>已完整扫描位点</th><th>最近更新</th></tr></thead><tbody>{cursor_html}</tbody></table></div>{scan_html}</section>
<section class="boundary"><h2>当前能力边界</h2><p>本页展示真实数据库中的公开赛事采集结果。训练赛内容不会进入本报告。未知位置保留为空，不推测选手分工。</p>
<p><b>连续 48 小时稳定性：未验收。</b>一次采集成功、单元测试通过和短时重启测试都不能代替持续观测。</p>
<p>本页只呈现采集证据。六维画像需另查画像接口的样本与降级结果；胜率评估、对手下一手预测和应对剧本尚未接入。本页不是 BP 推荐器。</p></section>
<footer>来源：OpenDota 公共 API；若库中已加载历史语料，统计同时包含 Kaggle 引导数据。只显示汇总与比赛公开字段。</footer></main>
<script>
const search = document.getElementById('search');
const state = document.getElementById('state');
function filterMatches() {{
 const q = search.value.trim().toLowerCase(); let visible = 0;
 const rows = document.querySelectorAll('#matches tbody tr[data-search]');
 for (const row of rows) {{ const show = row.dataset.search.includes(q) && (!state.value || state.value === row.dataset.state); row.hidden = !show; if(show) visible++; }}
 document.getElementById('visible-count').textContent = visible + ' / ' + rows.length + ' 场';
 document.getElementById('no-results').hidden = visible !== 0 || rows.length === 0;
}}
search.addEventListener('input', filterMatches); state.addEventListener('change', filterMatches); filterMatches();
</script></body></html>'''


def collect_snapshot(conn, *, now=None, window_days=90, daily_limit=2760) -> dict:
    """同一只读事务内调用，只从对手侧公开视图读取比赛。"""
    from psycopg.rows import dict_row

    if window_days < 1 or daily_limit < 1:
        raise ValueError("时间窗和日预算必须为正整数")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("报告时间必须包含时区")
    now = now.astimezone(timezone.utc)
    cutoff = now - timedelta(days=window_days)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    public = """SELECT * FROM opponent_profile_matches
                WHERE started_at >= %(cutoff)s AND started_at <= %(now)s"""
    params = {"cutoff": cutoff, "now": now, "day_start": day_start}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"""WITH m AS ({public}) SELECT
            count(*) AS matches,
            count(*) FILTER (WHERE draft_state='complete' AND (game_mode=2 OR game_mode IS NULL)) AS complete,
            count(*) FILTER (WHERE draft_state='pending' AND (game_mode=2 OR game_mode IS NULL)) AS pending,
            count(*) FILTER (WHERE draft_state='unavailable' AND (game_mode=2 OR game_mode IS NULL)) AS unavailable,
            count(*) FILTER (WHERE game_mode IS NOT NULL AND game_mode<>2) AS non_cm,
            count(*) FILTER (WHERE anomaly AND (game_mode=2 OR game_mode IS NULL)) AS anomalies,
            (SELECT count(*) FROM draft_actions JOIN m USING(match_id)) AS draft_actions,
            (SELECT count(DISTINCT t) FROM m, unnest(ARRAY[radiant_team_id,dire_team_id]) t) AS teams,
            (SELECT count(DISTINCT account_id) FROM match_players JOIN m USING(match_id)) AS players,
            (SELECT count(DISTINCT match_id) FROM match_players JOIN m USING(match_id)) AS detailed_matches,
            (SELECT count(*) FROM match_players JOIN m USING(match_id) WHERE position IS NULL) AS position_unknown,
            max(started_at) FILTER (WHERE draft_state='complete' AND NOT anomaly
                AND n_draft_actions>0 AND (game_mode=2 OR game_mode IS NULL)) AS latest_bp
            FROM m""", params)
        counts = cur.fetchone()
        latest_bp = counts.pop("latest_bp")
        cur.execute("""SELECT count(*) AS n FROM collector_attempts
                       WHERE at >= %(day_start)s AND at <= %(now)s""", params)
        daily_used = cur.fetchone()["n"]
        cur.execute(f"""WITH m AS ({public}), attempts AS (
            SELECT a.outcome, a.at,
                row_number() OVER (PARTITION BY a.match_id ORDER BY a.at,a.attempt_id) AS n
            FROM collector_attempts a JOIN m USING(match_id)
            WHERE a.source='match_details' AND a.at <= %(now)s
                AND (m.game_mode=2 OR m.game_mode IS NULL))
            SELECT count(*) AS attempts, count(*) FILTER (WHERE outcome='ok') AS successes
            FROM attempts WHERE n>1 AND at >= %(cutoff)s""", params)
        retries = cur.fetchone()
        cur.execute("""SELECT count(*) AS pending
            FROM collector_match_queue q
            WHERE q.status IN ('pending','error','unavailable') AND NOT q.final_attempt_done
              AND q.next_attempt_at IS NOT NULL""")
        pending_queue = cur.fetchone()["pending"]
        eligible = counts["matches"] - counts["non_cm"]
        metrics = {
            "latest_bp_lag_hours": (now - latest_bp).total_seconds() / 3600 if latest_bp else None,
            "requests_today": daily_used, "daily_limit": daily_limit,
            "pending_queue": pending_queue,
            "retry_attempts": retries["attempts"],
            "retry_success_rate": retries["successes"] / retries["attempts"] if retries["attempts"] else None,
            "unavailable_rate": counts["unavailable"] / eligible if eligible else None,
        }
        cur.execute("""SELECT source,last_seen_id,updated_at FROM collector_cursors
                       WHERE source='pro_matches' ORDER BY source""")
        cursors = [{**r, "updated_at": r["updated_at"].astimezone(timezone.utc).isoformat()}
                   for r in cur.fetchall()]
        cur.execute("""SELECT mode,next_less_than_match_id,cycle_high_match_id
                       FROM collector_pro_scans WHERE source='pro_matches'""")
        scans = cur.fetchall()
        cur.execute(f"""WITH m AS ({public}) SELECT m.match_id,m.started_at,
            coalesce(r.name,'队伍未知') AS radiant, coalesce(d.name,'队伍未知') AS dire,
            coalesce(l.name,'赛事未知') AS league, p.version_name AS patch,
            m.draft_state,m.game_mode,m.anomaly,coalesce(m.n_draft_actions,0) AS n_draft_actions,
            (SELECT count(*) FROM match_players mp WHERE mp.match_id=m.match_id) AS n_players
            FROM m LEFT JOIN teams r ON r.team_id=m.radiant_team_id
            LEFT JOIN teams d ON d.team_id=m.dire_team_id
            LEFT JOIN leagues l ON l.league_id=m.league_id
            LEFT JOIN patches p USING(patch_id)
            ORDER BY m.started_at DESC,m.match_id DESC LIMIT 100""", params)
        matches = cur.fetchall()
        drafts = {}
        if matches:
            cur.execute("""SELECT a.match_id,a.ord,a.is_pick,a.team,h.localized_name AS hero
                           FROM draft_actions a JOIN heroes h USING(hero_id)
                           WHERE a.match_id=ANY(%s) ORDER BY a.match_id,a.ord""",
                        ([m["match_id"] for m in matches],))
            for action in cur.fetchall():
                drafts.setdefault(action.pop("match_id"), []).append(action)
        for match in matches:
            match["started_at"] = match["started_at"].astimezone(timezone.utc).isoformat()
            match["draft"] = drafts.get(match["match_id"], [])
    return {"generated_at": now.isoformat(), "window_days": window_days,
            "counts": counts, "metrics": metrics, "cursors": cursors, "scans": scans,
            "recent_matches": matches}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="从公开赛事库生成自包含的中文采集报告")
    parser.add_argument("--output", type=pathlib.Path, required=True, help="HTML 输出路径")
    parser.add_argument("--json-output", type=pathlib.Path, help="可选的汇总 JSON 输出路径")
    parser.add_argument("--window-days", type=int, default=90)
    parser.add_argument("--daily-limit", type=int, default=2760)
    args = parser.parse_args(argv)
    with psycopg.connect(dsn_from_env()) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        data = collect_snapshot(conn, window_days=args.window_days, daily_limit=args.daily_limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_report(data), encoding="utf-8")
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"报告已生成：{args.output.resolve()}")


if __name__ == "__main__":
    main()
