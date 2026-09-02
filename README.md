# Elaris → World Anvil import

Converts a **Fantasia Archive** markdown export and an **Azgaar Fantasy Map
Generator** `.map` file into World Anvil articles, a usable map image, and
marker coordinates.

World Anvil has no bulk article import. The only programmatic route in is the
Boromir API (v2), and creating an API application requires a Worldbuilder's
Guild membership above Grandmaster rank. This repo covers both cases: it
generates paste-ready article bodies that need no API access, and an uploader
for when API access is available.

## What's in the box

| Path | What it is |
| --- | --- |
| `gui.py` | the desktop app (Tkinter, stdlib only) |
| `extension/` | Chrome/Edge paste assistant for World Anvil's editor |
| `build.py` / `push.py` | the same pipeline on the command line |
| `tests/fixtures/` | a small **invented** export and map the tests run on |

The repository holds no real worldbuilding data. Everything the converter
writes goes to a local `out/` folder (git-ignored), containing:

| Output | What it is |
| --- | --- |
| `bbcode/<template>/*.txt` | one paste-ready article per document |
| `articles.json` | the same articles as Boromir API payloads / extension input |
| `manifest.csv` | title, template, tags, outbound link count, source |
| `dangling-links.txt` | mentions pointing at a title nothing provides |
| `map/elaris.png` | the map at 4× the SVG's size, ready to upload |
| `map/elaris.svg` | the same map, vector, fixed to render standalone |
| `map/burgs.csv` | settlements with pixel and fractional positions |
| `map/states.csv` | political entities with area and population |

## The app

Plain-language steps for non-programmers are in [`INSTRUCTIONS.txt`](INSTRUCTIONS.txt).

`ElarisImporter.exe` is a desktop front end for all of it: point it at your
export zip and your `.map`, press **Convert**, and optionally paste your World
Anvil credentials and press **Convert and upload**.

**Download the bundle from [Releases](../../releases)** —
`ElarisImporter-vX.Y.Z.zip` holds the exe, the extension and the
instructions — tools only, no data. Every push of a `v*` tag
builds and publishes one; a manual run of the
[workflow](../../actions/workflows/build-exe.yml) produces the same zip as an
artifact without publishing a release.

It is a single file with no installer and no Python needed. Settings are
remembered in `%APPDATA%\ElarisImporter\settings.json`; API credentials are
only written there if you tick **Remember credentials on this computer**, and
they are stored in the clear, so leave it unticked on a shared machine.

To run the app from source instead:

```bash
pip install -r requirements.txt
python gui.py
```

## The browser extension

`extension/` is a Manifest V3 Chrome/Edge extension that sits on World Anvil's
pages and fills the open article editor from `articles.json` — title, content,
sidebar, excerpt — when you click **Fill**. You press Save. It submits nothing
itself, which keeps it inside ordinary use of the site and avoids the
Grandmaster gate entirely.

World Anvil's editor markup isn't documented, so fields are found two ways:
heuristics on `name`/`id`/`placeholder` (`article[title]`, `content`, the
largest textarea for the body), and a **pick** mode where you click a field
once and its selector is remembered. Progress (which articles are done) and
picked selectors persist in `chrome.storage.local`.

Install: `chrome://extensions` → Developer mode → Load unpacked → the
`extension` folder. Then click the icon and load `articles.json`.

It is tested by loading it into a real Chromium against mock editor pages —
one with obvious field names, one with obscure ones — covering the popup file
load, heuristic fill, largest-textarea fallback, pick mode, and persistence
across reloads (`tests/test_extension.py`, needs Playwright + a display).

## Command line

```bash
python build.py --export "My World - Export.zip" --map world.map
python build.py --export path/to/unzipped/folder --out converted
python build.py --map world.map --no-png      # map data only, no render
python build.py --export x.zip --scale 6      # bigger map image
```

With API access:

