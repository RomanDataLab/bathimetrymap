# Methodology: Strait of Gibraltar Interactive Maritime Map

## Overview

This project produces a self-contained interactive web map of the Strait of Gibraltar showing seabed bathymetry, vessel traffic, and navigational aids. The pipeline consists of four Python scripts executed sequentially, each producing intermediate data consumed by the next stage.

```
fetch_bathymetry.py  -->  generate_isolines.py  -->  fetch_vessels.py  -->  create_map.py
     (Step 1)                  (Step 2)                 (Step 3)              (Step 4)

  data/bathymetry.tif    derived/isolines.geojson    derived/*_vessels.geojson    web/index.html
```

---

## Step 1: Bathymetry Acquisition (`fetch_bathymetry.py`)

### Purpose
Download seabed elevation data for the Strait of Gibraltar region as a GeoTIFF raster.

### Geographic Extent
| Bound | Value |
|-------|-------|
| North | 36.25 N |
| South | 35.75 N |
| East  | -4.59 E |
| West  | -7.47 E |

Coverage: approximately 240 km x 55 km.

### Data Sources

**Primary: EMODnet Bathymetry (WCS 2.0.1)**
- Service endpoint: EMODnet Web Coverage Service
- Coverage ID: `emodnet:mean`
- Resolution: ~115 m/pixel
- No authentication required
- Response format: GeoTIFF

**Fallback: NOAA ETOPO 2022 (OPeNDAP)**
- Source: NOAA NCEI THREDDS server
- Resolution: ~1800 m (60 arc-seconds)
- Requires `netCDF4` package
- NoData value: -9999

### Processing
1. Request raster via WCS GetCoverage (subset by lat/lon bounds)
2. Validate response content type (must be TIFF, not error XML)
3. Download in 1 MB chunks
4. Write single-band float32 GeoTIFF with EPSG:4326 CRS

### Output
- **File:** `data/bathymetry.tif` (~5.7 MB)
- **Dimensions:** 2765 x 480 pixels
- **Convention:** negative values = water depth, positive values = land elevation
- **Value range:** -1293.6 m to +821.6 m

---

## Step 2: Depth Contour Generation (`generate_isolines.py`)

### Purpose
Extract depth contour lines (isolines) from the bathymetry raster and export as GeoJSON.

### Algorithm
1. Load GeoTIFF raster with rasterio
2. Handle nodata values (set to NaN)
3. Detect depth convention: if median > 0, multiply by -1 to normalize to negative-depth convention
4. Apply Gaussian smoothing (sigma = 1.0 pixel) to reduce raster noise while preserving NaN mask
5. For each depth level, run marching-squares contour detection (`skimage.measure.find_contours`)
6. Transform contour pixel coordinates to geographic coordinates using the raster affine transform
7. Simplify contour geometry with Douglas-Peucker algorithm (tolerance = 0.002 deg, ~200 m)
8. Filter out fragments with fewer than 5 vertices
9. Classify isolines: "major" if depth is a multiple of 200 m

### Parameters
| Parameter | Default | Meaning |
|-----------|---------|---------|
| interval | 50 m | Depth step between contour levels |
| min-depth | -1000 m | Deepest contour |
| max-depth | 0 m | Shallowest contour (sea level) |
| smooth | 1.0 px | Gaussian filter sigma |
| simplify | 0.002 deg | Douglas-Peucker tolerance |
| min-vertices | 5 | Minimum points per contour segment |

### Coordinate Transformation
Pixel (row, col) to geographic (lon, lat) via rasterio affine transform:
```
lon = transform.c + col * transform.a
lat = transform.f + row * transform.e
```

### Output
- **File:** `derived/isolines.geojson` (~455 KB)
- **Format:** GeoJSON FeatureCollection of LineString geometries
- **Feature count:** ~752 contour segments
- **Properties per feature:**
  - `depth` (float): depth in meters (negative)
  - `label` (string): e.g. "-500 m"
  - `major` (boolean): true for every-200m contours

### Styling in Map
| Type | Weight | Opacity | Dash |
|------|--------|---------|------|
| Major (every 200 m) | 2.5 px | 0.9 | solid |
| Minor (every 50 m) | 1.0 px | 0.5 | "4 4" |

