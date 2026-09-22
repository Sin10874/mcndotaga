"use strict";

const DIMENSIONS = [
  ["hero_pool", "英雄池"],
  ["laning", "对线与发育"],
  ["combat", "战斗"],
  ["map_vision", "地图与视野"],
  ["tempo", "节奏与稳定性"],
];

const ARCHETYPES = [
  ["initiate", "先手"],
  ["protect", "反手"],
  ["push", "推进"],
  ["teamfight", "团战"],
  ["pickoff", "抓单"],
  ["splitpush", "分推"],
];

const REASONS = {
  insufficient_samples: "样本或位置不足",
  stat_unavailable: "解析统计缺失",
  source_not_allowed: "来源未授权",
  needs_replay: "需要回放",
};

const SOURCE_NAMES = {
  pro_match: "职业比赛",
  pub_match: "天梯比赛",
};

const state = {
  catalog: null,
  profile: null,
  selectedTeamId: null,
  activePlayerId: null,
  compareLeftId: null,
  compareRightId: null,
  bpOrdFilter: "all",
  activeView: "overview",
  requestController: null,
  requestSerial: 0,
  appliedQuery: null,
  suggestions: [],
  suggestionIndex: -1,
};

const nodes = {};

class WorkbenchError extends Error {
  constructor(code, message, status = 0) {
    super(message);
    this.name = "WorkbenchError";
    this.code = code || "upstream_unavailable";
    this.status = status;
  }
}

document.addEventListener("DOMContentLoaded", init);

function init() {
  bindNodes();
  bindEvents();
  const requestedView = window.location.hash.replace("#", "");
  if (document.querySelector(`[data-view-target="${cssEscape(requestedView)}"]`)) {
    state.activeView = requestedView;
  }
  setActiveView(state.activeView, false);
  loadCatalog();
}

function bindNodes() {
  const ids = [
    "connection-dot", "connection-label", "export-button", "catalog-window",
    "catalog-error", "catalog-error-message", "catalog-retry", "profile-form",
    "team-search", "team-id", "team-search-count", "team-suggestions",
    "patch-select", "patch-help", "as-of-input", "query-button", "draft-notice",
    "draft-query-button", "request-state", "result-shell", "result-team-name",
    "result-meta", "result-summary", "overview-content", "players-content", "compare-content",
    "bp-content", "sources-content", "empty-stage", "export-dialog",
    "export-json", "export-copy", "export-close", "export-status", "export-description",
  ];
  ids.forEach((id) => {
    nodes[toCamel(id)] = document.getElementById(id);
  });
  nodes.navButtons = Array.from(document.querySelectorAll("[data-view-target]"));
  nodes.viewPanels = Array.from(document.querySelectorAll("[data-view]"));
}

function bindEvents() {
  nodes.profileForm.addEventListener("submit", (event) => {
    event.preventDefault();
    queryProfile();
  });
  nodes.catalogRetry.addEventListener("click", loadCatalog);
  nodes.draftQueryButton.addEventListener("click", queryProfile);
  nodes.exportButton.addEventListener("click", exportProfile);
  nodes.exportClose.addEventListener("click", () => nodes.exportDialog.close());
  nodes.exportCopy.addEventListener("click", copyProfile);
  nodes.teamSearch.addEventListener("input", handleTeamSearch);
  nodes.teamSearch.addEventListener("focus", handleTeamSearch);
  nodes.teamSearch.addEventListener("keydown", handleTeamKeydown);
  nodes.teamSearch.addEventListener("blur", () => {
    window.setTimeout(() => hideSuggestions(), 120);
  });
  nodes.patchSelect.addEventListener("change", markDraftChanged);
  nodes.asOfInput.addEventListener("change", markDraftChanged);
  nodes.navButtons.forEach((button) => {
    button.addEventListener("click", () => setActiveView(button.dataset.viewTarget, true));
  });
  window.addEventListener("hashchange", () => {
    const view = window.location.hash.replace("#", "");
    if (nodes.navButtons.some((button) => button.dataset.viewTarget === view)) {
      setActiveView(view, false);
    }
  });
}

async function loadCatalog() {
  setCatalogState("loading");
  try {
    const catalog = await fetchPayload("/v1/catalog");
    validateCatalog(catalog);
    state.catalog = catalog;
    state.suggestions = catalog.teams;
    configureControlsFromCatalog();
    setCatalogState("ready");
    const query = restoredQuery(catalog);
    if (query) {
      applyDraftQuery(query);
      if (query.queryable === false) {
        renderUnqueryableTeam(findTeam(query.team_id));
      } else {
        await queryProfile();
      }
    } else {
      renderNoCatalogData();
    }
  } catch (error) {
    setCatalogState("error", normalizedError(error));
  }
}

async function fetchPayload(url, options = {}) {
  let response;
  try {
    response = await fetch(url, {
      headers: { Accept: "application/json" },
      ...options,
    });
  } catch (error) {
    if (error && error.name === "AbortError") throw error;
    throw new WorkbenchError("upstream_unavailable", "无法连接本地画像服务，请确认服务仍在运行。", 0);
  }

  let payload;
  try {
    payload = await response.json();
  } catch (_) {
    throw new WorkbenchError("upstream_unavailable", "服务返回了无法读取的数据，请稍后重试。", response.status);
  }

  if (payload && payload.error) {
    const error = payload.error;
    throw new WorkbenchError(error.code, error.message || "当前请求无法完成。", response.status);
  }
  if (!response.ok) {
    throw new WorkbenchError("upstream_unavailable", `服务请求失败，HTTP ${response.status}。`, response.status);
  }
  return payload;
}

function validateCatalog(catalog) {
  if (!catalog || !Array.isArray(catalog.teams) || !Array.isArray(catalog.patches)) {
    throw new WorkbenchError("upstream_unavailable", "战队目录结构不完整，无法安全展示。", 0);
  }
}

function configureControlsFromCatalog() {
  const catalog = state.catalog;
  const hasTeams = catalog.teams.length > 0;
  nodes.teamSearch.disabled = !hasTeams;
  nodes.patchSelect.disabled = !hasTeams;
  nodes.asOfInput.disabled = !hasTeams;
  nodes.queryButton.disabled = !hasTeams;
  nodes.asOfInput.max = safeText(catalog.window && catalog.window.as_of);
  nodes.catalogWindow.textContent = catalog.window
    ? `目录窗口 ${formatDate(catalog.window.from)} 至 ${formatDate(catalog.window.as_of)}，共 ${formatInteger(catalog.summary && catalog.summary.team_count)} 支战队`
    : "公开目录已载入";
}

function restoredQuery(catalog) {
  if (!catalog.teams.length) return null;
  const params = new URLSearchParams(window.location.search);
  const teamFromUrl = parsePositiveInteger(params.get("team_id"));
  const defaultTeamId = parsePositiveInteger(catalog.defaults && catalog.defaults.team_id);
  const team = teamFromUrl ? findTeam(teamFromUrl) : (findTeam(defaultTeamId) || catalog.teams[0]);
  const restoredTeamId = teamFromUrl || (team && team.team_id);
  const teamPatches = team && Array.isArray(team.patches) ? team.patches : [];
  const patchFromUrl = params.get("patch");
  const hasExplicitPatch = params.has("patch") && Boolean(patchFromUrl);
  const defaultPatch = safeText(catalog.defaults && catalog.defaults.patch);
  const patch = hasExplicitPatch
    ? patchFromUrl
    : (teamPatches.includes(defaultPatch) ? defaultPatch : firstTeamPatch(team));
  const dateFromUrl = params.get("as_of");
  const asOf = isDateString(dateFromUrl)
    ? dateFromUrl
    : safeText(catalog.defaults && catalog.defaults.as_of) || safeText(catalog.window && catalog.window.as_of);
  if (!restoredTeamId || !asOf) return null;
  return {
    team_id: restoredTeamId,
    patch,
    as_of: asOf,
    queryable: Boolean(patch),
    restore_explicit_patch: hasExplicitPatch,
    restore_explicit_team: Boolean(teamFromUrl && !team),
  };
}

