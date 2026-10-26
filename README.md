# MOTO-HUB road data

Valhalla routing tiles for offline navigation in MOTO-HUB, one download per region, listed in
[`valhalla-index.json`](valhalla-index.json). The phone routes on them with the same Valhalla the
online router runs, so offline turns carry street names and spoken instructions.

Each country is built on its own, so offline routes stay inside one country. The `.tar.gz` files
are attached to one release per country (`valhalla-<extract>`).

## How it is made

- `countries.json` lists the Geofabrik extracts. `plan-countries.yml` cuts a country into regions
  of a sensible size (`scripts/plan_country.py`) and records them in `regions.json`.
- `valhalla.yml` downloads each extract, builds its tiles with Valhalla 3.9.1 (the version the
  app's library ships), packs the tiles each region needs (`scripts/valhalla_regions.py`) and
  updates the index (`scripts/merge_valhalla_index.py`). It runs every three months and by hand.
- Tiles of two builds are never mixed: every region of a country carries the build's `rev`, and
  the phone keeps each build in a folder of its own.

## Data and licence

The tiles are derived from OpenStreetMap data, © OpenStreetMap contributors, and are made
available under the [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
Source extracts: [Geofabrik](https://download.geofabrik.de/).
