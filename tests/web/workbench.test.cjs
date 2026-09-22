const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');
class SvgNode {
  constructor(tag) { this.tag = tag; this.attrs = {}; this.children = []; this.textContent = ''; }
  setAttribute(key, value) { this.attrs[key] = value; }
  append(child) { this.children.push(child); }
}
const context = vm.createContext({ document: { addEventListener() {}, createElementNS(_namespace, tag) { return new SvgNode(tag); } }, Intl, URL, console });
vm.runInContext(readFileSync(resolve(__dirname, '../../web/workbench.js'), 'utf8'), context);
function run(code) { return vm.runInContext(code, context); }

test('缺失维度不在雷达图中伪造为零或闭合面积', () => {
  const result = run(`radarSeries({dimensions:{hero_pool:{percentile:0},laning:{percentile:80},combat:{reason:'insufficient_samples'},map_vision:{percentile:60},tempo:{percentile:70}}})`);
  assert.equal(result.complete, false);
  assert.equal(result.points.length, 4);
  assert.equal(result.points[0].value, 0);
  assert.equal(result.points.some(p => p.key === 'combat'), false);
});

test('完整五维允许闭合且百分位只决定轴上半径', () => {
  const result = run(`radarSeries({dimensions:Object.fromEntries(DIMENSIONS.map(([key])=>[key,{percentile:100}]))})`);
  assert.equal(result.complete, true);
  assert.equal(result.points.length, 5);
  assert.equal(result.points[0].x, 210);
  assert.equal(result.points[0].y, 49);
});

test('摘要仅统计已知维度数量，不把不同位置混成综合评分', () => {
  const result = run(`profileSummary({players:[{dimensions:{hero_pool:{percentile:0},laning:{percentile:70}}},{dimensions:{combat:{percentile:80}}}],coverage:{pro_match:{n_matches:17,n_stat_available:12}}})`);
  assert.equal(result.availableDimensions, 3);
  assert.equal(result.totalDimensions, 10);
  assert.equal(result.matches, 17);
  assert.equal(result.details, 12);
  assert.equal('score' in result, false);
});

test('代表英雄依据真实场次排序，缺英雄池保持空', () => {
  run(`state.catalog={heroes:{'1':{name:'npc_dota_hero_antimage'},'2':{name:'npc_dota_hero_axe'}}}`);
  assert.equal(run(`featuredHero({hero_pool:{signature:[{hero_id:1,games:3}],comfortable:[{hero_id:2,games:8}]}}).hero_id`), 2);
  assert.equal(run('featuredHero({hero_pool:{signature:[],comfortable:[]}})'), null);
});

test('英雄图片地址仅接受目录内的安全标识', () => {
  run(`state.catalog={heroes:{'1':{name:'npc_dota_hero_tidehunter'},'2':{name:'../../private'},'3':{name:'npc_dota_hero_x?secret=1'}}}`);
  assert.equal(run('heroImageUrl(1)'), 'https://cdn.steamstatic.com/apps/dota2/images/dota_react/heroes/tidehunter.png');
  assert.equal(run('heroImageUrl(2)'), '');
  assert.equal(run('heroImageUrl(3)'), '');
  assert.equal(run('heroImageUrl(999)'), '');
});

test('BP 摘要保留并列第一，不制造唯一偏好', () => {
  const result = run(`leadingBp([{hero_id:1,freq:.4,n:10},{hero_id:2,freq:.6,n:10},{hero_id:3,freq:.6,n:10}])`);
  assert.equal(result.length, 2);
  assert.equal(result[0].hero_id, 2);
  assert.equal(result[1].hero_id, 3);
});


test('雷达渲染保留解析缺失原因，并且不画不完整面积', () => {
  const chart = run(`radarChart({name:'测试选手',dimensions:{hero_pool:{percentile:0},laning:{percentile:80},combat:{reason:'stat_unavailable'},map_vision:{percentile:60},tempo:{percentile:70}}})`);
  const missing = chart.children.find(node => node.attrs.class === 'radar-value radar-placeholder');
  assert.equal(missing.textContent, '缺值');
  assert.equal(missing.attrs['aria-label'], '战斗：解析统计缺失');
  assert.equal(chart.children.some(node => node.attrs.class === 'radar-shape'), false);
  assert.equal(chart.children.filter(node => node.attrs.class === 'radar-node').length, 4);
});