function applyDraftQuery(query) {
  const team = findTeam(query.team_id);
  if (team) {
    selectTeam(team, false, query.patch);
  } else if (query.restore_explicit_team) {
    state.selectedTeamId = Number(query.team_id);
    nodes.teamId.value = String(query.team_id);
    nodes.teamSearch.value = `战队 ID ${query.team_id}（URL 恢复）`;
    nodes.patchSelect.replaceChildren(el("option", { value: "", text: "等待 URL 版本" }));
    nodes.patchSelect.disabled = true;
    nodes.patchHelp.textContent = "该历史战队不在当前目录窗口，正在使用 URL 中的明确条件。";
    nodes.patchHelp.hidden = false;
  }
  const teamPatches = team && Array.isArray(team.patches) ? team.patches : [];
  if (teamPatches.includes(query.patch)) {
    nodes.patchSelect.value = query.patch;
  } else if (query.restore_explicit_patch && query.patch) {
    addPatchOption(query.patch);
    nodes.patchSelect.value = query.patch;
    nodes.patchSelect.disabled = false;
    if (query.restore_explicit_team) {
      nodes.patchHelp.textContent = "该历史战队不在当前目录窗口，正在使用 URL 中的明确条件。";
      nodes.patchHelp.hidden = false;
    } else {
      nodes.patchHelp.hidden = true;
      nodes.patchHelp.textContent = "";
    }
  } else {
    nodes.patchSelect.value = "";
  }
  nodes.asOfInput.value = query.as_of;
  updateDraftNotice();
}

function setCatalogState(mode, error = null) {
  nodes.catalogError.hidden = mode !== "error";
  if (mode === "loading") {
    nodes.connectionDot.className = "connection-dot";
    nodes.connectionLabel.textContent = "正在连接真实数据";
    nodes.catalogWindow.textContent = "目录载入中";
    setControlsDisabled(true);
  } else if (mode === "ready") {
    nodes.connectionDot.className = "connection-dot is-ready";
    nodes.connectionLabel.textContent = "公开目录已载入";
  } else {
    nodes.connectionDot.className = "connection-dot is-error";
    nodes.connectionLabel.textContent = "目录连接失败";
    nodes.catalogErrorMessage.textContent = error.message;
    nodes.catalogWindow.textContent = "目录不可用";
    setControlsDisabled(true);
  }
}

function setControlsDisabled(disabled) {
  nodes.teamSearch.disabled = disabled;
  nodes.patchSelect.disabled = disabled;
  nodes.asOfInput.disabled = disabled;
  nodes.queryButton.disabled = disabled;
}

function renderNoCatalogData() {
  nodes.emptyStage.replaceChildren(
    el("span", { className: "empty-index", text: "D2" }),
    el("div", {}, [
      el("h2", { text: "目录窗口内没有可查询战队" }),
      el("p", { text: "当前窗口没有已发生的公开职业 CM 比赛。工作台不会制造默认队伍或演示结果。" }),
    ]),
  );
}

function renderUnqueryableTeam(team) {
  nodes.emptyStage.replaceChildren(
    el("span", { className: "empty-index", text: "D2" }),
    el("div", {}, [
      el("h2", { text: "该战队暂无可用精确版本" }),
      el("p", { text: `${team ? teamLabel(team) : "所选战队"}在当前目录窗口内没有可查询的精确版本。请选择其他战队。` }),
    ]),
  );
}

function handleTeamSearch(event) {
  const query = event.currentTarget.value.trim().toLocaleLowerCase("zh-CN");
  if (String(state.selectedTeamId) !== nodes.teamId.value || teamLabel(findTeam(state.selectedTeamId)) !== nodes.teamSearch.value) {
    state.selectedTeamId = null;
    nodes.teamId.value = "";
    nodes.patchSelect.disabled = true;
    nodes.patchHelp.textContent = "请先从搜索结果中选择战队。";
    nodes.patchHelp.hidden = false;
  }
  const teams = state.catalog ? state.catalog.teams : [];
  const matches = teams.filter((team) => {
    if (!query) return true;
    return [team.name, team.tag, team.team_id].some((value) => safeText(value).toLocaleLowerCase("zh-CN").includes(query));
  }).slice(0, 12);
  state.suggestions = matches;
  state.suggestionIndex = -1;
  renderSuggestions(matches);
  markDraftChanged();
}

function handleTeamKeydown(event) {
  if (nodes.teamSuggestions.hidden && ["ArrowDown", "ArrowUp"].includes(event.key)) {
    handleTeamSearch({ currentTarget: nodes.teamSearch });
  }
  if (!state.suggestions.length) return;
  if (event.key === "ArrowDown") {
    event.preventDefault();
    state.suggestionIndex = Math.min(state.suggestionIndex + 1, state.suggestions.length - 1);
    highlightSuggestion();
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    state.suggestionIndex = Math.max(state.suggestionIndex - 1, 0);
    highlightSuggestion();
  } else if (event.key === "Enter" && state.suggestionIndex >= 0) {
    event.preventDefault();
    selectTeam(state.suggestions[state.suggestionIndex]);
  } else if (event.key === "Escape") {
    hideSuggestions();
  }
}

function renderSuggestions(teams) {
  nodes.teamSuggestions.replaceChildren();
  nodes.teamSearch.setAttribute("aria-expanded", teams.length ? "true" : "false");
  nodes.teamSearchCount.textContent = teams.length ? `${teams.length} 项` : "无匹配";
  if (!teams.length) {
    nodes.teamSuggestions.hidden = true;
    return;
  }
  teams.forEach((team, index) => {
    const main = el("span", { className: "suggestion-main" }, [
      el("span", { className: "suggestion-name", text: safeText(team.name) || `战队 ${team.team_id}` }),
      el("span", { className: "suggestion-meta", text: `${safeText(team.tag) || "无缩写"} · ID ${team.team_id}` }),
    ]);
    const button = el("button", {
      className: "suggestion-button",
      type: "button",
      role: "option",
      dataset: { index: String(index) },
    }, [main, el("span", { className: "suggestion-matches", text: `${formatInteger(team.n_matches)} 场` })]);
    button.addEventListener("mousedown", (event) => event.preventDefault());
    button.addEventListener("click", () => selectTeam(team));
    nodes.teamSuggestions.append(button);
  });
  nodes.teamSuggestions.hidden = false;
}

