const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {resolve}=require('node:path');
const vm=require('node:vm');
class Node {
  constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.attrs={};this.className='';this.value='';this.style=new Proxy({}, {set(target,key,value){if (/^\d+$/.test(key)) throw new TypeError('CSS 不支持数字属性');target[key]=value;return true;}});this.classList={add(){},remove(){}};}
  setAttribute(key,value){this.attrs[key]=value;}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  addEventListener(){}
}
const nodes={};
const document={addEventListener(){},createElement(tag){return new Node(tag);},getElementById(id){return nodes[id] ||= new Node('div');}};
const context=vm.createContext({document,Intl,URL,console});
for(const file of ['workbench.js','analysis-workbench.js']) vm.runInContext(readFileSync(resolve(__dirname,'../../web',file),'utf8'),context);
const run=code=>vm.runInContext(code,context);
const flatten=node=>[node,...node.children.flatMap(flatten)];

test('真实响应渲染不会把 CSS 字符串当属性数组，并保留零概率',()=>{
  run(`renderLabResults({request:{patch:'7.41e',as_of:'2026-09-22',us_side:0},value:{radiant_win_prob:0,n_samples:0,confidence:'low',contributions:[{factor:'patch_strength',delta:-.5},{factor:'counter_matchup',delta:0},{factor:'player_comfort',delta:0},{factor:'first_pick',delta:0}]},policy:{error:{code:'insufficient_data',message:'无样本'}},advise:{error:{code:'insufficient_data',message:'无模型'}},playbook:{error:{code:'insufficient_data',message:'无模型'}}})`);
  const all=flatten(nodes['lab-results']);
  assert.ok(all.some(n=>n.textContent==='0.0%'));
  assert.ok(all.some(n=>n.style.width==='0%'));
  assert.ok(all.some(n=>n.textContent.includes('实验模式')));
});

test('局部模块不足不会隐藏已经返回的局面',()=>{
  const all=flatten(nodes['lab-results']);
  assert.ok(all.some(n=>n.textContent==='局面评估'));
  assert.ok(all.some(n=>n.textContent==='无模型'));
  assert.equal(all.filter(n=>n.className==='lab-module').length,4);
});

test('BP 规则从服务模板解析并响应先手换边',()=>{
  document.getElementById('lab-first').value='1';
  run(`labState.config={draft_template:[{ord:0,actor:'F',is_pick:false},{ord:1,actor:'O',is_pick:true}]}`);
  assert.equal(run('labResolved(0).team'),1);
  assert.equal(run('labResolved(1).team'),0);
  assert.equal(run('labResolved(1).is_pick'),true);
});

test('已出现英雄不允许再次加入当前手',()=>{
  run(`labState.draft=[{ord:0,team:0,is_pick:false,hero_id:80}];addLabHero(80)`);
  assert.equal(run('labState.draft.length'),1);
});
