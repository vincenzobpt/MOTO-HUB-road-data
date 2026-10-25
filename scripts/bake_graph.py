#!/usr/bin/env python3
"""Bake a MOTO-HUB offline road graph (`.graph`, format MHRG v1) from an OSM PBF.

Same graph the app builds from Overpass (OverpassRoadGraphClient.kt), produced ahead of
time so the phone downloads one finished file instead of querying public Overpass servers.
Edge rules, road classes and the binary layout mirror OfflineRoadGraph.kt exactly.

  bake_graph.py bake   <in.pbf> <west> <south> <east> <north> <out.graph> [--no-service]
  bake_graph.py verify <file.graph> [<lat1> <lon1> <lat2> <lon2>]

Needs the `osmium` CLI (brew install osmium-tool). Python stdlib only.
"""
import heapq
import math
import os
import struct
import subprocess
import sys
import tempfile
from array import array

MAGIC = 0x4D485247  # "MHRG"
FORMAT_VERSION = 1
EARTH_RADIUS_METERS = 6_371_000.0

# (code, speed m/s) per OfflineRoadGraph.kt RoadClass
CLASSES = {
    "motorway": (0, 30.6), "trunk": (1, 25.0), "primary": (2, 19.4),
    "secondary": (3, 16.7), "tertiary": (4, 13.9), "unclassified": (5, 11.1),
    "road": (5, 11.1), "residential": (6, 8.3), "service": (7, 5.6),
    "living_street": (8, 4.2),
}
SPEED_BY_CODE = {0: 30.6, 1: 25.0, 2: 19.4, 3: 16.7, 4: 13.9, 5: 11.1, 6: 8.3, 7: 5.6, 8: 4.2}


def haversine(lat1, lon1, lat2, lon2):
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    return 2 * EARTH_RADIUS_METERS * math.asin(math.sqrt(min(1.0, max(0.0, a))))


def run(*args):
    subprocess.run(args, check=True)


def filter_roads(pbf, out, include_service=True):
    """Keep only routable ways (and the nodes they use). Do it once per extract, not per region."""
    linked = ["motorway", "trunk", "primary", "secondary", "tertiary"]
    plain = ["unclassified", "residential", "living_street", "road"] + (["service"] if include_service else [])
    values = ",".join(linked + [c + "_link" for c in linked] + plain)
    run("osmium", "tags-filter", pbf, "w/highway=" + values, "-o", out, "--overwrite")


def bake(pbf, west, south, east, north, out, include_service=True, prefiltered=False):
    with tempfile.TemporaryDirectory() as tmp:
        roads = pbf
        if not prefiltered:
            roads = os.path.join(tmp, "roads.osm.pbf")
            filter_roads(pbf, roads, include_service)
        cut = os.path.join(tmp, "cut.osm.pbf")
        run("osmium", "extract", "-b", f"{west},{south},{east},{north}",
            "--strategy=complete_ways", roads, "-o", cut, "--overwrite")
        opl = subprocess.Popen(["osmium", "cat", cut, "-f", "opl", "-o", "-"],
                               stdout=subprocess.PIPE, text=True, bufsize=1 << 20)
        graph = parse_opl(opl.stdout)
        if opl.wait() != 0:
            sys.exit("osmium cat failed")
    write_graph(out, (south, west, north, east), *graph)


def parse_opl(lines):
    index = {}
    lats, lons = array("d"), array("d")
    from_nodes, to_nodes, meters, cls = array("i"), array("i"), array("f"), array("b")
    for line in lines:
        kind = line[0]
        if kind == "n":
            fields = line.split(" ")
            x = y = None
            for f in fields:
                if f[0] == "x":
                    x = float(f[1:])
                elif f[0] == "y":
                    y = float(f[1:])
            if x is not None and y is not None:
                index[int(fields[0][1:])] = len(lats)
                lats.append(y)
                lons.append(x)
        elif kind == "w":
            highway = oneway = junction = None
            refs = ""
            for f in line.split(" ")[1:]:
                if f[0] == "T":
                    for tag in f[1:].split(","):
                        k, _, v = tag.partition("=")
                        if k == "highway":
                            highway = v
                        elif k == "oneway":
                            oneway = v
                        elif k == "junction":
                            junction = v
                elif f[0] == "N":
                    refs = f[1:]
            if highway is None:
                continue
            base = highway[:-5] if highway.endswith("_link") else highway
            if base not in CLASSES:
                continue
            code = CLASSES[base][0]
            ids = [int(r[1:]) for r in refs.split(",")] if refs else []
            if len(ids) < 2:
                continue
            implied = junction in ("roundabout", "circular") or code == 0
            forward = oneway not in ("-1", "reverse")
            backward = oneway == "no" or (not implied and oneway not in ("yes", "true", "1"))
            for a, b in zip(ids, ids[1:]):
                i, j = index.get(a, -1), index.get(b, -1)
                if i < 0 or j < 0:
                    continue
                d = haversine(lats[i], lons[i], lats[j], lons[j])
                if forward:
                    from_nodes.append(i); to_nodes.append(j); meters.append(d); cls.append(code)
                if backward:
                    from_nodes.append(j); to_nodes.append(i); meters.append(d); cls.append(code)
    return lats, lons, from_nodes, to_nodes, meters, cls


