const $ = (id) => document.getElementById(id);

function say(text, isError = false) {
  const el = $("msg");
  el.textContent = text;
  el.className = "msg" + (isError ? " err" : "");
}

async function refresh() {
  const { articles = [], done = {} } = await chrome.storage.local.get(["articles", "done"]);
  $("count").textContent = articles.length;
  $("done").textContent = articles.filter((a) => done[a._faUuid]).length;
}

// The converter's articles.json is an array of objects; only these keys are
// used, so anything else in the file is ignored rather than rejected.
function validate(data) {
  if (!Array.isArray(data)) throw new Error("not a list of articles");
  const out = data.map((a, i) => {
    if (!a || typeof a.title !== "string" || typeof a.content !== "string") {
      throw new Error(`entry ${i + 1} has no title/content`);
    }
    return {
      title: a.title,
      templateType: a.templateType || "article",
      content: a.content,
      excerpt: a.excerpt || "",
      sidebarcontent: a.sidebarcontent || "",
      tags: a.tags || "",
      templateFields: a.templateFields || {},
      _faUuid: a._faUuid || `${i}:${a.title}`,
    };
  });
  if (!out.length) throw new Error("the file has no articles in it");
  return out;
}

$("file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    const articles = validate(JSON.parse(await file.text()));
    await chrome.storage.local.set({ articles });
    say(`Loaded ${articles.length} articles.`);
    await refresh();
  } catch (err) {
    say(`Could not load: ${err.message}`, true);
  }
  event.target.value = "";
});

$("open").addEventListener("click", () => {
  chrome.tabs.create({ url: "https://www.worldanvil.com/dashboard" });
});

$("reset").addEventListener("click", async () => {
  await chrome.storage.local.set({ done: {} });
  say("Progress cleared.");
  await refresh();
});

$("forget").addEventListener("click", async () => {
  await chrome.storage.local.set({ selectors: {} });
  say("Picked fields forgotten; the panel will guess again.");
});

refresh();
