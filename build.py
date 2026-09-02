#!/usr/bin/env python3
"""Convert the Fantasia Archive export and the Azgaar map into import-ready output.

    python build.py                      # everything into ./out
    python build.py --no-png             # skip the map render
    python build.py --scale 6            # bigger map image

The GUI (``python gui.py``) does the same thing and can upload the result.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from elaris_import.pipeline import PipelineError, convert

ROOT = Path(__file__).parent
DEFAULT_EXPORT = ROOT / "data" / "Elaris - Export"
DEFAULT_MAP = ROOT / "data" / "Lenyhaha.map"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--export", type=Path, default=DEFAULT_EXPORT,
                        help="Fantasia Archive export folder or .zip")
    parser.add_argument("--map", dest="map_path", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--out", type=Path, default=ROOT / "out")
    parser.add_argument("--scale", type=int, default=4,
                        help="map upscale factor over the SVG's native size")
    parser.add_argument("--no-png", action="store_true",
                        help="skip rasterising the map")
    args = parser.parse_args()

    export = args.export if args.export.exists() else None
    map_path = args.map_path if args.map_path.exists() else None
    if export is None:
        print(f"no export at {args.export}, skipping articles", file=sys.stderr)
    if map_path is None:
        print(f"no map at {args.map_path}, skipping", file=sys.stderr)

    try:
        convert(export, map_path, args.out,
                scale=args.scale, render_png=not args.no_png)
    except PipelineError as exc:
        sys.exit(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
