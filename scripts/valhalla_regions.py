#!/usr/bin/env python3
"""Package one extract's Valhalla tiles into one download per region.

  valhalla_regions.py <regions.json> <extract-id> <tiles-dir> <dist-dir> <repo-url> <build-rev>

Valhalla cuts the world into a fixed grid per hierarchy level (level 0: 4 degrees, level 1:
1 degree, level 2: 0.25 degrees). A region's package holds every tile, of every level, whose
square meets the region's box, so routing works anywhere inside it; neighbouring regions share
some tiles, which the phone stores once.

All the tiles of one extract come from one build and refer to each other by id, so tiles from
two builds must never be mixed: every region of an extract carries the extract's `tileSet` and
the build's `rev`, and the phone keeps each (tileSet, rev) in a folder of its own.

Writes `<region-id>.tar.gz` (sorted, no timestamps: the same tiles give the same bytes) and
`index-<extract-id>.json` (the regions' index entries) into <dist-dir>.
"""
import gzip
import hashlib
import io
import json
import os
import sys
import tarfile

LEVEL_SIZE = {0: 4.0, 1: 1.0, 2: 0.25}
FORMAT = "valhalla-3.9.1"


def tile_box(level, tile_id):
    size = LEVEL_SIZE[level]
    columns = int(360 / size)
    row, column = divmod(tile_id, columns)
    west = -180 + column * size
    south = -90 + row * size
    return west, south, west + size, south + size


def built_tiles(tiles_dir):
    """[(relative path, level, (west, south, east, north))] for every .gph under tiles_dir."""
    found = []
    for root, _, files in os.walk(tiles_dir):
        for name in files:
            if not name.endswith(".gph"):
                continue
            relative = os.path.relpath(os.path.join(root, name), tiles_dir)
            level_text, rest = relative.split(os.sep, 1)
            level = int(level_text)
            if level not in LEVEL_SIZE:
                continue  # transit and other levels: not used by road routing
            tile_id = int(rest[:-len(".gph")].replace(os.sep, ""))
            found.append((relative, level, tile_box(level, tile_id)))
    return sorted(found)


def meets(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def package(tiles_dir, paths, target):
    """A gzip tar of [paths], byte-for-byte reproducible."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for path in paths:
            info = tar.gettarinfo(os.path.join(tiles_dir, path), arcname=path.replace(os.sep, "/"))
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o644
            with open(os.path.join(tiles_dir, path), "rb") as data:
                tar.addfile(info, data)
    unpacked = raw.tell()
    with open(target, "wb") as out:
        with gzip.GzipFile(filename="", mode="wb", fileobj=out, compresslevel=9, mtime=0) as gz:
            gz.write(raw.getvalue())
    return unpacked


def main(regions_file, extract_id, tiles_dir, dist, repo_url, rev):
    extract = next(e for e in json.load(open(regions_file))["extracts"] if e["id"] == extract_id)
    release = "valhalla-" + extract_id
    tiles = built_tiles(tiles_dir)
    os.makedirs(dist, exist_ok=True)
    entries = []
    for region in extract["regions"]:
        b = region["bbox"]
        box = (b["west"], b["south"], b["east"], b["north"])
        paths = [path for path, _, tile in tiles if meets(tile, box)]
        if not any(path.startswith("2" + os.sep) for path in paths):
            print(f"{region['id']}: no local tiles - skipped")
            continue
        target = os.path.join(dist, region["id"] + ".tar.gz")
        unpacked = package(tiles_dir, paths, target)
        with open(target, "rb") as packed:
            digest = hashlib.sha256(packed.read()).hexdigest()
        entries.append({
            "id": region["id"], "name": region["name"], "country": region["country"],
            "bounds": {"south": b["south"], "west": b["west"], "north": b["north"], "east": b["east"]},
            "downloadBytes": os.path.getsize(target),
            "graphBytes": unpacked,
            "sha256": digest,
            "url": f"{repo_url}/releases/download/{release}/{region['id']}.tar.gz",
            "rev": rev,
            "tileSet": extract_id,
            "tiles": len(paths),
            "format": FORMAT,
        })
        print(f"{region['id']}: {len(paths)} tiles, {unpacked / 1e6:.1f} MB -> {os.path.getsize(target) / 1e6:.1f} MB gz")
    with open(os.path.join(dist, f"index-{extract_id}.json"), "w") as f:
        json.dump({"regions": entries}, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    if len(sys.argv) != 7:
        sys.exit(__doc__)
    main(*sys.argv[1:6], sys.argv[6])
