"""The conversion and upload pipeline, shared by the CLI and the GUI.

Everything reports progress through a ``log`` callable so the caller decides
where the text goes — stdout for ``build.py``, a text widget for the app.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import azgaar
from .bbcode import build_all, dangling_mentions
from .fa_parse import parse_export
from .wapi import WorldAnvil, WorldAnvilError

Log = Callable[[str], None]

# Folder names Fantasia Archive uses; finding one means we found the export root.
FA_CATEGORIES = {
    "Characters", "Currencies", "Items", "Languages", "Locations-Geography",
    "Occupations-Classes", "Organizations-Other groups", "Resources-Materials",
    "Schools of Magic-Magical groups", "Skills-Spells-Other",
    "Species-Races-Flora-Fauna", "Teachings-Religious groups",
}


class PipelineError(RuntimeError):
    """Something the user can fix, phrased for them rather than for a traceback."""


@dataclass
class ConvertResult:
    out_dir: Path
    article_count: int = 0
    by_template: dict[str, int] = field(default_factory=dict)
    dangling: int = 0
    map_png: Path | None = None
    map_svg: Path | None = None
    burg_count: int = 0
    state_count: int = 0
    warnings: list[str] = field(default_factory=list)


def safe_filename(name: str) -> str:
    """A filename that survives Windows, macOS and Linux."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name).strip(" .")
    return cleaned[:120] or "untitled"


def find_export_root(path: Path) -> Path:
    """The directory that actually holds the Fantasia Archive category folders.

    Accepts the export folder itself, its parent, or anything one level above
    it — an unzipped export usually sits one directory deeper than expected.
    """
    if not path.exists():
        raise PipelineError(f"{path} does not exist")

    candidates = [path, *(p for p in sorted(path.iterdir()) if p.is_dir())]
    for candidate in candidates:
        names = {p.name for p in candidate.iterdir() if p.is_dir()}
        if names & FA_CATEGORIES:
            return candidate
        # Fall back to any directory holding the export's UUID-suffixed files.
        if any(candidate.glob("*/*-[0-9a-f]*-*.md")):
            return candidate

    raise PipelineError(
        f"no Fantasia Archive export found under {path} — expected folders like "
        "'Characters' or 'Locations-Geography'"
    )


def open_export(path: Path, workdir: Path) -> Path:
    """Return the export root, unzipping first when handed a ``.zip``."""
    if path.is_file() and path.suffix.lower() == ".zip":
        target = workdir / "export"
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path) as archive:
            archive.extractall(target)
        return find_export_root(target)
    return find_export_root(path)


def _write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_articles(articles, out: Path) -> None:
    bbcode_dir = out / "bbcode"
    if bbcode_dir.exists():
        shutil.rmtree(bbcode_dir)

    for article in articles:
        folder = bbcode_dir / article.template
        folder.mkdir(parents=True, exist_ok=True)

        blocks = [f"TITLE: {article.title}", f"TEMPLATE: {article.template}",
                  f"TAGS: {article.tags}"]
        if article.excerpt:
            blocks.append(f"EXCERPT: {article.excerpt}")
        if article.template_fields:
            fields = "\n".join(
                f"  {k}: {v}" for k, v in article.template_fields.items()
            )
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


