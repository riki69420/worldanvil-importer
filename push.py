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

from elaris_import.pipeline import PipelineError, load_worlds, upload

ROOT = Path(__file__).parent


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--world", help="target world UUID")
    parser.add_argument("--out", type=Path, default=ROOT / "out",
                        help="folder holding articles.json from build.py")
    parser.add_argument("--list-worlds", action="store_true",
                        help="print the authenticated user's worlds and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be sent without calling the API")
    parser.add_argument("--no-template-fields", action="store_true",
                        help="send only generic fields; use this if a template "
                             "rejects a field with HTTP 422")
    args = parser.parse_args()

    articles_path = args.out / "articles.json"
    if not articles_path.exists():
        sys.exit(f"{articles_path} not found — run `python build.py` first.")

    if args.dry_run:
        articles = json.loads(articles_path.read_text(encoding="utf-8"))
        print(f"would send {len(articles)} articles to world {args.world or '<unset>'}")
        for a in articles[:5]:
            print(f"  {a['templateType']:<12} {a['title']}")
        if len(articles) > 5:
            print(f"  ... and {len(articles) - 5} more")
        return 0

    app_key = os.environ.get("WORLDANVIL_APP_KEY", "")
    auth_token = os.environ.get("WORLDANVIL_AUTH_TOKEN", "")
    if not app_key or not auth_token:
        sys.exit("set WORLDANVIL_APP_KEY and WORLDANVIL_AUTH_TOKEN")

    try:
        if args.list_worlds:
            for world in load_worlds(app_key, auth_token):
                print(f"  {world['id']}  {world['title']}")
            return 0

        if not args.world:
            sys.exit("--world is required (find it with --list-worlds)")

        _, _, failed = upload(
            args.out, args.world, app_key, auth_token,
            template_fields=not args.no_template_fields,
        )
    except PipelineError as exc:
        sys.exit(str(exc))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
