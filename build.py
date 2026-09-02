#!/usr/bin/env python3
"""Convert a Fantasia Archive export and/or an Azgaar map into import-ready output.

    python build.py --export "My World - Export.zip" --map world.map
    python build.py --export path/to/unzipped/folder --out converted
    python build.py --map world.map --no-png      # map data only, no render

The GUI (``python gui.py``) does the same thing and can upload the result.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from elaris_import.pipeline import PipelineError, convert


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--export", type=Path,
                        help="Fantasia Archive export folder or .zip")
    parser.add_argument("--map", dest="map_path", type=Path, help="Azgaar .map file")
    parser.add_argument("--out", type=Path, default=Path("out"))
    parser.add_argument("--scale", type=int, default=4,
                        help="map upscale factor over the SVG's native size")
    parser.add_argument("--no-png", action="store_true",
                        help="skip rasterising the map")
    args = parser.parse_args()

    if not args.export and not args.map_path:
        parser.error("give --export and/or --map")
    for label, path in (("--export", args.export), ("--map", args.map_path)):
        if path and not path.exists():
            sys.exit(f"{label}: {path} does not exist")

    try:
        convert(args.export, args.map_path, args.out,
                scale=args.scale, render_png=not args.no_png)
    except PipelineError as exc:
        sys.exit(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
