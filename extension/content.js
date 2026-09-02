// Elaris Paste Assistant — content script.
//
// Adds a panel to World Anvil pages listing the converted articles. "Fill"
// writes one article's title, content and sidebar into the editor that is
// open on the page. World Anvil's visual editor ("Plutarch", the block editor
// on /p/athena/... pages) saves automatically a moment after each change; the
// legacy editor still needs its own Save button.
//
// How the visual editor is filled (verified against the live site, Sep 2026):
//   * Title: clicking the header reveals <input placeholder="Title">; setting
//     its value through React's native setter and blurring commits it.
//   * Content / sidebar: BlockNote (ProseMirror) editors. They ignore
//     element.textContent and strip square brackets from plain-text pastes, so
//     BBCode is converted to HTML and delivered as a synthetic paste event.
//     HTML heading N becomes BBCode [h(N-1)]; a <span
//     data-inline-content-type="mention"> becomes @[Title](Article:id).
//   * Mentions need the target article's id. Titles are looked up through the
//     same world search the editor's own "@" menu uses; links to articles that
//     do not exist yet are left as plain text and remembered so "Fill" can be
//     pressed again once they do.
//   * The excerpt has no box in the visual editor; it stays on a Copy button.
// The legacy form editor (plain inputs/textareas) is still supported through
// the old heuristics and the "pick a field" mode.

