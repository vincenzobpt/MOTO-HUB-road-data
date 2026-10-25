#!/usr/bin/env python3
"""Put the extract entries planned by plan_country.py into regions.json.

  merge_plans.py <regions.json> <plan.json> [<plan.json> ...]

An extract already in regions.json is replaced; new ones are added in the order given. The
`nodes` estimate is kept for review. Prints one line per extract.
"""
import json
import sys


def main(regions_file, plans):
    data = json.load(open(regions_file))
    for path in plans:
        entry = json.load(open(path))
        if not entry["regions"]:
            print(f"{entry['id']}: no regions planned - left out")
            continue
        data["extracts"] = [e for e in data["extracts"] if e["id"] != entry["id"]] + [entry]
        print(f"{entry['id']}: {len(entry['regions'])} regions")
    with open(regions_file, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2:])