def convert(
    export_path: Path | None,
    map_path: Path | None,
    out_dir: Path,
    *,
    scale: int = 4,
    render_png: bool = True,
    log: Log = print,
) -> ConvertResult:
    """Turn an export and/or a map into everything under ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    result = ConvertResult(out_dir=out_dir)

    with tempfile.TemporaryDirectory() as tmp:
        if export_path:
            log(f"Reading export from {export_path}")
            root = open_export(export_path, Path(tmp))
            docs = parse_export(root)
            if not docs:
                raise PipelineError(f"no Fantasia Archive documents found in {root}")

            articles = build_all(docs)
            _write_articles(articles, out_dir)

            result.article_count = len(articles)
            for a in articles:
                result.by_template[a.template] = result.by_template.get(a.template, 0) + 1

            missing = dangling_mentions(articles)
            lines = [
                f"{title}\n" + "".join(f"    -> {m}\n" for m in names)
                for title, names in sorted(missing.items())
            ]
            (out_dir / "dangling-links.txt").write_text(
                "".join(lines) or "none\n", encoding="utf-8"
            )
            result.dangling = sum(len(v) for v in missing.values())

            log(f"Converted {len(articles)} articles")
            for template, count in sorted(result.by_template.items(), key=lambda kv: -kv[1]):
                log(f"    {count:4}  {template}")
            if result.dangling:
                log(f"    {result.dangling} links point at titles not in the export "
                    "(see dangling-links.txt)")

    if map_path:
        log(f"Reading map from {map_path}")
        map_dir = out_dir / "map"
        map_dir.mkdir(parents=True, exist_ok=True)

        data = azgaar.load(map_path)
        svg = azgaar.clean_svg(data.svg)
        result.map_svg = map_dir / "elaris.svg"
        result.map_svg.write_text(svg, encoding="utf-8")

        burgs = azgaar.burg_markers(data)
        states = azgaar.state_rows(data)
        _write_csv(burgs, map_dir / "burgs.csv")
        _write_csv(states, map_dir / "states.csv")
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
        result.burg_count = len(burgs)
        result.state_count = len(states)
        log(f"Map {data.version}: {data.width}x{data.height}, "
            f"{len(burgs)} settlements, {len(states)} states")

        if render_png:
            try:
                png = azgaar.render_png(svg, map_dir / "elaris.png", scale=scale)
                result.map_png = png
                log(f"    Rendered elaris.png at {scale}x "
                    f"({data.width * scale}x{data.height * scale})")
            except Exception as exc:
                warning = f"PNG render skipped: {exc}"
                result.warnings.append(warning)
                log(f"    {warning}")
                log("    elaris.svg is still there — open it in a browser, or use "
                    "FMG's own PNG export")

    log(f"Done. Output in {out_dir}")
    return result


def load_worlds(app_key: str, auth_token: str) -> list[dict]:
    """The authenticated user's worlds, as ``{id, title}`` dicts."""
    client = WorldAnvil(app_key, auth_token)
    me = client.identity()
    user_id = me.get("id") or me.get("user", {}).get("id")
    if not user_id:
        raise PipelineError("the API did not return a user id for these credentials")
    return [
        {"id": w.get("id"), "title": w.get("title") or "(untitled)"}
        for w in client.worlds(user_id)
    ]


def upload(
    out_dir: Path,
    world_id: str,
    app_key: str,
    auth_token: str,
    *,
    template_fields: bool = True,
    log: Log = print,
    should_stop: Callable[[], bool] = lambda: False,
) -> tuple[int, int, int]:
    """Create or update every article in ``out_dir/articles.json``.

    Returns ``(created, updated, failed)``. Progress is written to
    ``import-state.json`` after every article, so an interrupted or failed run
    resumes instead of duplicating.
    """
    articles_path = out_dir / "articles.json"
    if not articles_path.exists():
        raise PipelineError(f"{articles_path} not found — convert first")

    articles = json.loads(articles_path.read_text(encoding="utf-8"))
    state_path = out_dir / "import-state.json"
    state: dict[str, str] = {}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))

    client = WorldAnvil(app_key, auth_token)
    created = updated = failed = 0

    try:
        for i, article in enumerate(articles, 1):
            if should_stop():
                log("Stopped.")
                break

            payload = {
                "title": article["title"],
                "templateType": article["templateType"],
                "world": {"id": world_id},
                "content": article["content"],
                "excerpt": article["excerpt"],
                "tags": article["tags"],
                "state": "private",
            }
            if article.get("sidebarcontent"):
                payload["sidebarcontent"] = article["sidebarcontent"]
            if template_fields:
                payload.update(article.get("templateFields", {}))

            key = article["_faUuid"]
            label = f"[{i}/{len(articles)}] {article['title']}"
            try:
                if key in state:
                    client.update_article(state[key], payload)
                    updated += 1
                    log(f"{label} — updated")
                else:
                    response = client.create_article(payload)
                    state[key] = response.get("id") or response.get("article", {}).get("id")
                    created += 1
                    log(f"{label} — created")
            except WorldAnvilError as exc:
                failed += 1
                log(f"{label} — FAILED: {exc}")
    finally:
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    log(f"Created {created}, updated {updated}, failed {failed}.")
    return created, updated, failed