function highlightSuggestion() {
  const buttons = Array.from(nodes.teamSuggestions.querySelectorAll(".suggestion-button"));
  buttons.forEach((button, index) => {
    const active = index === state.suggestionIndex;
    button.classList.toggle("is-highlighted", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
    if (active) button.scrollIntoView({ block: "nearest" });
  });
}

function hideSuggestions() {
  nodes.teamSuggestions.hidden = true;
  nodes.teamSearch.setAttribute("aria-expanded", "false");
  nodes.teamSearchCount.textContent = "";
}

function selectTeam(team, markChanged = true, preferredPatch = "") {
  state.selectedTeamId = Number(team.team_id);
  nodes.teamId.value = String(team.team_id);
  nodes.teamSearch.value = teamLabel(team);
  hideSuggestions();
  populatePatches(team, preferredPatch || nodes.patchSelect.value);
  if (markChanged) markDraftChanged();
}

function populatePatches(team, preferredPatch) {
  const teamPatches = Array.isArray(team.patches) ? team.patches : [];
  nodes.patchSelect.replaceChildren();
  if (!teamPatches.length) {
    nodes.patchSelect.append(el("option", { value: "", text: "暂无可用精确版本" }));
    nodes.patchSelect.disabled = true;
    nodes.patchHelp.textContent = "该战队在目录窗口内没有可查询的精确版本。";
    nodes.patchHelp.hidden = false;
    return;
  }
  nodes.patchSelect.disabled = false;
  nodes.patchHelp.hidden = true;
  nodes.patchHelp.textContent = "";
  teamPatches.forEach((patch) => addPatchOption(patch));
  const fallback = safeText(state.catalog && state.catalog.defaults && state.catalog.defaults.patch) || teamPatches[0] || "";
  nodes.patchSelect.value = teamPatches.includes(preferredPatch) ? preferredPatch : (teamPatches.includes(fallback) ? fallback : teamPatches[0] || "");
}

function addPatchOption(patch) {
  if (!patch || Array.from(nodes.patchSelect.options).some((option) => option.value === patch)) return;
  nodes.patchSelect.append(el("option", { value: patch, text: patch }));
}

function currentDraftQuery() {
  const teamId = parsePositiveInteger(nodes.teamId.value);
  const patch = nodes.patchSelect.value;
  const asOf = nodes.asOfInput.value;
  if (!teamId || !patch || !isDateString(asOf)) return null;
  return { team_id: teamId, patch, as_of: asOf };
}

function markDraftChanged() {
  updateDraftNotice();
}

function updateDraftNotice() {
  const draft = currentDraftQuery();
  const changed = Boolean(state.profile && draft && !sameQuery(draft, state.appliedQuery));
  nodes.draftNotice.hidden = !changed;
  nodes.queryButton.disabled = !state.catalog || !draft;
}

async function queryProfile() {
  const query = currentDraftQuery();
  if (!query) {
    showRequestState("error", "查询条件不完整", "请从战队搜索结果中选择战队，并填写版本与截止日期。", true);
    return;
  }

  if (state.requestController) state.requestController.abort();
  const controller = new AbortController();
  state.requestController = controller;
  const serial = ++state.requestSerial;
  setQueryBusy(true);
  updateUrl(query);
  showRequestState("loading", "正在读取真实画像", "正在按所选战队、版本和日期计算。", false);

  const params = new URLSearchParams({
    team_id: String(query.team_id),
    patch: query.patch,
    as_of: query.as_of,
    sources: "pro_match,pub_match",
  });

  try {
    const profile = await fetchPayload(`/v1/profile?${params.toString()}`, { signal: controller.signal });
    if (serial !== state.requestSerial) return;
    validateProfile(profile);
    state.profile = profile;
    state.appliedQuery = query;
    initializePlayerSelection(profile.players);
    renderProfile();
    hideRequestState();
    nodes.emptyStage.hidden = true;
    nodes.resultShell.hidden = false;
    nodes.exportButton.disabled = false;
    updateDraftNotice();
  } catch (error) {
    if (error && error.name === "AbortError") return;
    if (serial !== state.requestSerial) return;
    const safeError = normalizedError(error);
    const isBusiness = safeError.code === "insufficient_data";
    showRequestState(
      isBusiness ? "business" : "error",
      isBusiness ? "当前样本不足" : "画像查询未完成",
      safeError.message,
      true,
    );
    if (!state.profile) {
      nodes.emptyStage.hidden = true;
      nodes.resultShell.hidden = true;
    }
  } finally {
    if (serial === state.requestSerial) {
      setQueryBusy(false);
      state.requestController = null;
    }
  }
}

function validateProfile(profile) {
  if (!profile || !Array.isArray(profile.players) || !profile.coverage || !profile.team_bp_tendency) {
    throw new WorkbenchError("upstream_unavailable", "画像响应结构不完整，页面没有采用这次结果。", 0);
  }
}

function initializePlayerSelection(players) {
  const ids = players.map((player) => String(player.account_id));
  if (!ids.includes(String(state.activePlayerId))) state.activePlayerId = ids[0] || null;
  if (!ids.includes(String(state.compareLeftId))) state.compareLeftId = ids[0] || null;
  if (!ids.includes(String(state.compareRightId))) state.compareRightId = ids[1] || ids[0] || null;
  state.bpOrdFilter = "all";
}

function setQueryBusy(busy) {
  nodes.queryButton.disabled = busy || !currentDraftQuery();
  nodes.queryButton.querySelector("span").textContent = busy ? "查询中" : "生成画像";
  nodes.profileForm.setAttribute("aria-busy", busy ? "true" : "false");
}

function showRequestState(type, title, message, retry) {
  nodes.requestState.className = `request-state is-${type}`;
  const content = el("div", {}, [
    el("strong", { text: title }),
    el("p", { text: message }),
  ]);
  const trailing = type === "loading"
    ? el("span", { className: "loading-rule", ariaHidden: "true" })
    : retry ? el("button", { className: "text-button", type: "button", text: "按当前条件重试" }) : null;
  if (trailing && trailing.tagName === "BUTTON") trailing.addEventListener("click", queryProfile);
  nodes.requestState.replaceChildren(el("div", { className: "state-layout" }, trailing ? [content, trailing] : [content]));
  nodes.requestState.hidden = false;
}

function hideRequestState() {
  nodes.requestState.hidden = true;
  nodes.requestState.replaceChildren();
}

function renderProfile() {
  const profile = state.profile;
  const team = findTeam(profile.team_id);
  nodes.resultTeamName.textContent = team ? safeText(team.name) || `战队 ${profile.team_id}` : `战队 ${profile.team_id}`;
  nodes.resultMeta.textContent = `战队 ID ${profile.team_id} · 精确版本 ${safeText(profile.patch)} · 截止 ${formatDate(profile.as_of)} · ${profile.players.length} 名历史参赛选手`;
  renderSummary();
  renderOverview();
  renderPlayers();
  renderComparison();
  renderBp();
  renderSources();
  setActiveView(state.activeView, false);
}

function renderOverview() {
  const profile = state.profile;
  const player = activePlayer();
  if (!player) {
    nodes.overviewContent.replaceChildren(emptyInline("观察窗口内尚无可识别的选手参赛记录。"));
    return;
  }
  const roster = el("div", { className: "roster-ribbon", ariaLabel: "切换选手" });
  profile.players.forEach((item, index) => {
    const hero = featuredHero(item);
    const button = el("button", {
      type: "button", ariaLabel: playerName(item),
      className: `roster-card${String(item.account_id) === String(state.activePlayerId) ? " is-active" : ""}`,
      "aria-pressed": String(String(item.account_id) === String(state.activePlayerId)),
    }, [
      heroImage(hero && hero.hero_id, "hero-portrait"),
      el("span", { className: "roster-number", text: String(index + 1).padStart(2, "0") }),
      el("span", { className: "roster-info" }, [
        el("strong", { className: "roster-name", text: playerName(item) }),
        el("span", { className: "roster-role", text: `${roleLabel(item.role)} · ${formatInteger(item.hero_pool && item.hero_pool.window_games)} 场` }),
      ]),
    ]);
    button.addEventListener("click", () => {
      state.activePlayerId = String(item.account_id);
      renderOverview(); renderPlayers();
    });
    roster.append(button);
  });
  const panel = el("section", { className: "radar-panel" }, [
    playerLead(player),
    el("div", { className: "panel-heading" }, [
      el("h3", { text: "五维画像" }),
      el("p", { text: "相同位置同侪百分位 · 0 至 100" }),
    ]),
    el("div", { className: "radar-layout" }, [
      radarChart(player), el("div", { className: "radar-metrics" }, [metricList(player)]),
    ]),
  ]);
  nodes.overviewContent.replaceChildren(
    insightStrip(profile), roster,
    el("p", { className: "roster-caption", text: "历史参赛选手，卡面为其出场最多的有效池英雄。" }),
    el("div", { className: "analysis-grid" }, [panel, heroFocus(player)]),
    el("div", { className: "overview-bottom" }, [archetypePanel(player), teamMatrix(profile)]),
  );
}

function renderPlayers() {
  const players = state.profile.players;
  if (!players.length) {
    nodes.playersContent.replaceChildren(emptyInline("观察窗口内尚无可识别的选手参赛记录。"));
    return;
  }
  const directory = el("div", { className: "player-directory" }, [
    el("div", { className: "directory-heading", text: `历史参赛选手 · ${players.length} 名` }),
  ]);
  players.forEach((player) => {
    const active = String(player.account_id) === String(state.activePlayerId);
    const button = el("button", { type: "button", className: `directory-button${active ? " is-active" : ""}` }, [
      el("strong", { text: playerName(player) }),
      el("span", { text: `${roleLabel(player.role)} · ${formatInteger(player.hero_pool && player.hero_pool.window_games)} 场` }),
    ]);
    button.addEventListener("click", () => {
      state.activePlayerId = String(player.account_id);
      renderPlayers();
      renderOverview();
    });
    directory.append(button);
  });

  const player = activePlayer();
  const sheet = el("article", { className: "player-sheet" }, [
    playerLead(player),
    el("div", { className: "sheet-grid" }, [
      el("div", {}, [radarChart(player), metricList(player), archetypePanel(player)]),
      el("div", {}, [
        poolStats(player),
        heroGroup("签名英雄", "窗口内达到签名阈值", player.hero_pool && player.hero_pool.signature),
        heroGroup("熟练英雄", "窗口内达到熟练阈值", player.hero_pool && player.hero_pool.comfortable),
      ]),
    ]),
  ]);
  nodes.playersContent.replaceChildren(el("div", { className: "players-layout" }, [directory, sheet]));
}

function renderComparison() {
  const players = state.profile.players;
  if (players.length < 2) {
    nodes.compareContent.replaceChildren(emptyInline("双人对比至少需要两名历史参赛选手。当前结果不足以形成对比。"));
    return;
  }

  const leftSelect = playerSelect("对比选手 A", state.compareLeftId);
  const rightSelect = playerSelect("对比选手 B", state.compareRightId);
  leftSelect.select.addEventListener("change", () => {
    state.compareLeftId = leftSelect.select.value;
    renderComparison();
  });
  rightSelect.select.addEventListener("change", () => {
    state.compareRightId = rightSelect.select.value;
    renderComparison();
  });
  const controls = el("div", { className: "compare-controls" }, [
    leftSelect.wrapper,
    el("span", { className: "compare-versus", text: "对照" }),
    rightSelect.wrapper,
  ]);
  const left = playerById(state.compareLeftId) || players[0];
  const right = playerById(state.compareRightId) || players[1];
  const table = el("div", { className: "comparison-table" }, [
    el("div", { className: "comparison-head" }, [
      el("span", { className: "microcopy", text: "五维百分位" }),
      el("h3", { text: playerName(left) }),
      el("span", { className: "microcopy", text: "同一版本与窗口" }),
      el("h3", { text: playerName(right) }),
    ]),
  ]);
  DIMENSIONS.forEach(([key, label]) => table.append(comparisonRow(label, dimensionValue(left, key), dimensionValue(right, key))));

  const caveat = el("p", {
    className: "comparison-caveat",
    text: "各选手按对应位置同侪计算，跨位置百分位不代表绝对强弱。",
  });

  const heroGrid = el("div", { className: "compare-hero-grid" }, [
    el("div", {}, [
      el("h3", { text: `${playerName(left)} 的签名英雄` }),
      heroList(left.hero_pool && left.hero_pool.signature),
    ]),
    el("div", {}, [
      el("h3", { text: `${playerName(right)} 的签名英雄` }),
      heroList(right.hero_pool && right.hero_pool.signature),
    ]),
  ]);
  const visual = el("div", { className: "comparison-visual" }, [
    personSummary(left, "A"), radarChart(left, right), personSummary(right, "B"),
  ]);
  nodes.compareContent.replaceChildren(controls, caveat, visual, table, heroGrid);
}

function renderBp() {
  const tendency = state.profile.team_bp_tendency || {};
  const phaseEntries = Array.isArray(tendency.ban_by_phase) ? tendency.ban_by_phase : [];
  const phaseFilter = bpPhaseFilter(phaseEntries);
  const bans = bpCard("首阶段常禁", "对手英雄在首阶段被本队禁用的真实频率", tendency.first_phase_ban_freq, false);
  const picks = bpCard("本队第一手常选", "本队第一手 pick 的真实频率", tendency.first_pick_freq, true);
  const phase = el("section", { className: "bp-card phase-card" }, [
    el("div", { className: "phase-tools" }, [
      el("div", {}, [
        el("h3", { text: "按绝对手数观察禁用" }),
        el("p", { className: "microcopy", text: "手数使用完整 BP 的绝对顺序，机会分母只计算本队实际拥有该禁用位的比赛。" }),
      ]),
      phaseFilter.wrapper,
    ]),
    phaseTable(phaseEntries, state.bpOrdFilter),
  ]);
  nodes.bpContent.replaceChildren(el("div", { className: "bp-grid" }, [bans, picks, phase]));
}

function bpPhaseFilter(entries) {
  const select = el("select", { ariaLabel: "按绝对手数筛选" }, [
    el("option", { value: "all", text: "全部手数" }),
  ]);
  const ords = Array.from(new Set(entries
    .map((entry) => finiteNumber(entry.ord))
    .filter((ord) => ord !== null)))
    .sort((left, right) => left - right);
  ords.forEach((ord) => select.append(el("option", { value: String(ord), text: `第 ${ord + 1} 手` })));
  if (state.bpOrdFilter !== "all" && !ords.includes(Number(state.bpOrdFilter))) state.bpOrdFilter = "all";
  select.value = state.bpOrdFilter;
  select.addEventListener("change", () => {
    state.bpOrdFilter = select.value;
    renderBp();
  });
  return {
    wrapper: el("div", { className: "field phase-filter" }, [
      el("label", { text: "筛选手数" }),
      select,
    ]),
  };
}

function renderSources() {
  const profile = state.profile;
  const catalog = state.catalog || {};
  const coverage = profile.coverage || {};
  const pro = coverage.pro_match || {};
  const pub = coverage.pub_match || {};
  const cards = el("div", { className: "coverage-grid" }, [
    coverageCard("职业比赛", pro.n_matches, "窗口内比赛"),
    coverageCard("职业详情", pro.n_stat_available, "已有解析统计"),
    coverageCard("含位置未知", pro.n_position_unknown, "场比赛，未知选手行不参与位置统计"),
    coverageCard("天梯覆盖", pub.n_matches, "队伍关联比赛"),
  ]);

  const sourceNames = Array.isArray(profile.sources_used)
    ? profile.sources_used.map((source) => SOURCE_NAMES[source] || safeText(source)).join("、")
    : "未提供";
  const profileInfo = el("div", {}, [
    sourceBlock("当前画像", [
      `实际使用来源：${sourceNames || "无"}。`,
      `版本 ${safeText(profile.patch)}，截止 ${formatDate(profile.as_of)}。`,
      "画像选手来自窗口内历史参赛记录，不代表当前注册阵容。",
    ]),
    sourceBlock("位置与赛事口径", [
      safeText(catalog.provenance && catalog.provenance.position_notice) || "位置口径未提供。",
      safeText(catalog.provenance && catalog.provenance.tier_notice) || "赛事层级口径未提供。",
    ]),
    sourceBlock("目录窗口", [
      catalog.window ? `${formatDate(catalog.window.from)} 至 ${formatDate(catalog.window.as_of)}。` : "目录窗口未提供。",
      `公开 CM 比赛 ${formatInteger(catalog.summary && catalog.summary.public_cm_matches)} 场，含详情 ${formatInteger(catalog.summary && catalog.summary.detailed_matches)} 场。`,
      `目录生成时间 ${formatDateTime(catalog.generated_at)}。`,
    ]),
  ]);

  const leaguePanel = el("div", {}, [
    el("div", { className: "source-block" }, [
      el("h3", { text: "赛事证据" }),
      el("p", { className: "microcopy", text: "只列出当前窗口出现且名称仍匹配的核定记录。" }),
    ]),
    leagueList(catalog.provenance && catalog.provenance.leagues),
  ]);
  nodes.sourcesContent.replaceChildren(cards, capabilityPanel(), el("div", { className: "source-layout" }, [profileInfo, leaguePanel]));
}

function profileSummary(profile) {
  const players = Array.isArray(profile.players) ? profile.players : [];
  const coverage = (profile.coverage && profile.coverage.pro_match) || {};
  return {
    matches: finiteNumber(coverage.n_matches),
    details: finiteNumber(coverage.n_stat_available),
    players: players.length,
    availableDimensions: players.reduce((sum, player) => sum + DIMENSIONS.filter(([key]) => dimensionValue(player, key).value !== null).length, 0),
    totalDimensions: players.length * DIMENSIONS.length,
  };
}

function renderSummary() {
  if (!nodes.resultSummary) return;
  const summary = profileSummary(state.profile);
  const data = [[formatInteger(summary.matches), "职业比赛"], [formatInteger(summary.details), "已有详情"], [String(summary.players), "历史选手"], [`${summary.availableDimensions}/${summary.totalDimensions}`, "可算维度"]];
  nodes.resultSummary.replaceChildren(...data.map(([value, label]) => el("div", { className: "stat-tile" }, [
    el("strong", { className: "stat-number", text: value }), el("span", { className: "stat-label", text: label }),
  ])));
}

function leadingBp(entries) {
  const valid = (Array.isArray(entries) ? entries : []).filter((entry) => finiteNumber(entry.freq) !== null);
  if (!valid.length) return [];
  const maximum = Math.max(...valid.map((entry) => Number(entry.freq)));
  return valid.filter((entry) => Math.abs(Number(entry.freq) - maximum) < 1e-9);
}

function insightStrip(profile) {
  const tendency = profile.team_bp_tendency || {};
  const summary = profileSummary(profile);
  const blocks = [
    bpInsight("首阶段最高禁用频率", leadingBp(tendency.first_phase_ban_freq)),
    bpInsight("本队第一手最高选择频率", leadingBp(tendency.first_pick_freq)),
    el("article", { className: "insight-item" }, [
      el("span", { className: "insight-label", text: "画像覆盖" }),
      el("strong", { className: "insight-value", text: `${summary.availableDimensions} / ${summary.totalDimensions} 维` }),
      el("p", { className: "insight-note", text: summary.availableDimensions === summary.totalDimensions ? "所列选手五维均可计算，位置为历史分析口径。" : `${summary.totalDimensions - summary.availableDimensions} 维尚不可算，保留缺值及原因。` }),
    ]),
  ];
  return el("div", { className: "insight-strip" }, blocks);
}

function bpInsight(label, entries) {
  const first = entries[0];
  const names = entries.slice(0, 2).map((entry) => heroName(entry.hero_id)).join(" / ");
  const value = first ? `${names}${entries.length > 2 ? ` 等 ${entries.length} 个` : ""}` : "暂无可用记录";
  const note = first ? `${formatPercent(first.freq)} · ${formatInteger(first.n)} 场机会${entries.length > 1 ? " · 并列" : ""}，历史频率不代表建议。` : "需要当前顺序族的合法完整 BP 样本。";
  return el("article", { className: "insight-item" }, [
    el("span", { className: "insight-label", text: label }),
    el("strong", { className: "insight-value", text: value }),
    el("p", { className: "insight-note", text: note }),
  ]);
}

function featuredHero(player) {
  const pool = (player && player.hero_pool) || {};
  const entries = [...(pool.signature || []), ...(pool.comfortable || [])];
  return entries.sort((a, b) => (finiteNumber(b.games) || 0) - (finiteNumber(a.games) || 0) || Number(a.hero_id) - Number(b.hero_id))[0] || null;
}

function heroImageUrl(heroId, crop = false) {
  const hero = state.catalog && state.catalog.heroes && state.catalog.heroes[String(heroId)];
  const name = hero && safeText(hero.name);
  if (!name || !/^npc_dota_hero_[a-z0-9_]+$/.test(name)) return "";
  return `https://cdn.steamstatic.com/apps/dota2/images/dota_react/heroes/${crop ? "crops/" : ""}${name.slice(14)}.png`;
}

function heroImage(heroId, className) {
  const url = heroImageUrl(heroId, ["hero-focus-art", "person-hero"].includes(className));
  const fallback = () => el("div", { className: `${className} image-unavailable`, ariaHidden: "true", text: heroId ? heroName(heroId).slice(0, 2).toUpperCase() : "?" });
  if (!url) return fallback();
  const image = el("img", { className, src: url, alt: "", loading: "lazy", decoding: "async", referrerPolicy: "no-referrer" });
  image.addEventListener("error", () => image.replaceWith(fallback()), { once: true });
  return image;
}

function heroFocus(player) {
  const hero = featuredHero(player);
  if (!hero) return el("section", { className: "hero-focus" }, [emptyInline("暂无达到有效英雄池阈值的代表英雄。")]);
  const pool = player.hero_pool || {};
  const signature = (pool.signature || []).some((item) => item.hero_id === hero.hero_id);
  return el("section", { className: "hero-focus" }, [
    heroImage(hero.hero_id, "hero-focus-art"),
    el("div", { className: "hero-focus-content" }, [
      el("span", { className: "hero-kicker", text: `${playerName(player)} · ${signature ? "签名英雄" : "熟练英雄"}` }),
      el("h3", { text: heroName(hero.hero_id) }),
      el("div", { className: "hero-focus-stats" }, [
        smallStat(formatInteger(hero.games), "出场"),
        smallStat(formatPercent(hero.wr), "样本内胜率"),
        smallStat(formatNullablePercentPoint(hero.pct), "出场占比"),
      ]),
      el("p", { className: "hero-focus-note", text: "有效英雄池中出场最多的英雄，胜率为历史样本描述。" }),
    ]),
  ]);
}

function radarSeries(player) {
  const points = [];
  DIMENSIONS.forEach(([key, label], index) => {
    const metric = dimensionValue(player, key);
    if (metric.value === null) return;
    const angle = -Math.PI / 2 + index * Math.PI * 2 / 5;
    const radius = clamp(metric.value, 0, 100) * 1.31;
    points.push({ key, label, value: metric.value, index, x: 210 + Math.cos(angle) * radius, y: 180 + Math.sin(angle) * radius });
  });
  return { complete: points.length === DIMENSIONS.length, points };
}

function svgElement(tagName, attrs = {}, text = "") {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tagName);
  Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, String(value)));
  if (text) element.textContent = text;
  return element;
}

