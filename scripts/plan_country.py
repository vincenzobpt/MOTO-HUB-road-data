#!/usr/bin/env python3
"""Cut a country into regions of a sensible size, with no hand-made list.

  plan_country.py <extract.osm.pbf> <extract-id> <source-url> <release-tag> <out.json> \
      <CC> [<CC> ...] [--level CC=N ...]

For each country code in the extract:
  1. count the routable road nodes, and from that how many regions are wanted (about
     TARGET_NODES nodes each, which is a graph of a few tens of MB);
  2. read the country's admin boundaries (levels 3-7), keep those that lie inside the country,
     and pick the level whose region count is closest to the wanted one (`--level` overrides);
  3. measure what each region's graph would hold, and cut any region over CAP_NODES into
     tiles, halving along its longer side until every tile fits.
Writes the extract entry for regions.json to <out.json> and prints a summary.

Region ids are `<cc>-<osm relation>` (`-<n>` for tiles): stable when OSM renames a place.
Boxes are padded so neighbours overlap; that overlap is the seam the app joins graphs on.
"""
import json
import math
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bake_graph  # noqa: E402

TARGET_NODES = 600_000
CAP_NODES = 1_100_000
MIN_TILE_NODES = 5_000
PAD = 0.01
ISLAND_SHARE = 0.1
LEVELS = range(3, 8)
MAX_DEPTH = 4
RING_SAMPLE = 20_000


def sh(*args):
    subprocess.run(args, check=True)


def count_nodes(pbf):
    out = subprocess.run(["osmium", "fileinfo", "-e", "-g", "data.count.nodes", pbf],
                         check=True, capture_output=True, text=True).stdout.strip()
    return int(out)


def measure(roads, bbox, tmp):
    """Nodes a graph cut with this box would hold (the cut keeps only nodes of whole ways)."""
    cut = os.path.join(tmp, "measure.osm.pbf")
    west, south, east, north = bbox
    sh("osmium", "extract", "-b", f"{west},{south},{east},{north}", "--strategy=complete_ways",
       roads, "-o", cut, "--overwrite")
    return count_nodes(cut)


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


