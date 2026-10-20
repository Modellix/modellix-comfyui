import assert from "node:assert/strict";
import { mkdtemp, writeFile, mkdir, copyFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";

// Minimal DOM adapter: tests execute the production modules and user actions.
// Real ComfyUI/browser acceptance is separate from this fast regression suite.
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.attrs = {}; this.style = {}; this.dataset = {}; this.value = ""; this.hidden = false; this.events = {}; }
  append(child) { child.parent = this; this.children.push(child); }
  replaceChildren(...children) { this.children = []; this._text = ""; children.forEach(child => this.append(child)); }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return (this._text || "") + this.children.map(child => child.textContent).join(" "); }
  setAttribute(name, value) { this.attrs[name] = value; }
  removeAttribute(name) { delete this.attrs[name]; }
  addEventListener(name, action) { this.events[name] = action; }
  click() { return this.events.click?.(); }
  remove() { this.parent.children = this.parent.children.filter(child => child !== this); }
  querySelector() { return null; }
}
const walk = root => [root, ...root.children.flatMap(walk)];
const label = (root, name) => walk(root).find(el => el.attrs["aria-label"] === name);
const button = (root, name) => walk(root).find(el => el.tagName === "button" && el.textContent === name);
const flush = () => new Promise(resolve => setImmediate(resolve));
const fixture = {
  type: "object", properties: { prompt: { type: "string" }, empty_field: { type: "string" },
    zero_field: { type: "integer" } }, required: ["prompt"],
};
const catalog = [{ slug: "qa/value-fixture", type: "text-to-image", supported: true, description: "old-account-pricing" }];

function node(type = "ModellixGenerate") {
  const values = { model_slug: "qa/value-fixture", schema_hash: "schema-v1", mode: "Submit only",
    parameters_json: JSON.stringify({ version: 1, values: { prompt: "test", empty_field: "", zero_field: 0 }, branches: {} }),
    bindings_json: JSON.stringify({ version: 1, bindings: [{ pointer: "/empty_field", socket: "strings.slot0" }] }),
    node_instance_id: "fixture-node", run_nonce: "stable", resume_task_id: "", timeout: 2, task_id: "current-task" };
  return { comfyClass: type, properties: {}, size: [480, 350], widgets: Object.entries(values).map(([name, value]) => ({ name, value })),
    inputs: [], roots: [], setDirtyCanvas() {}, setSize(size) { this.size = size; },
    addDOMWidget(name, type, root) { this.roots.push(root); },
    addInput(name, type, extra) { const value = { name, type, ...extra }; this.inputs.push(value); return value; },
    removeInput(index) { this.inputs.splice(index, 1); }, isInputConnected() { return false; } };
}
const widget = (node, name) => node.widgets.find(item => item.name === name);

