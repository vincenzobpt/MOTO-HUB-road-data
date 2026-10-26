#!/usr/bin/env python3
"""Build valhalla-index.json from the per-extract parts written by valhalla_regions.py.

  merge_valhalla_index.py <regions.json> <previous valhalla-index.json> <parts-dir> <out>

An extract whose build did not run or failed keeps ALL its previous entries, never some: its
tiles are one build and must not be mixed with another. Order follows regions.json.
"""
import json
import os
import sys


def main(regions_file, previous_file, parts_dir, out_file):
    extracts = json.load(open(regions_file))["extracts"]
    previous = {}
    if os.path.exists(previous_file):
        for entry in json.load(open(previous_file))["regions"]:
            previous.setdefault(entry["tileSet"], []).append(entry)
    merged = []
    for extract in extracts:
        part = os.path.join(parts_dir, f"index-{extract['id']}.json")
        if os.path.exists(part):
            merged += json.load(open(part))["regions"]
        else:
            merged += previous.get(extract["id"], [])
    ids = [r["id"] for r in merged]
    if len(set(ids)) != len(ids):
        sys.exit("duplicate region ids - refusing to write the index")
    for tile_set in {r["tileSet"] for r in merged}:
        if len({r["rev"] for r in merged if r["tileSet"] == tile_set}) != 1:
            sys.exit(f"{tile_set}: regions from more than one build - refusing to write the index")
    with open(out_file, "w") as f:
        json.dump({"schema": 1, "regions": merged}, f, indent=2)
        f.write("\n")
    print(f"{len(merged)} regions")


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    main(*sys.argv[1:])
