"use strict";

const labState = {catalog:null, config:null, draft:[], result:null, controller:null, serial:0, initialized:false};
const labNode = (id) => document.getElementById(`lab-${id}`);

async function initAnalysisWorkbench(catalog) {
  labState.catalog = catalog;
  if (labState.initialized) return;
  labState.initialized = true;
  const teams = catalog.teams;
  for (const key of ["us", "them"]) {
    const select = labNode(key);
    select.replaceChildren(...teams.map(team => el("option", {value:String(team.team_id), text:team.name || `战队 ${team.team_id}`})));
    select.disabled = false;
  }
  labNode("us").value = String(catalog.defaults.team_id);
  labNode("them").value = String(teams.find(team => team.team_id !== catalog.defaults.team_id && team.team_id === 7119388)?.team_id || teams.find(team => team.team_id !== catalog.defaults.team_id)?.team_id);
  labNode("patch").replaceChildren(...catalog.patches.map(patch => el("option", {value:patch.patch, text:`${patch.patch} · ${patch.n_matches} 场`} )));
  labNode("patch").value = catalog.defaults.patch;
  labNode("patch").disabled = false;
  labNode("date").value = catalog.defaults.as_of;
  labNode("date").max = new Date().toISOString().slice(0,10);
  labNode("form").addEventListener("submit", event => { event.preventDefault(); runLabAnalysis(); });
  for (const key of ["us", "them", "side", "date", "patch"]) labNode(key).addEventListener("change", () => { invalidateLab(); renderLabDraft(); });
  labNode("first").addEventListener("change", () => {
    labState.draft = [];
    invalidateLab(); renderLabDraft();
    labNode("status").textContent = "先手阵营已改变，BP 已清空，请按新顺序录入。";
  });
  labNode("hero-search").addEventListener("input", renderLabHeroes);
  labNode("undo").addEventListener("click", () => {labState.draft.pop(); invalidateLab(); renderLabDraft();});
  labNode("clear").addEventListener("click", () => {labState.draft=[]; invalidateLab(); renderLabDraft();});
  labNode("example").addEventListener("change", () => {labNode("load").disabled = !labNode("example").value;});
  labNode("load").addEventListener("click", loadLabExample);
  try {
    labState.config = await fetchPayload("/v1/analysis/catalog");
    labNode("example").append(...labState.config.examples.map((sample,index) => el("option", {value:String(index), text:`${sample.patch} · ${sample.radiant_name} vs ${sample.dire_name} · ${sample.match_id}`})));
    labNode("run").disabled = false;
    labNode("status").textContent = "规则已就绪。可以从第一手开始，也可以载入真实 BP 前缀。";
    renderLabDraft();
  } catch(error) {
    labNode("status").textContent = `分析目录载入失败：${error.message}。刷新页面可重试。`;
  }
}

function labResolved(index) {
  const row = labState.config?.draft_template[index];
  if (!row) return null;
  const first = Number(labNode("first").value);
  return {ord:index, is_pick:row.is_pick, team:row.actor === "F" ? first : 1-first};
}

function labTeamName(side) {
  const key = side === Number(labNode("side").value) ? "us" : "them";
  return labState.catalog?.teams.find(team => String(team.team_id) === labNode(key).value)?.name || (key === "us" ? "我方" : "对手");
}

function labSideName(side) { return side === 0 ? "天辉" : "夜魇"; }
function labPct(value) { return typeof value === "number" && Number.isFinite(value) ? `${(value*100).toFixed(1)}%` : "暂无"; }
function labMetric(label, value, note = "") { return el("div", {className:"lab-metric"}, [el("span", {text:label}), el("strong", {text:value}), el("small", {text:note})]); }

function invalidateLab() {
  labState.serial += 1;
  labState.controller?.abort();
  labNode("run").disabled = !labState.config;
  labNode("run").textContent = "运行对局分析 ↗";
  if (labState.result) {
    labNode("status").textContent = "条件或 BP 已修改，请重新运行。下方保留上一次结果。";
    labNode("results").classList.add("is-stale");
  }
}

