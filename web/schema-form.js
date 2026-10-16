/* Form projection only. The server validates the original schema before any paid POST. */
const GROUP = { IMAGE: "images", VIDEO: "videos", AUDIO: "audios", STRING: "strings",
  INT: "integers", FLOAT: "numbers", BOOLEAN: "booleans", MODELLIX_URL_LIST: "url_lists" };
const escape = (part) => String(part).replaceAll("~", "~0").replaceAll("/", "~1");
const parts = (path) => path.slice(1).split("/").map((p) => p.replaceAll("~1", "/").replaceAll("~0", "~"));
const clone = (value) => JSON.parse(JSON.stringify(value));

export function effective(schema, branches, path = "") {
  let result = { ...schema };
  const merge = (item) => {
    result = { ...result, ...item, properties: { ...result.properties, ...item.properties },
      required: [...new Set([...(result.required || []), ...(item.required || [])])] };
  };
  (schema.allOf || []).forEach((item, index) => merge(effective(item, branches, path + `/allOf/${index}`)));
  for (const kind of ["oneOf", "anyOf"]) {
    if (schema[kind]) merge(effective(schema[kind][branches[path + "/" + kind] || 0], branches, path));
  }
  return result;
}

export function mediaKind(name, schema) {
  const field = schema.type === "array" ? schema.items || {} : schema;
  if (field.type !== "string") return null;
  name = name.toLowerCase();
  if (["url", "prompt_audio"].includes(name) && /audio/i.test(field.description || "")) return "AUDIO";
  if (/image|frame|mask/.test(name)) return "IMAGE";
  if (/video/.test(name)) return "VIDEO";
  if (/audio/.test(name)) return "AUDIO";
  return null;
}

function get(object, path) {
  let value = object;
  for (const part of parts(path)) value = value?.[part];
  return value;
}

function put(object, path, value) {
  const keys = parts(path);
  let current = object;
  for (const key of keys.slice(0, -1)) current = current[key] ||= {};
  if (value === undefined) delete current[keys.at(-1)];
  else current[keys.at(-1)] = value;
}

function fieldAt(schema, branches, path) {
  let current = schema, prefix = "";
  for (const part of parts(path)) {
    current = effective(current, branches, prefix).properties?.[part];
    if (!current) return null;
    prefix += "/" + escape(part);
  }
  return effective(current, branches, path);
}