function radarChart(player, comparison = null) {
  const svg = svgElement("svg", { viewBox: "0 0 420 360", class: `radar-figure${comparison ? " radar-comparison" : ""}`, role: "img", "aria-label": `${playerName(player)}${comparison ? ` 与 ${playerName(comparison)}` : ""}的五维百分位，缺失维度不绘制面积` });
  const point = (index, radius) => {
    const angle = -Math.PI / 2 + index * Math.PI * 2 / 5;
    return [210 + Math.cos(angle) * radius, 180 + Math.sin(angle) * radius];
  };
  [0.25, 0.5, 0.75, 1].forEach((scale) => {
    svg.append(svgElement("polygon", { class: "radar-grid", points: DIMENSIONS.map((_, index) => point(index, 131 * scale).join(",")).join(" ") }));
  });
  DIMENSIONS.forEach(([key, label], index) => {
    const [x, y] = point(index, 131);
    svg.append(svgElement("line", { class: "radar-axis", x1: 210, y1: 180, x2: x, y2: y }));
    const [lx, ly] = point(index, 158);
    const labelText = svgElement("text", { x: lx, y: ly - 4, class: "radar-label", "text-anchor": "middle" }, label.replace("与", ""));
    svg.append(labelText);
    if (!comparison) {
      const metric = dimensionValue(player, key);
      const valueLabel = svgElement("text", { x: lx, y: ly + 16, class: `radar-value${metric.value === null ? " radar-placeholder" : ""}`, "text-anchor": "middle", "aria-label": `${label}：${metric.value === null ? metric.reason : `${metric.value} 百分位`}` }, metric.value === null ? "缺值" : String(metric.value));
      if (metric.value === null) valueLabel.append(svgElement("title", {}, metric.reason));
      svg.append(valueLabel);
    }
  });
  [player, comparison].filter(Boolean).forEach((item, seriesIndex) => {
    const series = radarSeries(item);
    const suffix = seriesIndex ? " is-secondary" : "";
    if (series.complete) svg.append(svgElement("polygon", { class: `radar-shape${suffix}`, points: series.points.map((p) => `${p.x},${p.y}`).join(" ") }));
    series.points.forEach((p) => {
      const circle = svgElement("circle", { class: `radar-node${suffix}`, cx: p.x, cy: p.y, r: 4 });
      circle.append(svgElement("title", {}, `${playerName(item)} · ${p.label}：${p.value} 百分位`));
      svg.append(circle);
    });
  });
  return svg;
}