function addLabHero(heroId) {
  if (labState.draft.length >= 24 || labState.draft.some(action => action.hero_id === heroId)) return;
  labState.draft.push({...labResolved(labState.draft.length), hero_id:heroId});
  labNode("hero-search").value = "";
  invalidateLab(); renderLabDraft();
}

function renderLabDraft() {
  if (!labState.config) return;
  const next = labResolved(labState.draft.length);
  labNode("progress").textContent = `${labState.draft.length} / 24 手 · 当前 CM 顺序`;
  document.getElementById("draft-title").textContent = next ? `第 ${next.ord+1} 手 · ${labTeamName(next.team)} ${next.is_pick ? "选择" : "禁用"}` : "双方阵容已落定";
  labNode("undo").disabled = !labState.draft.length;
  labNode("clear").disabled = !labState.draft.length;
  labNode("draft").replaceChildren(...labState.config.draft_template.map((_,index) => {
    const action = labState.draft[index];
    const slot = labResolved(index);
    return el("div", {className:`draft-slot ${slot.team === 0 ? "radiant" : "dire"}${action ? " is-filled" : ""}${index === labState.draft.length ? " is-next" : ""}${slot.is_pick ? " is-pick" : " is-ban"}`, ariaLabel:`第 ${index+1} 手 ${labSideName(slot.team)} ${slot.is_pick ? "选" : "禁"}${action ? ` ${heroName(action.hero_id)}` : ""}`}, [
      el("span", {className:"draft-slot-number", text:String(index+1).padStart(2,"0")}),
      ...(action ? [heroImage(action.hero_id,"draft-slot-image")] : [el("span", {className:"draft-slot-empty", text:slot.is_pick ? "+" : "×"})]),
      el("span", {className:"draft-slot-label", text:action ? heroName(action.hero_id) : `${labSideName(slot.team)}${slot.is_pick ? "选" : "禁"}`}),
    ]);
  }));
  labNode("lineups").replaceChildren(...[0,1].map(side => {
    const picks = labState.draft.filter(action => action.is_pick && action.team === side);
    return el("div", {className:`lab-lineup ${side === 0 ? "radiant" : "dire"}`}, [el("p", {text:`${labSideName(side)} · ${labTeamName(side)}`}), el("div", {}, Array.from({length:5},(_,index) => picks[index] ? el("div", {className:"lineup-hero"}, [heroImage(picks[index].hero_id,"lineup-image"), el("span",{text:heroName(picks[index].hero_id)})]) : el("div", {className:"lineup-hero is-empty", text:"待选"})))]);
  }));
  renderLabHeroes();
}

function renderLabHeroes() {
  const used = new Set(labState.draft.map(action => action.hero_id));
  const query = labNode("hero-search").value.toLowerCase().trim();
  const heroes = Object.entries(labState.catalog?.heroes || {}).filter(([id,hero]) => !used.has(Number(id)) && (!query || `${id} ${hero.name} ${hero.localized_name}`.toLowerCase().includes(query)));
  const ended = labState.draft.length >= 24;
  labNode("heroes").replaceChildren(...heroes.slice(0,24).map(([id]) => {
    const button = el("button", {type:"button", className:"hero-choice", disabled:ended, ariaLabel:`加入当前手 ${heroName(Number(id))}`}, [heroImage(Number(id),"hero-choice-image"),el("span",{text:heroName(Number(id))})]);
    button.addEventListener("click",() => addLabHero(Number(id)));
    return button;
  }));
  labNode("hero-count").textContent = ended ? "BP 已完成，可以评估最终阵容。" : `匹配 ${heroes.length} 名英雄，显示前 ${Math.min(24,heroes.length)} 名。已选禁英雄自动排除。候选池来自当前常量，不代表历史版本的权威 CM 快照。`;
}

