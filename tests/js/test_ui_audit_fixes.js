"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { pathToFileURL } = require('node:url');
const ui = path.join(__dirname, '../../kazma-ui/kazma_ui/static/js');

(async () => {
  const events = new Map();
  let confirmed = false;
  const timers = [];
  const window = {
    kazmaConfirm: async () => confirmed,
    addEventListener: (type, fn) => events.set(type, fn),
    removeEventListener: (type, fn) => { if (events.get(type) === fn) events.delete(type); },
  };
  const ctx = vm.createContext({ window, setTimeout: fn => timers.push(fn), Map, console });
  vm.runInContext(fs.readFileSync(path.join(ui, 'ide.js'), 'utf8'), ctx);
  const editor = vm.runInContext('ideApp()', ctx);
  ['initEditor','loadTree','loadSkills','initChat'].forEach(key => editor[key] = () => {});
  editor.init();
  editor.tabs = [{path:'a',dirty:false}, {path:'b',dirty:true}];
  assert.equal(editor.hasUnsavedChanges(), true, 'a background draft is protected');
  assert.equal(await editor.confirmLeave(), false);
  assert.equal(editor.tabs[1].dirty, true, 'Cancel retains the draft');
  let warned = 0;
  events.get('beforeunload')({preventDefault:()=>warned++});
  assert.equal(warned, 1, 'browser close/reload warns about dirty background tabs');
  confirmed = true;
  assert.equal(await editor.confirmLeave(), true);
  timers.forEach(fn=>fn());
  assert.equal(editor._confirmedLeave, false, 'a failed navigation cannot leave warnings suppressed');
  editor.destroy();
  assert.equal(window.kazmaBeforeNavigate, null);
  assert.equal(events.has('beforeunload'), false);

  global.window = window;
  const nav = await import(pathToFileURL(path.join(ui, 'modules/nav.js')).href);
  window.kazmaBeforeNavigate = async () => false;
  assert.equal(await nav.canLeavePage(), false);
  window.kazmaBeforeNavigate = async () => true;
  assert.equal(await nav.canLeavePage(), true);
  window.kazmaBeforeNavigate = async () => {throw Error('dialog unavailable');};
  assert.equal(await nav.canLeavePage(), false, 'guard failure preserves the page');

  const documentEvents = new Map(), historyMoves = [];
  let fetched = 0, destroyed = 0;
  const classes = {toggle:()=>{}};
  global.location = {href:'http://localhost/ide',origin:'http://localhost',pathname:'/ide',search:''};
  window.location = global.location;
  global.history = {
    state:{kazmaNavIndex:4,kazmaNavHistory:'same-history'},
    replaceState(state){this.state=state;},
    pushState(state){this.state=state;},
    go:distance=>historyMoves.push(distance),
  };
  global.document = {
    querySelectorAll:()=>[],documentElement:{classList:classes},body:{classList:classes},
    addEventListener:(type,fn)=>documentEvents.set(type,fn),
  };
  global.fetch = async ()=>{fetched++;throw Error('fetch failed');};
  window.kazmaBeforeNavigate = async ()=>false;
  window.kazmaOnSoftNavLeave = ()=>destroyed++;
  nav.initSoftNav();
  assert.equal(history.state.kazmaNavIndex, 4, 'reload keeps the history position');
  const anchor = {href:'http://localhost/settings',origin:'http://localhost',target:'',hasAttribute:()=>false};
  const click = {button:0,target:{closest:()=>anchor},preventDefault:()=>{}};
  documentEvents.get('click')(click);
  await new Promise(setImmediate);
  assert.equal(fetched, 0, 'Cancel prevents fetching or replacing the page');
  assert.equal(destroyed, 0);
  await events.get('popstate')({state:{kazmaNavIndex:3,kazmaNavHistory:'same-history'}});
  assert.deepEqual(historyMoves, [1], 'Cancel on Back restores the committed history entry');
  await events.get('popstate')({state:history.state});
  window.kazmaBeforeNavigate = async ()=>true;
  documentEvents.get('click')(click);
  await new Promise(setImmediate);
  assert.equal(fetched, 1);
  assert.equal(destroyed, 0, 'a failed fetch does not destroy the editor');
  assert.equal(window.location.href, anchor.href, 'confirmed navigation can fall back to a full load');

  let resolveFetch, fail = false;
  const agentCtx = vm.createContext({
    window: {KAZMA_LANG:'en'}, t:key=>key, console,
    fetch:()=>fail ? Promise.reject(Error('offline')) : new Promise(resolve=>resolveFetch=resolve),
  });
  vm.runInContext(fs.readFileSync(path.join(ui, 'agents.js'), 'utf8'), agentCtx);
  const agents = vm.runInContext('agentsPage()', agentCtx);
  assert.equal(agents.statusLabel(), 'agents.unknown_status');
  const pending = agents.fetchStatus();
  assert.equal(agents.statusLabel(), 'agents.loading_status');
  resolveFetch({ok:true,json:async()=>({running:true,agent_state:'idle',tools:{count:174}})});
  await pending;
  assert.equal(agents.statusLabel(), 'agents.ready');
  const timestamp = agents.statusUpdatedAt;
  fail = true;
  await agents.fetchStatus();
  assert.equal(agents.statusLabel(), 'agents.stale_status');
  assert.equal(agents.agent.tools.count, 174, 'last good data is retained');
  assert.equal(agents.statusUpdatedAt, timestamp, 'failure is not marked as a successful refresh');

  const catalog = [{name:'x',description:'X Studio',description_ar:'استوديو X',page:'/x',arguments:''}];
  const slashWindow = {KAZMA_LANG:'ar',document:{getElementById:()=>({textContent:JSON.stringify(catalog)})}};
  vm.runInNewContext(fs.readFileSync(path.join(ui,'chat_slash.js'),'utf8'), {window:slashWindow});
  assert.ok(slashWindow.KAZMA_SLASH_COMMANDS.find(c=>c.cmd==='/x').desc.startsWith('استوديو X'));
  console.log('UI draft protection, command discovery and status freshness passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
