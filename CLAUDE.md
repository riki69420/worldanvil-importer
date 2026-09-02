# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this folder is

`E:\Desktop\importer` is the **user's working copy**, not the git repo. It holds the
unzipped release (`ElarisImporter-v1.0.1\`: exe, `extension\`, `out\`), the user's real
Fantasia Archive export and Azgaar `.map` (private data, never commit), `HANDOFF.md`
(the running project log — read it first) and screenshots.

The source lives in the public GitHub repo `riki69420/claude`, default branch
`claude/free-vm-setup-ayvrlt`. Clone it into the session scratchpad to work on it;
never clone it into this folder next to the data. `extension/` in the release folder
is what Edge loads unpacked, so edits to `ElarisImporter-v1.0.1\extension\content.js`
take effect after the user presses Reload on `edge://extensions` (tools cannot open
that page). Copy the same file into the repo clone before committing.

## Commands (run in the repo clone)

```bash
pip install -r requirements.txt            # only `requests`; tkinter comes with Python
python -m pytest -q                        # 25 tests, fixtures only, < 1 s
python -m pytest -q tests/test_pipeline.py -k stub   # one test
python build.py --export "path/Export.zip" --out out --no-png   # convert without the map render
python build.py --export x.zip --map world.map                  # full convert (Edge/Chrome headless for the PNG)
python gui.py                              # the desktop app; `python gui.py --selftest` is what CI runs
node -e "new Function(require('fs').readFileSync('extension/content.js','utf8'))"   # syntax check the content script
python tests/test_extension.py             # Playwright + Chromium + display; NOT collected by pytest, and it still
                                           # targets the pre-1.1.0 paste-only panel (stale)
```

Release: push a `v*` tag (`git tag -a v1.0.3 -m ... && git push origin v1.0.3`). The
workflow builds the exe on `windows-latest`, runs pytest, self-tests the exe,
smoke-converts the fixtures, and publishes `ElarisImporter-vX.Y.Z.zip`. A manual
workflow run without `release_tag` only produces an artifact. Git identity is not
configured on this machine: pass `-c user.name=... -c user.email=...` to `git commit`
and `git tag -a`.

## Architecture

**Converter (`elaris_import/`)** is a straight pipeline, shared by `build.py`, `push.py`
and `gui.py` through `pipeline.convert()`:

- `fa_parse.py` reads the export (`.zip` or folder): one `.md` per document, title from
  the filename `Name-<uuid>.md`, `# Section` / `## Field` / bullet values.
- `mapping.py` decides the World Anvil `templateType` from "Document type" (+ Location
  type → location/settlement/landmark, Skill type → spell) and holds the field policy:
  `LINK_FIELDS` (values become `@[Title]` mentions), `SIDEBAR_FIELDS`, `LOCATION_FIELDS`
  (→ real template fields), `CONSUMED` (fields not repeated in the body).
- `bbcode.py` renders an `Article` (content, sidebar, excerpt ≤ 255, tags, template
  fields, mention list). Every FA field survives as `[h3]Field[/h3]` + values, in the
  export's order; the extension later parses those sections back out.
- `azgaar.py` handles the `.map` (SVG clean-up, burg/state CSVs, headless-browser PNG).
- `wapi.py` / `push.py` / the GUI's upload button use the official Boromir API. That
  path needs an application key the user cannot get; it is kept but unused.

**Extension (`extension/`, MV3)** is the real upload path. `content.js` is one IIFE on
every worldanvil.com page with a Shadow-DOM panel; state lives in
`chrome.storage.local` (`articles`, `done`, `pending`, `created`, `links`, `worldId`,
`selectors`). It writes to World Anvil two ways:

1. **Fill** (one article, the open editor): title via the header's inner `<p>` click →
   `input[placeholder="Title"]`; body/sidebar via a synthetic HTML paste into the
   BlockNote/ProseMirror editors (`bbcodeToHtml`, heading level +1, two-stage clear in
   `pasteIntoEditor`). Falls back to path 2 when the boxes are not found.
2. **Fill all shown / Sort / Delete** use the site's internal API
   (`/api/internal/aboleth/...`, cookie auth, plain JSON): pass 1 creates or adopts every
   article (`ensureArticle`, exact-title search), pass 2 PATCHes content in the editor's
   own BBCode shape (`bbcodeToPlutarch`), sidebar, excerpt, icon, category and the
   template boxes from `FIELD_MAP` (`templateBody`). Mentions must carry ids
   (`@[Title](Article:id)`); titles resolve through `lookupTitle` with `loose()`
   matching as the last resort. `api()` retries 429/5xx.

The precise World Anvil facts (field names per template, endpoints, editor quirks,
the 255-char excerpt limit) are recorded in `HANDOFF.md` under "Update 2026-09-02";
check there before re-probing the site.

## Testing changes to `content.js` against the live site

There is no way to reload the extension from the tools. To exercise new code before
the user reloads: build a harness that inlines `content.js` with `chrome.storage`
replaced by an in-page shim and a few articles embedded, inject it with the Chrome
`javascript_tool` (fetching from localhost hangs on Edge's local-network permission,
so inline it), then drive the panel through its shadow root. Only create articles the
import needs anyway; never delete on the user's behalf.

## Ground rules from the project

- No worldbuilding data in the repo: `.gitignore` refuses `data/`, `out/`, `*.map`,
  `*.zip`. Tests use the invented fixtures in `tests/fixtures/`.
- Keep `INSTRUCTIONS.txt` in plain language and in sync with what the panel does; the
  user is a non-programmer and works from it.
- `README.md` in the repo still describes the extension as "you press Save"
  heuristics; it predates 1.1.0 and should be updated when next touched.
