#!/usr/bin/env python3
"""Speed cameras and mountain passes of one extract, as files the phone reads instead of Overpass.

  points.py bake  <regions.json> <extract-id> <extract.osm.pbf> <dist-dir> <repo-url> <rev>
  points.py merge <regions.json> <previous points-index.json> <parts-dir> <out>

`bake` writes `<id>-cameras.json` and `<id>-passes.json` and `points-<id>.json` (the extract's
index entry) into <dist-dir>. The files are shaped like an Overpass answer
(`{"elements":[{"id","lat","lon","tags"}]}`), so the apps read them with the parsers they
already use for Overpass, and they carry only the tags those parsers read. Sorted and without
timestamps: the same data gives the same bytes.

The cameras are the nodes an Overpass lookup asked for: `highway=speed_camera`, plus nodes whose
`enforcement` is maxspeed, average_speed or traffic_signals. The passes are named
`mountain_pass=yes` nodes.

`merge` builds points-index.json: an extract whose bake did not run keeps its previous entry.
The entry lists the boxes of the extract's regions, which is what the phone tests its position
against (the box of a whole country can reach far into its neighbours).
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile

CAMERA_FILTERS = ["n/highway=speed_camera", "n/enforcement=maxspeed,average_speed,traffic_signals"]
CAMERA_TAGS = ("highway", "speed_camera", "enforcement", "maxspeed", "direction")
PASS_FILTERS = ["n/mountain_pass=yes"]
PASS_TAGS = ("name", "ele")


def nodes(pbf, filters):
    """[(id, lat, lon, tags)] of the nodes matching any of the osmium tag filters."""
    with tempfile.TemporaryDirectory() as work:
        picked = os.path.join(work, "picked.osm.pbf")
        subprocess.run(["osmium", "tags-filter", "-R", "-o", picked, pbf, *filters],
                       check=True, stdout=subprocess.DEVNULL)
        exported = subprocess.run(
            ["osmium", "export", "-f", "geojsonseq", "--geometry-types=point",
             "-a", "type,id", picked, "-o", "-"],
            check=True, capture_output=True, text=True).stdout
    found = []
    for line in exported.splitlines():
        line = line.strip("\x1e").strip()
        if not line:
            continue
        feature = json.loads(line)
        props = feature["properties"]
        if props.get("@type") != "node":
            continue
        lon, lat = feature["geometry"]["coordinates"]
        tags = {k: v for k, v in props.items() if not k.startswith("@")}
        found.append((int(props["@id"]), round(lat, 7), round(lon, 7), tags))
    return found


def elements(found, keep):
    out = []
    for node_id, lat, lon, tags in sorted(found, key=lambda n: (n[1], n[2], n[0])):
        kept = {k: tags[k] for k in keep if tags.get(k)}
        out.append({"id": node_id, "lat": lat, "lon": lon, "tags": kept})
    return out


def write(path, elements_):
    data = json.dumps({"elements": elements_}, ensure_ascii=False, separators=(",", ":")).encode()
    with open(path, "wb") as f:
        f.write(data)
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "count": len(elements_)}


def bake(regions_file, extract_id, pbf, dist, repo_url, rev):
    extract = next(e for e in json.load(open(regions_file))["extracts"] if e["id"] == extract_id)
    os.makedirs(dist, exist_ok=True)
    cameras = elements(nodes(pbf, CAMERA_FILTERS), CAMERA_TAGS)
    # An unnamed saddle is a dot nobody can use; the apps drop it too.
    passes = [e for e in elements(nodes(pbf, PASS_FILTERS), PASS_TAGS) if e["tags"].get("name", "").strip()]
    entry = {
        "id": extract_id,
        "rev": rev,
        "countries": sorted({r["country"] for r in extract["regions"]}),
        "boxes": [{k: r["bbox"][k] for k in ("south", "west", "north", "east")} for r in extract["regions"]],
    }
    for kind, found in (("cameras", cameras), ("passes", passes)):
        name = f"{extract_id}-{kind}.json"
        entry[kind] = {"url": f"{repo_url}/releases/download/points/{name}", **write(os.path.join(dist, name), found)}
    with open(os.path.join(dist, f"points-{extract_id}.json"), "w") as f:
        json.dump(entry, f, indent=2)
        f.write("\n")
    print(f"{extract_id}: {entry['cameras']['count']} cameras ({entry['cameras']['bytes']} B), "
          f"{entry['passes']['count']} passes ({entry['passes']['bytes']} B)")


def merge(regions_file, previous_file, parts_dir, out_file):
    previous = {}
    if os.path.exists(previous_file):
        previous = {e["id"]: e for e in json.load(open(previous_file))["extracts"]}
    merged = []
    for extract in json.load(open(regions_file))["extracts"]:
        part = os.path.join(parts_dir, f"points-{extract['id']}.json")
        if os.path.exists(part):
            merged.append(json.load(open(part)))
        elif extract["id"] in previous:
            merged.append(previous[extract["id"]])
    with open(out_file, "w") as f:
        json.dump({"schema": 1, "extracts": merged}, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"{len(merged)} extracts")


if __name__ == "__main__":
    if len(sys.argv) == 8 and sys.argv[1] == "bake":
        bake(*sys.argv[2:])
    elif len(sys.argv) == 6 and sys.argv[1] == "merge":
        merge(*sys.argv[2:])
    else:
        sys.exit(__doc__)
