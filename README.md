# MOTO-HUB road data

Ready-made road networks for offline rerouting in MOTO-HUB. One file per region, listed in
[`index.json`](index.json); the app downloads the file for the region you pick.

The `.graph.gz` files are attached to the `graphs` release.

## How it is made

`regions.json` lists the regions (name, OSM relation, bounding box) and the extract they are cut
from. A monthly workflow downloads the extract from Geofabrik, bakes each region with
`scripts/build_release.py`, uploads the files whose roads changed and updates `index.json`.
An unchanged region keeps its file, checksum and `rev`, so the app only offers real updates.

Run it by hand from the Actions tab; switch "publish" off for a dry run that only reports.

## Data and licence

The road networks are derived from OpenStreetMap data, © OpenStreetMap contributors, and are
made available under the [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
Source extracts: [Geofabrik](https://download.geofabrik.de/).

Each file holds routable roads only (motorway to service roads, with links), as nodes and
directed edges with length and road class.