def read_boundaries(extract, tmp):
    """Every admin boundary of levels 2-7 as {level, name, relation, bbox, centre, rings?}."""
    boundaries = os.path.join(tmp, "boundaries.osm.pbf")
    sh("osmium", "tags-filter", extract, "r/boundary=administrative", "-o", boundaries, "--overwrite")
    wanted = os.path.join(tmp, "levels.osm.pbf")
    sh("osmium", "tags-filter", boundaries, "r/admin_level=2,3,4,5,6,7", "-o", wanted, "--overwrite")
    proc = subprocess.Popen(
        ["osmium", "export", wanted, "-f", "geojsonseq", "--geometry-types=polygon", "-a", "type,id",
         "-x", "print_record_separator=false", "-o", "-"],
        stdout=subprocess.PIPE, text=True, bufsize=1 << 20)
    found = []
    for line in proc.stdout:
        if not line.strip():
            continue
        feature = json.loads(line)
        props = feature.get("properties", {})
        if props.get("@type") != "relation" or not props.get("admin_level", "").isdigit():
            continue
        geometry = feature["geometry"]
        polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
        outers = [polygon[0] for polygon in polygons if polygon and polygon[0]]
        if not outers:
            continue
        boxes = []
        for ring in outers:
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            boxes.append((min(xs), min(ys), max(xs), max(ys)))
        area = [(b[2] - b[0]) * (b[3] - b[1]) for b in boxes]
        biggest = max(area)
        kept = [b for b, a in zip(boxes, area) if a >= ISLAND_SHARE * biggest]
        main = outers[area.index(biggest)]
        step = max(1, len(main) // 2000)
        sample = main[::step]
        found.append({
            "level": int(props["admin_level"]),
            "name": props.get("name:en") or props.get("name") or "",
            "relation": int(props["@id"]),
            "iso": props.get("ISO3166-1:alpha2") or props.get("ISO3166-1") or "",
            "bbox": (min(b[0] for b in kept), min(b[1] for b in kept),
                     max(b[2] for b in kept), max(b[3] for b in kept)),
            "centre": (sum(p[0] for p in sample) / len(sample), sum(p[1] for p in sample) / len(sample)),
            # Whole outline, thinned, only for countries: it is what ownership is tested against.
            "rings": [(r[::max(1, len(r) // RING_SAMPLE)]) for r in outers] if props["admin_level"] == "2" else None,
        })
    if proc.wait() != 0:
        sys.exit("osmium export failed")
    return found


def inside_country(centre, rings):
    x, y = centre
    for ring in rings:
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        if min(xs) <= x <= max(xs) and min(ys) <= y <= max(ys) and point_in_ring(x, y, ring):
            return True
    return False


def padded(bbox):
    west, south, east, north = bbox
    return (round(west - PAD, 7), round(south - PAD, 7), round(east + PAD, 7), round(north + PAD, 7))


def split(roads, bbox, nodes, tmp, depth=0):
    """[(bbox, nodes)] tiles of at most CAP_NODES each, halving the longer side (with overlap)."""
    if nodes <= CAP_NODES or depth >= MAX_DEPTH:
        return [(bbox, nodes)]
    west, south, east, north = bbox
    height = (north - south) * 111.32
    width = (east - west) * 111.32 * math.cos(math.radians((north + south) / 2))
    if height >= width:
        mid = (south + north) / 2
        halves = [(west, south, east, mid + PAD), (west, mid - PAD, east, north)]
    else:
        mid = (west + east) / 2
        halves = [(west, south, mid + PAD, north), (mid - PAD, south, east, north)]
    tiles = []
    for half in halves:
        count = measure(roads, half, tmp)
        if count >= MIN_TILE_NODES:
            tiles += split(roads, half, count, tmp, depth + 1)
    return tiles


def tile_names(name, tiles):
    """Compass suffixes for 2 or 4 tiles, numbers otherwise; tiles ordered north to south, west to east."""
    tiles = sorted(tiles, key=lambda t: (-(t[0][1] + t[0][3]), t[0][0] + t[0][2]))
    centres = [((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for b, _ in tiles]
    mean_x = sum(c[0] for c in centres) / len(centres)
    mean_y = sum(c[1] for c in centres) / len(centres)
    labels = None
    if len(tiles) == 2:
        dx = abs(centres[0][0] - centres[1][0]) * math.cos(math.radians(mean_y))
        dy = abs(centres[0][1] - centres[1][1])
        labels = ["N", "S"] if dy >= dx else [("W" if c[0] < mean_x else "E") for c in centres]
    elif len(tiles) == 4:
        labels = [("N" if c[1] > mean_y else "S") + ("E" if c[0] > mean_x else "W") for c in centres]
    if not labels or len(set(labels)) != len(labels):
        labels = [str(i + 1) for i in range(len(tiles))]
    return [(f"{name} · {label}", tile) for label, tile in zip(labels, tiles)]


def plan_country(extract, roads, code, forced_level, total_nodes, boundaries, tmp, log, multi=False):
    country = [b for b in boundaries if b["level"] == 2 and b["iso"] == code]
    if not country:
        log(f"{code}: no level-2 boundary with that code - skipped")
        return []
    rings = [r for c in country for r in c["rings"]]
    country_name = country[0]["name"] or code
    if multi:
        # Several countries share this extract: count only the roads inside this one's box.
        total_nodes = measure(roads, padded(country[0]["bbox"]), tmp)
    wanted = max(1, round(total_nodes / TARGET_NODES))
    owned = {}
    for level in LEVELS:
        seen = {}
        for b in boundaries:
            if b["level"] != level or not b["name"] or not inside_country(b["centre"], rings):
                continue
            area = (b["bbox"][2] - b["bbox"][0]) * (b["bbox"][3] - b["bbox"][1])
            if b["name"] not in seen or area > seen[b["name"]][0]:
                seen[b["name"]] = (area, b)
        owned[level] = [b for _, b in seen.values()]
    counts = {level: len(items) for level, items in owned.items()}
    usable = {level: n for level, n in counts.items() if n >= 2}
    if forced_level:
        level = forced_level
    elif wanted <= 1 or not usable:
        level = None
    else:
        level = min(usable, key=lambda lv: (abs(math.log(usable[lv] / wanted)), lv))
    log(f"{code} {country_name}: {total_nodes:,} road nodes, wants ~{wanted} regions, "
        f"boundaries per level {counts}, chosen level {level or 'whole country'}")

    if level is None:
        units = [{"name": country_name, "relation": country[0]["relation"], "bbox": country[0]["bbox"]}]
    else:
        units = owned.get(level, [])
    regions = []
    for unit in units:
        box = padded(unit["bbox"])
        nodes = measure(roads, box, tmp)
        if nodes < MIN_TILE_NODES:
            continue
        tiles = split(roads, box, nodes, tmp)
        named = [(unit["name"], tiles[0])] if len(tiles) == 1 else tile_names(unit["name"], tiles)
        for index, (name, (tile_box, tile_nodes)) in enumerate(named):
            suffix = "" if len(tiles) == 1 else f"-{index + 1}"
            regions.append({
                "id": f"{code.lower()}-{unit['relation']}{suffix}",
                "name": name,
                "country": code,
                "relation": unit["relation"],
                "bbox": dict(zip(("west", "south", "east", "north"), tile_box)),
                "nodes": tile_nodes,
            })
    regions.sort(key=lambda r: r["name"])
    sizes = [r["nodes"] for r in regions]
    if sizes:
        log(f"{code}: {len(regions)} regions, largest {max(sizes):,} nodes, smallest {min(sizes):,}, "
            f"{sum(1 for r in regions if '-' in r['id'].split('-', 1)[1])} from tiles")
    return regions


def main(argv):
    forced = {}
    args = []
    i = 0
    while i < len(argv):
        if argv[i] == "--level":
            code, _, value = argv[i + 1].partition("=")
            forced[code] = int(value)
            i += 2
        else:
            args.append(argv[i])
            i += 1
    if len(args) < 6:
        sys.exit(__doc__)
    extract, extract_id, source, release, out_path, *codes = args
    lines = []

    def log(message):
        print(message, flush=True)
        lines.append(message)

    with tempfile.TemporaryDirectory() as tmp:
        roads = os.path.join(tmp, "roads.osm.pbf")
        bake_graph.filter_roads(extract, roads)
        total = count_nodes(roads)
        boundaries = read_boundaries(extract, tmp)
        regions = []
        for code in codes:
            regions += plan_country(extract, roads, code, forced.get(code), total, boundaries, tmp, log,
                                    multi=len(codes) > 1)
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
