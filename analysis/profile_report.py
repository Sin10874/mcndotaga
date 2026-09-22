"""从已校验的公开画像响应生成中文静态审阅页。"""
from __future__ import annotations

from html import escape


DIMENSIONS = {"hero_pool": "英雄池", "laning": "对线与发育", "combat": "战斗", "map_vision": "地图与视野", "tempo": "节奏与稳定性"}
ARCHETYPES = {"initiate": "先手", "protect": "反手", "push": "推进", "teamfight": "团战", "pickoff": "抓单", "splitpush": "分推"}
REASONS = {"insufficient_samples": "样本或位置不足", "stat_unavailable": "解析统计缺失", "source_not_allowed": "来源未授权", "needs_replay": "需要回放"}


def _safe(value) -> str:
    return escape(str(value), quote=True)


def _hero_label(hero_id, names) -> str:
    name = names.get(hero_id) or names.get(str(hero_id))
    return f'{_safe(name)}（{hero_id}）' if name else f'英雄 {hero_id}'


def _heroes(entries, names) -> str:
    if not entries:
        return "暂无达到阈值的英雄"
    return "；".join(f'{_hero_label(item["hero_id"], names)}：{item["games"]} 场，胜率 {item["wr"]:.0%}，出场占比 {item["pct"]}%' for item in entries)