function teamMatrix(profile) {
  const table = el("table", { className: "team-matrix" });
  table.append(el("thead", {}, [el("tr", {}, [el("th", { scope: "col", text: "历史选手" }), ...DIMENSIONS.map(([, label]) => el("th", { scope: "col", text: label.replace("与", "") }))])]));
  const body = el("tbody");
  profile.players.forEach((player) => {
    const button = el("button", { className: "matrix-player", type: "button", text: playerName(player), ariaLabel: `查看 ${playerName(player)} 的档案` });
    button.addEventListener("click", () => {
      state.activePlayerId = String(player.account_id); renderPlayers(); renderOverview(); setActiveView("players", true);
    });
    const row = el("tr", {}, [el("th", { scope: "row" }, [button, el("span", { className: "matrix-role", text: roleLabel(player.role) })])]);
    DIMENSIONS.forEach(([key]) => {
      const metric = dimensionValue(player, key);
      const cell = el("span", { className: `matrix-score${metric.value === null ? " is-missing" : ""}`, title: metric.value === null ? metric.reason : `${metric.value} 百分位`, text: metric.value === null ? "缺值" : String(metric.value) });
      if (metric.value !== null) cell.style.setProperty("--score", String(metric.value / 100));
      row.append(el("td", {}, [cell]));
    });
    body.append(row);
  });
  table.append(body);
  return el("section", { className: "team-matrix-panel" }, [
    el("div", { className: "panel-heading" }, [el("h3", { text: "选手维度矩阵" }), el("p", { text: "各自按对应位置同侪计算，不表示跨位置的绝对强弱。" })]),
    el("div", { className: "matrix-scroll" }, [table]),
  ]);
}

