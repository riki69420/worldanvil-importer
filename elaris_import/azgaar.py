"""Read an Azgaar Fantasy Map Generator ``.map`` file.

The format is line-delimited: a pipe-separated header, a few JSON blobs, the
rendered SVG (pretty-printed across many lines), then one JSON array per entity
collection. Indices are counted from the line after ``</svg>`` because the SVG's
line count varies with map complexity.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Collections are identified by the keys their records carry rather than by a
# fixed line offset, because the offsets shift between FMG versions. Each entry
# is (required keys, forbidden keys); the first signature that matches wins, so
# order matters where signatures overlap.
SIGNATURES: list[tuple[str, set[str], set[str]]] = [
    ("burgs", {"cell", "x", "y", "population", "capital"}, set()),
    ("rivers", {"source", "mouth", "discharge", "basin"}, set()),
    ("provinces", {"formName", "center", "burg"}, set()),
    ("states", {"neighbors", "diplomacy"}, set()),
    ("cultures", {"base", "shield"}, set()),
    ("religions", {"form", "culture", "cells"}, {"base", "shield"}),
    ("features", {"type", "land", "border", "firstCell"}, set()),
    ("zones", {"cells", "type", "color"}, {"land", "form"}),
    ("markers", {"icon", "cell", "type"}, {"population"}),
]


@dataclass
class MapData:
    version: str
    width: int
    height: int
    distance_unit: str
    distance_scale: float
    svg: str
    entities: dict[str, list]

    def named(self, kind: str) -> list[dict]:
        """Entity records that are real, named and not tombstoned.

        FMG keeps deleted entries in place as ``{"i": n, "removed": true}`` and
        pads index 0 with a literal ``0`` so array positions match cell ids.
        """
        out = []
        for item in self.entities.get(kind, []):
            if not isinstance(item, dict) or item.get("removed"):
                continue
            if not item.get("name"):
                continue
            out.append(item)
        return out


def _classify(records: list) -> str | None:
    """Name the collection a JSON array belongs to, from its record keys.

    Removed records still count here: a map whose religions were all deleted
    keeps their tombstones, and those are the only records with the keys that
    identify the collection. ``named()`` is where tombstones get filtered.
    """
    keys: set[str] = set()
    for item in records:
        if isinstance(item, dict):
            keys |= set(item)
    if not keys:
        return None
    for name, required, forbidden in SIGNATURES:
        if required <= keys and not (forbidden & keys):
            return name
    return None


def load(path: Path) -> MapData:
    lines = path.read_text(encoding="utf-8", errors="replace").replace(
        "\r\n", "\n"
    ).split("\n")

    header = lines[0].split("|")
    settings = lines[1].split("|")

    svg_end = max(i for i, line in enumerate(lines) if "</svg>" in line)
    svg_start = next(i for i, line in enumerate(lines) if "<svg" in line)

    entities: dict[str, list] = {}
    for line in lines[svg_end + 1 :]:
        line = line.strip()
        if not line.startswith("["):
            continue
        try:
            records = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = _classify(records)
        # Keep the richest match; FMG writes several arrays of similar shape.
        if kind and len(records) > len(entities.get(kind, [])):
            entities[kind] = records

    return MapData(
        version=header[0],
        width=int(header[4]),
        height=int(header[5]),
        distance_unit=settings[0] or "km",
        distance_scale=float(settings[1] or 1),
        svg="\n".join(lines[svg_start : svg_end + 1]),
        entities=entities,
    )


def clean_svg(svg: str) -> str:
    """Make the SVG render correctly outside the FMG web app.

    Two things break in a standalone viewer:

    * Ocean and texture patterns point at ``./images/pattern*.png``, relative to
      the FMG app. They 404 and leave holes, so the references are dropped.
    * The ``#landmass`` group is exported without ``mask="url(#land)")`` — FMG
      re-applies it when it loads the file. Unmasked, its full-canvas rect
      paints over the ocean and the map comes out as a flat sheet. The ``#land``
      mask itself is present in the defs, so re-attaching it is enough.
    """
    svg = re.sub(r'(xlink:href|href)="\./images/[^"]*"', 'href=""', svg)
    svg = _set_attr(svg, "landmass", "mask", "url(#land)")

    # Coastlines are stroke-only groups; without an explicit fill they inherit
    # black and flood every landmass.
    for group in ("sea_island", "lake_island"):
        svg = _set_attr(svg, group, "fill", "none")

    return svg


def _set_attr(svg: str, group_id: str, attr: str, value: str) -> str:
    """Add an attribute to a ``<g>`` that lacks it, self-closing tags included."""
    pattern = re.compile(rf'<g id="{re.escape(group_id)}"([^>]*?)(/?)>')

    def replace(m: re.Match) -> str:
        attrs, slash = m.group(1), m.group(2)
        if re.search(rf'\b{attr}=', attrs):
            return m.group(0)
        return f'<g id="{group_id}"{attrs.rstrip()} {attr}="{value}"{slash}>'

    return pattern.sub(replace, svg, count=1)


def burg_markers(data: MapData) -> list[dict]:
    """Settlements with their pixel position on the exported SVG.

    ``x``/``y`` are in the SVG's own coordinate space (``width`` x ``height``);
    ``fx``/``fy`` are the same point as a 0-1 fraction, which is what any
    re-scaled export of the map needs.
    """
    states = {s["i"]: s.get("name", "") for s in data.named("states")}
    cultures = {c["i"]: c.get("name", "") for c in data.named("cultures")}
    religions = {r["i"]: r.get("name", "") for r in data.named("religions")}

    markers = []
    for burg in data.named("burgs"):
        markers.append(
            {
                "name": burg["name"],
                "x": round(burg["x"], 2),
                "y": round(burg["y"], 2),
                "fx": round(burg["x"] / data.width, 6),
                "fy": round(burg["y"] / data.height, 6),
                "state": states.get(burg.get("state"), ""),
                "culture": cultures.get(burg.get("culture"), ""),
                "religion": religions.get(burg.get("religion"), ""),
                "population": burg.get("population"),
                "is_capital": bool(burg.get("capital")),
                "is_port": bool(burg.get("port")),
                "type": burg.get("type", ""),
            }
        )
    markers.sort(key=lambda m: m["name"].casefold())
    return markers


def state_rows(data: MapData) -> list[dict]:
    rows = []
    for state in data.named("states"):
        if state.get("name") == "Neutrals":
            continue
        rows.append(
            {
                "name": state["name"],
                "full_name": state.get("fullName") or state["name"],
                "form": state.get("form", ""),
                "burgs": state.get("burgs"),
                "area_cells": state.get("cells"),
                "area": state.get("area"),
                "urban_population": state.get("urban"),
                "rural_population": state.get("rural"),
                "color": state.get("color", ""),
            }
        )
    rows.sort(key=lambda r: r["name"].casefold())
    return rows


class NoBrowserError(RuntimeError):
    """No Chromium-family browser was found to rasterise with."""


# Where a Chrome, Edge or Chromium install actually lives, per platform.
BROWSER_PATHS = {
    "win32": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Chromium\Application\chrome.exe",
    ],
    "darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ],
}

BROWSER_COMMANDS = [
    "chrome", "google-chrome", "google-chrome-stable",
    "chromium", "chromium-browser", "msedge", "microsoft-edge",
]


def find_browser() -> str | None:
    """A Chromium-family browser to rasterise with, or ``None``.

    Chrome, Edge and Chromium all accept ``--headless --screenshot``, so any of
    them will do and no browser has to be shipped alongside the app. Edge is
    present on every Windows 10/11 install, which is what makes this workable
    for a packaged build.
    """
    candidates = [os.environ.get("CHROMIUM_PATH", "")]

    # A Playwright install, if one happens to be present.
    browsers = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))
    if browsers.is_dir():
        candidates.append(str(browsers / "chromium"))
        candidates.extend(str(p) for p in sorted(browsers.glob("chromium-*/*/chrome")))
        candidates.extend(str(p) for p in sorted(browsers.glob("chromium-*/*/chrome.exe")))

    candidates.extend(shutil.which(name) or "" for name in BROWSER_COMMANDS)
    candidates.extend(BROWSER_PATHS.get(sys.platform, []))

    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def render_png(svg: str, out_path: Path, scale: int = 4, timeout: int = 180) -> Path:
    """Rasterise the map by screenshotting it in a headless browser.

    ``scale`` multiplies the SVG's native size; the FMG export is only ~1500px
    wide, which is too small to use as a World Anvil map layer.

    Chromium's own SVG engine is used rather than a Python renderer because the
    FMG export leans on masks, ``<use>`` and per-label transforms that the
    lighter renderers get wrong.
    """
    browser = find_browser()
    if not browser:
        raise NoBrowserError(
            "no Chrome, Edge or Chromium found — install one, or set "
            "CHROMIUM_PATH to its executable"
        )

    m = re.search(r'width="(\d+)"\s+height="(\d+)"', svg)
    if not m:
        raise ValueError("SVG has no width/height attributes")
    width, height = int(m.group(1)), int(m.group(2))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    page = out_path.with_suffix(".render.html")
    page.write_text(
        "<!doctype html><meta charset='utf-8'>"
        "<style>html,body{margin:0;padding:0;background:#466eab}"
        "svg{display:block}</style>" + svg,
        encoding="utf-8",
    )

    # A dedicated profile keeps the run from colliding with the user's own
    # browser session, which otherwise makes headless Chrome exit immediately.
    with tempfile.TemporaryDirectory() as profile:
        command = [
            browser,
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--hide-scrollbars",
            "--virtual-time-budget=5000",
            f"--force-device-scale-factor={scale}",
            f"--window-size={width},{height}",
            f"--user-data-dir={profile}",
            f"--screenshot={out_path}",
            page.as_uri(),
        ]
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            raise NoBrowserError(f"{Path(browser).name} timed out after {timeout}s")
        finally:
            page.unlink(missing_ok=True)

    if not out_path.exists():
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise NoBrowserError(
            f"{Path(browser).name} wrote no image"
            + (f": {detail[-1]}" if detail else "")
        )
    return out_path
