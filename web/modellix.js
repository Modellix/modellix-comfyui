import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { createForm } from "./schema-form.js";

const liveNodes = new Set();
let csrf = "";

export function element(tag, text, parent) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (parent) parent.append(node);
  return node;
}

export function button(parent, label, action) {
  const node = element("button", label, parent);
  node.type = "button";
  node.addEventListener("click", action);
  return node;
}

export async function request(path, options) {
  const response = await api.fetchApi("/modellix/" + path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "Modellix request failed.");
  return body;
}

export function widget(node, name) {
  return node.widgets?.find((item) => item.name === name);
}

function credentialControl() {
  const root = element("div");
  root.className = "modellix-controls modellix-key";
  element("p", "Configure a local Modellix key. It is never stored in ordinary ComfyUI settings.", root);
  const input = element("input", undefined, root);
  input.type = "password";
  input.autocomplete = "off";
  input.placeholder = "Modellix API key";
  input.setAttribute("aria-label", "Modellix API key");
  const actions = element("div", undefined, root);
  actions.className = "modellix-actions";
  const status = element("p", "Loading credential status…", root);
  status.setAttribute("role", "status");
  const refresh = async () => {
    const data = await request("session");
    csrf = data.csrf;
    status.textContent = data.configured ? `Configured · ${data.source}` : `No key configured · ${data.source}`;
  };
  const apply = async (action) => {
    const key = input.value;
    input.value = "";
    status.textContent = "Applying…";
    try {
      if (!csrf) await refresh();
      await request("credentials", { method: "POST", headers: {
        "Content-Type": "application/json", "X-Modellix-CSRF": csrf,
      }, body: JSON.stringify({ action, ...(action === "save" || action === "session" ? { key } : {}) }) });
      await refresh();
      window.dispatchEvent(new Event("modellix.credentials"));
    } catch (error) { status.textContent = error.message; }
  };
  button(actions, "Session Only", () => apply("session"));
  button(actions, "Secure Save", () => apply("save"));
  button(actions, "Clear", () => apply("clear"));
  button(actions, "Use Environment", () => apply("environment"));
  button(actions, "Clean Expired Cache", async () => {
    try {
      if (!csrf) await refresh();
      status.textContent = (await request("cache/clean", { method: "POST", headers: {
        "Content-Type": "application/json", "X-Modellix-CSRF": csrf,
      }, body: "{}" })).status;
    } catch (error) { status.textContent = error.message; }
  });
  refresh().catch((error) => { status.textContent = error.message; });
  return root;
}

function statusWidget(node) {
  const root = element("div");
  root.className = "modellix-controls modellix-task";
  const status = element("p", "Ready · Queue executes this node. Mute it to exclude it.", root);
  status.setAttribute("role", "status");
  const actions = element("div", undefined, root);
  actions.className = "modellix-actions";
  button(actions, "Copy Task ID", () => {
    const tid = node.properties?.modellixLastTask;
    if (tid) navigator.clipboard.writeText(tid);
  });
  button(actions, "Recover in Get Task", () => {
    const tid = node.properties?.modellixLastTask;
    if (!tid) { status.textContent = "No confirmed task ID. Check Console history if submission is unknown."; return; }
    const task = globalThis.LiteGraph.createNode("ModellixGetTask");
    node.graph.add(task);
    task.pos = [node.pos[0] + node.size[0] + 50, node.pos[1]];
    widget(task, "task_id").value = tid;
  });
  if (node.comfyClass === "ModellixGenerate") {
    button(actions, "New Attempt", () => {
      widget(node, "run_nonce").value = crypto.randomUUID();
      status.textContent = "New attempt selected. Next Queue may submit one paid request.";
      node.setDirtyCanvas(true, true);
    });
  }
  if (node.comfyClass === "ModellixUploadMedia") {
    button(actions, "New Upload Attempt", () => {
      widget(node, "run_nonce").value = crypto.randomUUID();
      status.textContent = "New upload attempt selected. Check existing files/quota before Queue.";
    });
  }
  const files = element("div", undefined, root);
  node.addDOMWidget("modellix_status", "div", root, { serialize: false, hideOnZoom: false,
    getMinHeight: () => 110, getMaxHeight: () => 220 });
  node.modellixStatus = (data) => {
    const tid = data.task_id || node.properties?.modellixLastTask || "";
    if (data.task_id) node.properties.modellixLastTask = data.task_id;
    status.textContent = `${data.cached ? "Existing attempt · " : ""}${data.stage || data.status || "ready"}` +
      `${data.elapsed !== undefined ? ` · ${data.elapsed}s` : ""}${tid ? ` · ${tid}` : ""}`;
    files.replaceChildren();
    for (const file of data.manifest || []) {
      const link = element("a", `${file.type}: ${file.filename}`, files);
      link.href = api.apiURL(`/view?filename=${encodeURIComponent(file.filename)}&subfolder=modellix&type=output`);
      link.target = "_blank";
      link.rel = "noopener";
    }
  };
  const executed = node.onExecuted;
  node.onExecuted = function (message) {
    executed?.apply(this, arguments);
    if (message.modellix?.[0]) this.modellixStatus(message.modellix[0]);
  };
}

app.registerExtension({
  name: "Modellix.Media",
  settings: [{ id: "Modellix.APIKey", name: "API Key", category: ["Modellix", "Credentials", "API Key"],
    type: credentialControl, defaultValue: "", telemetry: { trackChanges: false } }],
  setup() {
    const style = element("link");
    style.rel = "stylesheet";
    style.href = new URL("./style.css", import.meta.url).href;
    document.head.append(style);
    window.addEventListener("modellix.open-settings", () => {
      app.extensionManager.command.execute("Comfy.ShowSettingsDialog");
    });
    api.addEventListener("modellix.status", ({ detail }) => {
      const node = app.graph.getNodeById(detail.node);
      node?.modellixStatus?.(detail);
    });
    api.addEventListener("execution_error", ({ detail }) => {
      app.graph.getNodeById(detail.node_id)?.modellixStatus?.({ status: detail.exception_message });
    });
  },
  nodeCreated(node) {
    if (!["ModellixGenerate", "ModellixGetTask", "ModellixUploadMedia"].includes(node.comfyClass)) return;
    liveNodes.add(node);
    if (node.comfyClass === "ModellixGenerate") {
      const identity = widget(node, "node_instance_id");
      if (identity && !identity.value) identity.value = crypto.randomUUID();
      const clone = node.clone;
      node.clone = function () {
        const copy = clone.apply(this, arguments);
        if (copy) widget(copy, "node_instance_id").value = crypto.randomUUID();
        return copy;
      };
    }
    statusWidget(node);
    if (node.comfyClass === "ModellixGenerate") createForm(node, { request, element, button, widget });
    node.setSize([Math.max(480, node.size[0]), Math.max(350, node.size[1])]);
    const removed = node.onRemoved;
    node.onRemoved = function () { liveNodes.delete(this); removed?.apply(this, arguments); };
  },
});
