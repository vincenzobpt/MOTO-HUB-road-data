#!/usr/bin/env python3
"""Cut an extract into tiles that each fit a phone, and name them after the biggest place inside.

  plan_country.py <extract.osm.pbf> <extract-id> <source-url> <release-tag> <out.json> \
      <CC>[=<Country name>] [<CC>[=<Country name>] ...]

Admin boundaries are not used to cut: their levels differ from country to country, some cover
only part of a country, and one province can hold a hundred times the roads of another. Instead
the extract's own bounding box is halved along its longer side, over and over, until every tile
holds at most CAP_NODES road nodes (a graph of about 45 MB). Tiles over the sea or empty land are
dropped. The tiles keep a little overlap (PAD): that overlap is the seam the app joins graphs on.

A tile is named "<largest city or town inside> area"; a country that fits in one tile is named
after the country. With several country codes (an extract that holds more than one country) each
tile gets the code whose border contains its centre, and the first code if none does.

Writes the extract entry for regions.json to <out.json> and prints a summary.
"""
import json
import math
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bake_graph  # noqa: E402

CAP_NODES = 1_100_000
MIN_TILE_NODES = 5_000
PAD = 0.01
MAX_DEPTH = 12
RING_SAMPLE = 20_000


def sh(*args):
    subprocess.run(args, check=True)


def count_nodes(pbf):
    out = subprocess.run(["osmium", "fileinfo", "-e", "-g", "data.count.nodes", pbf],
                         check=True, capture_output=True, text=True).stdout.strip()
    return int(out)


def extract_box(pbf):
    """(west, south, east, north) from the extract's header: Geofabrik cuts it to the country."""
    out = subprocess.run(["osmium", "fileinfo", "-g", "header.boxes", pbf],
                         check=True, capture_output=True, text=True).stdout.strip()
    west, south, east, north = map(float, out.strip("()").split(",")[:4])
    return west, south, east, north


def measure(roads, box, tmp):
    """Nodes a graph cut with this box would hold (the cut keeps only nodes of whole ways)."""
    cut = os.path.join(tmp, "measure.osm.pbf")
    west, south, east, north = box
    sh("osmium", "extract", "-b", f"{west},{south},{east},{north}", "--strategy=complete_ways",
       roads, "-o", cut, "--overwrite")
    return count_nodes(cut)


def padded(box):
    west, south, east, north = box
    return (round(west - PAD, 7), round(south - PAD, 7), round(east + PAD, 7), round(north + PAD, 7))


def tiles_of(roads, core, tmp, depth=0):
    """[(core box, padded box, nodes)]: halve the core along its longer side until each tile fits."""
    box = padded(core)
    nodes = measure(roads, box, tmp)
    if nodes < MIN_TILE_NODES:
        return []
    if nodes <= CAP_NODES or depth >= MAX_DEPTH:
        return [(core, box, nodes)]
    west, south, east, north = core
    height = (north - south) * 111.32
    width = (east - west) * 111.32 * math.cos(math.radians((north + south) / 2))
    if height >= width:
        mid = (south + north) / 2
        halves = [(west, south, east, mid), (west, mid, east, north)]
    else:
        mid = (west + east) / 2
        halves = [(west, south, mid, north), (mid, south, east, north)]
    return [t for half in halves for t in tiles_of(roads, half, tmp, depth + 1)]


def read_places(extract, tmp):
    """Cities and towns as (lon, lat, name, population, is_city)."""
    places = os.path.join(tmp, "places.osm.pbf")
    sh("osmium", "tags-filter", extract, "n/place=city,town", "-o", places, "--overwrite")
    out = subprocess.run(
        ["osmium", "export", places, "-f", "geojsonseq", "--geometry-types=point",
         "-x", "print_record_separator=false", "-o", "-"],
        check=True, capture_output=True, text=True).stdout
    found = []
    for line in out.splitlines():
        if not line.strip():
            continue
        feature = json.loads(line)
        props = feature.get("properties", {})
        name = props.get("name:en") or props.get("name")
        if not name:
            continue
        try:
            population = int(str(props.get("population", "0")).replace(",", "").split()[0])
        except (ValueError, IndexError):
            population = 0
        lon, lat = feature["geometry"]["coordinates"]
        found.append((lon, lat, name, population, props.get("place") == "city"))
    return found