function personSummary(player, side) {
  const hero = featuredHero(player);
  return el("div", { className: `person-summary${side === "B" ? " is-secondary" : ""}` }, [
    heroImage(hero && hero.hero_id, "person-hero"),
    el("span", { className: "person-side", text: side }),
    el("h3", { text: playerName(player) }),
    el("p", { text: `${roleLabel(player.role)} · ${formatInteger(player.hero_pool && player.hero_pool.window_games)} 场` }),
    el("p", { className: "microcopy", text: hero ? `代表英雄 · ${heroName(hero.hero_id)}` : "暂无代表英雄" }),
  ]);
}

function capabilityPanel() {
  const rows = [
    ["历史画像", "已接入", "英雄池、选手五维、BP 历史频率与来源覆盖"],
    ["局面评估 Value", "未接入", "尚不能计算对局胜率与条件化对位效应"],
    ["下一手预测 Policy", "未接入", "已有离线频率实验，尚无在线预测与序列模型"],
    ["剧本 Playbook", "未接入", "尚不能生成双方选禁分支与应对方案"],
    ["决策 Advise", "未接入", "尚不能给出整手建议和稳健性评估"],
  ];
  const table = el("table", { className: "capability-table" }, [
    el("thead", {}, [el("tr", {}, ["能力", "状态", "实际范围"].map((label) => el("th", { scope: "col", text: label })))]),
    el("tbody", {}, rows.map(([name, status, note]) => el("tr", {}, [el("th", { scope: "row", text: name }), el("td", { className: status === "已接入" ? "capability-ready" : "capability-pending", text: status }), el("td", { text: note })]))),
  ]);
  return el("section", { className: "capability-panel" }, [
    el("h3", { text: "当前分析能力" }),
    el("p", { className: "microcopy", text: "当前可做历史画像分析，完整选禁决策链尚未完成。" }),
    el("div", { className: "matrix-scroll" }, [table]),
    el("p", { className: "asset-credit", text: "英雄图像来自 Valve 的 Dota 2 公开 CDN，相关图像与商标归 Valve 所有。图像无法载入时保留名称与全部分析数据。" }),
  ]);
}

function playerLead(player) {
  return el("div", { className: "player-lead" }, [
    el("div", {}, [
      el("h3", { className: "player-name", text: playerName(player) }),
      el("div", { className: "player-id", text: `账号 ID ${safeText(player.account_id)}` }),
    ]),
    el("span", { className: "role-badge", text: roleLabel(player.role) }),
  ]);
}