export function createForm(node, { request, element: el, button, widget }) {
  const root = el("div");
  root.className = "modellix-controls modellix-form";
  const title = el("strong", "Model and Parameters", root);
  title.tabIndex = 0;
  const status = el("p", "Loading catalog…", root);
  status.setAttribute("role", "status");
  const actions = el("div", undefined, root);
  actions.className = "modellix-actions";
  button(actions, "Refresh Online", () => loadCatalog(true));
  button(actions, "Open Cached", () => loadCatalog(false, true));
  const settings = button(actions, "API Key Settings", () => {
    window.dispatchEvent(new Event("modellix.open-settings"));
  });
  settings.title = "Open ComfyUI Settings and select Modellix";
  const category = el("select", undefined, root);
  category.setAttribute("aria-label", "Model category");
  const search = el("input", undefined, root);
  search.placeholder = "Search model, slug or provider";
  search.setAttribute("aria-label", "Search Modellix models");
  const models = el("select", undefined, root);
  models.size = 3;
  models.setAttribute("aria-label", "Modellix model");
  const details = el("details", undefined, root);
  el("summary", "Model Details and Pricing", details);
  const description = el("p", undefined, details);
  const fields = el("div", undefined, root);
  const advanced = el("details", undefined, root);
  el("summary", "Advanced JSON and Request Controls", advanced);
  const json = el("textarea", undefined, advanced);
  json.setAttribute("aria-label", "Model parameters JSON");
  button(advanced, "Apply JSON", () => {
    try {
      const value = JSON.parse(json.value);
      state = value.version === 1 ? value : { version: 1, values: value, branches: {} };
      if (!state.values || typeof state.values !== "object" || Array.isArray(state.values)) throw new Error("Expected a parameter object.");
      state.branches ||= {};
      save(); render();
    } catch (error) { status.textContent = error.message; }
  });
  const mode = el("select", undefined, advanced);
  mode.setAttribute("aria-label", "Execution mode");
  for (const name of ["Wait", "Submit only"]) el("option", name, mode).value = name;
  mode.onchange = () => { widget(node, "mode").value = mode.value; };
  const resume = el("input", undefined, advanced);
  resume.placeholder = "Resume existing task ID (no new generation)";
  resume.setAttribute("aria-label", "Resume task ID");
  resume.oninput = () => { widget(node, "resume_task_id").value = resume.value; };
  const timeout = el("input", undefined, advanced);
  timeout.type = "number"; timeout.min = "1"; timeout.max = "86400";
  timeout.setAttribute("aria-label", "Local timeout in seconds");
  timeout.onchange = () => { widget(node, "timeout").value = Number(timeout.value); };
  button(advanced, "Reset Model Parameters", () => { state.values = {}; state.branches = {}; bindings = [];
    syncSockets(); render(); save(); });

  let catalog = [], schema = null, state = { version: 1, values: {}, branches: {} }, bindings = [], version = 0;
  const hidden = new Set(["model_slug", "parameters_json", "schema_hash", "bindings_json", "node_instance_id",
    "run_nonce", "mode", "resume_task_id", "timeout"]);
  for (const item of node.widgets || []) {
    if (!hidden.has(item.name)) continue;
    item.computeSize = () => [0, -4];
    item.type = "converted-widget";
    if (item.element) item.element.style.display = "none";
  }

  const save = () => {
    widget(node, "parameters_json").value = JSON.stringify(state);
    widget(node, "bindings_json").value = JSON.stringify({ version: 1, bindings });
    json.value = JSON.stringify(state, null, 2);
    node.setDirtyCanvas(true, true);
  };
  const syncSockets = () => {
    for (let i = (node.inputs?.length || 0) - 1; i >= 0; i--) {
      const input = node.inputs[i];
      if (hidden.has(input.name) && !node.isInputConnected(i)) { node.removeInput(i); continue; }
      if (/^(images|videos|audios|strings|integers|numbers|booleans|url_lists)(\.|$)/.test(input.name)
          && !bindings.some((entry) => entry.socket === input.name)) node.removeInput(i);
    }
    for (const entry of bindings) {
      const kind = Object.keys(GROUP).find((key) => entry.socket.startsWith(GROUP[key] + "."));
      if (!kind) continue;
      let input = node.inputs?.find((item) => item.name === entry.socket);
      if (!input) input = node.addInput(entry.socket, kind, { label: entry.pointer });
      input.label = entry.pointer;
    }
    node.setDirtyCanvas(true, true);
  };
  const bind = (path, kind, replace = false) => {
    const previousValue = get(state.values, path);
    const hasValue = previousValue !== undefined && previousValue !== "" &&
      !(Array.isArray(previousValue) && previousValue.length === 0);
    if (kind && hasValue && !replace) {
      status.textContent = `Remove the entered value for ${path} before connecting it. One source per field.`;
      actions.querySelector("[data-replace-binding]")?.remove();
      const replaceButton = button(actions, `Replace ${path} with Connection`, () => {
        bind(path, kind, true); replaceButton.remove();
      });
      replaceButton.dataset.replaceBinding = path;
      render(); return;
    }
    if (kind) put(state.values, path, undefined);
    bindings = bindings.filter((entry) => entry.pointer !== path);
    if (kind) {
      const group = GROUP[kind];
      let index = 0;
      while (bindings.some((entry) => entry.socket === `${group}.slot${index}`)) index++;
      if (index >= 100) { status.textContent = "This socket group exceeds 100 inputs."; return; }
      bindings.push({ socket: `${group}.slot${index}`, pointer: path });
    }
    syncSockets(); save(); render();
  };

  function prune(oldSchema) {
    let removed = 0;
    const walk = (object, path = "") => {
      for (const key of Object.keys(object)) {
        const pointer = path + "/" + escape(key), current = fieldAt(schema, state.branches, pointer);
        const old = oldSchema && fieldAt(oldSchema, state.branches, pointer);
        if (!current || (old && old.type && current.type !== old.type)) {
          delete object[key]; removed++; continue;
        }
        if (current.type === "object" && object[key] && typeof object[key] === "object" && !Array.isArray(object[key])) walk(object[key], pointer);
      }
    };
    walk(state.values);
    bindings = bindings.filter((entry) => {
      const current = fieldAt(schema, state.branches, entry.pointer);
      const old = oldSchema && fieldAt(oldSchema, state.branches, entry.pointer);
      const okay = current && (!old || old.type === current.type);
      if (!okay) removed++;
      return okay;
    });
    syncSockets();
    if (removed) status.textContent = `Removed ${removed} incompatible fields/bindings. Review before Queue.`;
  }

  function branchSelectors(raw, parent, path) {
    (raw.allOf || []).forEach((item, index) => branchSelectors(item, parent, path + `/allOf/${index}`));
    for (const kind of ["oneOf", "anyOf"]) {
      if (!raw[kind]) continue;
      const label = el("label", `${path || "Model"} mode`, parent);
      const select = el("select", undefined, label);
      select.setAttribute("aria-label", `${path || "Model"} schema branch`);
      raw[kind].forEach((branch, i) => { el("option", branch.title || branch.description || `Option ${i + 1}`, select).value = i; });
      select.value = state.branches[path + "/" + kind] || 0;
      select.onchange = () => { state.branches[path + "/" + kind] = Number(select.value); prune(schema); render(); save(); };
    }
  }

  function renderSchema(raw, parent, path = "", required = true) {
    branchSelectors(raw, parent, path);
    const current = effective(raw, state.branches, path);
    if (current.type === "object" || current.properties) {
      const requiredNames = current.required || [];
      const ordered = Object.entries(current.properties || {}).sort(([a], [b]) => {
        const explicit = current["x-order"] || current["x-propertyOrder"];
        if (Array.isArray(explicit)) return explicit.indexOf(a) - explicit.indexOf(b);
        return Number(requiredNames.includes(b)) - Number(requiredNames.includes(a));
      });
      let optional = null;
      for (const [name, field] of ordered) {
        const needed = requiredNames.includes(name);
        if (!needed && !optional) { optional = el("details", undefined, parent); el("summary", "Optional and Advanced Fields", optional); }
        renderField(name, field, needed ? parent : optional, path + "/" + escape(name), needed);
      }
    } else if (path) renderField(parts(path).at(-1), current, parent, path, required);
  }

  function renderField(name, raw, parent, path, required) {
    const field = effective(raw, state.branches, path);
    const row = el("div", undefined, parent); row.className = "modellix-field";
    const head = el("div", undefined, row); head.className = "modellix-field-head";
    const binding = bindings.find((entry) => entry.pointer === path);
    if (required && get(state.values, path) === undefined && !binding && field.default !== undefined &&
        !state.disabled?.includes(path)) put(state.values, path, clone(field.default));
    const toggle = el("input", undefined, head); toggle.type = "checkbox";
    toggle.checked = required || !!binding || get(state.values, path) !== undefined;
    toggle.disabled = required;
    toggle.setAttribute("aria-label", `Enable ${path}`);
    el("strong", `${name}${required ? " *" : ""}`, head);
    const desc = el("small", field.description || `${field.type || "value"}${field.enum ? " · enum" : ""}`, row);
    if (field.default !== undefined) desc.textContent += ` · Default: ${JSON.stringify(field.default)}`;
    if (field.minItems !== undefined || field.maxItems !== undefined) desc.textContent += ` · Items: ${field.minItems ?? 0}–${field.maxItems ?? "unlimited"}`;
    const editor = el("div", undefined, row);
    const source = el("select", undefined, head);
    source.setAttribute("aria-label", `Source for ${path}`);
    el("option", "Entered value", source).value = "";
    const media = mediaKind(name, field);
    const item = field.type === "array" ? field.items || {} : field;
    const primitive = { string: "STRING", integer: "INT", number: "FLOAT", boolean: "BOOLEAN" }[item.type];
    for (const kind of [...new Set([media, primitive, field.type === "array" && item.type === "string" ? "MODELLIX_URL_LIST" : null].filter(Boolean))]) {
      el("option", `Connect ${kind}`, source).value = kind;
    }
    source.value = binding ? Object.keys(GROUP).find((kind) => binding.socket.startsWith(GROUP[kind] + ".")) : "";
    source.onchange = () => bind(path, source.value);
    toggle.onchange = () => {
      state.disabled = (state.disabled || []).filter((p) => p !== path);
      if (!toggle.checked) { state.disabled.push(path); put(state.values, path, undefined); bindings = bindings.filter((b) => b.pointer !== path); syncSockets(); }
      else if (!binding) put(state.values, path, field.default ?? field.enum?.[0] ?? (field.type === "array" ? [] : field.type === "object" ? {} : field.type === "boolean" ? false : field.type === "string" ? "" : 0));
      save(); render();
    };
    if (binding) { el("p", `Connect ${path} at the ${source.value} input socket. Entered-value editing is disabled.`, editor); return; }
    source.disabled = !toggle.checked;
    if (!toggle.checked) return;
    const change = (value) => { put(state.values, path, value); save(); };
    let value = get(state.values, path);
    if (value === undefined && required && field.type !== "object") {
      value = field.enum?.[0] ?? (field.type === "array" ? [] : field.type === "boolean" ? false : field.type === "string" ? "" : undefined);
      if (value !== undefined) change(value);
    }
    if (field.enum) {
      const select = el("select", undefined, editor); select.setAttribute("aria-label", path);
      field.enum.forEach((option, i) => { el("option", JSON.stringify(option), select).value = i; });
      select.value = Math.max(0, field.enum.findIndex((option) => JSON.stringify(option) === JSON.stringify(value)));
      select.onchange = () => change(clone(field.enum[Number(select.value)]));
    } else if (field.type === "object" && field.properties) {
      renderSchema(raw, editor, path, required);
    } else if (field.type === "array" && ["string", "integer", "number", "boolean"].includes(field.items?.type)) {
      const values = Array.isArray(value) ? value : [];
      values.forEach((entry, index) => {
        const line = el("div", undefined, editor); line.className = "modellix-array-row";
        const input = el("input", undefined, line);
        input.setAttribute("aria-label", `${path} item ${index + 1}`);
        input.type = field.items.type === "string" ? "text" : field.items.type === "boolean" ? "checkbox" : "number";
        if (input.type === "checkbox") input.checked = entry; else input.value = entry;
        input.oninput = () => { values[index] = input.type === "checkbox" ? input.checked : input.type === "number" ? Number(input.value) : input.value; change(values); };
        button(line, "↑", () => { if (index) [values[index - 1], values[index]] = [values[index], values[index - 1]]; change(values); render(); }).setAttribute("aria-label", `Move item ${index + 1} up`);
        button(line, "↓", () => { if (index + 1 < values.length) [values[index], values[index + 1]] = [values[index + 1], values[index]]; change(values); render(); }).setAttribute("aria-label", `Move item ${index + 1} down`);
        button(line, "Remove", () => { values.splice(index, 1); change(values); render(); });
      });
      const add = button(editor, "Add Item", () => { values.push(field.items.type === "string" ? "" : field.items.type === "boolean" ? false : 0); change(values); render(); });
      add.disabled = field.maxItems !== undefined && values.length >= field.maxItems;
    } else if (["string", "integer", "number", "boolean"].includes(field.type)) {
      const input = el(field.type === "string" && /prompt|text|description/.test(name) ? "textarea" : "input", undefined, editor);
      input.setAttribute("aria-label", path);
      if (field.type === "boolean") { input.type = "checkbox"; input.checked = !!value; }
      else if (field.type === "number" || field.type === "integer") { input.type = "number"; input.step = field.type === "integer" ? "1" : "any"; }
      if (field.type !== "boolean") input.value = value ?? "";
      if (field.minimum !== undefined) input.min = field.minimum;
      if (field.maximum !== undefined) input.max = field.maximum;
      if (field.maxLength !== undefined) input.maxLength = field.maxLength;
      if (field.minLength !== undefined) input.minLength = field.minLength;
      input.oninput = () => change(field.type === "boolean" ? input.checked : ["number", "integer"].includes(field.type) ? (input.value === "" ? undefined : Number(input.value)) : input.value);
    } else {
      const input = el("textarea", undefined, editor); input.value = JSON.stringify(value ?? (field.type === "array" ? [] : {}), null, 2);
      input.setAttribute("aria-label", `${path} structured JSON`);
      input.onchange = () => { try { change(JSON.parse(input.value)); status.textContent = "JSON field applied."; } catch { status.textContent = `Invalid JSON in ${path}.`; } };
    }
  }

  function render() {
    fields.replaceChildren();
    if (schema) renderSchema(schema, fields);
    mode.value = widget(node, "mode").value;
    resume.value = widget(node, "resume_task_id").value;
    timeout.value = widget(node, "timeout").value;
    save();
  }

  async function choose(slug, refresh = false, offline = false) {
    if (!slug) return;
    const currentVersion = ++version;
    status.textContent = "Loading selected model schema…";
    try {
      const result = await request(`schema?slug=${encodeURIComponent(slug)}&refresh=${refresh}&offline=${offline}`);
      if (currentVersion !== version) return;
      const old = schema;
      const switching = widget(node, "model_slug").value !== slug;
      if (switching && old) {
        for (const [key, index] of Object.entries(state.branches)) {
          const kind = key.split("/").at(-1), path = key.slice(0, key.lastIndexOf("/"));
          const before = path ? fieldAt(old, state.branches, path)?.[kind] : old[kind];
          const after = path ? fieldAt(result.schema, state.branches, path)?.[kind] : result.schema[kind];
          if (!before || !after || JSON.stringify(before[index]) !== JSON.stringify(after[index])) delete state.branches[key];
        }
      }
      schema = result.schema;
      widget(node, "model_slug").value = slug;
      // Preserve a saved schema hash until explicit refresh/model change; server blocks drift.
      if (refresh || switching || !widget(node, "schema_hash").value) widget(node, "schema_hash").value = result.hash;
      const drift = widget(node, "schema_hash").value !== result.hash;
      const model = catalog.find((item) => item.slug === slug);
      description.textContent = `${model?.type || ""} · ${slug.split("/")[0]} · ${model?.description || ""}` +
        (model?.price ? ` · Pricing: ${typeof model.price === "object" ? JSON.stringify(model.price) : model.price}` : "");
      status.textContent = drift ? "Saved schema changed. Refresh Online and review fields before Queue." : offline ? "Cached schema · Offline editing only. Submission revalidates online." : "Ready · Review fields and Queue.";
      if (!drift) prune(old);
      render();
    } catch (error) { if (currentVersion === version) { status.textContent = error.message; fields.replaceChildren(); } }
  }

  function filterModels() {
    const previous = widget(node, "model_slug").value;
    const filtered = catalog.filter((model) => (!category.value || model.type === category.value) &&
      `${model.name || ""} ${model.slug}`.toLowerCase().includes(search.value.toLowerCase()));
    models.replaceChildren();
    for (const model of filtered) {
      const option = el("option", `${model.name || model.slug} · ${model.slug}${model.supported ? "" : " · " + model.reason}`, models);
      option.value = model.slug; option.disabled = !model.supported;
    }
    if (filtered.some((model) => model.slug === previous)) models.value = previous;
    else models.selectedIndex = -1;
    if (!filtered.length) status.textContent = "No models match this category/search.";
  }
  async function loadCatalog(refresh = false, offline = false) {
    status.textContent = "Loading catalog…";
    try {
      catalog = (await request(`models?refresh=${refresh}&offline=${offline}`)).models;
      const previous = category.value;
      category.replaceChildren(); el("option", "All media categories", category).value = "";
      for (const type of [...new Set(catalog.map((model) => model.type))]) el("option", type, category).value = type;
      category.value = previous;
      filterModels();
      status.textContent = `${catalog.length} models · Select a model. Text-output models are listed with a scope explanation.`;
      if (widget(node, "model_slug").value) await choose(widget(node, "model_slug").value, refresh, offline);
    } catch (error) { status.textContent = error.message + " Open Settings → Modellix to configure your key, then retry."; }
  }
  search.oninput = filterModels; category.onchange = filterModels;
  models.onchange = () => choose(models.value);
  const restore = () => {
    try {
      const value = JSON.parse(widget(node, "parameters_json").value);
      state = value.version === 1 ? value : { version: 1, values: value, branches: {} };
      state.branches ||= {};
      bindings = JSON.parse(widget(node, "bindings_json").value).bindings || [];
      syncSockets();
      loadCatalog();
    } catch { status.textContent = "Workflow parameter format is invalid. Review Advanced JSON."; }
  };
  const configured = node.onConfigure;
  node.onConfigure = function () { configured?.apply(this, arguments); queueMicrotask(restore); };
  const credentials = () => loadCatalog(true);
  window.addEventListener("modellix.credentials", credentials);
  const removed = node.onRemoved;
  node.onRemoved = function () { window.removeEventListener("modellix.credentials", credentials); removed?.apply(this, arguments); };
  node.addDOMWidget("modellix_form", "div", root, { serialize: false, hideOnZoom: false,
    getMinHeight: () => 420, getMaxHeight: () => 650 });
  queueMicrotask(restore);
  return root;
}
