#!/usr/bin/env python3
"""Convert the Fantasia Archive export and the Azgaar map into import-ready output.

    python build.py                      # everything into ./out
    python build.py --no-png             # skip Chromium rasterisation

Produces:
    out/articles.json          every article as a Boromir PUT payload
    out/bbcode/<Template>/*.txt paste-ready article bodies
    out/manifest.csv           title, template, tags, source file
    out/dangling-links.txt     mentions with no matching article
    out/map/elaris.svg|.png    the map, standalone and rasterised
    out/map/burgs.csv          settlements with pixel + fractional positions
    out/map/states.csv         the map's political entities
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

from elaris_import import azgaar
from elaris_import.bbcode import build_all, dangling_mentions
from elaris_import.fa_parse import parse_export

ROOT = Path(__file__).parent
DEFAULT_EXPORT = ROOT / "data" / "Elaris - Export"
DEFAULT_MAP = ROOT / "data" / "Lenyhaha.map"


def safe_filename(name: str) -> str:
    """A filename that survives Windows, macOS and Linux."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name).strip(" .")
    return cleaned[:120] or "untitled"


def write_articles(articles, out: Path) -> None:
    bbcode_dir = out / "bbcode"
    for article in articles:
        folder = bbcode_dir / article.template
        folder.mkdir(parents=True, exist_ok=True)

        blocks = [f"TITLE: {article.title}", f"TEMPLATE: {article.template}",
                  f"TAGS: {article.tags}"]
        if article.excerpt:
            blocks.append(f"EXCERPT: {article.excerpt}")
        if article.template_fields:
            fields = "\n".join(f"  {k}: {v}" for k, v in article.template_fields.items())
            blocks.append(f"TEMPLATE FIELDS:\n{fields}")
        if article.sidebar:
            blocks.append(f"--- SIDEBAR ---\n{article.sidebar}")
        blocks.append(f"--- CONTENT ---\n{article.content}")

        path = folder / f"{safe_filename(article.title)}.txt"
        path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")

    payloads = [
        {
            "title": a.title,
            "templateType": a.template,
            "content": a.content,
            "excerpt": a.excerpt,
            "tags": a.tags,
            "sidebarcontent": a.sidebar,
            "templateFields": a.template_fields,
            "_source": a.source,
            "_faUuid": a.fa_uuid,
            "_mentions": a.mentions,
        }
        for a in articles
    ]
    (out / "articles.json").write_text(
        json.dumps(payloads, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    with (out / "manifest.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["title", "template", "tags", "links_out", "source"])
        for a in articles:
            writer.writerow([a.title, a.template, a.tags, len(a.mentions), a.source])


def write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--export", type=Path, default=DEFAULT_EXPORT)
    parser.add_argument("--map", dest="map_path", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--out", type=Path, default=ROOT / "out")
    parser.add_argument("--scale", type=int, default=4,
                        help="PNG upscale factor over the SVG's native size")
    parser.add_argument("--no-png", action="store_true",
                        help="skip Chromium rasterisation of the map")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    if args.export.exists():
        docs = parse_export(args.export)
        articles = build_all(docs)
        write_articles(articles, args.out)

        counts: dict[str, int] = {}
        for a in articles:
            counts[a.template] = counts.get(a.template, 0) + 1
        print(f"{len(articles)} articles from {args.export}")
        for template, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {count:4}  {template}")

        missing = dangling_mentions(articles)
        lines = [
            f"{title}\n" + "".join(f"    -> {m}\n" for m in names)
            for title, names in sorted(missing.items())
        ]
        (args.out / "dangling-links.txt").write_text(
            "".join(lines) or "none\n", encoding="utf-8"
        )
        total = sum(len(v) for v in missing.values())
        print(f"  {total} mentions point at titles not in this export "
              f"(see out/dangling-links.txt)")
    else:
        print(f"no export at {args.export}, skipping articles", file=sys.stderr)

    if args.map_path.exists():
        map_dir = args.out / "map"
        map_dir.mkdir(parents=True, exist_ok=True)
        data = azgaar.load(args.map_path)

        svg = azgaar.clean_svg(data.svg)
        (map_dir / "elaris.svg").write_text(svg, encoding="utf-8")

        burgs = azgaar.burg_markers(data)
        states = azgaar.state_rows(data)
        write_csv(burgs, map_dir / "burgs.csv")
        write_csv(states, map_dir / "states.csv")
        (map_dir / "entities.json").write_text(
            json.dumps(
                {
                    "width": data.width,
                    "height": data.height,
                    "distanceUnit": data.distance_unit,
                    "distanceScale": data.distance_scale,
                    "burgs": burgs,
                    "states": states,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"map {data.version}: {data.width}x{data.height}, "
              f"{len(burgs)} settlements, {len(states)} states")

        if not args.no_png:
            try:
                png = azgaar.render_png(svg, map_dir / "elaris.png", scale=args.scale)
                print(f"  rendered {png.name} at {args.scale}x "
                      f"({data.width * args.scale}x{data.height * args.scale})")
            except Exception as exc:  # rasterisation is optional, never fatal
                print(f"  PNG render failed ({exc}); the SVG is still usable",
                      file=sys.stderr)
    else:
        print(f"no map at {args.map_path}, skipping", file=sys.stderr)

    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