(() => {
  if (window.top !== window) return; // editors live in the top frame
  if (document.getElementById("elaris-host")) return;

  const FIELDS = [
    { key: "title", label: "Title", source: (a) => a.title },
    { key: "content", label: "Content", source: (a) => a.content },
    { key: "sidebar", label: "Sidebar", source: (a) => a.sidebarcontent },
  ];

  // Ordered guesses per field for the legacy editor; first visible match wins.
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
  };

  // Names as they appear on World Anvil's "Create" template picker.
  const TEMPLATE_NAMES = {
    article: "Generic", settlement: "Settlement", location: "Geography",
    landmark: "Building", person: "Character", species: "Species",
    organization: "Organization", language: "Language", item: "Item",
    material: "Material", profession: "Profession", spell: "Spell",
    ethnicity: "Culture / Ethnicity", myth: "Myth", condition: "Condition",
    rank: "Title", formation: "Military Formation", vehicle: "Vehicle",
    technology: "Technology", document: "Document", prose: "Prose", law: "Natural Law",
    ritual: "Tradition", report: "Report", plot: "Plot",
    militaryConflict: "Conflict", country: "Country", religion: "Religion",
    deity: "God/Deity", military: "Military",
  };

  const state = {
    articles: [], done: {}, selectors: {}, links: {}, pending: {}, created: {},
    filter: "", template: "", picking: null, collapsed: false,
    worldId: null,
  };

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // -- storage --------------------------------------------------------------
  async function load() {
    const data = await chrome.storage.local.get(["articles", "done", "selectors", "collapsed", "links", "pending", "created", "worldId"]);
    state.articles = data.articles ?? [];
    state.done = data.done ?? {};
    state.selectors = data.selectors ?? {};
    state.collapsed = data.collapsed ?? false;
    state.links = data.links ?? {};
    state.pending = data.pending ?? {};
    state.created = data.created ?? {};
    state.worldId = data.worldId ?? null;
  }

  chrome.storage.onChanged.addListener((changes) => {
    if (changes.articles) state.articles = changes.articles.newValue ?? [];
    if (changes.done) state.done = changes.done.newValue ?? {};
    if (changes.selectors) state.selectors = changes.selectors.newValue ?? {};
    if (changes.links) state.links = changes.links.newValue ?? {};
    if (changes.pending) state.pending = changes.pending.newValue ?? {};
    if (changes.created) state.created = changes.created.newValue ?? {};
    render();
  });

  // -- field discovery ------------------------------------------------------
  function visible(el) {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden";
  }

  // The visual editor's block editors, if this page has any.
  function blockEditors() {
    return [...document.querySelectorAll(".ProseMirror.bn-editor")].filter(visible);
  }

  function isVisualEditor() {
    return blockEditors().length > 0 || !!document.querySelector(".entity-header-title");
  }

  // Body = the widest block editor, top-most if several share the width
  // (template sections such as "Geography" are separate editors below it).
  // Sidebar = the narrower editor to the right of the body.
  function visualField(key) {
    if (key === "title") {
      return document.querySelector('input[placeholder="Title"]')
        || document.querySelector(".entity-header-title");
    }
    const eds = blockEditors().map((el) => ({ el, r: el.getBoundingClientRect() }));
    if (!eds.length) return null;
    const maxW = Math.max(...eds.map((e) => e.r.width));
    const wide = eds.filter((e) => e.r.width >= maxW - 1).sort((a, b) => a.r.top - b.r.top);
    const body = wide[0];
    if (key === "content") return body.el;
    if (key === "sidebar") {
      const side = eds.filter((e) => e.r.left >= body.r.right - 1).sort((a, b) => a.r.top - b.r.top)[0];
      return side ? side.el : null;
    }
    return null;
  }

  function findField(key) {
    const picked = state.selectors[key];
    if (picked) {
      try {
        const el = document.querySelector(picked);
        if (el) return { el, how: "picked" };
      } catch (_) { /* stale or invalid selector: fall through to guesses */ }
    }
    if (isVisualEditor()) {
      const el = visualField(key);
      if (el) return { el, how: "visual editor" };
    }
    for (const sel of GUESSES[key] ?? []) {
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

  // -- writing values -------------------------------------------------------
  function setNative(el, value) {
    const proto = el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    el.focus();
    if (setter) setter.call(el, value); else el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // The header shows the title as text; clicking that text (the innermost
  // element, not the wrapper) swaps in the input.
  async function setVisualTitle(el, value) {
    let input = el.tagName === "INPUT" ? el : document.querySelector('input[placeholder="Title"]');
    if (!input) {
      let leaf = el;
      while (leaf && leaf.children.length) {
        leaf = [...leaf.children].find((c) => c.textContent.trim()) || leaf.children[0];
      }
      for (const target of [leaf, el]) {
        target.click();
        for (let i = 0; i < 6 && !input; i++) {
          await sleep(100);
          input = document.querySelector('input[placeholder="Title"]');
        }
        if (input) break;
      }
    }
    if (!input) return null;
    setNative(input, value);
    input.blur();
    return input;
  }

  function selectRange(range) {
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }

  function pasteEvent(pm, html, plain) {
    const dt = new DataTransfer();
    dt.setData("text/html", html);
    dt.setData("text/plain", plain);
    return !pm.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
  }

  // Replace everything in a block editor with HTML, the way a paste would.
  // ProseMirror merges the first pasted block into whatever block the
  // selection starts in, which turns a leading heading into a paragraph. It
  // only keeps the block intact when the cursor sits in an empty paragraph,
  // so: replace everything with a marker word, select just that word and
  // paste nothing over it (leaving an empty paragraph), then paste for real.
  async function pasteIntoEditor(pm, html, plain) {
    const MARK = "ELARISCLEAR";
    pm.focus();
    const all = document.createRange();
    all.selectNodeContents(pm);
    selectRange(all);
    await sleep(100);
    pasteEvent(pm, `<p>${MARK}</p>`, MARK);
    await sleep(200);

    const walker = document.createTreeWalker(pm, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) if (node.textContent.includes(MARK)) break;
    if (node) {
      const at = node.textContent.indexOf(MARK);
      const word = document.createRange();
      word.setStart(node, at);
      word.setEnd(node, at + MARK.length);
      selectRange(word);
      await sleep(100);
      pasteEvent(pm, "<p></p>", "");
      await sleep(200);
    }

    const handled = pasteEvent(pm, html, plain);
    await sleep(200);
    return handled;
  }

  // Plain inputs, textareas, generic contenteditable and CodeMirror.
  function setValue(el, value) {
    const cm = el.closest?.(".CodeMirror")?.CodeMirror;
    if (cm && typeof cm.setValue === "function") { cm.setValue(value); return true; }
    if (el.isContentEditable) {
      el.focus();
      el.textContent = value;
      el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }));
      return true;
    }
    if ("value" in el) { setNative(el, value); return true; }
    return false;
  }

  // -- BBCode → HTML for the block editor -----------------------------------
  const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const escapeAttr = escapeHtml;

  const MENTION_RE = /@\[([^\]\n]+)\](?:\([^)\n]*\))?/g;

  function mentionTitles(text) {
    const out = new Set();
    for (const m of String(text || "").matchAll(MENTION_RE)) out.add(m[1].trim());
    return [...out];
  }

  function uuid() {
    return crypto.randomUUID ? crypto.randomUUID()
      : "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
        const r = Math.random() * 16 | 0; return (c === "x" ? r : (r & 3 | 8)).toString(16);
      });
  }

  // links: title -> {id, url, title} | null. Unresolved titles are collected.
  function bbcodeToHtml(text, links, unresolved) {
    const tokens = [];
    const inline = (raw) => {
      // Mentions first (kept out of the escaper), then escape, then tags.
      let s = raw.replace(MENTION_RE, (m, t) => {
        const title = t.trim();
        const hit = links(title);
        if (hit) {
          tokens.push(`<span data-inline-content-type="mention" data-id="${uuid()}" data-target-id="${escapeAttr(hit.id)}" data-label="${escapeAttr(hit.title)}" data-url="${escapeAttr(hit.url || "")}" data-entity-class="Article">@${escapeHtml(hit.title)}</span>`);
        } else {
          unresolved.add(title);
          tokens.push(escapeHtml(title));
        }
        return ` ${tokens.length - 1} `;
      });
      s = escapeHtml(s)
        .replace(/\[b\]([\s\S]*?)\[\/b\]/gi, "<strong>$1</strong>")
        .replace(/\[i\]([\s\S]*?)\[\/i\]/gi, "<em>$1</em>")
        .replace(/\[u\]([\s\S]*?)\[\/u\]/gi, "<u>$1</u>")
        .replace(/\[s\]([\s\S]*?)\[\/s\]/gi, "<s>$1</s>")
        .replace(/\[url=([^\]]+)\]([\s\S]*?)\[\/url\]/gi, '<a href="$1">$2</a>')
        .replace(/\[url\]([^\[]+)\[\/url\]/gi, '<a href="$1">$1</a>')
        .replace(/\[br\]/gi, "<br>")
        .replace(/\[\/?(?:p|div|container|section)\]/gi, "");
      return s.replace(/ (\d+) /g, (m, i) => tokens[+i]);
    };

    const out = [];
    for (const rawLine of String(text || "").replace(/\r/g, "").split("\n")) {
      const line = rawLine.trim();
      if (!line) continue;
      let m;
      if ((m = line.match(/^\[h([1-6])\]([\s\S]*)\[\/h\1\]$/i))) {
        const level = Math.min(+m[1] + 1, 6); // editor stores <hN> as [h(N-1)]
        out.push(`<h${level}>${inline(m[2])}</h${level}>`);
      } else if ((m = line.match(/^\[(ul|ol|list)\]$/i))) {
        out.push(m[1].toLowerCase() === "ol" ? "<ol>" : "<ul>");
      } else if ((m = line.match(/^\[\/(ul|ol|list)\]$/i))) {
        out.push(m[1].toLowerCase() === "ol" ? "</ol>" : "</ul>");
      } else if ((m = line.match(/^\[li\]([\s\S]*)\[\/li\]$/i)) || (m = line.match(/^\[\*\]([\s\S]*)$/))) {
        out.push(`<li>${inline(m[1])}</li>`);
      } else if ((m = line.match(/^\[quote\]([\s\S]*)\[\/quote\]$/i))) {
        out.push(`<blockquote><p>${inline(m[1])}</p></blockquote>`);
      } else if (/^\[(hr|line)\]$/i.test(line)) {
        out.push("<hr>");
      } else {
        out.push(`<p>${inline(line)}</p>`);
      }
    }
    return out.join("");
  }

  function bbcodeToPlain(text) {
    return String(text || "")
      .replace(MENTION_RE, "$1")
      .replace(/\[\/?[a-z0-9*]+(?:=[^\]]*)?\]/gi, "");
  }

  // BBCode in the shape the visual editor itself saves: every paragraph in
  // [p]…[/p], headings carry a block id, list items are "- " lines, mentions
  // are @[Title](Article:id). Unknown names become plain text.
  function bbcodeToPlutarch(text, links, unresolved) {
    const inline = (s) => s.replace(MENTION_RE, (m, t) => {
      const title = t.trim();
      const hit = links(title);
      if (hit) return `@[${hit.title}](Article:${hit.id})`;
      unresolved.add(title);
      return title;
    });
    const out = [];
    for (const rawLine of String(text || "").replace(/\r/g, "").split("\n")) {
      const line = rawLine.trim();
      if (!line) continue;
      let m;
      if ((m = line.match(/^\[h([1-6])\]([\s\S]*)\[\/h\1\]$/i))) {
        out.push(`[h${m[1]}|${uuid()}]${inline(m[2])}[/h${m[1]}]`);
      } else if (/^\[\/?(ul|ol|list)\]$/i.test(line)) {
        out.push(line.toLowerCase().replace("list", "ul"));
      } else if ((m = line.match(/^\[li\]([\s\S]*)\[\/li\]$/i)) || (m = line.match(/^\[\*\]([\s\S]*)$/))) {
        out.push(`- ${inline(m[1])}`);
      } else if (/^\[\/?(quote|hr|line)\]/i.test(line)) {
        out.push(inline(line));
      } else {
        out.push(`[p]${inline(line)}[/p]`);
      }
    }
    return out.join("\n");
  }

  // -- Fantasia Archive fields → World Anvil template boxes -----------------
  // The converter keeps every Fantasia Archive field as a "[h3]Field[/h3]"
  // section in the content. Those sections are read back here and the ones
  // with a matching World Anvil box (internal field names as the editor
  // saves them) are written into that box as well.
  const LOCATION_FIELDS = {
    "Population": "population", "Size": "areaSize", "Location type": "locationTemplateType",
    "Description & History": "history", "Local Resources/Materials": "naturalresources",
    "Other Names & Epithets": "alternativename", "Date of creation": "constructed",
    "Local Species/Races/Flora/Fauna": "demographics", "Common Occupations/Classes": "industry",
    "Governing Schools of Magic/Magical groups": "government",
    "Governing Teachings/Religious groups": "government",
  };
  const FIELD_MAP = {
    person: {
      "Sex": "sex", "Age": "age", "Height": "height", "Weight": "weight",
      "Species/Races": "speciesDisplay", "Occupation/Class": "employment",
      "Place of origin": "birthplace", "Connected to Skills/Spells/Other": "specialAbilities",
      "Leading Figure of Organizations/Other groups": "titles",
      "Leading Figure of Teachings/Religious groups": "religion",
      "Connected to Schools of Magic/Magical groups": "education", "Status": "currentstatus",
    },
    location: LOCATION_FIELDS, settlement: LOCATION_FIELDS, landmark: LOCATION_FIELDS,
    species: {
      "Average lifespan": "lifespan", "Average adulthood": "growthrate", "Average size": "averageHeight",
      "Inhabited Locations": "geographicalOrigin", "Commonly spoken Languages": "languages",
      "Common in Organizations/Other groups": "majorOrganizations",
      "Characters of Species/Races/Flora/Fauna": "historicalFigures", "Related Species/Races/Flora/Fauna": "ancenstry",
    },
    item: {
      "Exchange rates to other Currencies": "price", "Description & History": "history",
      "Used in Locations": "currentLocation", "Used by Races": "significance",
    },
    organization: {
      "Description & History": "history", "Ruled/Influenced Locations": "territory",
      "Follower/Subject count": "demographics", "Leading Figures": "structure",
      "Used Languages": "culture", "Common Languages": "culture", "Type of group": "publicAgenda",
      "Form of religion": "tenets", "Type of religion": "mythos",
    },
    profession: {
      "Occupation/Class type": "type", "Commonly used Items": "tools", "Common Species/Races/Flora/Fauna": "demographics",
      "Connected to Locations/Geography": "workplace", "Commonly used Skills/Spells/Other": "qualifications",
      "Connected to Skills/Spells/Other": "qualifications", "Related Occupations/Classes": "alternativeNames",
    },
    spell: { "Complexity to use": "level", "Type": "school", "Prerequisites Skills/Spells/Other": "restrictions", "Required by Skills/Spells/Other": "effect" },
    language: { "Connected to Locations": "geographicdistribution" },
    material: { "Found in Locations": "geo" },
  };

  // {"Field name": ["raw value", …]} from the converter's content BBCode.
  function faFields(content) {
    const out = {};
    let current = null;
    for (const raw of String(content || "").split("\n")) {
      const line = raw.trim();
      let m;
      if ((m = line.match(/^\[h3\](.*)\[\/h3\]$/i))) { current = m[1].trim(); out[current] = out[current] || []; continue; }
      if (/^\[h[12]\]/i.test(line)) { current = null; continue; }
      if (!current || !line || /^\[\/?ul\]$/i.test(line)) continue;
      const v = (line.match(/^\[li\](.*)\[\/li\]$/i) || [null, line])[1].trim();
      if (v) out[current].push(v);
    }
    return out;
  }

  // Extra PATCH fields for one article: converter-provided template fields
  // plus everything FIELD_MAP can place, links resolved.
  function templateBody(a, links, unresolved) {
    const body = {};
    for (const [k, v] of Object.entries(a.templateFields || {})) {
      if (typeof v === "string" && v.trim()) body[k] = v;
    }
    const map = FIELD_MAP[a.templateType] || {};
    const fields = faFields(a.content);
    const inline = (s) => s.replace(MENTION_RE, (m, t) => {
      const title = t.trim();
      const hit = links(title);
      if (hit) return `@[${hit.title}](Article:${hit.id})`;
      unresolved.add(title);
      return title;
    });
    for (const [name, values] of Object.entries(fields)) {
      const key = map[name];
      if (!key || !values.length) continue;
      const text = values.map(inline).join(values.length > 1 ? "\n" : "");
      body[key] = body[key] ? `${body[key]}\n${text}` : text;
    }
    return body;
  }

  // -- World Anvil's internal API (what the editor itself calls) -----------
  const API = "/api/internal/aboleth";

  // Retries on rate limiting (429) and server hiccups (5xx) with growing
  // pauses, so a long "Fill all" run survives World Anvil throttling it.
  async function api(method, path, body) {
    let last = "";
    for (let attempt = 0; attempt < 6; attempt++) {
      if (attempt) await sleep(1500 * 2 ** (attempt - 1));
      let r;
      try {
        r = await fetch(API + path, {
          method, credentials: "include",
          headers: body ? { "content-type": "application/json" } : {},
          body: body ? JSON.stringify(body) : undefined,
        });
      } catch (err) { last = err.message; continue; }
      if (r.ok) return r.json();
      last = `HTTP ${r.status}`;
      if (r.status !== 429 && r.status < 500) break;
    }
    throw new Error(`${method} ${path} → ${last}`);
  }

  // The world id is read once from whatever article editor is open, then
  // remembered so "Fill all" also works from the dashboard.
  async function getWorldId() {
    if (state.worldId) return state.worldId;
    const m = location.pathname.match(/\/article\/([0-9a-f-]{36})/i);
    if (!m) return null;
    try {
      const j = await api("GET", `/article?granularity=2&id=${m[1]}`);
      state.worldId = j.world?.id ?? null;
      if (state.worldId) await chrome.storage.local.set({ worldId: state.worldId });
    } catch (_) { state.worldId = null; }
    return state.worldId;
  }

  // links: exact title -> {id, url, title}. Exact match first; a
  // case-insensitive match is accepted only when nothing exact exists
  // ("Academy of Myr" and "Academy of MYR" are different articles).
  // Loose form of a title for last-resort matching: case, "&" vs "-",
  // a leading "_" and trailing punctuation ("Mistriver Gorge:") are ignored.
  function loose(title) {
    return String(title).toLowerCase().replace(/&/g, "-").replace(/^_+/, "")
      .replace(/[\s:.\-–—]+$/, "").replace(/\s+/g, " ").trim();
  }

  function linkFor(title) {
    if (state.links[title]) return state.links[title];
    const key = title.toLowerCase();
    let k = Object.keys(state.links).find((t) => t.toLowerCase() === key);
    if (!k) { const l = loose(title); k = Object.keys(state.links).find((t) => loose(t) === l); }
    return k ? state.links[k] : null;
  }

  function rememberLink(a) {
    if (!a?.id || !a?.title) return null;
    state.links[a.title] = { id: a.id, url: a.url || "", title: a.title };
    return state.links[a.title];
  }

  // Same world search the editor's "@" menu uses.
  async function searchTitle(title) {
    const worldId = await getWorldId();
    if (!worldId) return [];
    try {
      const j = await api("POST", `/world/search?id=${worldId}`, { term: title });
      return Array.isArray(j.articles) ? j.articles : [];
    } catch (_) { return []; }
  }

  async function lookupTitle(title) {
    const known = linkFor(title);
    if (known) return known;
    const key = title.toLowerCase();
    const l = loose(title);
    const pick = (list) => list.find((a) => a.title === title)
      || list.find((a) => (a.title || "").toLowerCase() === key)
      || list.find((a) => loose(a.title || "") === l);
    let hit = pick(await searchTitle(title));
    if (!hit) {
      // "Mistriver Gorge:" or "X & Y" will not match as a whole; search by
      // the longest word and compare loosely.
      const word = l.split(/[\s-]+/).sort((a, b) => b.length - a.length)[0];
      if (word && word.length > 2 && word !== l) hit = pick(await searchTitle(word));
    }
    return hit ? rememberLink(hit) : null;
  }

  async function resolveLinks(article, onProgress) {
    const titles = [...new Set([...mentionTitles(article.content), ...mentionTitles(article.sidebarcontent)])];
    let done = 0;
    const queue = [...titles];
    const worker = async () => {
      while (queue.length) {
        await lookupTitle(queue.shift());
        done += 1;
        onProgress?.(done, titles.length);
      }
    };
    await Promise.all(Array.from({ length: Math.min(4, titles.length) }, worker));
    await chrome.storage.local.set({ links: state.links });
    return linkFor;
  }

  // -- "Fill all": create and write articles through the internal API ------
  // Pass 1 makes sure every article exists (adopting an existing article with
  // exactly the same title only if it is still empty), so every id is known.
  // Pass 2 writes title, content, sidebar and excerpt with all links resolved.
  let stopAll = false;

  // World Anvil's excerpt column holds 255 characters; longer ones are
  // rejected with HTTP 422. Cut at a word boundary.
  function shortExcerpt(text) {
    const s = String(text || "").trim();
    if (s.length <= 255) return s;
    const cut = s.slice(0, 254);
    const at = cut.lastIndexOf(" ");
    return (at > 150 ? cut.slice(0, at) : cut) + "…";
  }

  async function ensureArticle(article) {
    const have = state.created[article._faUuid];
    if (have) return { ...have, how: "already created" };
    const existing = (await searchTitle(article.title)).filter((a) => a.title === article.title);
    if (existing.length) {
      const full = await api("GET", `/article?granularity=2&id=${existing[0].id}`);
      const empty = !String(full.content || "").replace(/\[\/?p\]/g, "").trim();
      if (!empty) {
        // Someone already wrote this one by hand: count it as done, leave it.
        state.done = { ...state.done, [article._faUuid]: true };
        rememberLink(full);
        return { skip: `"${article.title}" already had text — left alone and ticked. Open it and press Fill if you want it replaced.` };
      }
      const rec = { id: full.id, url: full.url || "", title: full.title };
      state.created[article._faUuid] = rec;
      rememberLink(rec);
      return { ...rec, how: "adopted empty article" };
    }
    const worldId = await getWorldId();
    const made = await api("PUT", "/article", {
      title: article.title, templateType: article.templateType || "article", world: { id: worldId },
      icon: iconFor(article),
    });
    const rec = { id: made.id, url: made.url || "", title: made.title || article.title };
    state.created[article._faUuid] = rec;
    rememberLink(rec);
    return { ...rec, how: "created" };
  }

  // -- "Sort into folders": file every article into a category by type ----
  // World Anvil's tree folders are categories. One per kind of article, named
  // from the template type plus a few Fantasia Archive types that deserve
  // their own folder (currencies, religions, schools of magic).
  function categoryFor(a) {
    const fa = String(a.tags || "").split(",").pop().trim();
    const t = a.templateType;
    if (t === "person") return "Characters";
    if (t === "item") return fa === "currency" ? "Currencies" : "Items";
    if (t === "landmark") return "Buildings & Landmarks";
    if (t === "language") return "Languages";
    if (t === "location") return "Geography";
    if (t === "settlement") return "Settlements";
    if (t === "material") return "Materials";
    if (t === "species") return "Species";
    if (t === "profession") return "Professions & Classes";
    if (t === "spell" || t === "article") return "Spells & Skills";
    if (t === "organization") {
      if (fa.startsWith("school-of-magic")) return "Schools of Magic";
      if (fa.includes("religious")) return "Religions";
      return "Organizations";
    }
    return TEMPLATE_NAMES[t] || "Imported";
  }

  // Tree icons World Anvil's own "Create" tiles assign per template type.
  const ICONS = {
    article: "fa-books", landmark: "fa-chart-pyramid", person: "fa-person",
    location: "fa-mountain-sun", item: "fa-sword", organization: "fa-gear",
    species: "fa-fish-fins", vehicle: "fa-horse-saddle", settlement: "fa-building-columns",
    condition: "fa-biohazard", militaryConflict: "fa-swords", document: "fa-scroll-old",
    ethnicity: "fa-shoe-prints", language: "fa-sign-hanging", material: "fa-coins",
    formation: "fa-hammer-crash", myth: "fa-dragon", law: "fa-cloud-bolt", plot: "fa-staff",
    profession: "fa-hammer", prose: "fa-gem", rank: "fa-crown", spell: "fa-wand-magic-sparkles",
    technology: "fa-vial", ritual: "fa-heart",
  };
  function iconFor(a) {
    const fa = String(a.tags || "").split(",").pop().trim();
    if (a.templateType === "organization" && fa.includes("religious")) return "fa-ankh";
    if (a.templateType === "item" && fa === "currency") return "fa-coins";
    return ICONS[a.templateType] || "fa-books";
  }

  // Folder name -> category id for this world, loaded once, created on demand.
  let catCache = null;
  async function categoryId(name, worldId) {
    if (!catCache) {
      catCache = {};
      for (const c of (await api("POST", `/world/categories?id=${worldId}`, {})).entities || []) {
        if (c.title) catCache[c.title.toLowerCase()] = c.id;
      }
    }
    const key = name.toLowerCase();
    if (!catCache[key]) {
      const c = await api("PUT", "/category", { title: name, world: { id: worldId } });
      catCache[key] = c.id;
    }
    return catCache[key];
  }

  async function sortAll() {
    if (busy) return;
    busy = true;
    stopAll = false;
    let filed = 0, already = 0, missing = 0;
    const made = [];
    try {
      const worldId = await getWorldId();
      if (!worldId) {
        status("Open any article of your world in the editor first (that tells me which world), then press Sort again.", "warn");
        return;
      }
      status("Reading folders…", "info");
      catCache = null;
      const index = await api("GET", "/world/index");
      const idByTitle = {};
      for (const e of Object.values(index.articles || {})) if (e.title) idByTitle[e.title] = e.id;

      for (let i = 0; i < state.articles.length; i++) {
        if (stopAll) break;
        const a = state.articles[i];
        status(`Filing ${i + 1}/${state.articles.length}: ${a.title}`, "info");
        const id = state.created[a._faUuid]?.id || idByTitle[a.title];
        if (!id) { missing += 1; continue; }
        try {
          const full = await api("GET", `/article?granularity=2&id=${id}`);
          const patch = {};
          if (!full.icon) patch.icon = iconFor(a);
          if (!full.category?.id) { // filed by hand already: leave it there
            const name = categoryFor(a);
            const known = catCache && catCache[name.toLowerCase()];
            patch.category = { id: await categoryId(name, worldId) };
            if (!known) made.push(name);
          }
          if (!Object.keys(patch).length) { already += 1; continue; }
          await api("PATCH", `/article?id=${id}`, patch);
          if (patch.category) filed += 1; else already += 1;
        } catch (err) {
          missing += 1;
        }
        await sleep(200);
      }
      const parts = [`Filed ${filed} article${filed === 1 ? "" : "s"}`];
      if (made.length) parts.push(`made ${made.length} folder${made.length === 1 ? "" : "s"} (${made.join(", ")})`);
      if (already) parts.push(`${already} were already in a folder and were left there`);
      if (missing) parts.push(`${missing} not found on World Anvil yet`);
      status(`${stopAll ? "Stopped. " : ""}${parts.join("; ")}. Reload the page to see the tree.`, "ok");
    } catch (err) {
      status(`Sort failed: ${err.message}`, "warn");
    } finally {
      busy = false;
      stopAll = false;
      render();
    }
  }

  // -- "Delete imported": remove every loaded article (and our folders) -----
  // For starting over. Matches by exact title against the world index, so an
  // article of the same name written by hand goes too; the confirmation
  // names those. Cannot be undone from here.
  const FOLDER_NAMES = new Set([
    "Characters", "Currencies", "Items", "Buildings & Landmarks", "Languages", "Geography",
    "Settlements", "Materials", "Species", "Professions & Classes", "Spells & Skills",
    "Schools of Magic", "Religions", "Organizations", "Imported",
  ]);

  async function wipePlan() {
    const worldId = await getWorldId();
    if (!worldId) return null;
    const index = await api("GET", "/world/index");
    const idByTitle = {};
    for (const e of Object.values(index.articles || {})) if (e.title) idByTitle[e.title] = e.id;
    const articles = [];
    for (const a of state.articles) {
      const id = state.created[a._faUuid]?.id || idByTitle[a.title];
      if (id) articles.push({ id, title: a.title, preexisting: !state.created[a._faUuid] });
    }
    const cats = ((await api("POST", `/world/categories?id=${worldId}`, {})).entities || [])
      .filter((c) => FOLDER_NAMES.has(c.title));
    return { articles, cats };
  }

  async function wipe(plan) {
    if (busy) return;
    busy = true;
    stopAll = false;
    let gone = 0, failed = 0;
    try {
      for (let i = 0; i < plan.articles.length; i++) {
        if (stopAll) break;
        const t = plan.articles[i];
        status(`Deleting ${i + 1}/${plan.articles.length}: ${t.title}`, "info");
        try { await api("DELETE", `/article?id=${t.id}`); gone += 1; } catch (_) { failed += 1; }
        await sleep(150);
      }
      let folders = 0;
      for (const c of plan.cats) {
        if (stopAll) break;
        status(`Deleting folder: ${c.title}`, "info");
        try { await api("DELETE", `/category?id=${c.id}`); folders += 1; } catch (_) { failed += 1; }
        await sleep(150);
      }
      state.done = {}; state.pending = {}; state.created = {}; state.links = {};
      catCache = null;
      await chrome.storage.local.set({ done: {}, pending: {}, created: {}, links: {} });
      status(`${stopAll ? "Stopped. " : ""}Deleted ${gone} article${gone === 1 ? "" : "s"} and ${folders} folder${folders === 1 ? "" : "s"}${failed ? `, ${failed} failed` : ""}. Ticks cleared. Reload the page, then Fill all shown starts from scratch.`, failed ? "warn" : "ok");
    } catch (err) {
      status(`Delete failed: ${err.message}`, "warn");
    } finally {
      busy = false;
      stopAll = false;
      render();
    }
  }

  async function fillAll(list) {
    if (busy) return;
    busy = true;
    stopAll = false;
    const skipped = [];
    let written = 0;
    try {
      if (!(await getWorldId())) {
        status("Open any article of your world in the editor first (that tells me which world), then press Fill all again.", "warn");
        return;
      }
      catCache = null; // re-read folders each run
      // Pass 1: ids for everything.
      const todo = [];
      for (let i = 0; i < list.length; i++) {
        if (stopAll) break;
        const a = list[i];
        status(`Creating ${i + 1}/${list.length}: ${a.title}`, "info");
        try {
          const r = await ensureArticle(a);
          if (r.skip) skipped.push(r.skip); else todo.push(a);
        } catch (err) {
          skipped.push(`"${a.title}": ${err.message}`);
        }
        await chrome.storage.local.set({ created: state.created, links: state.links });
        await sleep(250);
      }
      // Pass 2: content, with every link that can be resolved.
      for (let i = 0; i < todo.length; i++) {
        if (stopAll) break;
        const a = todo[i];
        const rec = state.created[a._faUuid];
        status(`Writing ${i + 1}/${todo.length}: ${a.title}`, "info");
        const unresolved = new Set();
        const titles = [...new Set([...mentionTitles(a.content), ...mentionTitles(a.sidebarcontent)])];
        for (const t of titles) if (!linkFor(t)) await lookupTitle(t);
        const body = {
          title: a.title,
          content: bbcodeToPlutarch(a.content, linkFor, unresolved),
          sidebarcontent: bbcodeToPlutarch(a.sidebarcontent, linkFor, unresolved),
          excerpt: shortExcerpt(a.excerpt),
          icon: iconFor(a),
        };
        // Template boxes (population, gender, lifespan…) get their values too.
        for (const [k, v] of Object.entries(templateBody(a, linkFor, unresolved))) {
          if (!(k in body)) body[k] = v;
        }
        // File it into its folder at the same time (folder made on demand).
        try { body.category = { id: await categoryId(categoryFor(a), state.worldId) }; } catch (_) { /* folder optional */ }
        try {
          await api("PATCH", `/article?id=${rec.id}`, body);
          written += 1;
          const missing = [...unresolved].sort();
          if (missing.length) state.pending[a._faUuid] = missing; else delete state.pending[a._faUuid];
          state.done = { ...state.done, [a._faUuid]: true };
          await chrome.storage.local.set({ done: state.done, pending: state.pending, links: state.links });
          render();
        } catch (err) {
          skipped.push(`"${a.title}": ${err.message}`);
        }
        await sleep(250);
      }
      const note = skipped.length ? ` Skipped ${skipped.length}: ${skipped.slice(0, 3).join(" · ")}${skipped.length > 3 ? " …" : ""}` : "";
      status(`${stopAll ? "Stopped. " : ""}Wrote ${written} article${written === 1 ? "" : "s"}.${note} Reload World Anvil's tree to see them.`, skipped.length ? "warn" : "ok");
    } catch (err) {
      status(`Fill all failed: ${err.message}`, "warn");
    } finally {
      busy = false;
      stopAll = false;
      render();
    }
  }

  // -- pick mode (legacy editor) --------------------------------------------
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
  let busy = false;

  async function fill(article) {
    if (busy) return;
    busy = true;
    try {
      const visual = isVisualEditor();
      const unresolved = new Set();
      let links = () => null;
      if (visual) {
        status("Looking up links…", "info");
        links = await resolveLinks(article, (d, n) => status(`Looking up links… ${d}/${n}`, "info"));
      }

      const results = [];
      for (const field of FIELDS) {
        const value = field.source(article);
        if (!value) continue;
        const found = findField(field.key);
        if (!found) { results.push(`${field.label}: not found`); continue; }
        let ok = false;
        let target = found.el;
        if (field.key === "title" && (found.el.tagName === "INPUT" || found.el.classList.contains("entity-header-title"))) {
          target = await setVisualTitle(found.el, value);
          ok = !!target;
        } else if (found.el.classList.contains("ProseMirror")) {
          ok = await pasteIntoEditor(found.el, bbcodeToHtml(value, links, unresolved), bbcodeToPlain(value));
        } else {
          ok = setValue(found.el, value);
        }
        results.push(`${field.label}: ${ok ? "filled" : "could not write"} (${found.how})`);
        if (ok && target) flash(target);
      }

      const filledTitle = results.some((r) => r.startsWith("Title: filled"));
      const filledBody = results.some((r) => r.startsWith("Content: filled"));
      const missing = [...unresolved].sort();
      if (missing.length) state.pending[article._faUuid] = missing; else delete state.pending[article._faUuid];
      await chrome.storage.local.set({ pending: state.pending });

      if (filledTitle && filledBody) {
        await markDone(article, true);
        const saveNote = visual
          ? "World Anvil saves by itself (the clock at the bottom updates)."
          : "Check it, then press Save.";
        const linkNote = missing.length
          ? ` ${missing.length} link${missing.length > 1 ? "s" : ""} left as plain text (${missing.slice(0, 4).join(", ")}${missing.length > 4 ? "…" : ""}) — press Fill again on this article once those exist.`
          : "";
        status(`Filled "${article.title}". ${saveNote}${linkNote}`, missing.length ? "warn" : "ok");
      } else {
        status(`Only partly filled — ${results.join("; ")}. Use "Pick fields" or the copy buttons.`, "warn");
      }
    } catch (err) {
      status(`Fill failed: ${err.message}`, "warn");
    } finally {
      busy = false;
      render();
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
      .all { display: flex; gap: 6px; padding: 0 10px 8px; }
      .all button { flex: 1; padding: 5px 8px; border: 1px solid #16a34a; border-radius: 6px; background: #f0fdf4; color: #166534; font: inherit; font-weight: 600; cursor: pointer; }
      .all button.confirm { background: #fef3c7; border-color: #d97706; color: #92400e; }
      .all button.sort { background: #eff6ff; border-color: #2563eb; color: #1e40af; }
      .all button.stop { flex: 0 0 auto; background: #fef2f2; border-color: #dc2626; color: #991b1b; }
      .all button:disabled { opacity: .5; cursor: default; }
      .danger { display: flex; gap: 8px; align-items: center; padding: 0 10px 8px; font-size: 11px; color: #6b7280; }
      .danger button { font: inherit; font-size: 11px; padding: 2px 8px; border: 1px solid #fca5a5; border-radius: 5px; background: #fff; color: #991b1b; cursor: pointer; }
      .danger button.confirm { background: #dc2626; border-color: #dc2626; color: #fff; font-weight: 600; }
      .danger button:disabled { opacity: .5; cursor: default; }
      .danger .wipenote { flex: 1; }
      .list { overflow: auto; padding: 0 6px 6px; }
      .row { display: grid; grid-template-columns: 18px 1fr auto; gap: 6px; align-items: center; padding: 5px 4px; border-radius: 6px; }
      .row:hover { background: #f3f4f6; }
      .row.done { opacity: .55; }
      .row.done.pending { opacity: .85; }
      .row .t { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; }
      .row .badge { display: inline-block; font-size: 10px; padding: 1px 5px; border-radius: 4px; background: #e0e7ff; color: #3730a3; margin-left: 4px; vertical-align: 1px; }
      .row .badge.links { background: #fef3c7; color: #92400e; }
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
      .details button { font: inherit; font-size: 11px; padding: 1px 6px; border: 1px solid #d1d5db; border-radius: 4px; background: #fff; cursor: pointer; }
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
        <div class="all">
          <button class="fillall" title="Creates every article shown below that is not ticked yet and writes its text, links included. Nothing to click per article.">Fill all shown</button>
          <button class="sort" title="Puts every imported article into a folder by kind (Characters, Items, Settlements…). Articles already in a folder are left where they are.">Sort into folders</button>
          <button class="stop" hidden>Stop</button>
        </div>
        <div class="danger">
          <button class="wipe" title="Deletes every article in the loaded list from World Anvil, plus the folders this tool made, so you can start over. Asks twice.">Delete imported…</button>
          <span class="wipenote"></span>
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

  const PENDING_FILTER = "__pending";
  let shownTodo = [];
  let confirmTimer = null;

  q(".fillall").addEventListener("click", () => {
    const fa = q(".fillall");
    if (busy || !shownTodo.length) return;
    if (!fa.classList.contains("confirm")) {
      fa.classList.add("confirm");
      fa.textContent = `Create and write ${shownTodo.length} article${shownTodo.length === 1 ? "" : "s"} on World Anvil? Click again`;
      clearTimeout(confirmTimer);
      confirmTimer = setTimeout(() => { fa.classList.remove("confirm"); render(); }, 8000);
      return;
    }
    clearTimeout(confirmTimer);
    fa.classList.remove("confirm");
    fillAll([...shownTodo]).then(render);
    render();
  });
  q(".stop").addEventListener("click", () => { stopAll = true; status("Stopping after the current article…", "info"); });
  q(".sort").addEventListener("click", () => { if (!busy) sortAll().then(render); });

  let wipePending = null;
  let wipeTimer = null;
  q(".wipe").addEventListener("click", async () => {
    const b = q(".wipe");
    if (busy) return;
    if (!wipePending) {
      b.disabled = true;
      status("Counting what would be deleted…", "info");
      try {
        wipePending = await wipePlan();
      } catch (err) { wipePending = null; status(`Could not read the world: ${err.message}`, "warn"); }
      b.disabled = false;
      if (!wipePending) { status("Open any article of your world in the editor first, then try again.", "warn"); return; }
      const pre = wipePending.articles.filter((t) => t.preexisting).map((t) => t.title);
      b.classList.add("confirm");
      b.textContent = `Really delete ${wipePending.articles.length} articles and ${wipePending.cats.length} folders? Click again`;
      q(".wipenote").textContent = pre.length
        ? `${pre.length} of them were not made by this tool and will go too: ${pre.slice(0, 6).join(", ")}${pre.length > 6 ? "…" : ""}`
        : "";
      status("This cannot be undone from here. Click the red button again to go ahead, or wait 10 s to cancel.", "warn");
      clearTimeout(wipeTimer);
      wipeTimer = setTimeout(() => { wipePending = null; b.classList.remove("confirm"); render(); status("Delete cancelled.", "info"); }, 10000);
      return;
    }
    clearTimeout(wipeTimer);
    const plan = wipePending;
    wipePending = null;
    b.classList.remove("confirm");
    q(".wipenote").textContent = "";
    await wipe(plan);
  });

  function render() {
    panel.classList.toggle("collapsed", state.collapsed);
    const doneCount = state.articles.filter((a) => state.done[a._faUuid]).length;
    q(".count").textContent = state.articles.length
      ? `${doneCount} / ${state.articles.length}` : "";

    const tpl = q(".tpl");
    const templates = [...new Set(state.articles.map((a) => a.templateType))].sort();
    const pendingCount = state.articles.filter((a) => state.pending[a._faUuid]?.length).length;
    const current = tpl.value;
    tpl.innerHTML = `<option value="">All types</option>` + templates
      .map((t) => `<option value="${t}">${TEMPLATE_NAMES[t] ?? t}</option>`).join("")
      + (pendingCount ? `<option value="${PENDING_FILTER}">Links to redo (${pendingCount})</option>` : "");
    tpl.value = templates.includes(current) || (current === PENDING_FILTER && pendingCount) ? current : "";
    state.template = tpl.value;

    const list = q(".list");
    list.innerHTML = "";
    if (!state.articles.length) {
      list.innerHTML = `<div class="empty">No articles loaded.<br>Click the extension icon and load <code>articles.json</code>.</div>`;
    }
    const needle = state.filter.trim().toLowerCase();
    const rows = state.articles
      .map((a, i) => ({ a, i }))
      .filter(({ a }) => state.template === PENDING_FILTER
        ? state.pending[a._faUuid]?.length
        : (!state.template || a.templateType === state.template))
      .filter(({ a }) => !needle || a.title.toLowerCase().includes(needle))
      .sort((x, y) => (state.done[x.a._faUuid] ? 1 : 0) - (state.done[y.a._faUuid] ? 1 : 0) || x.i - y.i);

    for (const { a } of rows) {
      const pending = state.pending[a._faUuid] ?? [];
      const row = document.createElement("div");
      row.className = "row" + (state.done[a._faUuid] ? " done" : "") + (pending.length ? " pending" : "");
      row.innerHTML = `
        <input type="checkbox" ${state.done[a._faUuid] ? "checked" : ""} title="Done">
        <div class="t" title="${escapeAttr(a.title)}">${escapeHtml(a.title)}<span class="badge">${escapeHtml(TEMPLATE_NAMES[a.templateType] ?? a.templateType)}</span>${pending.length ? `<span class="badge links" title="Not linked yet: ${escapeAttr(pending.join(", "))}">${pending.length} link${pending.length > 1 ? "s" : ""}</span>` : ""}</div>
        <div class="acts">
          <button class="fill" title="Fill the editor on this page">Fill</button>
          <button class="copy" title="Copy the content (BBCode) to the clipboard">Copy</button>
        </div>`;
      row.querySelector("input").addEventListener("change", (e) => markDone(a, e.target.checked));
      row.querySelector(".fill").addEventListener("click", () => fill(a));
      row.querySelector(".copy").addEventListener("click", () => copyText(a.content, "Content"));
      row.querySelector(".t").addEventListener("click", () => showDetails(a));
      list.appendChild(row);
    }

    shownTodo = rows.map(({ a }) => a).filter((a) => !state.done[a._faUuid]);
    const fa = q(".fillall");
    if (!fa.classList.contains("confirm")) {
      fa.textContent = `Fill all shown (${shownTodo.length})`;
      fa.disabled = busy || !shownTodo.length;
    }
    q(".stop").hidden = !busy;
    q(".sort").disabled = busy || !state.articles.length;
    const wb = q(".wipe");
    if (!wb.classList.contains("confirm")) {
      wb.textContent = "Delete imported…";
      wb.disabled = busy || !state.articles.length;
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
    const pending = state.pending[a._faUuid] ?? [];
    q(".details").innerHTML = `
      <div><b>${escapeHtml(a.title)}</b> → create as <code>${escapeHtml(TEMPLATE_NAMES[a.templateType] ?? a.templateType)}</code></div>
      ${a.tags ? `<div>Tags: <code>${escapeHtml(a.tags)}</code> <button data-copy="${escapeAttr(a.tags)}">copy</button></div>` : ""}
      ${a.excerpt ? `<div>Excerpt (no box in this editor): ${escapeHtml(a.excerpt.slice(0, 80))}${a.excerpt.length > 80 ? "…" : ""} <button data-copy="${escapeAttr(a.excerpt)}">copy</button></div>` : ""}
      ${pending.length ? `<div>Not linked yet: ${escapeHtml(pending.join(", "))}</div>` : ""}
      ${fields.length ? `<div>Template fields (copy into the matching boxes):</div>` : ""}
      ${fields.map(([k, v]) => `<div>· <code>${escapeHtml(k)}</code> ${escapeHtml(String(v).slice(0, 80))}${String(v).length > 80 ? "…" : ""} <button data-copy="${escapeAttr(String(v))}">copy</button></div>`).join("")}`;
    q(".details").querySelectorAll("button[data-copy]").forEach((b) =>
      b.addEventListener("click", () => copyText(b.dataset.copy, "Value")));
  }

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
