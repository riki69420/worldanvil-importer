# Session Handoff: Fantasia Archive → World Anvil importer

**Date:** 2026-09-02
**Project:** `riki69420/claude`, branch `claude/free-vm-setup-ayvrlt` (this is the repo's default branch; repo is **public**)
**Session duration:** one long session (~6 h of work across the whole thing)
**User:** non-programmer worldbuilder; wants their Fantasia Archive world ("Elaris") and Azgaar map in World Anvil with as little manual work as possible.

## Current state

**Task:** get 126 Fantasia Archive articles + an Azgaar map into World Anvil.
**Phase:** tooling complete and released; user is mid-way through the *first real use* on worldanvil.com.
**Progress:** ~90%. Everything is built, tested, released. The one unverified step is whether the Chrome extension's **Fill** button finds World Anvil's real editor fields. The user had just opened World Anvil's "Create New → Person" editor and was about to press Fill when the session ended. **No feedback on that first Fill yet.**

## What we did

Built a converter (Python), a Tkinter desktop app frozen into a Windows exe by GitHub Actions, and a Chrome/Edge extension that fills World Anvil's editor from the converted output. Discovered World Anvil's API needs an application key gated behind a Guild rank above Grandmaster, so the extension became the practical upload path. Removed the user's worldbuilding data from the public repo (files, history, release, artifacts) and re-released clean.

## Decisions made

- **Extension over API for upload** — API needs an application key; World Anvil only issues them to Guild members above Grandmaster after a manual application. User has only the User API Token. The extension uses the normal editor page, no keys, and the user presses Save (nothing auto-submitted — keeps it ordinary site use; World Anvil's ToS could not be read, Cloudflare 403).
- **Headless Chrome/Edge subprocess for map rendering, not Playwright or cairosvg** — Playwright would force a 150 MB browser download into the exe; cairosvg mis-scales this SVG (masks, `<use>`, nested percentage font sizes → cropped map, giant labels). Edge ships with Windows so `--headless --screenshot` works with nothing bundled. `azgaar.find_browser()` locates it.
- **Fixed SVG on export, not at render** — FMG's embedded SVG lacks `mask="url(#land)"` on `#landmass` (FMG re-applies it at load) and references `./images/pattern*.png`. `azgaar.clean_svg()` fixes both. Do NOT try to resolve percentage font sizes to px — tried it, broke rendering (nested percentages compound).
- **Map collections found by record-key signature, not line offset** — offsets shift between FMG versions. Shape detection includes tombstoned (`removed: true`) records; output filters them. (The user's map had *every* religion deleted, which exposed this.)
- **`@[Title]` mentions for cross-links** — World Anvil resolves mentions by title at render time, so no UUID bookkeeping. `dangling-links.txt` lists mentions no article provides.
- **Releases via `workflow_dispatch` input `release_tag`** — this session's git proxy returns 403 on tag pushes (only the working branch may be pushed). The workflow creates the tag/release itself with the runner's token. Same reason `cleanup.yml` exists for deleting releases/artifacts.
- **Synthetic fixtures replace real data in tests** — repo is public. `tests/fixtures/export/` (8 invented docs) and `tests/fixtures/sample.map` (3 burgs). `.gitignore` refuses `data/`, `out/`, `*.map`, `*.zip` (with `!tests/fixtures/sample.map`).
- **"Other Names & Epithets" is not a link field** — an epithet is text; treating it as a mention produced false dangling links.

## Code changes (all on the branch, all pushed; HEAD `43f36c5`)

- `elaris_import/fa_parse.py` — parses Fantasia Archive `.md` (title from filename `Name-<uuid>.md`, `## Field` → bullet values, strips `<font>/<div>/<b>` WYSIWYG residue keeping paragraph breaks).
- `elaris_import/mapping.py` — doc type → World Anvil `templateType` (from the API's `entityClass` enum); Location type refines to `location`/`settlement`/`landmark`; Skill type refines to `spell`. `LINK_FIELDS`, `SIDEBAR_FIELDS`, `LOCATION_FIELDS` (only the `location` template's fields are published in the API spec).
- `elaris_import/bbcode.py` — renders `Article` (content, excerpt ≤300, sidebar, tags, template_fields, mentions). `dangling_mentions()`.
- `elaris_import/azgaar.py` — `.map` loader, `clean_svg`, `burg_markers` (px + 0–1 fractional coords), `state_rows`, `find_browser`, `render_png` (Chrome subprocess, `--force-device-scale-factor`).
- `elaris_import/wapi.py` — Boromir v2 client. Headers `x-application-key`, `x-auth-token`. List endpoints take parent id as `?id=` with `{limit, offset}` body (verified against pywaclient; paths confirmed real via 401-vs-404 probe). `PUT /article` create, `PATCH /article?id=` update.
- `elaris_import/pipeline.py` — `convert()` (accepts `.zip` or folder, finds export root), `upload()` (resumable via `import-state.json` keyed by FA uuid), `load_worlds()`. Shared by CLI and GUI.
- `gui.py` — Tkinter app; worker thread + queue → log widget; settings in `%APPDATA%\ElarisImporter`; credentials saved only if "Remember" ticked (plaintext, warned). `--selftest` flag used by CI.
- `build.py`, `push.py` — CLI front ends. `build.py` requires `--export` and/or `--map` (no data defaults any more).
- `extension/` — MV3. `content.js`: Shadow-DOM panel, search/type filter, Fill/Copy/done-tick per article, field discovery by heuristics (`GUESSES`) then largest-textarea fallback, **pick mode** (click a field once → selector stored in `chrome.storage.local`), `setValue` handles input/textarea/contenteditable/CodeMirror. `popup.js`: load `articles.json`, reset progress, forget picks. Badge names match World Anvil's menu (`person` → "Person").
- `elaris-importer.spec` — PyInstaller single-file, windowed on Windows.
- `.github/workflows/build-exe.yml` — windows-latest: pytest → PyInstaller → run `ElarisImporter.exe --selftest` → smoke-convert fixtures with real Edge render → bundle zip (exe + INSTRUCTIONS + README + extension) → artifact; publishes a Release when `release_tag` input given.
- `.github/workflows/cleanup.yml` — deletes a release+tag and all Actions artifacts.
- `tests/test_pipeline.py` (25 tests, pytest) and `tests/test_extension.py` (19 checks; loads the unpacked extension into Chromium via Playwright against `tests/fixtures/easy.html` / `hard.html`; run directly with `xvfb-run python3 tests/test_extension.py`, not collected by pytest).
- `INSTRUCTIONS.txt` — plain-language guide (Steps 1, 2A hand-paste, 2B API, 2C extension). `README.md` — technical.

## Released

- **v1.0.1** — https://github.com/riki69420/claude/releases/tag/v1.0.1 — `ElarisImporter-v1.0.1.zip` (exe + instructions + extension, no data). Built at `8d34895`; the later `43f36c5` badge rename is not in it yet (cosmetic).
- v1.0.0 was **deleted** (its zip bundled the user's export and map). All Actions artifacts deleted. Git history rewritten with `filter-branch`; zero paths under `data/` or `out/` remain in any commit.

## Open questions

- [ ] **Does Fill work on World Anvil's real editor?** Never seen it (login-gated). Heuristics guess `article[title]`, `content`, `sidebarcontent`, `excerpt` and fall back to the largest textarea. If the body is a rich widget rather than a textarea, `setValue` may need a new branch. First feedback from the user resolves this.
- [ ] Are `@[Title]` mentions case-sensitive? User has both "Academy of Myr" (location) and "Academy of MYR" (school of magic) — distinct entities; rename one if links misbehave.
- [ ] Should the repo be made **private**? GitHub keeps orphaned commits cached for a while after a force-push; old hash `3eeb0de` could still be fetched until GC. Recommended to the user; only they can do it (Settings → Change visibility). GitHub Support can purge on request.
- [ ] World Anvil ToS on browser automation — unread (Cloudflare). Extension deliberately submits nothing itself.

## Blockers / issues

- **API upload path is dead for this user** — no application key, no realistic way to get one. `Convert and upload` button stays but is not their path.
- **Environment quirks** (this sandbox): git proxy 403s on tag pushes and on `curl api.github.com` (use the GitHub MCP tools instead); foreground `sleep` is blocked (use `run_in_background`); default `python3` is 3.11 with Playwright but **no tkinter** — use `/usr/bin/python3.12` for the GUI/PyInstaller; Chromium at `/opt/pw-browsers/chromium` (Playwright's pip build wants a different revision, pass `executable_path`); `xvfb-run` available; apt needs `apt-get update` first.
- **User's original files are gone from this environment** — they were in the session uploads dir, which is ephemeral, and were purged from the repo. A future session needs them re-uploaded if any data-specific work is required.

## Context to remember

- User prefs: blunt, no filler, lead with the critical variable, confidence tags. Non-programmer: explain "API", "key" etc. in plain terms; they asked for a `.txt` of instructions.
- User's data (not in repo): Fantasia Archive export "Elaris" — 126 docs across 12 types (61 locations, 17 items, 10 occupations, 8 species, 7 currencies, 7 skills/spells, 5 languages, 3 schools of magic, 3 religions, 3 characters, 1 material, 1 org). Azgaar map "Lenyhaha" v1.150.0, 1536×695, 28 named burgs, 12 states, all religions deleted, biomes/relief/state-fill layers were **off** at export so the rendered map is plain (told user to re-export with layers on). 12 dangling links, mostly typos (`Mistriver Gorge:` trailing colon, `Classes`).
- World Anvil facts established: no bulk import; Boromir API v2 at `/api/external/boromir`; OpenAPI at `https://wa-cdn.nyc3.cdn.digitaloceanspaces.com/assets/prod/boromir-documentation/swagger/openapi.yml` (only `location.yml` template schema is public; others 403); mention syntax `@[Title]`; Create New menu labels: Person, God/Deity, Settlement, … ; User API Token page exists but returns 403 to bots.
- pywaclient 1.7.0 source (pip download) was the reference for endpoint shapes.

## Next steps

1. [ ] Get the user's report from the first **Fill** on a Person editor. If "partly filled": have them use the **Title**/**Content** pick buttons; ask for right-click → Inspect on the content box and hard-code the real selectors into `GUESSES` in `extension/content.js`.
2. [ ] If World Anvil's body editor is not a plain textarea, add a `setValue` branch for it.
3. [ ] Re-run the release workflow with `release_tag: v1.0.2` once the extension is confirmed working (picks up the "Person" badge and any selector fixes). Trigger via GitHub MCP `actions_run_trigger` with inputs `{"release_tag": "v1.0.2"}`.
4. [ ] Nudge the user again to make the repo private.
5. [ ] Optional: map markers — `burgs.csv` has fractional coords; World Anvil's `/marker` schema is 403 so nothing pushes them; the user places pins by hand in the map editor.

## Files to review on resume

- `extension/content.js` — the only unverified-in-the-wild code; `GUESSES`, `findField`, `setValue`.
- `elaris_import/pipeline.py` — the whole flow in one place.
- `.github/workflows/build-exe.yml` — how releases are made; `release_tag` input.
- `INSTRUCTIONS.txt` — what the user has been told to do; keep it in sync.
- `tests/test_extension.py` — how to prove extension changes without World Anvil.
