#!/usr/bin/env python3
"""Bake every region in regions.json from one OSM extract and package it for the app.

  build_release.py <regions.json> <pbf-dir> <dist-dir> <repo-url> [--previous index.json] [--only ID]

<pbf-dir> holds one `<extract-id>.osm.pbf` per extract named in regions.json. Writes `<id>.graph.gz` for each region and `index.json` next to them. The gzip carries no
timestamp, so an unchanged graph keeps its SHA-256; with `--previous`, a region whose SHA-256
did not change keeps its old `rev`, and only regions whose roads really changed are offered
to the app as an update. Prints the ids that changed, one per line, on the last output line
prefixed `CHANGED:` (empty when nothing changed). A region whose graph differs from the published
one by less than SIGNIFICANT (size) and has the same bounds keeps its published file and entry: OSM
edits constantly, and a 20 MB download for a few corrected roads is not an update worth offering.
Files are served from `<repo-url>/releases/download/<release>/`, the release named per extract.
"""
import gzip
import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bake_graph  # noqa: E402


def extract_date(pbf):
    """The extract's own timestamp (YYYY-MM-DD): the data's age, not the day the job ran."""
    out = subprocess.run(
        ["osmium", "fileinfo", "-g", "header.option.osmosis_replication_timestamp", pbf],
        check=True, capture_output=True, text=True).stdout.strip()
    return out[:10]


def read_bounds(path):
    with open(path, "rb") as f:
        magic, version = struct.unpack(">ii", f.read(8))
        assert magic == bake_graph.MAGIC and version == 1, f"{path}: not an MHRG v1 file"
        south, west, north, east = struct.unpack(">4d", f.read(32))
    return {"south": south, "west": west, "north": north, "east": east}


SIGNIFICANT = 0.015


def same_bounds(a, b):
    return all(abs(a[k] - b[k]) < 1e-6 for k in ("south", "west", "north", "east"))


def main(regions_file, pbf_dir, dist, repo_url, previous_file, only):
    extracts = [e for e in json.load(open(regions_file))["extracts"] if not only or e["id"] == only]
    previous = {}
    if previous_file and os.path.exists(previous_file):
        previous = {r["id"]: r for r in json.load(open(previous_file))["regions"]}
    os.makedirs(dist, exist_ok=True)
    index, changed = [], []
    with tempfile.TemporaryDirectory() as tmp:
        for extract in extracts:
            pbf = os.path.join(pbf_dir, extract["id"] + ".osm.pbf")
            rev = extract_date(pbf)
            base_url = f"{repo_url}/releases/download/{extract.get('release') or 'graphs-' + extract['id']}"
            # One pass over the whole extract; every region is then cut from the small roads-only file.
            roads = os.path.join(tmp, extract["id"] + "-roads.osm.pbf")
            bake_graph.filter_roads(pbf, roads)
            for region in extract["regions"]:
                b = region["bbox"]
                graph = os.path.join(tmp, region["id"] + ".graph")
                bake_graph.bake(roads, b["west"], b["south"], b["east"], b["north"], graph, prefiltered=True)
                target = os.path.join(dist, region["id"] + ".graph.gz")
                with open(graph, "rb") as src, open(target, "wb") as raw:
                    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as out:
                        out.write(src.read())
                with open(target, "rb") as packed:
                    digest = hashlib.sha256(packed.read()).hexdigest()
                old = previous.get(region["id"])
                bounds = read_bounds(graph)
                graph_bytes = os.path.getsize(graph)
                unchanged = old is not None and (
                    old["sha256"] == digest
                    or (same_bounds(old["bounds"], bounds)
                        and abs(graph_bytes - old["graphBytes"]) < SIGNIFICANT * old["graphBytes"]))
                if unchanged:
                    # Keep the published file: the new one is not uploaded and the index does not move.
                    index.append(old)
                    os.remove(graph)
                    os.remove(target)
                    continue
                changed.append(region["id"])
                index.append({
                    "id": region["id"], "name": region["name"], "country": region["country"],
                    "bounds": read_bounds(graph),
                    "downloadBytes": os.path.getsize(target),
                    "graphBytes": os.path.getsize(graph),
                    "sha256": digest,
                    "url": f"{base_url}/{region['id']}.graph.gz",
                    "rev": rev,
                })
                os.remove(graph)
            os.remove(roads)
    with open(os.path.join(dist, "index.json"), "w") as f:
        json.dump({"schema": 1, "regions": index}, f, indent=2)
        f.write("\n")
    print("CHANGED:" + ",".join(changed))


if __name__ == "__main__":
    args = sys.argv[1:]
    previous = None
    only = None
    if "--only" in args:
        i = args.index("--only")
        only = args[i + 1]
        del args[i:i + 2]
    if "--previous" in args:
        i = args.index("--previous")
        previous = args[i + 1]
        del args[i:i + 2]
    if len(args) != 4:
        sys.exit(__doc__)
    main(args[0], args[1], args[2], args[3].rstrip("/"), previous, only)