def point_in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def read_country_rings(extract, codes, tmp):
    """{code: [thinned outer rings]} for the level-2 boundaries with those ISO codes (when they build)."""
    all_boundaries = os.path.join(tmp, "b.osm.pbf")
    countries = os.path.join(tmp, "countries.osm.pbf")
    sh("osmium", "tags-filter", extract, "r/boundary=administrative", "-o", all_boundaries, "--overwrite")
    sh("osmium", "tags-filter", all_boundaries, "r/admin_level=2", "-o", countries, "--overwrite")
    out = subprocess.run(
        ["osmium", "export", countries, "-f", "geojsonseq", "--geometry-types=polygon",
         "-x", "print_record_separator=false", "-o", "-"],
        check=True, capture_output=True, text=True).stdout
    rings = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        feature = json.loads(line)
        props = feature.get("properties", {})
        iso = props.get("ISO3166-1:alpha2") or props.get("ISO3166-1")
        if iso not in codes:
            continue
        geometry = feature["geometry"]
        polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
        for polygon in polygons:
            ring = polygon[0]
            rings.setdefault(iso, []).append(ring[::max(1, len(ring) // RING_SAMPLE)])
    return rings


def country_of(centre, codes, rings):
    x, y = centre
    for code in codes:
        for ring in rings.get(code, []):
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            if min(xs) <= x <= max(xs) and min(ys) <= y <= max(ys) and point_in_ring(x, y, ring):
                return code
    # In no border we could build: with several countries the first ones have borders and the last
    # is what is left over (Northern Ireland in the Ireland extract).
    return codes[-1] if rings else codes[0]


def main(argv):
    if len(argv) < 6:
        sys.exit(__doc__)
    extract, extract_id, source, release, out_path, *country_args = argv
    countries = {}
    for arg in country_args:
        code, _, name = arg.partition("=")
        countries[code] = (name or code).replace("_", " ")
    codes = list(countries)
    lines = []

    def log(message):
        print(message, flush=True)
        lines.append(message)

    with tempfile.TemporaryDirectory() as tmp:
        roads = os.path.join(tmp, "roads.osm.pbf")
        bake_graph.filter_roads(extract, roads)
        total = count_nodes(roads)
        box = extract_box(extract)
        tiles = tiles_of(roads, box, tmp)
        places = read_places(extract, tmp)
        rings = read_country_rings(extract, codes, tmp) if len(codes) > 1 else {}

    # Deterministic order, so ids and names do not depend on how the halving happened to unfold.
    tiles.sort(key=lambda t: (-round((t[0][1] + t[0][3]) / 2, 3), (t[0][0] + t[0][2]) / 2))
    regions = []
    counters = {}
    for core, bbox, nodes in tiles:
        centre = ((core[0] + core[2]) / 2, (core[1] + core[3]) / 2)
        code = country_of(centre, codes, rings)
        counters[code] = counters.get(code, 0) + 1
        inside = [p for p in places if core[0] <= p[0] < core[2] and core[1] <= p[1] < core[3]]
        if len(tiles) == 1 and len(codes) == 1:
            name = countries[code]
        elif inside:
            best = max(inside, key=lambda p: (p[3], p[4]))
            name = f"{best[2]} area"
        else:
            name = f"{countries[code]} {counters[code]}"
        regions.append({
            "id": f"{code.lower()}-{counters[code]}",
            "name": name,
            "country": code,
            "bbox": dict(zip(("west", "south", "east", "north"), bbox)),
            "nodes": nodes,
        })
    names = [r["name"] for r in regions]
    for region in regions:
        if names.count(region["name"]) > 1:
            region["name"] = f"{region['name']} {region['id'].split('-')[-1]}"
    sizes = [r["nodes"] for r in regions]
    log(f"{extract_id}: {total:,} road nodes in {len(regions)} tiles"
        + (f", largest {max(sizes):,}, smallest {min(sizes):,}" if sizes else ""))
    for region in regions:
        log(f"  {region['id']:8s} {region['nodes']:>9,}  {region['name']}")
    entry = {"id": extract_id, "source": source, "release": release, "regions": regions}
    with open(out_path, "w") as f:
        json.dump(entry, f, indent=2, ensure_ascii=False)
        f.write("\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(f"### {extract_id}\n```\n" + "\n".join(lines) + "\n```\n")


if __name__ == "__main__":
    main(sys.argv[1:])