function loadLabExample() {
  const sample = labState.config.examples[Number(labNode("example").value)];
  if (!sample) return;
  const actionSlot = labState.config.draft_template[sample.draft.length];
  const actingSide = actionSlot.actor === "F" ? sample.first_pick_team : 1-sample.first_pick_team;
  for (const [key,value] of Object.entries({us:actingSide === 0 ? sample.radiant_team_id : sample.dire_team_id, them:actingSide === 0 ? sample.dire_team_id : sample.radiant_team_id, side:actingSide, first:sample.first_pick_team, patch:sample.patch})) labNode(key).value = String(value);
  labState.draft = sample.draft.map(action => ({...action}));
  invalidateLab(); renderLabDraft();
  labNode("status").textContent = `已载入比赛 ${sample.match_id} 的前 12 手。点击运行，使用 ${labNode("date").value} 截点的模型分析。`;
}

function labRequest() {
  return {us:Number(labNode("us").value), them:Number(labNode("them").value), us_side:Number(labNode("side").value), patch:labNode("patch").value, as_of:labNode("date").value, first_pick_team:Number(labNode("first").value), draft:labState.draft.map(action => ({...action})), sources:["pro_match"], top_n:8};
}

async function runLabAnalysis() {
  const request = labRequest();
  if (request.us === request.them) {labNode("status").textContent="请为双方选择不同战队。";return;}
  const serial = ++labState.serial;
  labState.controller?.abort();
  labState.controller = new AbortController();
  labNode("run").disabled = true;
  labNode("run").textContent = "正在计算…";
  labNode("status").textContent = "正在读取双方历史、评估局面并搜索可用应对。";
  try {
    const body = await fetchPayload("/v1/analysis", {method:"POST",headers:{"Content-Type":"application/json","Accept":"application/json"},body:JSON.stringify(request),signal:labState.controller.signal});
    if (serial !== labState.serial) return;
    labState.result = body;
    renderLabResults(body);
    labNode("results").classList.remove("is-stale");
    labNode("status").textContent = `本次计算已返回 · ${request.patch} · 截止 ${request.as_of} · ${request.draft.length} 手输入。各模块就绪情况见下方。`;
    labNode("results").scrollIntoView({behavior:"smooth",block:"start"});
  } catch(error) {
    if (serial !== labState.serial || error.name === "AbortError") return;
    labNode("status").textContent = `分析未完成：${error.message}。修改条件或点击运行重试。`;
  } finally {
    if (serial === labState.serial) {labNode("run").disabled=false;labNode("run").textContent="运行对局分析 ↗";}
  }
}

function labModule(title, number, body) {
  const section = el("section", {className:"lab-module"}, [el("div", {className:"lab-module-heading"}, [el("span",{className:"section-number",text:number}),el("h3",{text:title})])]);
  if (body?.error) section.append(el("div", {className:"lab-unavailable"}, [el("strong",{text:body.error.code === "insufficient_data" ? "当前条件不足以给出结论" : "当前模块未返回有效结果"}),el("p",{text:body.error.message || "暂不可用"})]));
  return section;
}

function labPlanText(value) {
  const openings = Object.fromEntries([...ARCHETYPES,["unknown","未知"]]);
  return String(value).replace(/英雄 (\d+)/g, (_, id) => heroName(Number(id))).replace(/备选 ([0-9 /]+)/g, (_, ids) => `备选 ${ids.split("/").map(id => heroName(Number(id.trim()))).join("、")}`).replace(/对手 (teamfight|push|pickoff|splitpush|protect|initiate|unknown) 体系/g, (_, name) => `对手${openings[name]}体系`);
}

function labJsonDetails(title, data) {
  return el("details", {className:"lab-details"}, [el("summary",{text:title}),el("pre",{text:JSON.stringify(data,null,2)})]);
}

