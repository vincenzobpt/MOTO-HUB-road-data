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

`bake` also writes the map's points of interest: one gzipped `<region>-pois.json.gz` per region
of regions.json (a whole country is tens of MB of JSON; a region, compressed, is a few hundred KB
to about 1.5 MB) and `pois-<id>.json`, the regions' index entries; `merge` builds pois-index.json
from those. Kept: the tags of every POI category either app offers, except the ones that came
with Google's place icons (shops, parks, schools, sports, beauty). A place without a name is kept
only where a rider still wants it (water, toilets, a viewpoint, a fuel station...): unnamed car
parks and signposts are most of the raw data and nobody stops for them.
"""
import gzip
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


POI_FILTERS = [
    "nwr/amenity=fuel,restaurant,fast_food,ice_cream,cafe,bar,pub,biergarten,parking,charging_station,"
    "pharmacy,hospital,clinic,atm,bank,toilets,drinking_water,place_of_worship,police,fire_station,post_office",
    "nwr/tourism=hotel,guest_house,hostel,motel,chalet,alpine_hut,viewpoint,attraction,museum,artwork,gallery,"
    "theme_park,zoo,camp_site,caravan_site,picnic_site,information",
    "nwr/shop=motorcycle,motorcycle_repair,car_repair,tyres,supermarket,convenience,bakery,pastry,confectionery,bicycle",
    "nwr/historic=castle,fort,fortress,ruins,monastery,archaeological_site,monument,tower,manor,city_gate",
    "nwr/natural=beach,spring,peak,cave_entrance,arch",
    "nwr/waterway=waterfall",
    "nwr/highway=rest_area,services",
    "nwr/leisure=picnic_table",
    "nwr/aeroway=aerodrome",
]
POI_KEYS = ("amenity", "tourism", "shop", "historic", "natural", "waterway", "highway", "leisure", "aeroway")
POI_TAGS = POI_KEYS + ("name", "brand", "operator", "opening_hours", "phone", "website", "wikidata",
                       "information", "motorcycle")
UNNAMED_OK = {
    ("amenity", "fuel"), ("amenity", "drinking_water"), ("amenity", "toilets"), ("amenity", "atm"),
    ("amenity", "charging_station"), ("tourism", "viewpoint"), ("tourism", "picnic_site"),
    ("leisure", "picnic_table"), ("highway", "rest_area"), ("highway", "services"),
    ("natural", "spring"), ("natural", "beach"), ("natural", "cave_entrance"), ("natural", "arch"),
    ("waterway", "waterfall"),
}


def poi_wanted(tags):
    """Whether a matched object is worth a pin: see the module doc."""
    if tags.get("tourism") == "information":
        return tags.get("information") in ("office", "visitor_centre")
    if tags.get("name", "").strip() or tags.get("brand", "").strip():
        return True
    if tags.get("amenity") == "parking":
        return tags.get("motorcycle") in ("yes", "designated")
    return any((key, tags.get(key)) in UNNAMED_OK for key in POI_KEYS)


def representative(geometry):
    """One point for a node, a way or an area: the vertex average is enough for a pin."""
    kind, coords = geometry["type"], geometry["coordinates"]
    if kind == "Point":
        return coords
    if kind == "LineString":
        return coords[len(coords) // 2]
    ring = coords[0] if kind == "Polygon" else coords[0][0]
    ring = ring[:-1] or ring
    return [sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)]


def pois(pbf):
    """[(type, id, lat, lon, tags)] of the points of interest of the extract."""
    found, seen = [], set()
    with tempfile.TemporaryDirectory() as work:
        picked = os.path.join(work, "pois.osm.pbf")
        subprocess.run(["osmium", "tags-filter", "-o", picked, pbf, *POI_FILTERS],
                       check=True, stdout=subprocess.DEVNULL)
        # Read line by line: a large country exports hundreds of MB, mostly car parks.
        export = subprocess.Popen(
            ["osmium", "export", "-f", "geojsonseq", "-a", "type,id", picked, "-o", "-"],
            stdout=subprocess.PIPE, text=True)
        for line in export.stdout:
            line = line.strip("\x1e").strip()
            if not line:
                continue
            feature = json.loads(line)
            props = feature["properties"]
            tags = {k: v for k, v in props.items() if not k.startswith("@")}
            if not any(tags.get(key) for key in POI_KEYS) or not poi_wanted(tags):
                continue
            identity = (props.get("@type"), props.get("@id"))
            if identity in seen:
                continue
            seen.add(identity)
            lon, lat = representative(feature["geometry"])
            kept = {k: tags[k] for k in POI_TAGS if tags.get(k)}
            found.append((props.get("@type"), int(props["@id"]), round(lat, 7), round(lon, 7), kept))
        if export.wait() != 0:
            sys.exit(f"osmium export failed ({export.returncode})")
    return found


def bake_pois(extract, pbf, dist, repo_url, rev):
    found = sorted(pois(pbf), key=lambda p: (p[2], p[3], p[0], p[1]))
    regions = []
    for region in extract["regions"]:
        box = region["bbox"]
        inside = [{"type": t, "id": i, "lat": lat, "lon": lon, "tags": tags}
                  for t, i, lat, lon, tags in found
                  if box["south"] <= lat <= box["north"] and box["west"] <= lon <= box["east"]]
        # Gzipped: a city region is several MB of JSON and about a fifth of that compressed.
        name = f"{region['id']}-pois.json.gz"
        regions.append({
            "id": region["id"],
            "extract": extract["id"],
            "rev": rev,
            "boxes": [{k: box[k] for k in ("south", "west", "north", "east")}],
            "pois": {"url": f"{repo_url}/releases/download/pois-{extract['id']}/{name}",
                     **write(os.path.join(dist, "pois", name), inside, compress=True)},
        })
    with open(os.path.join(dist, f"pois-{extract['id']}.json"), "w") as f:
        json.dump({"regions": regions}, f, indent=2, ensure_ascii=False)
        f.write("\n")
    sizes = sorted(r["pois"]["bytes"] for r in regions)
    print(f"{extract['id']}: {len(found)} pois in {len(regions)} regions, "
          f"largest {sizes[-1] // 1024} KB, total {sum(sizes) // 1024} KB")


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


def write(path, elements_, compress=False):
    data = json.dumps({"elements": elements_}, ensure_ascii=False, separators=(",", ":")).encode()
    if compress:
        # mtime 0: the same data gives the same bytes, so an unchanged region keeps its sha256.
        data = gzip.compress(data, compresslevel=9, mtime=0)
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
    os.makedirs(os.path.join(dist, "pois"), exist_ok=True)
    bake_pois(extract, pbf, dist, repo_url, rev)


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

    # The regions' POI files, beside it: same rule, an extract that did not run keeps its regions.
    pois_previous_file = os.path.join(os.path.dirname(previous_file) or ".", "pois-index.json")
    pois_previous = {}
    if os.path.exists(pois_previous_file):
        for entry in json.load(open(pois_previous_file))["regions"]:
            pois_previous.setdefault(entry["extract"], []).append(entry)
    pois_merged = []
    for extract in json.load(open(regions_file))["extracts"]:
        part = os.path.join(parts_dir, f"pois-{extract['id']}.json")
        pois_merged += json.load(open(part))["regions"] if os.path.exists(part) else pois_previous.get(extract["id"], [])
    pois_out = os.path.join(os.path.dirname(out_file) or ".", os.path.basename(out_file).replace("points-index", "pois-index"))
    with open(pois_out, "w") as f:
        json.dump({"schema": 1, "regions": pois_merged}, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"{len(pois_merged)} POI regions")


if __name__ == "__main__":
    if len(sys.argv) == 8 and sys.argv[1] == "bake":
        bake(*sys.argv[2:])
    elif len(sys.argv) == 6 and sys.argv[1] == "merge":
        merge(*sys.argv[2:])
    else:
        sys.exit(__doc__)