Depth color scale (darker = deeper):
| Depth | Color |
|-------|-------|
| >= 800 m | #0D47A1 |
| >= 600 m | #1565C0 |
| >= 400 m | #1976D2 |
| >= 200 m | #1E88E5 |
| >= 100 m | #42A5F5 |
| >= 50 m  | #64B5F6 |
| < 50 m   | #90CAF9 |

---

## Step 3: Vessel Traffic Generation (`fetch_vessels.py`)

### Purpose
Generate realistic simulated vessel traffic for three categories based on known shipping lanes and fishing zones. Uses bathymetry raster to validate all coordinates are over water.

### Water Validation
Before outputting any route, every coordinate is checked against the bathymetry raster:
- Sample pixel at (lon, lat) using the raster transform
- Accept if elevation < 0 (water); reject if >= 0 (land)
- Coordinates falling on land are removed from the route

### Category A: Passenger Vessels

**Route Definitions (4 ferry routes, 5 waypoints each):**
| Route | Start | End |
|-------|-------|-----|
| Algeciras - Tangier Med | (-5.44, 36.12) | (-5.505, 35.88) |
| Algeciras - Ceuta | (-5.44, 36.12) | (-5.31, 35.89) |
| Tarifa - Tangier | (-5.605, 36.005) | (-5.75, 35.79) |
| Gibraltar - Tangier Med | (-5.36, 36.13) | (-5.50, 35.88) |

**TSS (Traffic Separation Scheme) cruise routes (8 waypoints each):**
| Lane | Start | End |
|------|-------|-----|
| Eastbound | (-7.20, 35.90) | (-4.80, 36.00) |
| Westbound | (-4.80, 35.96) | (-7.20, 35.85) |

**Generation process:**
1. Select route based on vessel index (round-robin across 4 routes)
2. Alternate direction: even index = outbound, odd = return (reversed waypoints)
3. Apply Gaussian jitter to each waypoint (sigma = 0.003 deg for ferries, 0.004 deg for cruises)
4. Filter out any land-based coordinates
5. Output LineString geometry with straight segments between waypoints

**Output:** 20 features (16 ferries + 4 TSS cruises)
- Speed: 18-28 knots (ferries), 15-22 knots (cruises)
- Animation speed: 0.08 (ferries), 0.06 (cruises)
- All non-looping (back-and-forth animation)

### Category B: Cargo Vessels

**Route Definitions (6 routes, 5-6 waypoints each):**
| Route | Waypoints |
|-------|-----------|
| Atlantic - Med (north) | 6 waypoints, lat ~35.94-36.00 |
| Atlantic - Med (south) | 6 waypoints, lat ~35.90-35.95 |
| Med - Atlantic (north) | 6 waypoints (reverse of above) |
| Med - Atlantic (south) | 6 waypoints (reverse of above) |
| Algeciras approach (E) | 5 waypoints, port approach from east |
| Algeciras approach (W) | 5 waypoints, port approach from west |

**Generation:** Same process as passenger (jitter sigma = 0.004 deg, alternating direction).

**Output:** 12 features
- Speed: 10-18 knots
- Animation speed: 0.05
- All non-looping

### Category C: Fishing Vessels

**Fishing Zone Definitions:**
| Zone | Center | Radius |
|------|--------|--------|
| Bay of Algeciras | (-5.38, 36.05) | 0.025 deg |
| Tarifa Waters | (-5.60, 35.96) | 0.025 deg |
| Northern Morocco | (-5.42, 35.87) | 0.020 deg |
| Mid-Strait | (-5.50, 35.93) | 0.030 deg |
| Western Approach | (-5.70, 35.91) | 0.025 deg |
| Eastern Approach | (-5.25, 35.96) | 0.025 deg |

**Track Patterns:**
- **Circling (2 of every 3 vessels):** 30-point randomized ellipse with Gaussian perturbation. Radii vary by N(0, 0.2) factor. Random rotation angle. Closed loop (first point appended at end). `is_loop = true`.
- **Trawling (1 of every 3 vessels):** 5-8 zigzag legs with alternating lateral offsets (0.2-0.5 x extent). `is_loop = false`.