function renderLabResults(body) {
  const fragments = [];
  const request = body.request;
  const overview = el("section",{className:"lab-matchup-report"},[el("p",{className:"kicker",text:`本次对局分析 · ${request.patch} · ${request.as_of}`}), el("p",{className:"lab-experiment-banner",text:"实验模式：完整流程可操作，模型尚未完成独立质量验收。当前建议用于开发测试。"})]);
  const summary = body.matchup_summary;
  if (summary?.teams) {
    overview.append(el("div",{className:"lab-versus"},[el("h3",{text:summary.teams.us.name}),el("span",{text:"VS"}),el("h3",{text:summary.teams.them.name})]));
    overview.append(el("div",{className:"lab-historical-stats"},[...Object.entries(summary.teams).map(([side,team]) => labMetric(`${side === "us" ? "我方" : "对手"}历史战绩`,`${team.wins} 胜 / ${team.n_outcomes-team.wins} 负`,`${team.n_matches} 场 · 样本胜率 ${labPct(team.win_rate)}`)),labMetric("窗口内直接交手",`${summary.head_to_head.n_matches} 场`,summary.head_to_head.n_matches ? "见原始报告中的比赛记录" : "没有直接交手，不外推对位结论")]));
    overview.append(el("p",{className:"microcopy",text:summary.note}));
  }
  overview.append(el("div",{className:"lab-module-statuses"},[["value","局面评估"],["policy","下一手候选"],["advise","当前手决策"],["playbook","赛前剧本"]].map(([key,label])=>el("span",{className:body[key]?.error ? "is-pending" : "is-ready",text:`${label} · ${body[key]?.error ? "条件不足" : "已返回"}`}))));
  const exportButton = el("button",{type:"button",className:"quiet-button",text:"查看本次完整 JSON"});
  exportButton.addEventListener("click",exportLabAnalysis);
  overview.append(exportButton);
  fragments.push(overview);

  const value = body.value;
  const valueSection = labModule("局面评估", "01", value);
  if (value && !value.error) {
    const ourProbability = request.us_side === 0 ? value.radiant_win_prob : 1-value.radiant_win_prob;
    valueSection.append(el("div",{className:"lab-value-head"},[labMetric("模型估计我方胜率",labPct(ourProbability),"实验模型输出，不是承诺"),labMetric("有效支撑样本",String(value.n_samples),`样本等级：${{low:"低",medium:"中",high:"高"}[value.confidence] || value.confidence}，不代表预测质量`)]));
    const bar = el("div",{className:"lab-probability-bar",ariaLabel:`我方 ${labPct(ourProbability)}`},[el("span",{style:{width:`${Math.max(0,Math.min(100,ourProbability*100))}%`}})]);
    valueSection.append(bar);
    const labels = {patch_strength:"版本与已选阵容",counter_matchup:"选手条件对位",player_comfort:"选手熟练度",first_pick:"先手影响"};
    valueSection.append(el("div",{className:"lab-contributions"},value.contributions.map(row=>labMetric(labels[row.factor] || row.factor,`${row.delta*(request.us_side === 0 ? 1 : -1)>=0?"+":""}${(row.delta*100*(request.us_side === 0 ? 1 : -1)).toFixed(2)} 点`,"相对我方 50% 基准"))));
  }
  const valueModel = body.value_diagnostics?.model;
  if (valueModel?.validation && valueModel?.baseline) {
    valueSection.append(el("p",{className:"lab-model-note",text:`开发留出验证：准确率 ${labPct(valueModel.validation.accuracy)}，固定胜率基准 ${labPct(valueModel.baseline.accuracy)}。${valueModel.evaluation_status === "development_reused_holdout" ? "此留出集已用于开发复核，还需要新的独立测试集。" : "时间切分验证仅覆盖已有样本。"}`}));
  }
  if (body.value_diagnostics) valueSection.append(labJsonDetails("查看模型验证与贡献缺失原因",body.value_diagnostics));
  fragments.push(valueSection);

  const policy = body.policy;
  const policySection = labModule("下一手候选", "02", policy);
  if (policy && !policy.error) {
    policySection.append(el("p",{className:"lab-model-note",text:policy.model ? `模型：${policy.model}。概率只在当前候选词表内解释。` : "当前显示历史频率基准，序列预测模型未就绪。"}));
    policySection.append(el("div",{className:"lab-candidates"},policy.candidates.map((candidate,index)=>{
      const button = el("button",{type:"button",className:"text-button",text:"采用此手"});
      button.addEventListener("click",()=>{addLabHero(candidate.hero_id);document.querySelector(".draft-board").scrollIntoView({behavior:"smooth",block:"start"});});
      return el("article",{className:"lab-candidate"},[el("span",{className:"lab-rank",text:String(index+1).padStart(2,"0")}),heroImage(candidate.hero_id,"lab-candidate-image"),el("div",{},[el("h4",{text:heroName(candidate.hero_id)}),el("p",{text:candidate.reasons.join("；")}),el("small",{text:`证据比赛：${candidate.evidence_match_ids.join("、") || "无可显示记录"}`})]),el("strong",{text:labPct(candidate.prob)}),button]);
    })));
    if (body.policy_diagnostics?.evaluation?.independent_holdout === false) policySection.append(el("p",{className:"lab-model-note",text:"以下数字来自开发复用的时间留出集，独立质量验收尚未通过。"}));
    policySection.append(el("p",{className:"microcopy",text:`其余候选合计 ${labPct(policy.other_prob)}。留出集 Top 1：模型 ${labPct(policy.baseline.model_top1)}，频率基准 ${labPct(policy.baseline.frequency_top1)}。`}));
  }
  if (body.policy_diagnostics) policySection.append(labJsonDetails("查看时间留出验证与模型状态",body.policy_diagnostics));
  fragments.push(policySection);

  const advice = body.advise;
  const adviceSection = labModule("当前手决策", "03", advice);
  if (advice && !advice.error) {
    adviceSection.append(el("p",{className:"microcopy",text:"按稳健性降权后的分数排序。原始估计高但容易被换招打破的方案会降位。"}));
    for (const [index,option] of (advice.options || []).entries()) {
      adviceSection.append(el("article",{className:"lab-advice"},[el("div",{className:"lab-advice-title"},[el("span",{text:`方案 ${index+1}`}),heroImage(option.hero_id,"lab-candidate-image"),el("h4",{text:heroName(option.hero_id)})]),el("div",{className:"lab-advice-metrics"},[labMetric("原始期望",labPct(option.expected_wr)),labMetric("换招波动",labPct(option.robustness_delta)),labMetric("排序分",Number(option.penalized_score).toFixed(3))]),el("p",{text:option.why}),el("p",{text:`替代英雄：${option.fallback.map(heroName).join("、")}`}),el("p",{text:labPlanText(option.counterparty_plan)}),...(option.risk_note?[el("p",{className:"lab-risk",text:option.risk_note})]:[])]));
    }
  }
  fragments.push(adviceSection);

  const playbook = body.playbook;
  const bookSection = labModule("赛前剧本", "04", playbook);
  if (playbook && !playbook.error) {
    for (const branch of playbook.branches) {
      const details = el("details",{className:"lab-branch"},[el("summary",{text:`${branch.condition.first_pick === "us" ? "我方" : "对手"}先手 · ${Object.fromEntries(ARCHETYPES)[branch.condition.their_opening] || "体系未知"}`})]);
      branch.plans.forEach(plan=>details.append(el("div",{},[el("h4",{text:labPlanText(plan.goal)}),el("p",{text:plan.key_picks.map(pick=>`第 ${pick.by_ord+1} 手 ${heroName(pick.hero_id)}，替代 ${pick.fallback.map(heroName).join("、")}`).join("；")}),el("p",{text:`期望 ${labPct(plan.expected_wr)} · 换招波动 ${labPct(plan.robustness_delta)} · 排序分 ${plan.penalized_score.toFixed(3)}`})])));
      bookSection.append(details);
    }
  }
  if (body.decision_diagnostics) bookSection.append(labJsonDetails("查看 OP 三选一、来源覆盖与候选池依据",body.decision_diagnostics));
  fragments.push(bookSection);
  if (body.notes?.length) fragments.push(el("section",{className:"lab-footnotes"},[el("h3",{text:"这份结果的边界"}),...body.notes.map(note=>el("p",{text:note}))]));
  labNode("results").replaceChildren(...fragments);
  if (nodes.exportButton) nodes.exportButton.disabled = false;
}

function exportLabAnalysis() {
  const body = labState.result;
  if (!body) return;
  document.getElementById("export-title").textContent = "当前对局分析 JSON";
  nodes.exportJson.value = JSON.stringify(body,null,2);
  nodes.exportDescription.textContent = `双方对局分析 · ${body.request.patch} · ${body.request.as_of}`;
  nodes.exportStatus.textContent = "包含输入、模块输出、降级原因与模型验证信息。";
  nodes.exportDialog.showModal();
}