function metricList(player) {
  const list = el("div", { className: "metric-list" });
  DIMENSIONS.forEach(([key, label]) => {
    const metric = dimensionValue(player, key);
    const track = el("div", { className: `bar-track${metric.value === null ? " is-missing" : ""}` });
    if (metric.value !== null) track.append(el("span", { className: "bar-fill", style: { width: `${clamp(metric.value, 0, 100)}%` } }));
    const value = metric.value === null
      ? el("span", { className: "metric-value metric-note", text: metric.reason })
      : el("span", { className: "metric-value" }, [el("strong", { text: String(metric.value) }), document.createTextNode(" 百分位")]);
    list.append(el("div", { className: "metric-row" }, [
      el("span", { className: "metric-label", text: label }),
      track,
      value,
    ]));
  });
  return list;
}

function poolStats(player) {
  const pool = player.hero_pool || {};
  return el("div", { className: "aside-stat-grid" }, [
    smallStat(formatNullableInteger(pool.window_games), "窗口比赛"),
    smallStat(formatNullableInteger(pool.effective_count), "有效英雄"),
    smallStat(formatPercent(pool.presence_pick_rate), "放出后选择率"),
    smallStat(roleLabel(player.role), "分析位置"),
  ]);
}

function smallStat(value, label) {
  return el("div", { className: "aside-stat" }, [
    el("strong", { text: value }),
    el("span", { text: label }),
  ]);
}

function heroGroup(title, note, entries) {
  return el("section", { className: "hero-group" }, [
    el("h3", { text: title }),
    el("p", { className: "microcopy", text: note }),
    heroList(entries),
  ]);
}

function heroList(entries) {
  if (!Array.isArray(entries) || !entries.length) return emptyInline("暂无达到阈值的英雄。");
  const list = el("ol", { className: "hero-list" });
  entries.forEach((item, index) => {
    list.append(el("li", { className: "hero-item" }, [
      el("span", { className: "hero-rank", text: String(index + 1).padStart(2, "0") }),
      heroImage(item.hero_id, "hero-thumb"),
      el("span", { className: "hero-name", text: heroName(item.hero_id) }),
      el("span", { className: "hero-record", text: `${formatNullableInteger(item.games)} 场 · 胜率 ${formatPercent(item.wr)} · 占比 ${formatNullablePercentPoint(item.pct)}` }),
    ]));
  });
  return list;
}

function archetypePanel(player) {
  const archetypes = player.dimensions && player.dimensions.hero_archetype;
  const panel = el("section", { className: "archetype-panel" }, [
    el("h3", { text: "英雄类型偏好" }),
    el("p", { className: "microcopy", text: "按实际英雄出场加权的归一化分布" }),
  ]);
  ARCHETYPES.forEach(([key, label]) => {
    const value = finiteNumber(archetypes && archetypes[key]);
    const track = el("span", { className: `bar-track${value === null ? " is-missing" : ""}` });
    if (value !== null) track.append(el("span", { className: "bar-fill", style: { width: `${clamp(value * 100, 0, 100)}%` } }));
    panel.append(el("div", { className: "archetype-row" }, [
      el("span", { text: label }),
      track,
      el("strong", { text: value === null ? "缺值" : formatPercent(value) }),
    ]));
  });
  return panel;
}

function dimensionValue(player, key) {
  const metric = player && player.dimensions && player.dimensions[key];
  const percentile = finiteNumber(metric && metric.percentile);
  if (percentile !== null) return { value: percentile, reason: "" };
  return { value: null, reason: REASONS[metric && metric.reason] || "暂不可算" };
}

function playerSelect(label, selectedId) {
  const select = el("select", { ariaLabel: label });
  state.profile.players.forEach((player) => {
    const option = el("option", { value: String(player.account_id), text: `${playerName(player)} · ${roleLabel(player.role)}` });
    option.selected = String(player.account_id) === String(selectedId);
    select.append(option);
  });
  const wrapper = el("div", { className: "field" }, [el("label", { text: label }), select]);
  return { wrapper, select };
}

function comparisonRow(label, left, right) {
  const dual = el("div", { className: "dual-track", ariaHidden: "true" });
  const leftTrack = el("span", { className: `half-track${left.value === null ? " is-missing" : ""}` });
  const rightTrack = el("span", { className: `half-track${right.value === null ? " is-missing" : ""}` });
  if (left.value !== null) leftTrack.append(el("span", { className: "bar-fill", style: { width: `${clamp(left.value, 0, 100)}%` } }));
  if (right.value !== null) rightTrack.append(el("span", { className: "bar-fill", style: { width: `${clamp(right.value, 0, 100)}%` } }));
  dual.append(leftTrack, rightTrack);
  return el("div", { className: "comparison-row" }, [
    el("strong", { text: label }),
    el("span", { className: "compare-value-left", text: left.value === null ? left.reason : `${left.value}` }),
    dual,
    el("span", { className: "compare-value-right", text: right.value === null ? right.reason : `${right.value}` }),
  ]);
}

function bpCard(title, note, entries, accent) {
  const card = el("section", { className: `bp-card${accent ? " is-accent" : ""}` }, [
    el("h3", { text: title }),
    el("p", { className: "microcopy", text: note }),
  ]);
  if (!Array.isArray(entries) || !entries.length) {
    card.append(emptyInline("当前顺序族没有可展示的合法完整 BP。"));
    return card;
  }
  const more = entries.length > 6 ? el("details", { className: "bp-more" }, [
    el("summary", { text: `查看其余 ${entries.length - 6} 个英雄` }),
  ]) : null;
  entries.forEach((entry, index) => {
    const meter = el("span", { className: "bp-meter", ariaHidden: "true" });
    if (finiteNumber(entry.freq) !== null) meter.append(el("span", { className: "bar-fill", style: { width: `${clamp(entry.freq * 100, 0, 100)}%` } }));
    (index < 6 ? card : more).append(el("div", { className: "bp-row" }, [
      heroImage(entry.hero_id, "hero-thumb"),
      el("strong", { text: heroName(entry.hero_id) }),
      meter,
      el("span", { className: "bp-frequency", text: formatPercent(entry.freq) }),
      el("span", { className: "bp-sample", text: `${formatNullableInteger(entry.n)} 场机会` }),
    ]));
  });
  if (more) card.append(more);
  return card;
}

function phaseTable(entries, ordFilter = "all") {
  if (!Array.isArray(entries) || !entries.length) return emptyInline("当前顺序族没有按手数可展示的禁用记录。");
  const visibleEntries = ordFilter === "all"
    ? entries
    : entries.filter((entry) => String(entry.ord) === String(ordFilter));
  if (!visibleEntries.length) return emptyInline("所选手数没有可展示的禁用记录。");
  const table = el("table", { className: "phase-table" });
  table.append(el("thead", {}, [el("tr", {}, [
    el("th", { scope: "col", text: "绝对手数" }),
    el("th", { scope: "col", text: "英雄" }),
    el("th", { scope: "col", text: "频率" }),
    el("th", { scope: "col", text: "机会分母" }),
  ])]));
  const body = el("tbody");
  visibleEntries.forEach((entry) => {
    const ord = finiteNumber(entry.ord);
    body.append(el("tr", {}, [
      el("td", { text: ord === null ? "缺值" : `第 ${ord + 1} 手` }),
      el("td", {}, [el("span", { className: "phase-hero" }, [heroImage(entry.hero_id, "hero-thumb"), el("span", { text: heroName(entry.hero_id) })])]),
      el("td", { text: formatPercent(entry.freq) }),
      el("td", { text: `${formatNullableInteger(entry.n)} 场` }),
    ]));
  });
  table.append(body);
  return table;
}