**Output:** ~20 features
- Speed: 3-8 knots
- Animation speed: 0.03
- Loop: true for circling, false for trawling

### Random Seed
All random generators seeded with `--seed 42` for reproducibility.

### Output Files
| File | Size | Features |
|------|------|----------|
| `derived/passenger_vessels.geojson` | ~9 KB | 20 |
| `derived/cargo_vessels.geojson` | ~6 KB | 12 |
| `derived/fishing_vessels.geojson` | ~22 KB | 20 |

---

## Step 4: Map Assembly (`create_map.py`)

### Purpose
Combine all data layers into a single self-contained HTML file with an interactive Leaflet.js map. Applies trail processing to ensure 3 km land clearance and coastline-touching endpoints.

### 4a. Coastline Extraction from Bathymetry Raster

Since vessel trails must avoid land, real coastline geometry is extracted directly from the bathymetry raster:

1. Load `data/bathymetry.tif` with rasterio
2. Create binary land mask: `land_mask = (elevation >= 0)`
3. Vectorize mask using `rasterio.features.shapes()` to produce land polygons
4. Filter out tiny slivers (area < 1e-5 sq deg)
5. Merge all land polygons with `shapely.ops.unary_union`
6. Simplify coastline with tolerance 0.002 deg (~200 m) to reduce vertex count

Result: 11 land polygons representing Spain, Morocco, Gibraltar, and small islands.

### 4b. Buffer Zone Construction

Three concentric zones are built from the coastline:

| Zone | Buffer | Purpose |
|------|--------|---------|
| `_land_raw` | 0 (actual coastline) | Extending trail endpoints to touch land |
| `_land_buffered` | 0.030 deg (~3 km) | Validation: no mid-trail points inside |
| `_land_exclusion` | 0.033 deg (~3.3 km) | Trail subtraction zone (slightly larger to ensure rerouted paths stay outside the 3 km buffer) |

At 36 N latitude:
- 1 deg latitude = 111.32 km
- 1 deg longitude = 90.06 km
- 3 km = ~0.027 deg lat, ~0.033 deg lon, average ~0.030 deg

### 4c. Trail Processing Algorithm

Each vessel trail is processed through `_process_trail()`:

```
Input: list of [lon, lat] waypoints (straight segments, no interpolation)

1. Build LineString from waypoints
2. Subtract exclusion zone:  safe = trail.difference(_land_exclusion)
   - Result: one or more LineString segments that are outside the buffer
3. Sort safe segments by position along original trail
4. For each gap between consecutive safe segments:
   a. Find the exit point (end of segment N) and entry point (start of segment N+1)
   b. Project both onto the exclusion zone boundary
   c. Extract the shorter boundary path between them (_boundary_path)
   d. Simplify the boundary path (tolerance 0.005 deg) to straight segments
   e. Insert simplified path between the safe segments
5. Extend trail start: find nearest point on raw coastline, prepend
6. Extend trail end: find nearest point on raw coastline, append
7. Deduplicate near-coincident consecutive points (distance^2 < 1e-9)
```

**Boundary path extraction** (`_boundary_path`):
- Projects two points onto the buffer boundary ring
- Calculates both clockwise and counter-clockwise distances
- Returns the shorter path using `shapely.ops.substring`
- Handles MultiLineString boundaries by selecting the closest ring

**Coastline extension** (`_extend_to_land`):
- Uses `shapely.ops.nearest_points(point, _land_raw.boundary)`
- Returns the nearest point on the actual coastline
- Ensures every trail visually starts and ends at the shore

### 4d. Lighthouse Layer

12 lighthouses are hardcoded with real geographic positions and signal characteristics:

