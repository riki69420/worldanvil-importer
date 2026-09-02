// Elaris Paste Assistant — content script.
//
// Adds a panel to World Anvil pages listing the converted articles. "Fill"
// writes one article's title, content, sidebar and excerpt into the editor
// that is open on the page; the user reads it over and presses World Anvil's
// own Save. Nothing is submitted by this script.
//
// World Anvil's editor markup is not something this script can know ahead of
// time, so fields are found two ways: heuristics that match the obvious
// name/id/placeholder patterns, and a "pick" mode where the user clicks the
// field once and the selector is remembered.

(() => {
  if (window.top !== window) return; // editors live in the top frame
  if (document.getElementById("elaris-host")) return;

  const FIELDS = [
    { key: "title", label: "Title", source: (a) => a.title },
    { key: "content", label: "Content", source: (a) => a.content },
    { key: "sidebar", label: "Sidebar", source: (a) => a.sidebarcontent },
    { key: "excerpt", label: "Excerpt", source: (a) => a.excerpt },
  ];

  // Ordered guesses per field; the first match that is visible wins.
  const GUESSES = {
    title: [
      'input[name$="[title]"]', 'input[name="title"]', 'input[name*="title" i]',
      "#title", 'input[id*="title" i]', 'input[placeholder*="title" i]',
    ],
    content: [
      'textarea[name$="[content]"]', 'textarea[name="content"]',
      'textarea[name*="content" i]', "#content", 'textarea[id*="content" i]',
      '[contenteditable="true"][id*="content" i]',
    ],
    sidebar: [
      'textarea[name$="[sidebarcontent]"]', 'textarea[name*="sidebarcontent" i]',
      'textarea[name*="sidebar" i]', "#sidebarcontent", 'textarea[id*="sidebar" i]',
    ],
    excerpt: [
      'textarea[name$="[excerpt]"]', 'textarea[name*="excerpt" i]',
      'input[name*="excerpt" i]', "#excerpt", '[id*="excerpt" i]',
    ],
  };

  const TEMPLATE_NAMES = {
    article: "Generic article", settlement: "Settlement", location: "Geographic Location",
    landmark: "Building / Landmark", person: "Character", species: "Species",
    organization: "Organization", language: "Language", item: "Item",
    material: "Material", profession: "Profession", spell: "Spell",
    ethnicity: "Ethnicity", myth: "Myth / Legend", condition: "Condition",
    rank: "Rank / Title", formation: "Military Formation", vehicle: "Vehicle",
    technology: "Technology", document: "Document", prose: "Prose", law: "Law",
    ritual: "Tradition / Ritual", report: "Report", plot: "Plot",
    militaryConflict: "Military Conflict",
  };

  const state = {
    articles: [], done: {}, selectors: {},
    filter: "", template: "", picking: null, collapsed: false,
  };

  // -- storage --------------------------------------------------------------
  async function load() {
    const data = await chrome.storage.local.get(["articles", "done", "selectors", "collapsed"]);
    state.articles = data.articles ?? [];
    state.done = data.done ?? {};
    state.selectors = data.selectors ?? {};
    state.collapsed = data.collapsed ?? false;
  }

  chrome.storage.onChanged.addListener((changes) => {
    if (changes.articles) state.articles = changes.articles.newValue ?? [];
    if (changes.done) state.done = changes.done.newValue ?? {};
    if (changes.selectors) state.selectors = changes.selectors.newValue ?? {};
    render();
  });

  // -- field discovery ------------------------------------------------------
  function visible(el) {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden";
  }

  function findField(key) {
    const picked = state.selectors[key];
    if (picked) {
      try {
        const el = document.querySelector(picked);
        if (el) return { el, how: "picked" };
      } catch (_) { /* stale or invalid selector: fall through to guesses */ }
    }
    for (const sel of GUESSES[key]) {
      const el = [...document.querySelectorAll(sel)].find(visible);
      if (el) return { el, how: "guessed" };
    }
    if (key === "content") {
      // Last resort: the biggest textarea on the page is almost always the body.
      const biggest = [...document.querySelectorAll("textarea")]
        .filter(visible)
        .sort((a, b) => b.offsetHeight * b.offsetWidth - a.offsetHeight * a.offsetWidth)[0];
      if (biggest) return { el: biggest, how: "largest textarea" };
    }
    return null;
  }

  // Write a value the way a user typing would, so any framework listening to
  // the field notices. Covers plain inputs, contenteditable and CodeMirror.
  function setValue(el, value) {
    const cm = el.closest?.(".CodeMirror")?.CodeMirror;
    if (cm && typeof cm.setValue === "function") { cm.setValue(value); return true; }

    if (el.isContentEditable) {
      el.focus();
      el.textContent = value;
      el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }));
      return true;
    }

    if ("value" in el) {
      const proto = el instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
      el.focus();
      if (setter) setter.call(el, value); else el.value = value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    }
    return false;
  }

  // A selector that identifies this element again on a later page load.
  function selectorFor(el) {
    if (el.id && document.querySelectorAll(`#${CSS.escape(el.id)}`).length === 1) {
      return `#${CSS.escape(el.id)}`;
    }
    const tag = el.tagName.toLowerCase();
    if (el.name) {
      const s = `${tag}[name="${CSS.escape(el.name)}"]`;
      if (document.querySelectorAll(s).length === 1) return s;
    }
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 8) {
      let part = node.tagName.toLowerCase();
      if (node.id) { parts.unshift(`#${CSS.escape(node.id)}`); break; }
      const siblings = [...node.parentNode?.children ?? []].filter((c) => c.tagName === node.tagName);
      if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
      parts.unshift(part);
      node = node.parentNode;
    }
    return parts.join(" > ");
  }

  function startPicking(key) {
    state.picking = key;
    status(`Click the ${key} field on the page…`, "info");
    render();
  }

  document.addEventListener("click", async (event) => {
    if (!state.picking) return;
    if (host.contains(event.target)) return; // clicks inside our own panel
    event.preventDefault();
    event.stopPropagation();
    const el = event.target.closest("input, textarea, [contenteditable='true'], .CodeMirror") || event.target;
    const key = state.picking;
    state.picking = null;
    state.selectors = { ...state.selectors, [key]: selectorFor(el) };
    await chrome.storage.local.set({ selectors: state.selectors });
    status(`${key} field remembered.`, "ok");
    render();
  }, true);

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.picking) {
      state.picking = null;
      status("Pick cancelled.", "info");
      render();
    }
  }, true);

  // -- actions --------------------------------------------------------------
  async function fill(article) {
    const results = [];
    for (const field of FIELDS) {
      const value = field.source(article);
      if (!value) continue;
      const found = findField(field.key);
      if (!found) { results.push(`${field.label}: not found`); continue; }
      const ok = setValue(found.el, value);
      results.push(`${field.label}: ${ok ? "filled" : "could not write"} (${found.how})`);
      if (ok) flash(found.el);
    }
    const filledTitle = results.some((r) => r.startsWith("Title: filled"));
    const filledBody = results.some((r) => r.startsWith("Content: filled"));
    if (filledTitle && filledBody) {
      await markDone(article, true);
      status(`Filled "${article.title}". Check it, then press Save.`, "ok");
    } else {
      status(`Only partly filled — ${results.join("; ")}. Use "Pick fields" or the copy buttons.`, "warn");
    }
  }

  async function copyText(text, what) {
    try {
      await navigator.clipboard.writeText(text);
      status(`${what} copied — paste it with Ctrl+V.`, "ok");
    } catch (err) {
      status(`Clipboard blocked: ${err.message}`, "warn");
    }
  }

  async function markDone(article, value) {
    state.done = { ...state.done, [article._faUuid]: !!value };
    if (!value) delete state.done[article._faUuid];
    await chrome.storage.local.set({ done: state.done });
    render();
  }

  function flash(el) {
    const prev = el.style.outline;
    el.style.outline = "3px solid #3b82f6";
    setTimeout(() => { el.style.outline = prev; }, 900);
  }

  // -- panel ----------------------------------------------------------------
  const host = document.createElement("div");
  host.id = "elaris-host";
  const shadow = host.attachShadow({ mode: "open" });
  shadow.innerHTML = `
    <style>
      :host { all: initial; }
      * { box-sizing: border-box; }
      .panel {
        position: fixed; top: 80px; right: 12px; width: 340px; max-height: calc(100vh - 100px);
        display: flex; flex-direction: column; z-index: 2147483000;
        background: #fff; color: #1f2937; border: 1px solid #cbd5e1; border-radius: 10px;
        box-shadow: 0 8px 28px rgba(0,0,0,.18); font: 13px/1.4 system-ui, sans-serif;
      }
      .panel.collapsed .body, .panel.collapsed .foot { display: none; }
      .head { display: flex; align-items: center; gap: 8px; padding: 9px 10px; border-bottom: 1px solid #e5e7eb; cursor: move; }
      .head b { flex: 1; font-size: 13px; }
      .head .count { color: #6b7280; font-variant-numeric: tabular-nums; }
      .head button { border: 0; background: none; font-size: 15px; cursor: pointer; color: #6b7280; padding: 0 4px; }
      .body { display: flex; flex-direction: column; min-height: 0; }
      .tools { display: flex; gap: 6px; padding: 8px 10px; }
      .tools input, .tools select { flex: 1; min-width: 0; padding: 5px 7px; border: 1px solid #d1d5db; border-radius: 6px; font: inherit; }
      .list { overflow: auto; padding: 0 6px 6px; }
      .row { display: grid; grid-template-columns: 18px 1fr auto; gap: 6px; align-items: center; padding: 5px 4px; border-radius: 6px; }
      .row:hover { background: #f3f4f6; }
      .row.done { opacity: .55; }
      .row .t { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .row .badge { display: inline-block; font-size: 10px; padding: 1px 5px; border-radius: 4px; background: #e0e7ff; color: #3730a3; margin-left: 4px; vertical-align: 1px; }
      .row .acts { display: flex; gap: 3px; }
      .row .acts button { padding: 3px 7px; border: 1px solid #d1d5db; border-radius: 5px; background: #fff; font: inherit; font-size: 12px; cursor: pointer; }
      .row .acts button.fill { background: #2563eb; color: #fff; border-color: #2563eb; }
      .row .acts button:hover { filter: brightness(.95); }
      .empty { padding: 18px 12px; color: #6b7280; text-align: center; }
      .foot { border-top: 1px solid #e5e7eb; padding: 7px 10px; }
      .status { min-height: 1.3em; font-size: 12px; color: #374151; margin-bottom: 5px; }
      .status.ok { color: #166534; } .status.warn { color: #92400e; } .status.info { color: #1d4ed8; }
      .pick { display: flex; flex-wrap: wrap; gap: 4px; }
      .pick span { color: #6b7280; font-size: 11px; width: 100%; }
      .pick button { font: inherit; font-size: 11px; padding: 2px 7px; border: 1px solid #d1d5db; border-radius: 5px; background: #fff; cursor: pointer; }
      .pick button.set { border-color: #16a34a; color: #166534; }
      .pick button.active { background: #dbeafe; border-color: #2563eb; }
      .details { padding: 0 10px 8px; font-size: 12px; color: #4b5563; }
      .details code { background: #f3f4f6; padding: 1px 4px; border-radius: 3px; }
    </style>
    <div class="panel">
      <div class="head">
        <b>Elaris paste assistant</b>
        <span class="count"></span>
        <button class="toggle" title="Collapse">–</button>
      </div>
      <div class="body">
        <div class="tools">
          <input class="search" placeholder="Search titles…">
          <select class="tpl"><option value="">All types</option></select>
        </div>
        <div class="details"></div>
        <div class="list"></div>
      </div>
      <div class="foot">
        <div class="status"></div>
        <div class="pick"></div>
      </div>
    </div>`;

  const q = (s) => shadow.querySelector(s);
  const panel = q(".panel");

  function status(text, kind = "") {
    const el = q(".status");
    el.textContent = text;
    el.className = `status ${kind}`;
  }

  function render() {
    panel.classList.toggle("collapsed", state.collapsed);
    const doneCount = state.articles.filter((a) => state.done[a._faUuid]).length;
    q(".count").textContent = state.articles.length
      ? `${doneCount} / ${state.articles.length}` : "";

    const tpl = q(".tpl");
    const templates = [...new Set(state.articles.map((a) => a.templateType))].sort();
    const current = tpl.value;
    tpl.innerHTML = `<option value="">All types</option>` + templates
      .map((t) => `<option value="${t}">${TEMPLATE_NAMES[t] ?? t}</option>`).join("");
    tpl.value = templates.includes(current) ? current : "";

    const list = q(".list");
    list.innerHTML = "";
    if (!state.articles.length) {
      list.innerHTML = `<div class="empty">No articles loaded.<br>Click the extension icon and load <code>articles.json</code>.</div>`;
    }
    const needle = state.filter.trim().toLowerCase();
    const rows = state.articles
      .map((a, i) => ({ a, i }))
      .filter(({ a }) => !state.template || a.templateType === state.template)
      .filter(({ a }) => !needle || a.title.toLowerCase().includes(needle))
      .sort((x, y) => (state.done[x.a._faUuid] ? 1 : 0) - (state.done[y.a._faUuid] ? 1 : 0) || x.i - y.i);

    for (const { a } of rows) {
      const row = document.createElement("div");
      row.className = "row" + (state.done[a._faUuid] ? " done" : "");
      row.innerHTML = `
        <input type="checkbox" ${state.done[a._faUuid] ? "checked" : ""} title="Done">
        <div class="t" title="${escapeAttr(a.title)}">${escapeHtml(a.title)}<span class="badge">${escapeHtml(TEMPLATE_NAMES[a.templateType] ?? a.templateType)}</span></div>
        <div class="acts">
          <button class="fill" title="Fill the editor on this page">Fill</button>
          <button class="copy" title="Copy the content to the clipboard">Copy</button>
        </div>`;
      row.querySelector("input").addEventListener("change", (e) => markDone(a, e.target.checked));
      row.querySelector(".fill").addEventListener("click", () => fill(a));
      row.querySelector(".copy").addEventListener("click", () => copyText(a.content, "Content"));
      row.querySelector(".t").addEventListener("click", () => showDetails(a));
      list.appendChild(row);
    }

    const pick = q(".pick");
    pick.innerHTML = `<span>Fill not finding the boxes? Pick them once:</span>` + FIELDS
      .map((f) => `<button data-key="${f.key}" class="${state.selectors[f.key] ? "set" : ""}${state.picking === f.key ? " active" : ""}">${state.selectors[f.key] ? "✓ " : ""}${f.label}</button>`)
      .join("");
    pick.querySelectorAll("button").forEach((b) =>
      b.addEventListener("click", () => startPicking(b.dataset.key)));
  }

  function showDetails(a) {
    const fields = Object.entries(a.templateFields ?? {});
    q(".details").innerHTML = `
      <div><b>${escapeHtml(a.title)}</b> → create as <code>${escapeHtml(TEMPLATE_NAMES[a.templateType] ?? a.templateType)}</code></div>
      ${a.tags ? `<div>Tags: <code>${escapeHtml(a.tags)}</code></div>` : ""}
      ${fields.length ? `<div>Template fields (copy into the matching boxes):</div>` : ""}
      ${fields.map(([k, v]) => `<div>· <code>${escapeHtml(k)}</code> ${escapeHtml(String(v).slice(0, 80))}${String(v).length > 80 ? "…" : ""} <button data-copy="${escapeAttr(String(v))}">copy</button></div>`).join("")}`;
    q(".details").querySelectorAll("button[data-copy]").forEach((b) =>
      b.addEventListener("click", () => copyText(b.dataset.copy, "Value")));
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  const escapeAttr = escapeHtml;

  q(".search").addEventListener("input", (e) => { state.filter = e.target.value; render(); });
  q(".tpl").addEventListener("change", (e) => { state.template = e.target.value; render(); });
  q(".toggle").addEventListener("click", async () => {
    state.collapsed = !state.collapsed;
    q(".toggle").textContent = state.collapsed ? "+" : "–";
    await chrome.storage.local.set({ collapsed: state.collapsed });
    render();
  });

  // Drag the panel by its header; position is per page load, not persisted.
  (() => {
    const head = q(".head");
    let drag = null;
    head.addEventListener("mousedown", (e) => {
      if (e.target.tagName === "BUTTON") return;
      const r = panel.getBoundingClientRect();
      drag = { dx: e.clientX - r.left, dy: e.clientY - r.top };
      e.preventDefault();
    });
    window.addEventListener("mousemove", (e) => {
      if (!drag) return;
      panel.style.left = `${Math.max(0, e.clientX - drag.dx)}px`;
      panel.style.top = `${Math.max(0, e.clientY - drag.dy)}px`;
      panel.style.right = "auto";
    });
    window.addEventListener("mouseup", () => { drag = null; });
  })();

  document.documentElement.appendChild(host);
  load().then(render);
})();
