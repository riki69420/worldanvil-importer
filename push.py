#!/usr/bin/env python3
"""Push the generated articles into a World Anvil world over the Boromir API.

    export WORLDANVIL_APP_KEY=...      # from your approved API application
    export WORLDANVIL_AUTH_TOKEN=...   # worldanvil.com -> Settings -> API Keys

    python push.py --list-worlds
    python push.py --world <world-uuid> --dry-run
    python push.py --world <world-uuid>

The run is resumable: every created article is recorded in
``out/import-state.json`` keyed by its Fantasia Archive UUID, so re-running
updates those articles in place instead of creating duplicates.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from elaris_import.wapi import WorldAnvil, WorldAnvilError

ROOT = Path(__file__).parent


def load_articles(path: Path) -> list[dict]:
    if not path.exists():
        sys.exit(f"{path} not found — run `python build.py` first.")
    return json.loads(path.read_text(encoding="utf-8"))


def to_payload(article: dict, world_id: str, template_fields: bool) -> dict:
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
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--world", help="target world UUID")
    parser.add_argument("--articles", type=Path, default=ROOT / "out" / "articles.json")
    parser.add_argument("--state", type=Path, default=ROOT / "out" / "import-state.json")
    parser.add_argument("--list-worlds", action="store_true",
                        help="print the authenticated user's worlds and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be sent without calling the API")
    parser.add_argument("--only", action="append", default=[],
                        help="restrict to a templateType (repeatable)")
    parser.add_argument("--limit", type=int, help="stop after N articles")
    parser.add_argument("--no-template-fields", action="store_true",
                        help="send only generic fields; use this if a template "
                             "rejects a field with HTTP 422")
    args = parser.parse_args()

    app_key = os.environ.get("WORLDANVIL_APP_KEY", "")
    auth_token = os.environ.get("WORLDANVIL_AUTH_TOKEN", "")

    articles = load_articles(args.articles)
    if args.only:
        articles = [a for a in articles if a["templateType"] in args.only]
    if args.limit:
        articles = articles[: args.limit]

    if args.dry_run:
        print(f"would send {len(articles)} articles to world {args.world or '<unset>'}")
        for a in articles[:5]:
            print(f"  {a['templateType']:<12} {a['title']}")
        if len(articles) > 5:
            print(f"  ... and {len(articles) - 5} more")
        return 0

    if not app_key or not auth_token:
        sys.exit("set WORLDANVIL_APP_KEY and WORLDANVIL_AUTH_TOKEN")

    client = WorldAnvil(app_key, auth_token)

    if args.list_worlds:
        me = client.identity()
        user_id = me.get("id") or me.get("user", {}).get("id")
        print(f"authenticated as {me.get('username', user_id)}")
        for world in client.worlds(user_id):
            print(f"  {world.get('id')}  {world.get('title')}")
        return 0

    if not args.world:
        sys.exit("--world is required (find it with --list-worlds)")

    state: dict[str, str] = {}
    if args.state.exists():
        state = json.loads(args.state.read_text(encoding="utf-8"))

    created = updated = failed = 0
    try:
        for i, article in enumerate(articles, 1):
            key = article["_faUuid"]
            payload = to_payload(article, args.world, not args.no_template_fields)
            label = f"[{i}/{len(articles)}] {article['title']}"
            try:
                if key in state:
                    client.update_article(state[key], payload)
                    updated += 1
                    print(f"{label} — updated")
                else:
                    result = client.create_article(payload)
                    state[key] = result.get("id", result.get("article", {}).get("id"))
                    created += 1
                    print(f"{label} — created {state[key]}")
            except WorldAnvilError as exc:
                failed += 1
                print(f"{label} — FAILED: {exc}", file=sys.stderr)
    finally:
        # Always persist, so an interrupted run stays resumable.
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(json.dumps(state, indent=2), encoding="utf-8")

    print(f"\ncreated {created}, updated {updated}, failed {failed}")
    print(f"state written to {args.state}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
