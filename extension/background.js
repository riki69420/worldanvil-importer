// Nothing runs in the background. The worker exists so the extension has a
// stable identity Chrome can report (test tooling needs it) and so first-run
// defaults are written once rather than lazily by every content script.
chrome.runtime.onInstalled.addListener(async () => {
  const current = await chrome.storage.local.get(["articles", "done", "selectors"]);
  await chrome.storage.local.set({
    articles: current.articles ?? [],
    done: current.done ?? {},
    selectors: current.selectors ?? {},
  });
});
