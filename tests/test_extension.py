# Loads the unpacked extension into Chromium and drives it against mock editor
# pages. Needs: pip install playwright, a Chromium at CHROMIUM_PATH or
# /opt/pw-browsers/chromium, and a display (xvfb-run works). Not collected by
# pytest on purpose: run it directly with python.

import json, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import sync_playwright

import os, shutil, tempfile
ROOT = Path(__file__).resolve().parent.parent
WORK = Path(tempfile.mkdtemp(prefix="elaris-ext-"))
EXT = str(WORK / "ext")
shutil.copytree(ROOT / "extension", EXT)
_m = json.loads((Path(EXT) / "manifest.json").read_text())
_m["content_scripts"][0]["matches"].append("http://127.0.0.1/*")
_m["host_permissions"].append("http://127.0.0.1/*")
(Path(EXT) / "manifest.json").write_text(json.dumps(_m, indent=2))
for _n in ("easy.html", "hard.html"):
    shutil.copy(ROOT / "tests" / "fixtures" / _n, WORK / _n)
from elaris_import.pipeline import convert
convert(ROOT / "tests" / "fixtures" / "export", None, WORK / "converted", render_png=False, log=lambda _: None)
ARTICLES = str(WORK / "converted" / "articles.json")
articles = json.load(open(ARTICLES))
first = articles[0]

srv = subprocess.Popen([sys.executable, "-m", "http.server", "8765", "--bind", "127.0.0.1"],
                       cwd=str(WORK), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
failures = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg); 
    if not cond: failures.append(msg)

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        str(WORK / "profile"), headless=False,
        executable_path=os.environ.get("CHROMIUM_PATH", "/opt/pw-browsers/chromium"),
        args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}", "--no-sandbox"],
        viewport={"width": 1280, "height": 900})
    try:
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
        ext_id = sw.url.split("/")[2]
        print("extension id:", ext_id)

        # 1. Popup: load articles.json through the real file input
        pop = ctx.new_page(); pop.goto(f"chrome-extension://{ext_id}/popup.html")
        pop.set_input_files("#file", ARTICLES)
        pop.wait_for_function("document.getElementById('count').textContent === '8'", timeout=5000)
        check(pop.locator("#msg").inner_text() == "Loaded 8 articles.", "popup loads 8 articles")
        pop.screenshot(path=str(WORK / "popup.png"))

        # 2. Easy page: heuristics
        pg = ctx.new_page(); pg.goto("http://127.0.0.1:8765/easy.html")
        pg.wait_for_selector("#elaris-host", state="attached", timeout=5000)
        host = pg.locator("#elaris-host")
        check("0 / 8" in host.locator(".count").inner_text(), "panel shows 0 / 8")
        host.locator(".search").fill(first["title"])
        row = host.locator(".row").first
        check(first["title"] in row.inner_text(), f"search finds '{first['title']}'")
        row.locator("button.fill").click()
        pg.wait_for_timeout(300)
        check(pg.input_value('input[name="article[title]"]') == first["title"], "title filled (heuristic)")
        check(pg.input_value('textarea[name="article[content]"]') == first["content"], "content filled (heuristic)")
        check(pg.input_value('textarea[name="article[excerpt]"]') == first["excerpt"], "excerpt filled (heuristic)")
        check(pg.input_value('textarea[name="article[sidebarcontent]"]') == first["sidebarcontent"], "sidebar filled (heuristic)")
        check("press Save" in host.locator(".status").inner_text(), "status tells user to press Save")
        host.locator(".search").fill("")
        check("1 / 8" in host.locator(".count").inner_text(), "done count is 1 / 8 after fill")
        pg.screenshot(path=str(WORK / "easy.png"))

        # 3. Hard page: heuristics fail for title -> pick mode; content -> largest textarea
        pg2 = ctx.new_page(); pg2.goto("http://127.0.0.1:8765/hard.html")
        pg2.wait_for_selector("#elaris-host", state="attached", timeout=5000)
        h2 = pg2.locator("#elaris-host")
        check("1 / 8" in h2.locator(".count").inner_text(), "done state persists across pages")
        second = articles[1]
        h2.locator(".search").fill(second["title"])
        h2.locator(".row").first.locator("button.fill").click()
        pg2.wait_for_timeout(300)
        check(pg2.input_value("#x1") == "", "obscure title NOT filled before picking (no false positive)")
        check(pg2.input_value('textarea[name="f3"]') == second["content"], "content fell back to largest textarea")
        check("partly" in h2.locator(".status").inner_text(), "status says partly filled")
        # pick the title field
        h2.locator('.pick button[data-key="title"]').click()
        check("Click the title field" in h2.locator(".status").inner_text(), "pick mode prompts")
        pg2.click("#x1")
        pg2.wait_for_timeout(200)
        check("remembered" in h2.locator(".status").inner_text(), "picked selector remembered")
        h2.locator(".row").first.locator("button.fill").click()
        pg2.wait_for_timeout(300)
        check(pg2.input_value("#x1") == second["title"], "title filled via picked selector")
        # reload: selector persisted in storage?
        pg2.reload(); pg2.wait_for_selector("#elaris-host", state="attached")
        h2 = pg2.locator("#elaris-host")
        check("✓ Title" in h2.locator('.pick button[data-key="title"]').inner_text(), "picked selector survives reload")
        pg2.screenshot(path=str(WORK / "hard.png"))

        # 4. Escape cancels picking; details shows template fields
        h2.locator('.pick button[data-key="excerpt"]').click()
        pg2.keyboard.press("Escape")
        check("cancelled" in h2.locator(".status").inner_text(), "Escape cancels pick")
        h2.locator(".search").fill("Testland")
        h2.locator(".row").first.locator(".t").click()
        d = h2.locator(".details").inner_text()
        check("Geographic Location" in d and "population" in d, "details show template + fields for Testland")
    finally:
        ctx.close(); srv.terminate()

print("\n%d failures" % len(failures)); sys.exit(1 if failures else 0)