async function harness(t, fetch) {
  const directory = await mkdtemp(join(tmpdir(), "modellix-web-test-"));
  assert.ok(resolve(directory).startsWith(resolve(tmpdir()) + sep + "modellix-web-test-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
  const web = join(directory, "extensions/modellix");
  await mkdir(web, { recursive: true });
  await mkdir(join(directory, "scripts"));
  await writeFile(join(directory, "package.json"), '{"type":"module"}');
  for (const file of ["modellix.js", "schema-form.js"]) await copyFile(join(root, "web", file), join(web, file));
  if (process.env.MODELLIX_FORM_TEST_SOURCE) await copyFile(process.env.MODELLIX_FORM_TEST_SOURCE, join(web, "schema-form.js"));
  if (process.env.MODELLIX_MAIN_TEST_SOURCE) await copyFile(process.env.MODELLIX_MAIN_TEST_SOURCE, join(web, "modellix.js"));
  await writeFile(join(directory, "scripts/app.js"), "export const app = globalThis.__modellixHost.app;");
  await writeFile(join(directory, "scripts/api.js"), "export const api = globalThis.__modellixHost.api;");
  globalThis.window = new EventTarget();
  globalThis.document = { createElement: tag => new Element(tag), head: new Element("head") };
  const api = new EventTarget();
  api.fetchApi = async (path, options) => ({ ok: true, json: async () => fetch(path, options) });
  api.apiURL = path => path;
  const app = { graph: { getNodeById: () => app.current }, registerExtension(extension) { this.extension = extension; } };
  globalThis.__modellixHost = { app, api };
  await import(pathToFileURL(join(web, "modellix.js")));
  app.extension.setup();
  return { app, api, create(type) { const value = node(type); app.current = value; app.extension.nodeCreated(value); return value; } };
}

test("schema refresh requires explicit acceptance; Queue hash stays blocked across refresh and reopen", async t => {
  let hash = "schema-v1", schema = structuredClone(fixture), posts = 0;
  const h = await harness(t, async (path, options) => {
    if (options?.method === "POST") posts++;
    return path.includes("/models?") ? { models: catalog } : { hash, schema };
  });
  const n = h.create(); await flush();
  const form = n.roots[1];
  hash = "schema-v2"; schema = { ...fixture, properties: { prompt: { type: "string" }, zero_field: { type: "string" } } };
  await button(form, "Refresh Online").click(); await flush();
  assert.notEqual(widget(n, "schema_hash").value, hash);
  assert.match(form.textContent, /\/empty_field/); assert.match(form.textContent, /\/zero_field/);
  assert.equal(button(form, "Accept Reviewed Schema").parent.hidden, false);
  await button(form, "Refresh Online").click(); await flush();
  assert.notEqual(widget(n, "schema_hash").value, hash);
  n.onConfigure(); await flush();
  assert.notEqual(widget(n, "schema_hash").value, hash);
  button(form, "Accept Reviewed Schema").click();
  assert.equal(widget(n, "schema_hash").value, hash);
  assert.equal(widget(n, "run_nonce").value, "stable");
  assert.equal(posts, 0);
});

test("Clear Key removes all old account model options, schema fields and pricing", async t => {
  const h = await harness(t, async path => path.includes("/models?") ? { models: catalog } : { hash: "schema-v1", schema: fixture });
  const n = h.create(); await flush(); const form = n.roots[1];
  assert.equal(label(form, "Modellix model").children.length, 1);
  window.dispatchEvent(new CustomEvent("modellix.credentials", { detail: { configured: false } }));
  assert.equal(label(form, "Modellix model").children.length, 0);
  assert.equal(label(form, "Model category").children.length, 0);
  assert.ok(!form.textContent.includes("old-account-pricing"));
  assert.equal(label(form, "/prompt"), undefined);
});

test("refreshing an unchanged union type preserves compatible values and the accepted hash", async t => {
  const union = { ...fixture, properties: { ...fixture.properties, zero_field: { type: ["integer", "null"] } } };
  const h = await harness(t, async path => path.includes("/models?") ? { models: catalog } : { hash: "schema-v1", schema: structuredClone(union) });
  const n = h.create(); await flush(); const form = n.roots[1];
  await button(form, "Refresh Online").click(); await flush();
  assert.equal(widget(n, "schema_hash").value, "schema-v1");
  assert.equal(JSON.parse(widget(n, "parameters_json").value).values.zero_field, 0);
  assert.equal(button(form, "Accept Reviewed Schema").parent.hidden, true);
});

test("an old saved workflow loaded directly against a new schema stays blocked and identifies invalid fields", async t => {
  const changed = { ...fixture, properties: { prompt: { type: "string" }, zero_field: { type: "string" } } };
  const h = await harness(t, async path => path.includes("/models?") ? { models: catalog } : { hash: "schema-v2", schema: changed });
  const n = h.create(); await flush(); const form = n.roots[1];
  assert.notEqual(widget(n, "schema_hash").value, "schema-v2");
  assert.match(form.textContent, /\/zero_field/); assert.match(form.textContent, /\/empty_field/);
  assert.deepEqual(JSON.parse(widget(n, "parameters_json").value).values, { prompt: "test" });
});

test("late catalog responses cannot restore a cleared account", async t => {
  let finish;
  const h = await harness(t, path => path.includes("/models?") ? new Promise(resolve => { finish = resolve; }) : { hash: "schema-v1", schema: fixture });
  const n = h.create(); await flush(); const form = n.roots[1];
  window.dispatchEvent(new CustomEvent("modellix.credentials", { detail: { configured: false } }));
  finish({ models: catalog }); await flush();
  assert.equal(label(form, "Modellix model").children.length, 0);
});

test("Get Task error, copy and recovery actions refer to this execution", async t => {
  const h = await harness(t, () => ({}));
  const n = h.create("ModellixGetTask"); const root = n.roots[0];
  n.properties.modellixLastTask = "previous-task";
  h.api.dispatchEvent(new CustomEvent("executing", { detail: "node-1" }));
  assert.equal(n.properties.modellixLastTask, undefined);
  h.api.dispatchEvent(new CustomEvent("modellix.status", { detail: { node: "node-1", stage: "querying", task_id: "current-task" } }));
  h.api.dispatchEvent(new CustomEvent("execution_error", { detail: { node_id: "node-1", exception_message: "Local deadline expired" } }));
  assert.ok(root.textContent.includes("current-task") && !root.textContent.includes("previous-task"));
  let copied, recovered;
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { clipboard: { writeText: value => { copied = value; } } } });
  globalThis.LiteGraph = { createNode: () => node("ModellixGetTask") };
  n.graph = { add: value => { recovered = value; } }; n.pos = [0, 0];
  button(root, "Copy Task ID").click(); button(root, "Recover in Get Task").click();
  assert.equal(copied, "current-task"); assert.equal(widget(recovered, "task_id").value, "current-task");
});

test("partial Clear Key failure still broadcasts unconfigured credential state", async t => {
  const h = await harness(t, async (path, options) => {
    if (options?.method === "POST") throw Error("Secure storage could not be cleared");
    return { csrf: "fixture-csrf", configured: false, source: "cleared" };
  });
  let changed;
  window.addEventListener("modellix.credentials", event => { changed = event.detail; });
  const root = h.app.extension.settings[0].type(); await flush();
  await button(root, "Clear").click();
  assert.equal(changed.configured, false);
  assert.match(root.textContent, /Secure storage could not be cleared/);
});
