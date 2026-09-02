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
    """Name the collection a JSON array belongs to, from its record keys."""
    keys: set[str] = set()
    for item in records:
        if isinstance(item, dict) and not item.get("removed"):
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

    def add_mask(m: re.Match) -> str:
        return m.group(0) if "mask=" in m.group(0) else (
            m.group(0)[:-1] + ' mask="url(#land)">'
        )

    svg = re.sub(r'<g id="landmass"[^>]*>', add_mask, svg, count=1)

    # Coastlines are stroke-only groups; without an explicit fill they inherit
    # black and flood every landmass.
    for group in ("sea_island", "lake_island"):
        svg = re.sub(
            rf'<g id="{group}"((?:(?!fill=)[^>])*)>',
            lambda m: f'<g id="{group}"{m.group(1)} fill="none">',
            svg,
            count=1,
        )
    return svg


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


def find_chromium() -> str | None:
    """An installed Chromium, if one is somewhere Playwright will not look.

    Playwright only accepts the browser build it was compiled against, so a
    system or pre-provisioned Chromium has to be passed as ``executable_path``.
    """
    candidates = [os.environ.get("CHROMIUM_PATH", "")]
    browsers = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))
    candidates.append(str(browsers / "chromium"))
    candidates.extend(
        str(p) for p in sorted(browsers.glob("chromium-*/chrome-linux/chrome"))
    )
    candidates.extend(
        shutil.which(n) or "" for n in ("chromium", "chromium-browser", "google-chrome")
    )
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def render_png(svg: str, out_path: Path, scale: int = 4) -> Path:
    """Rasterise the map with Chromium via Playwright.

    ``scale`` multiplies the SVG's native size; the FMG export is only ~1500px
    wide, which is too small to use as a World Anvil map layer.
    """
    from playwright.sync_api import sync_playwright

    m = re.search(r'width="(\d+)"\s+height="(\d+)"', svg)
    if not m:
        raise ValueError("SVG has no width/height attributes")
    width, height = int(m.group(1)), int(m.group(2))

    page_html = (
        "<!doctype html><meta charset='utf-8'>"
        "<style>html,body{margin:0;padding:0;background:#466eab}"
        "svg{display:block}</style>" + svg
    )
    html_path = out_path.with_suffix(".html")
    html_path.write_text(page_html, encoding="utf-8")

    launch: dict = {}
    executable = find_chromium()
    if executable:
        launch["executable_path"] = executable

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(**launch)
            try:
                page = browser.new_page(
                    viewport={"width": width, "height": height},
                    device_scale_factor=scale,
                )
                page.goto(html_path.as_uri())
                page.wait_for_timeout(1500)  # let webfonts and filters settle
                page.screenshot(path=str(out_path), full_page=False)
            finally:
                browser.close()
    finally:
        html_path.unlink(missing_ok=True)

    return out_path
