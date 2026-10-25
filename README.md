# MOTO-HUB road data

Ready-made road networks for offline rerouting in MOTO-HUB. One file per region, listed in
[`index.json`](index.json); the app downloads the file for the region you pick.

The `.graph.gz` files are attached to the `graphs` release.

## Data and licence

The road networks are derived from OpenStreetMap data, © OpenStreetMap contributors, and are
made available under the [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
Source extracts: [Geofabrik](https://download.geofabrik.de/).

Each file holds routable roads only (motorway to service roads, with links), as nodes and
directed edges with length and road class.
