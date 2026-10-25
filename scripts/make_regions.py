#!/usr/bin/env python3
"""Cut a country into regions from its admin boundaries and add them to regions.json.

  make_regions.py <regions.json> <extract-id> <source-url> <extract.osm.pbf> <country> <iso-prefix> \
      <admin-level> [--also ISO ...]

Takes every boundary relation with that admin_level whose ISO3166-2 starts with <iso-prefix>
(e.g. level 6 and "IT-" gives the Italian provinces) and records its id, a short name and its
bounding box. `--also` adds boundaries of any level by exact ISO code - for the places the
level skips, like Valle d'Aosta, which is a region with no provinces under it.

The region id is `<country>-<iso suffix>` in lower case (it-mi), stable even when OSM renames
a place. The bounding box is what the app uses to decide whether a graph covers a route, and
the extract is cut by it, so neighbouring regions overlap a little: that overlap is the seam
the app joins them on.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

PREFIXES = re.compile(
    r"^(Provincia autonoma di|Provincia autonoma|Provincia di|Provincia del|Provincia della|"
    r"Provincia dell'|Città metropolitana di|Città metropolitana del|Città metropolitana della|"
    r"Libero consorzio comunale di|Libero consorzio comunale|Região Autónoma d[aeo]s?|Distrito d[aeo]s?)\s*",
    re.IGNORECASE)


ISLAND_SHARE = 0.1


def short_name(name):
    name = re.sub(r"\s+Capitale$", "", PREFIXES.sub("", name.strip()))
    for separator in (" - ", " / ", "/"):
        name = name.split(separator)[0]
    return name.strip()


def main(regions_file, extract_id, source, pbf, country, prefix, level, also):
    with tempfile.TemporaryDirectory() as tmp:
        boundaries = os.path.join(tmp, "boundaries.osm.pbf")
        subprocess.run(["osmium", "tags-filter", pbf, "r/boundary=administrative", "-o", boundaries,
                        "--overwrite"], check=True)
        # Regions of the wanted level only keep their ISO-coded relations; dropping the rest makes the export small.
        coded = os.path.join(tmp, "coded.osm.pbf")
        subprocess.run(["osmium", "tags-filter", boundaries, f"r/admin_level={level}", "r/ISO3166-2",
                        "-o", coded, "--overwrite"], check=True)
        export = subprocess.run(
            ["osmium", "export", coded, "-f", "geojsonseq", "--geometry-types=polygon",
             "-a", "type,id", "-x", "print_record_separator=false", "-o", "-"],
            check=True, capture_output=True, text=True).stdout
    found = {}
    for line in export.splitlines():
        if not line.strip():
            continue
        feature = json.loads(line)
        props = feature.get("properties", {})
        iso = props.get("ISO3166-2", "")
        wanted = (props.get("admin_level") == str(level) and iso.startswith(prefix)) or iso in also
        if not wanted or props.get("@type") != "relation":
            continue
        geometry = feature["geometry"]
        polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
        boxes = []
        for polygon in polygons:
            outer = polygon[0]
            xs = [point[0] for point in outer]
            ys = [point[1] for point in outer]
            boxes.append((min(xs), min(ys), max(xs), max(ys)))
        # A far island (Lampedusa under Agrigento) would stretch the box across the sea and over
        # other regions' roads: keep the parts that are a real share of the largest one.
        biggest = max((b[2] - b[0]) * (b[3] - b[1]) for b in boxes)
        kept = [b for b in boxes if (b[2] - b[0]) * (b[3] - b[1]) >= ISLAND_SHARE * biggest]
        xs = [b[0] for b in kept] + [b[2] for b in kept]
        ys = [b[1] for b in kept] + [b[3] for b in kept]
        found[iso] = {
            "id": f"{country.lower()}-{iso.split('-', 1)[1].lower()}",
            "name": short_name(props.get("name:it") or props.get("name", iso)),
            "country": country.upper(),
            "relation": int(props["@id"]),
            "bbox": {"west": min(xs), "south": min(ys), "east": max(xs), "north": max(ys)},
        }
    regions = [found[iso] for iso in sorted(found)]
    missing = [iso for iso in also if iso not in found]
    if missing:
        print("WARNING: not found:", ", ".join(missing), file=sys.stderr)
    data = json.load(open(regions_file)) if os.path.exists(regions_file) else {"extracts": []}
    data["extracts"] = [e for e in data["extracts"] if e["id"] != extract_id]
    data["extracts"].append({"id": extract_id, "source": source, "regions": regions})
    with open(regions_file, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"{len(regions)} regions for {extract_id}")
    for region in regions:
        print(f"  {region['id']:8s} {region['name']}")


if __name__ == "__main__":
    args = sys.argv[1:]
    also = []
    if "--also" in args:
        i = args.index("--also")
        also = args[i + 1:]
        args = args[:i]
    if len(args) != 7:
        sys.exit(__doc__)
    main(args[0], args[1], args[2], args[3], args[4], args[5], int(args[6]), also)