function coverageCard(label, value, note) {
  return el("div", { className: "coverage-card" }, [
    el("span", { text: label }),
    el("strong", { text: formatNullableInteger(value) }),
    el("span", { text: note }),
  ]);
}

function sourceBlock(title, paragraphs) {
  const block = el("section", { className: "source-block" }, [el("h3", { text: title })]);
  paragraphs.forEach((text) => block.append(el("p", { text })));
  return block;
}

function leagueList(leagues) {
  if (!Array.isArray(leagues) || !leagues.length) return emptyInline("当前窗口没有可列出的赛事证据。");
  const list = el("ul", { className: "league-list" });
  leagues.forEach((league) => {
    const name = safeText(league.name) || `赛事 ${safeText(league.league_id)}`;
    const safeUrl = validatedHttpUrl(league.source_url);
    const title = safeUrl
      ? el("a", { href: safeUrl, target: "_blank", rel: "noreferrer", text: name })
      : el("strong", { text: name });
    list.append(el("li", { className: "league-item" }, [
      el("div", {}, [title, el("div", { className: "microcopy", text: `赛事 ID ${safeText(league.league_id)}` })]),
      el("div", { className: "league-meta", text: `${safeText(league.tier) || "层级未知"}\n核定 ${formatDate(league.reviewed_at)}` }),
    ]));
  });
  return list;
}

function setActiveView(view, writeHash) {
  if (!nodes.navButtons.some((button) => button.dataset.viewTarget === view)) view = "overview";
  state.activeView = view;
  nodes.navButtons.forEach((button) => {
    const active = button.dataset.viewTarget === view;
    button.classList.toggle("is-active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  nodes.viewPanels.forEach((panel) => {
    const active = panel.dataset.view === view;
    panel.hidden = !active;
    panel.classList.toggle("is-active", active);
  });
  if (writeHash && window.location.hash !== `#${view}`) {
    history.pushState(null, "", `${window.location.pathname}${window.location.search}#${view}`);
  }
  if (writeHash && nodes.resultShell && !nodes.resultShell.hidden) {
    nodes.resultShell.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function exportProfile() {
  if (!state.profile) return;
  nodes.exportJson.value = `${JSON.stringify(state.profile, null, 2)}\n`;
  nodes.exportDescription.textContent = `战队 ${state.profile.team_id} · ${state.profile.patch} · ${state.profile.as_of}，与当前已展示的画像一致。`;
  nodes.exportStatus.textContent = "复制后可保存为 .json 文件。";
  nodes.exportDialog.showModal();
  nodes.exportJson.scrollTop = 0;
  nodes.exportCopy.focus();
}

async function copyProfile() {
  try {
    await navigator.clipboard.writeText(nodes.exportJson.value);
    nodes.exportStatus.textContent = "已复制当前画像 JSON。";
  } catch (_) {
    nodes.exportJson.focus();
    nodes.exportJson.select();
    nodes.exportStatus.textContent = "浏览器未允许自动复制。内容已选中，请按 Cmd+C 或 Ctrl+C 复制。";
  }
}

function updateUrl(query) {
  const params = new URLSearchParams({
    team_id: String(query.team_id),
    patch: query.patch,
    as_of: query.as_of,
  });
  history.replaceState(null, "", `${window.location.pathname}?${params.toString()}${window.location.hash}`);
}

function activePlayer() {
  return playerById(state.activePlayerId) || (state.profile && state.profile.players[0]);
}

function playerById(id) {
  if (!state.profile) return null;
  return state.profile.players.find((player) => String(player.account_id) === String(id)) || null;
}

function findTeam(teamId) {
  if (!state.catalog || teamId === null || teamId === undefined) return null;
  return state.catalog.teams.find((team) => String(team.team_id) === String(teamId)) || null;
}

function firstTeamPatch(team) {
  return team && Array.isArray(team.patches) ? safeText(team.patches[0]) : "";
}

function teamLabel(team) {
  if (!team) return "";
  const name = safeText(team.name) || `战队 ${team.team_id}`;
  return team.tag ? `${name}（${safeText(team.tag)}）` : name;
}

function playerName(player) {
  if (!player) return "未知选手";
  return safeText(player.name) || `选手 ${safeText(player.account_id)}`;
}

function roleLabel(role) {
  const value = finiteNumber(role);
  return value === null ? "位置未知" : `${value} 号位`;
}

function heroName(heroId) {
  const heroes = state.catalog && state.catalog.heroes;
  const hero = heroes && heroes[String(heroId)];
  if (!hero) return `英雄 ${safeText(heroId)}`;
  return safeText(hero.localized_name) || safeText(hero.name) || `英雄 ${safeText(heroId)}`;
}

function emptyInline(message, tagName = "div") {
  return el(tagName, { className: "empty-inline" }, [el("p", { text: message })]);
}

function sameQuery(left, right) {
  return Boolean(left && right
    && String(left.team_id) === String(right.team_id)
    && left.patch === right.patch
    && left.as_of === right.as_of);
}

function normalizedError(error) {
  if (error instanceof WorkbenchError) return error;
  return new WorkbenchError("upstream_unavailable", "发生未预期错误，页面没有采用这次结果。", 0);
}

function finiteNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function parsePositiveInteger(value) {
  if (!/^\d+$/.test(safeText(value))) return null;
  const number = Number(value);
  return Number.isSafeInteger(number) && number > 0 ? number : null;
}

function formatInteger(value) {
  const number = finiteNumber(value);
  return number === null ? "缺值" : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(number);
}

function formatNullableInteger(value) {
  return formatInteger(value);
}

function formatPercent(value) {
  const number = finiteNumber(value);
  return number === null ? "缺值" : new Intl.NumberFormat("zh-CN", { style: "percent", maximumFractionDigits: 1 }).format(number);
}

function formatNullablePercentPoint(value) {
  const number = finiteNumber(value);
  return number === null ? "缺值" : `${number}%`;
}

function formatDate(value) {
  const text = safeText(value);
  if (!text) return "缺值";
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return match ? `${match[1]} 年 ${Number(match[2])} 月 ${Number(match[3])} 日` : text;
}

function formatDateTime(value) {
  const text = safeText(value);
  if (!text) return "缺值";
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return text;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", timeZoneName: "short",
  }).format(date);
}

function isDateString(value) {
  return /^\d{4}-\d{2}-\d{2}$/.test(safeText(value));
}

function safeText(value) {
  return value === null || value === undefined ? "" : String(value);
}

function validatedHttpUrl(value) {
  try {
    const url = new URL(safeText(value));
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch (_) {
    return "";
  }
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function cssEscape(value) {
  if (window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(value || "");
  return safeText(value).replace(/[^a-zA-Z0-9_-]/g, "");
}

function toCamel(value) {
  return value.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
}

function el(tagName, props = {}, children = []) {
  const element = document.createElement(tagName);
  Object.entries(props).forEach(([key, value]) => {
    if (value === null || value === undefined) return;
    if (key === "className") element.className = value;
    else if (key === "text") element.textContent = value;
    else if (key === "style") Object.assign(element.style, value);
    else if (key === "dataset") Object.entries(value).forEach(([name, dataValue]) => { element.dataset[name] = dataValue; });
    else if (key === "ariaLabel") element.setAttribute("aria-label", value);
    else if (key === "ariaHidden") element.setAttribute("aria-hidden", value);
    else if (key in element && !["role", "scope"].includes(key)) element[key] = value;
    else element.setAttribute(key, value);
  });
  const normalized = Array.isArray(children) ? children : [children];
  normalized.filter(Boolean).forEach((child) => element.append(child));
  return element;
}