def render_profile_report(cases: list[dict], *, generated_at: str, validation: dict, hero_names: dict | None = None) -> str:
    hero_names = hero_names or {}
    provenance = validation.get("provenance")
    provenance_note = ""
    if provenance:
        provenance_note = (
            '<p><b>位置来源：历史规则推断</b>。按整队分路和赛后经济推断，'
            '不是提供方标注或位置真值，不用于同场赛前 BP 预测。'
            f'本次 {_safe(provenance["inferred_player_rows"])} 条选手记录得到位置，'
            f'{_safe(provenance["unknown_player_rows"])} 条仍未知。覆盖率不代表准确率。</p>'
            f'<p>已逐项核对 {_safe(provenance["verified_leagues"])} 个赛事的官方证据，'
            '层级采用本项目口径。原始位置和赛事标签均保留，旧位置标注在输入变化后自动失效。</p>'
        )
    sections = []
    for case in cases:
        body = case["body"]
        title = f'<h2>{_safe(case["team_name"])}</h2><p class="muted">战队 {_safe(case["team_id"])} · 版本 {_safe(case["patch"])} · 截止 {_safe(case["as_of"])} · HTTP {case["http_status"]}</p>'
        if "error" in body:
            sections.append(f'<section>{title}<div class="notice"><b>当前无法形成完整画像</b><p>{_safe(body["error"]["message"])}</p><small>业务状态：{_safe(body["error"]["code"])}</small></div></section>')
            continue
        coverage = body["coverage"]
        details = []
        for player in body["players"]:
            pool = player["hero_pool"]
            metric_rows = []
            for key, label in DIMENSIONS.items():
                metric = player["dimensions"][key]
                if "percentile" in metric:
                    value = f'第 {metric["percentile"]} 百分位'
                    note = f'{metric["n"]} 条有效同侪记录'
                else:
                    value = REASONS.get(metric.get("reason"), "暂不可算")
                    note = "没有填入估算值"
                metric_rows.append(f'<tr><td>{label}</td><td>{value}</td><td class="muted">{note}</td></tr>')
            archetypes = "".join(f'<div class="archetype"><span>{label}</span><meter min="0" max="1" value="{player["dimensions"]["hero_archetype"][key]:.6f}" aria-label="{label}"></meter><b>{player["dimensions"]["hero_archetype"][key]:.1%}</b></div>' for key, label in ARCHETYPES.items())
            role = f'{player["role"]} 号位' if player.get("role") else "位置未知"
            details.append(f'''<details class="player"><summary><b>{_safe(player["name"] or player["account_id"])}</b><span>{role} · {pool["window_games"]} 场 · {pool["effective_count"]} 个有效英雄</span></summary>
<div class="player-body"><p><b>签名英雄</b>：{_heroes(pool["signature"], hero_names)}</p><p><b>熟练英雄</b>：{_heroes(pool["comfortable"], hero_names)}</p><p>有效英雄被放出时的选择率：<b>{pool["presence_pick_rate"]:.1%}</b></p><small>只统计有合法完整 BP 的可观测场次，不能将此数值解读为所有比赛的出场率。</small><div class="table-wrap"><table><thead><tr><th>维度</th><th>结果</th><th>样本说明</th></tr></thead><tbody>{"".join(metric_rows)}</tbody></table></div><h3>英雄类型偏好</h3><div class="archetypes">{archetypes}</div><small>六项按实际英雄出场加权，归一化后合计 100%。此分布不依赖位置。</small></div></details>''')
        if not details:
            details.append('<p class="notice">窗口内尚无可识别选手参赛记录，不生成虚构阵容。</p>')
        tendency = body["team_bp_tendency"]
        bp_rows = []
        for label, key in (("首阶段常禁", "first_phase_ban_freq"), ("本队第一手常选", "first_pick_freq")):
            for entry in tendency[key][:8]:
                bp_rows.append(f'<tr><td>{label}</td><td>{_hero_label(entry["hero_id"], hero_names)}</td><td>{entry["freq"]:.1%}</td><td>{entry["n"]} 场</td></tr>')
        bp_html = '<div class="table-wrap"><table><thead><tr><th>倾向</th><th>英雄</th><th>频率</th><th>机会分母</th></tr></thead><tbody>' + ''.join(bp_rows) + '</tbody></table></div>' if bp_rows else '<p class="muted">尚无当前顺序族的合法完整 BP，不推测禁选倾向。</p>'
        sources = "、".join("职业比赛" if item == "pro_match" else "天梯比赛" for item in body["sources_used"]) or "无"
        sections.append(f'''<section>{title}<p>实际使用来源：{sources}。</p><p class="coverage">职业比赛 {coverage["pro_match"]["n_matches"]} 场，已有统计 {coverage["pro_match"]["n_stat_available"]} 场，含位置未知 {coverage["pro_match"]["n_position_unknown"]} 场。天梯队伍覆盖 {coverage["pub_match"]["n_matches"]} 场。</p>{"".join(details)}<h3>战队 BP 倾向</h3>{bp_html}</section>''')
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>真实战队画像验收</title><style>
:root{{color-scheme:light;--ink:#242b25;--muted:#687269;--line:#d9dfd6;--green:#285f46}}*{{box-sizing:border-box}}body{{margin:0;background:#f6f7f3;color:var(--ink);font:15px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}}main{{max-width:1100px;margin:auto;padding:44px 32px 72px}}header{{border-bottom:1px solid var(--line);padding-bottom:24px}}h1{{font-size:34px;line-height:1.3;letter-spacing:-.03em;margin:12px 0}}h2{{font-size:24px;margin:0 0 6px}}h3{{font-size:16px;margin:22px 0 10px}}p{{margin:8px 0}}.eyebrow,small{{font-size:12px}}.eyebrow{{color:var(--green);letter-spacing:.12em}}.muted,small{{color:var(--muted)}}section{{margin-top:38px}}.summary{{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line);border:1px solid var(--line);margin-top:24px}}.summary div{{background:white;padding:18px}}.summary strong{{display:block;font-size:26px;line-height:1.4}}.notice,.coverage{{background:#e9eee5;padding:14px 18px}}.player{{background:white;border:1px solid var(--line);margin-top:10px}}summary{{padding:16px 18px;cursor:pointer}}summary span{{float:right;color:var(--muted);font-size:13px}}.player-body{{padding:0 20px 22px}}.table-wrap{{overflow:auto;background:white;border:1px solid var(--line);margin-top:16px}}table{{border-collapse:collapse;width:100%;font-size:13px;text-align:left}}th,td{{padding:10px 14px;border-bottom:1px solid var(--line);vertical-align:top}}th{{font-weight:500;background:#eef1e9;color:var(--muted);white-space:nowrap}}tbody tr:last-child td{{border-bottom:0}}.archetypes{{display:grid;grid-template-columns:1fr 1fr;gap:8px 30px;margin:12px 0}}.archetype{{display:grid;grid-template-columns:45px 1fr 60px;gap:12px;align-items:center;font-size:13px}}meter{{width:100%;height:11px}}.archetype b{{font-weight:500;text-align:right}}footer{{margin-top:32px;padding-top:18px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}}code{{font:12px ui-monospace,monospace;overflow-wrap:anywhere}}@media(max-width:700px){{main{{padding:26px 18px 40px}}h1{{font-size:27px}}.summary{{grid-template-columns:1fr}}summary span{{display:block;float:none;margin-top:5px}}.archetypes{{grid-template-columns:1fr}}.player-body{{padding:0 14px 16px}}}}
</style></head><body><main><header><div class="eyebrow">DOTA2 / M3 实际接口结果</div><h1>真实战队画像</h1><p>页面内容来自本机 HTTP 接口与 PostgreSQL。可以展开选手，查看英雄池、五个位置维度和英雄类型偏好。</p><p class="muted">静态快照：{_safe(generated_at)}。截至所选日期的近 90 天，目标指标按指定字母版本计算。</p></header><div class="summary"><div><small>最终全量测试</small><strong>{_safe(validation["passed"])} 项通过</strong></div><div><small>数据库选手详情</small><strong>{_safe(validation["detailed_matches"])} 场</strong></div><div><small>真实 HTTP 样本</small><strong>{len(cases)} 支队伍</strong></div></div>
<section class="notice"><b>如何读这份报告</b>{provenance_note}<p>位置或同侪样本不足时，五个依赖位置的维度明确降级。没有将 premium/professional 统一替换成 tier1/tier2，也没有补造百分位。英雄池、英雄原型和有证据的 BP 倾向仍可计算。</p><p>有效英雄池为空或没有合法 BP 分母时，整个画像返回 insufficient_data，HTTP 状态仍为 200，这是冻结契约要求。</p><p>自动浏览器视觉未验证；48 小时连续采集尚未验收；模型、Value 和剧本尚未接入。</p></section>{"".join(sections)}<footer>接口：<code>GET /v1/profile?team_id=...&amp;patch=...&amp;as_of=...&amp;sources=pro_match</code>。本页仅包含公开比赛结果，不包含训练赛、凭据或原始上游响应。来源详情与复算数据见同目录 JSON 文件。</footer></main></body></html>'''
