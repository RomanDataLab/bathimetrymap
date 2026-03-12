# Strait of Gibraltar - Interactive Maritime Map

Interactive web map showing seabed bathymetric isolines, passenger vessel
routes, and fishing vessel tracks in the Strait of Gibraltar.

## Quick Start

```bash
pip install -r requirements.txt

# 1. Download bathymetry data
python fetch_bathymetry.py

# 2. Generate depth contour lines
python generate_isolines.py

# 3. Generate vessel traffic data
python fetch_vessels.py

# 4. Build the interactive map
python create_map.py
```

Open `web/index.html` in a browser.

## Pipeline

| Step | Script | Input | Output |
|------|--------|-------|--------|
| 1 | `fetch_bathymetry.py` | — | `data/bathymetry.tif` |
| 2 | `generate_isolines.py` | `data/bathymetry.tif` | `derived/isolines.geojson` |
| 3 | `fetch_vessels.py` | — | `derived/passenger_vessels.geojson` `derived/fishing_vessels.geojson` |
| 4 | `create_map.py` | GeoJSON files | `web/index.html` |

## Data Sources

### Bathymetry

| Source | Flag | Credentials | Resolution |
|--------|------|-------------|------------|
| EMODnet Bathymetry WCS | `--source emodnet` (default) | None | ~115 m |
| NOAA ETOPO 2022 OPeNDAP | `--source etopo` | None (needs `netCDF4`) | ~1800 m |

- EMODnet: https://emodnet.ec.europa.eu/en/bathymetry
- ETOPO: https://www.ncei.noaa.gov/products/etopo-global-relief-model
- GEBCO (manual download): https://download.gebco.net/

### Vessel Traffic

Default routes are simulated from real shipping lanes and fishing areas.
For production data:

- **Global Fishing Watch API** — fishing vessel tracks
  https://globalfishingwatch.org/our-apis/
- **EMODnet Human Activities** — vessel density maps (WMS)
  https://emodnet.ec.europa.eu/en/human-activities
- **Marine Traffic API** — real-time and historical AIS
  https://www.marinetraffic.com/en/ais-api-services

## Map Layers

- **Seabed Isolines** — contour lines every 50 m, major lines every 200 m
- **Passenger Vessels** — ferry and cruise routes through the strait
- **Fishing Vessels** — trawling and circling tracks in coastal grounds

## Parameters

```bash
# Use ETOPO instead of EMODnet
python fetch_bathymetry.py --source etopo

# Wider contour interval
python generate_isolines.py --interval 100 --min-depth -900

# More vessels
python fetch_vessels.py --n-passenger 30 --n-fishing 40
```

Run any script with `--help` for full options.