```bash
export WORLDANVIL_APP_KEY=...      # from your approved API application
export WORLDANVIL_AUTH_TOKEN=...   # worldanvil.com → Settings → API Keys

python push.py --list-worlds
python push.py --world <uuid> --dry-run
python push.py --world <uuid>
```

Both the app and `push.py` record every created article in
`out/import-state.json`, keyed by its Fantasia Archive UUID. Re-running updates
those articles in place rather than creating duplicates, so an interrupted run
can just be repeated.

## Building the exe yourself

PyInstaller freezes the interpreter it runs on and cannot cross-compile, so the
Windows build has to happen on Windows — that is what
`.github/workflows/build-exe.yml` uses GitHub's free `windows-latest` runner
for. On your own Windows machine:

```bat
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean elaris-importer.spec
```

The result lands in `dist\ElarisImporter.exe` (~15 MB).

## How the conversion works

### Templates

Fantasia Archive's document types map onto World Anvil's fixed template set
(the `entityClass` enum in the Boromir OpenAPI spec). Locations are refined by
their Fantasia Archive "Location type", so a City becomes a `settlement` and a
Country becomes a `location`:

| Fantasia Archive | World Anvil |
| --- | --- |
| Location/Geography (Country, Continent, Area, Terrain formation) | `location` |
| Location/Geography (City, Town, Village) | `settlement` |
| Location/Geography (Building, Structure) | `landmark` |
| Item, Currency | `item` |
| Occupation/Class | `profession` |
| Species/Race/Flora/Fauna | `species` |
| Organization, School of Magic, Teaching/Religious group | `organization` |
| Language | `language` |
| Skill/Spell/Other (Spell, Blessing, Magical skill) | `spell` |
| Skill/Spell/Other (non-magical) | `article` |
| Character | `person` |
| Resource/Material | `material` |

Currencies go to `item` because World Anvil has no currency template;
religious groups go to `organization` for the same reason.

### Fields

Only the `location` template has its custom fields published in the API spec,
so location-family articles get real template fields (`population`, `areaSize`,
`alternativename`, `naturalresources`, `history`) and everything else is
rendered as BBCode sections inside the generic `content` and `sidebarcontent`.
If a template rejects a field with HTTP 422, re-run with
`push.py --no-template-fields`.

### Links

Cross-references become World Anvil mentions — `@[Article Title]` — which
resolve by title at render time. No UUID bookkeeping is needed, but a mention
whose title doesn't exist renders as plain text. `out/dangling-links.txt` lists
those; in practice they are typos in the source export (a stray trailing
character on a name, a reference to a document that was never written).

### The map

The `.map` file is line-delimited: a header, some JSON blobs, the rendered SVG,
then one JSON array per entity collection. Collections are identified by the
keys their records carry rather than by line offset, because the offsets move
between FMG versions.

Two fixes make the embedded SVG render outside the FMG web app: the
`./images/pattern*.png` references are dropped, and `mask="url(#land)"` is
re-attached to the `#landmass` group — FMG applies it at load time, and without
it the landmass rect paints over the whole ocean.

Rasterising goes through a headless Chrome, Edge or Chromium found on the
machine (`--headless --screenshot`), so nothing has to be bundled — Edge is on
every Windows 10/11 install. cairosvg and the other Python SVG renderers were
tried and rejected: this file leans on masks, `<use>` and per-label percentage
font sizes that they scale wrong, producing a cropped map with labels several
hundred pixels tall. If no browser is found the SVG is still written and the
app says so.

The exported SVG only contains the layers that were visible in FMG at export
time. Layers that were switched off — biomes, relief, heightmap, state fills —
are not in the file and cannot be recovered from it. For a richer map image,
turn them on in FMG and save again (or use FMG's own PNG export).

`burgs.csv` gives each settlement's position both in SVG pixels and as a 0–1
fraction of the canvas; the fractional form is what a re-scaled export needs.
World Anvil's marker API shape is not in the published spec, so markers are
left as data to import through the map editor rather than pushed blindly.