| Property | Source |
|----------|--------|
| Position (lat, lon) | Real lighthouse coordinates |
| Range (NM) | Published nominal range |
| Character | Standard IALA notation (e.g. "Fl(3) WR 10s") |
| Colors | Light color(s): white (#FFFFAA), red (#FF3333), green (#33FF33) |
| Flashes | Number of flashes per cycle |

**Rendering (Leaflet):**
- **Inner marker:** `L.circleMarker`, 10 px radius, gold fill (#FFD700), dark outline, shows tooltip with lighthouse name on hover
- **Visibility circle:** `L.circle`, radius = range_nm x 1852 meters, orange (#FF8C00), 5% fill opacity, non-interactive
- **Signal pattern on visibility circle:** For each flash sector (360 deg / n_flashes):
  - Two arcs (dashes): 15 deg spans at weight 5 px, rendered as polylines along the circle perimeter
  - Two dots: 4 px circle markers at specific angular positions
  - Color cycles through the lighthouse's color array

**Arc point calculation:**
```
For angle a (degrees), radius R (meters), at position (lat, lon):
  dLat = R * cos(a) / 6371000 * (180/pi)
  dLon = R * sin(a) / (6371000 * cos(lat_rad)) * (180/pi)
  point = [lat + dLat, lon + dLon]
```

### 4e. Vessel Trail Styling

All vessel categories use the same dash-dash-dot-dot line pattern:
```
dashArray: "12 5 12 5 3 5 3 5"
```
Pattern: 12 px dash, 5 px gap, 12 px dash, 5 px gap, 3 px dot, 5 px gap, 3 px dot, 5 px gap.

| Category | Color | Weight | Opacity |
|----------|-------|--------|---------|
| Passenger / ferry | #2E7D32 (green) | 2 px | 0.8 |
| Cargo | #D32F2F (red) | 2 px | 0.8 |
| Fishing | #E65100 (orange) | 2 px | 0.8 |

### 4f. Animation Engine

Each vessel has an animated dot (3 px circle marker) that moves along its trail:

1. Convert trail coordinates to [lat, lon] array
2. Compute cumulative distance along the path
3. Each frame (requestAnimationFrame, ~60 FPS):
   - Advance progress by `speed * dt`
   - **Loop mode** (`is_loop = true`): progress wraps in [0, 1], continuous forward motion
   - **Back-and-forth** (`is_loop = false`): progress wraps in [0, 2]; if progress > 1, reverse direction (t = 2 - progress)
   - Interpolate position along trail segments using linear interpolation
   - Update marker position with `setLatLng`

Speed per vessel: `base_anim_speed * (0.7 + random * 0.6)`, giving natural variation within each category.

### 4g. Layer Control UI

**Checkbox toggles** for overlay layers:
- Depth isolines (checked by default)
- Sea Marks / OpenSeaMap (unchecked by default)
- Passenger / ferry trails (checked)
- Cargo vessel trails (checked)
- Fishing vessel trails (checked)
- Lighthouses (checked)

**Radio buttons** for base map:
- Ocean Base (Esri World Ocean Base, default, max zoom 13)
- OpenStreetMap (max zoom 18)

All toggles use `map.addLayer()` / `map.removeLayer()` bound to DOM change events.

### 4h. Map Configuration
- **Library:** Leaflet.js 1.9.4 (loaded from CDN)
- **Initial view:** center [35.97 N, -5.80 E], zoom 9
- **Scale control:** metric (imperial disabled)

---

## Output

**File:** `web/index.html` (~512 KB)

A single self-contained HTML file with:
- All GeoJSON data embedded as inline JavaScript variables
- Lighthouse data as inline JSON array
- Complete CSS styling (responsive, full-viewport map)
- Legend panel with layer controls and statistics
- Animation engine running continuously

No external data dependencies at runtime (only tile server URLs for base maps).

---

## Dependencies

```
numpy>=1.24           Array operations, random distributions, trigonometry
rasterio>=1.3         GeoTIFF I/O, raster-to-vector conversion, coordinate transforms
requests>=2.28        HTTP requests for bathymetry data download
shapely>=2.0          Geometric operations: buffer, union, difference, simplify, substring
scikit-image>=0.21    Contour extraction (marching squares)
scipy>=1.10           Gaussian smoothing filter
```

---

## Execution

```bash
pip install -r requirements.txt

python fetch_bathymetry.py                # Step 1: download raster
python generate_isolines.py               # Step 2: extract contours
python fetch_vessels.py --seed 42         # Step 3: generate vessel tracks
python create_map.py                      # Step 4: build HTML map
```

All scripts support `--help` for full argument documentation.