def write_graph(out, bounds, lats, lons, from_nodes, to_nodes, meters, cls):
    # Drop nodes no edge touches, like compactToReferencedNodes() in the app.
    used = bytearray(len(lats))
    for n in from_nodes:
        used[n] = 1
    for n in to_nodes:
        used[n] = 1
    remap = array("i", [-1]) * len(lats)
    kept = array("d")
    count = 0
    for n in range(len(lats)):
        if used[n]:
            remap[n] = count
            count += 1
            kept.append(lats[n]); kept.append(lons[n])
    tmp_path = out + ".tmp"
    with open(tmp_path, "wb") as f:
        f.write(struct.pack(">ii4d", MAGIC, FORMAT_VERSION, *bounds))
        f.write(struct.pack(">i", count))
        if sys.byteorder == "little":
            kept.byteswap()
        f.write(kept.tobytes())
        f.write(struct.pack(">i", len(from_nodes)))
        pack = struct.Struct(">iifb").pack
        buf = []
        for a, b, d, c in zip(from_nodes, to_nodes, meters, cls):
            buf.append(pack(remap[a], remap[b], d, c))
            if len(buf) >= 100_000:
                f.write(b"".join(buf)); buf.clear()
        f.write(b"".join(buf))
    os.replace(tmp_path, out)
    print(f"wrote {out}: {count} nodes, {len(from_nodes)} edges, {os.path.getsize(out) / 1e6:.1f} MB")


def read_graph(path):
    with open(path, "rb") as f:
        magic, version = struct.unpack(">ii", f.read(8))
        assert magic == MAGIC and version == FORMAT_VERSION, "not an MHRG v1 file"
        bounds = struct.unpack(">4d", f.read(32))
        (n,) = struct.unpack(">i", f.read(4))
        coords = array("d"); coords.frombytes(f.read(16 * n))
        if sys.byteorder == "little":
            coords.byteswap()
        (m,) = struct.unpack(">i", f.read(4))
        raw = f.read(13 * m)
        assert len(raw) == 13 * m and f.read(1) == b"", "truncated or trailing bytes"
    edges = list(struct.iter_unpack(">iifb", raw))
    return bounds, coords, edges


def verify(path, route=None):
    bounds, coords, edges = read_graph(path)
    n = len(coords) // 2
    print(f"bounds S{bounds[0]} W{bounds[1]} N{bounds[2]} E{bounds[3]}; {n} nodes, {len(edges)} edges")
    assert all(0 <= a < n and 0 <= b < n and d >= 0 and c in SPEED_BY_CODE for a, b, d, c in edges), "bad edge"
    adjacency = [[] for _ in range(n)]
    undirected = [[] for _ in range(n)]
    for a, b, d, c in edges:
        adjacency[a].append((b, d, c))
        undirected[a].append(b); undirected[b].append(a)
    seen = bytearray(n); best = 0
    for s in range(n):
        if seen[s]:
            continue
        stack, size = [s], 0
        seen[s] = 1
        while stack:
            v = stack.pop(); size += 1
            for w in undirected[v]:
                if not seen[w]:
                    seen[w] = 1; stack.append(w)
        best = max(best, size)
    print(f"largest connected component: {best / n:.1%} of nodes")
    if route:
        lat1, lon1, lat2, lon2 = route
        def nearest(lat, lon):
            return min(range(n), key=lambda i: (coords[2 * i] - lat) ** 2 + ((coords[2 * i + 1] - lon) * math.cos(math.radians(lat))) ** 2)
        start, goal = nearest(lat1, lon1), nearest(lat2, lon2)
        best_s = {start: 0.0}; prev_m = {start: 0.0}; heap = [(0.0, start)]; done = set()
        while heap:
            s, v = heapq.heappop(heap)
            if v in done:
                continue
            done.add(v)
            if v == goal:
                break
            for w, d, c in adjacency[v]:
                cand = s + d / SPEED_BY_CODE[c]
                if cand < best_s.get(w, 1e18):
                    best_s[w] = cand; prev_m[w] = prev_m[v] + d; heapq.heappush(heap, (cand, w))
        if goal in done:
            print(f"route: {prev_m[goal] / 1000:.1f} km, {best_s[goal] / 60:.0f} min")
        else:
            print("route: NO PATH")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["bake"] and len(args) >= 7:
        bake(args[1], *map(float, args[2:6]), args[6], include_service="--no-service" not in args)
    elif args[:1] == ["verify"] and len(args) in (2, 6):
        verify(args[1], tuple(map(float, args[2:6])) if len(args) == 6 else None)
    else:
        sys.exit(__doc__)
