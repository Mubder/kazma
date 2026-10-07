"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");
const source = fs.readFileSync(path.join(__dirname, "../../kazma-ui/kazma_ui/static/js/swarm.js"), "utf8");
const start = source.indexOf("  function addEventLine(");
const end = source.indexOf("\n  //", start);
function node(tag) {
  return {
    tag, children: [], attrs: {}, style: {}, textContent: "",
    appendChild(child) { this.children.push(child); },
    setAttribute(k, v) { this.attrs[k] = v; },
    set innerHTML(v) { throw new Error("event lines must not accept HTML: " + v); },
  };
}
const events = node("div");
const document = {createElement: node, createTextNode: text => ({tag: "#text", textContent: text})};
const context = {document, $: id => id === "events-proof" ? events : null};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);
const payload = '<img src=x onerror="attack()">&العربية';
context.addEventLine("proof", "✓", payload);
assert.strictEqual(events.children[0].children[1].children[0].textContent, payload);
context.addEventLine("proof", "", ": done", payload);
const worker = events.children[1].children[1].children[0];
assert.strictEqual(worker.textContent, payload);
assert.strictEqual(worker.attrs.translate, "no");
assert.strictEqual(events.children[1].children[1].children[1].textContent, ": done");
assert(!source.includes("{worker: esc(data.worker)"), "text caller must not pre-escape worker names");
console.log("swarm event text and untranslated worker labels retain literal content");
