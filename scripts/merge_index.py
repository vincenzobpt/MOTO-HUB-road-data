#!/usr/bin/env python3
"""Build the app's index.json from what each extract's bake produced.

  merge_index.py <regions.json> <previous index.json> <parts-dir> <out index.json>

`parts-dir` holds `part-<extract-id>.json` (the index entries of one extract). An extract whose
bake did not run or failed keeps its entries from the previous index, so one broken country never
empties the list. Entries of regions no longer in regions.json are dropped. Order follows
regions.json.
"""
import json
import os
import sys


def main(regions_file, previous_file, parts_dir, out_file):
    extracts = json.load(open(regions_file))["extracts"]
    previous = {}
    if os.path.exists(previous_file):
        previous = {r["id"]: r for r in json.load(open(previous_file))["regions"]}
    merged = []
    for extract in extracts:
        part_path = os.path.join(parts_dir, f"part-{extract['id']}.json")
        part = {}
        if os.path.exists(part_path):
            part = {r["id"]: r for r in json.load(open(part_path))["regions"]}
        for region in extract["regions"]:
            entry = part.get(region["id"]) or previous.get(region["id"])
            if entry:
                merged.append(entry)
    with open(out_file, "w") as f:
        json.dump({"schema": 1, "regions": merged}, f, indent=2)
        f.write("\n")
    print(f"{len(merged)} regions")


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    main(*sys.argv[1:])
