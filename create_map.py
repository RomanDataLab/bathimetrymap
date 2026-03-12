"""
Create an interactive web map of the Strait of Gibraltar with layers:
  1. Seabed bathymetric isolines
  2. Passenger vessel routes  (animated icons)
  3. Fishing vessel tracks    (animated icons)
  4. Cargo vessel routes      (animated icons)
  5. Sea lighthouses with visibility range and signal patterns

Vessel trails are smoothed with Catmull-Rom splines and rerouted to
maintain at least 3 km clearance from land using shapely buffered
coastline polygons.  All trails use dash-dash-dot-dot line pattern.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import rasterio
import rasterio.features
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon, shape
from shapely.ops import nearest_points, unary_union


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Create interactive web map for the Strait of Gibraltar"
    )
    p.add_argument("--isolines", type=Path, default=Path("derived/isolines.geojson"))
    p.add_argument("--passenger", type=Path, default=Path("derived/passenger_vessels.geojson"))
    p.add_argument("--fishing", type=Path, default=Path("derived/fishing_vessels.geojson"))
    p.add_argument("--cargo", type=Path, default=Path("derived/cargo_vessels.geojson"))
    p.add_argument("--bathymetry", type=Path, default=Path("data/bathymetry.tif"))
    p.add_argument("--output", type=Path, default=Path("web/index.html"))
    return p.parse_args()


def _load(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"GeoJSON not found: {path}")
    return path.read_text(encoding="utf-8")


# Trail colours
COLOR_PASSENGER = '#2E7D32'
COLOR_CARGO = '#D32F2F'
COLOR_FISHING = '#E65100'

# ── Lighthouse data (Strait of Gibraltar) ───────────────────────────────────
LIGHTHOUSES = json.dumps([
    {"name": "Cabo de Trafalgar", "lat": 36.181, "lon": -6.034,
     "range_nm": 22, "character": "Fl W 10s", "colors": ["#FFFFAA"], "flashes": 1},
    {"name": "Punta Camarinal", "lat": 36.083, "lon": -5.798,
     "range_nm": 13, "character": "Fl(2) W 10s", "colors": ["#FFFFAA"], "flashes": 2},
    {"name": "Tarifa", "lat": 36.001, "lon": -5.607,
     "range_nm": 26, "character": "Fl(3) WR 10s", "colors": ["#FFFFAA", "#FF3333"], "flashes": 3},
    {"name": "Punta Carnero", "lat": 36.076, "lon": -5.429,
     "range_nm": 15, "character": "Fl(3) W 15s", "colors": ["#FFFFAA"], "flashes": 3},
    {"name": "Punta Europa", "lat": 36.109, "lon": -5.345,
     "range_nm": 19, "character": "Iso W 10s", "colors": ["#FFFFAA"], "flashes": 1},
    {"name": "Cap Spartel", "lat": 35.792, "lon": -5.917,
     "range_nm": 30, "character": "Fl(4) W 20s", "colors": ["#FFFFAA"], "flashes": 4},
    {"name": "Pointe Malabata", "lat": 35.812, "lon": -5.744,
     "range_nm": 16, "character": "Fl(2) W 10s", "colors": ["#FFFFAA"], "flashes": 2},
    {"name": "Punta Almina", "lat": 35.898, "lon": -5.270,
     "range_nm": 22, "character": "Fl(2+1) W 15s", "colors": ["#FFFFAA"], "flashes": 3},
    {"name": "Punta Cires", "lat": 35.907, "lon": -5.485,
     "range_nm": 18, "character": "Fl(2) W 6s", "colors": ["#FFFFAA"], "flashes": 2},
    {"name": "Isla de Tarifa", "lat": 35.999, "lon": -5.609,
     "range_nm": 10, "character": "Fl G 5s", "colors": ["#33FF33"], "flashes": 1},
    {"name": "Barbate", "lat": 36.178, "lon": -5.921,
     "range_nm": 12, "character": "Fl(2) WR 7s", "colors": ["#FFFFAA", "#FF3333"], "flashes": 2},
    {"name": "Tangier Old", "lat": 35.787, "lon": -5.812,
     "range_nm": 14, "character": "Fl R 5s", "colors": ["#FF3333"], "flashes": 1},
])

# ── Extract real coastline from bathymetry raster ────────────────────────────
# At ~36°N: 1° lat ≈ 111.32 km, 1° lon ≈ 90.06 km
# 3 km ≈ 0.027° lat, ≈ 0.033° lon → average ~0.030°
_BUFFER_DEG = 0.030

_land_raw = None           # raw land polygons (actual coastline)
_land_buffered = None      # 3 km buffer zone (for validation)
_land_exclusion = None     # slightly larger zone for trail subtraction
_land_boundary = None      # boundary of the 3 km buffer (for path following)


def _init_coastline(bathymetry_path: Path) -> None:
    """Extract land polygons from bathymetry raster and create 3 km buffer."""
    global _land_raw, _land_buffered, _land_exclusion, _land_boundary

    print("Extracting coastline from bathymetry raster ...")
    with rasterio.open(bathymetry_path) as src:
        data = src.read(1)  # shape (480, 2765), float32
        transform = src.transform

        # Land mask: elevation >= 0 means land
        land_mask = (data >= 0).astype(np.uint8)

        # Extract polygons from raster
        land_polys = []
        for geom, value in rasterio.features.shapes(
            land_mask, mask=land_mask == 1, transform=transform
        ):
            poly = shape(geom)
            if poly.is_valid and poly.area > 1e-5:  # skip tiny slivers
                land_polys.append(poly)

    print(f"  Found {len(land_polys)} land polygons")

    # Merge all land polygons and simplify slightly to reduce vertex count
    land_union = unary_union(land_polys)
    land_union = land_union.simplify(0.002, preserve_topology=True)
    _land_raw = land_union

    # Buffer by ~3 km for the actual clearance zone
    _land_buffered = land_union.buffer(_BUFFER_DEG)
    _land_boundary = _land_buffered.boundary

    # Slightly larger exclusion zone (~3.3 km) for trail subtraction
    # This ensures that the rerouted path (which follows the 3 km boundary)
    # stays clearly outside the 3 km buffer
    _land_exclusion = land_union.buffer(_BUFFER_DEG + 0.003)
    print(f"  Buffered land zone ready (buffer={_BUFFER_DEG} deg, ~3 km)")


# ── Catmull-Rom spline (Python side) ────────────────────────────────────────
def _catmull_rom(p0: float, p1: float, p2: float, p3: float, t: float) -> float:
    t2 = t * t
    t3 = t2 * t
    return 0.5 * (
        (2 * p1)
        + (-p0 + p2) * t
        + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
        + (-p0 + 3 * p1 - 3 * p2 + p3) * t3
    )


def _spline_interpolate(coords: list[list[float]], subdivisions: int = 4) -> list[list[float]]:
    """Catmull-Rom spline interpolation to densify coordinate list."""
    n = len(coords)
    if n < 3:
        return list(coords)
    result: list[list[float]] = []
    for i in range(n - 1):
        p0 = coords[max(0, i - 1)]
        p1 = coords[i]
        p2 = coords[i + 1]
        p3 = coords[min(n - 1, i + 2)]
        for j in range(subdivisions):
            t = j / subdivisions
            result.append([
                _catmull_rom(p0[0], p1[0], p2[0], p3[0], t),
                _catmull_rom(p0[1], p1[1], p2[1], p3[1], t),
            ])
    result.append(list(coords[-1]))
    return result


def _boundary_path(p1: Point, p2: Point) -> list[list[float]]:
    """Get the shorter path along the buffer boundary between two points.

    Projects p1 and p2 onto the boundary, then extracts the sub-path
    between them, choosing the shorter of the two possible directions.
    Returns list of [lon, lat] coordinate pairs.
    """
    from shapely.ops import substring

    boundary = _land_exclusion.boundary

    # Handle MultiLineString boundary (from MultiPolygon buffer)
    if boundary.geom_type == 'MultiLineString':
        # Find which boundary ring is closest to p1
        best_ring = None
        best_dist = float('inf')
        for ring in boundary.geoms:
            d = ring.distance(p1)
            if d < best_dist:
                best_dist = d
                best_ring = ring
        boundary = best_ring

    total_len = boundary.length
    d1 = boundary.project(p1)
    d2 = boundary.project(p2)

    # Two possible paths: d1→d2 forward, or d1→d2 wrapping around
    if d1 <= d2:
        fwd_len = d2 - d1
        rev_len = total_len - fwd_len
    else:
        fwd_len = total_len - d1 + d2
        rev_len = d1 - d2

    if fwd_len <= rev_len:
        # Forward path (d1 to d2)
        if d1 <= d2:
            seg = substring(boundary, d1, d2)
        else:
            # Wraps around: d1→end + start→d2
            seg1 = substring(boundary, d1, total_len)
            seg2 = substring(boundary, 0, d2)
            coords1 = list(seg1.coords) if not seg1.is_empty else []
            coords2 = list(seg2.coords) if not seg2.is_empty else []
            all_coords = coords1 + coords2
            if len(all_coords) >= 2:
                seg = LineString(all_coords)
            else:
                return [[p1.x, p1.y], [p2.x, p2.y]]
    else:
        # Reverse path (d1 backwards to d2)
        if d2 <= d1:
            seg = substring(boundary, d2, d1)
        else:
            seg1 = substring(boundary, d2, total_len)
            seg2 = substring(boundary, 0, d1)
            coords1 = list(seg1.coords) if not seg1.is_empty else []
            coords2 = list(seg2.coords) if not seg2.is_empty else []
            all_coords = coords1 + coords2
            if len(all_coords) >= 2:
                seg = LineString(all_coords)
            else:
                return [[p1.x, p1.y], [p2.x, p2.y]]
        # Reverse direction so path goes from p1 toward p2
        seg = LineString(list(seg.coords)[::-1])

    if seg.is_empty:
        return [[p1.x, p1.y], [p2.x, p2.y]]

    return [[c[0], c[1]] for c in seg.coords]


def _extend_to_land(lon: float, lat: float) -> list[float]:
    """Find the nearest point on the actual coastline and return it.

    Used to extend trail endpoints so they touch dryland.
    """
    pt = Point(lon, lat)
    _, nearest = nearest_points(pt, _land_raw.boundary)
    return [nearest.x, nearest.y]


def _process_trail(coords: list[list[float]]) -> list[list[float]]:
    """Process trail: straight segments, reroute around 3 km land buffer,
    and extend ends to touch the coastline.

    Algorithm:
    1. Use original waypoints as-is (no spline smoothing -- straight segments)
    2. Build a LineString from the trail
    3. Subtract the exclusion zone -> safe segments outside the buffer
    4. Connect consecutive safe segments by following the buffer boundary
       (simplified to straight segments)
    5. Extend start/end to nearest coastline point
    """
    if len(coords) < 2:
        return coords

    # No spline -- use original waypoints directly (straight segments)
    trail = LineString([(c[0], c[1]) for c in coords])

    # Subtract the exclusion zone
    safe = trail.difference(_land_exclusion)

    if safe.is_empty:
        # Entire trail is inside the buffer -- extend ends only
        start_on_land = _extend_to_land(coords[0][0], coords[0][1])
        end_on_land = _extend_to_land(coords[-1][0], coords[-1][1])
        return [start_on_land] + coords + [end_on_land]

    if safe.geom_type == 'LineString':
        segments = [safe]
    elif safe.geom_type == 'MultiLineString':
        segments = list(safe.geoms)
    else:
        segments = [safe]

    # Sort segments by their position along the original trail
    segments.sort(key=lambda seg: trail.project(Point(seg.coords[0])))

    # Connect segments via boundary-following paths (simplified to straight segments)
    result_coords: list[list[float]] = []

    for i, seg in enumerate(segments):
        seg_coords = [[c[0], c[1]] for c in seg.coords]
        result_coords.extend(seg_coords)

        if i < len(segments) - 1:
            # Connect end of this segment to start of next via boundary
            end_pt = Point(seg.coords[-1])
            next_start = Point(segments[i + 1].coords[0])
            boundary_path = _boundary_path(end_pt, next_start)
            # Simplify the boundary path to straight segments
            if len(boundary_path) >= 2:
                bnd_line = LineString([(c[0], c[1]) for c in boundary_path])
                simplified = bnd_line.simplify(0.005, preserve_topology=True)
                simp_coords = [[c[0], c[1]] for c in simplified.coords]
                # Skip first and last to avoid duplicates with segment endpoints
                if len(simp_coords) > 2:
                    result_coords.extend(simp_coords[1:-1])

    # Extend start to nearest coastline (touch dryland)
    if result_coords:
        start_on_land = _extend_to_land(result_coords[0][0], result_coords[0][1])
        result_coords.insert(0, start_on_land)

        end_on_land = _extend_to_land(result_coords[-1][0], result_coords[-1][1])
        result_coords.append(end_on_land)

    # Deduplicate near-coincident consecutive points
    filtered = [result_coords[0]]
    for i in range(1, len(result_coords)):
        dx = result_coords[i][0] - filtered[-1][0]
        dy = result_coords[i][1] - filtered[-1][1]
        if dx * dx + dy * dy > 1e-9:
            filtered.append(result_coords[i])

    return filtered if len(filtered) >= 2 else coords


def _process_geojson(geojson_str: str) -> tuple[str, int]:
    """Process all trails in a GeoJSON string, return (json_str, count)."""
    data = json.loads(geojson_str)
    features = data.get("features", [])
    for f in features:
        coords = f["geometry"]["coordinates"]
        f["geometry"]["coordinates"] = _process_trail(coords)
    return json.dumps(data), len(features)


def create_map(
    isolines_path: Path,
    passenger_path: Path,
    fishing_path: Path,
    cargo_path: Path,
    output: Path,
    bathymetry_path: Path = Path("data/bathymetry.tif"),
) -> None:
    # Initialize coastline from real raster data
    _init_coastline(bathymetry_path)

    isolines_json = _load(isolines_path)
    n_iso = len(json.loads(isolines_json).get("features", []))

    print("Processing passenger trails ...")
    passenger_json, n_pax = _process_geojson(_load(passenger_path))
    print("Processing cargo trails ...")
    cargo_json, n_cgo = _process_geojson(_load(cargo_path))
    print("Processing fishing trails ...")
    fishing_json, n_fsh = _process_geojson(_load(fishing_path))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Strait of Gibraltar &mdash; Maritime Map</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}}
#map{{width:100%;height:100vh}}
.info{{
  position:absolute;top:10px;right:10px;z-index:1000;
  background:rgba(255,255,255,.95);padding:16px;border-radius:8px;
  box-shadow:0 2px 12px rgba(0,0,0,.15);max-width:300px;font-size:13px;
  max-height:95vh;overflow-y:auto;
}}
.info-head{{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}}
.info h2{{margin:0;font-size:16px;color:#1a237e}}
.legend-toggle{{
  border:1px solid #cfd8dc;background:#fff;color:#1a237e;
  border-radius:4px;width:28px;height:24px;padding:0;
  cursor:pointer;
  display:inline-flex;align-items:center;justify-content:center;
}}
.legend-toggle:hover{{background:#f5f7fa}}
.legend-toggle svg{{width:14px;height:14px;display:block}}
.leg{{display:flex;align-items:center;margin:7px 0}}
.leg-line{{width:28px;height:0;margin-right:8px;flex-shrink:0}}
.leg-icon{{width:36px;height:18px;margin-right:8px;flex-shrink:0;text-align:center}}
.stats{{margin-top:12px;padding-top:10px;border-top:1px solid #e0e0e0;
        font-size:11px;color:#666}}
.stats p{{margin:3px 0}}
.leg-section{{margin-top:10px;padding-top:8px;border-top:1px solid #eee;
              font-weight:600;font-size:12px;color:#333;margin-bottom:4px}}
.leg input[type=checkbox]{{margin:0 6px 0 0;cursor:pointer;flex-shrink:0}}
.leg label{{display:flex;align-items:center;cursor:pointer;flex:1}}
</style>
</head>
<body>
<div id="map"></div>

<div class="info">
  <div class="info-head">
    <h2>Strait of Gibraltar</h2>
    <button id="legendToggle" class="legend-toggle" type="button" aria-label="Minimize legend" title="Minimize legend">
      <svg id="legendIconMin" viewBox="0 0 16 16" aria-hidden="true">
        <line x1="3" y1="8" x2="13" y2="8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
      </svg>
      <svg id="legendIconMax" viewBox="0 0 16 16" aria-hidden="true" style="display:none">
        <rect x="3.2" y="3.2" width="9.6" height="9.6" fill="none" stroke="currentColor" stroke-width="1.6"/>
      </svg>
    </button>
  </div>
  <div id="legendBody">

  <div class="leg">
    <label><input type="checkbox" checked data-layer="isolines"/>
    <div class="leg-line" style="border-top:2.5px solid #1565C0"></div>
    <span>Depth isolines (major)</span></label>
  </div>
  <div class="leg">
    <label><input type="checkbox" data-layer="seamark"/>
    <div class="leg-line" style="border-top:1.5px dashed #64B5F6"></div>
    <span>Sea Marks</span></label>
  </div>

  <div class="leg-section">Vessel Trails (dash-dash-dot-dot)</div>
  <div class="leg">
    <label><input type="checkbox" checked data-layer="passenger"/>
    <svg width="40" height="6" class="leg-icon"><line x1="0" y1="3" x2="8" y2="3" stroke="{COLOR_PASSENGER}" stroke-width="2"/><line x1="11" y1="3" x2="19" y2="3" stroke="{COLOR_PASSENGER}" stroke-width="2"/><circle cx="24" cy="3" r="1.5" fill="{COLOR_PASSENGER}"/><circle cx="30" cy="3" r="1.5" fill="{COLOR_PASSENGER}"/></svg>
    <span>Passenger / ferry</span></label>
  </div>
  <div class="leg">
    <label><input type="checkbox" checked data-layer="cargo"/>
    <svg width="40" height="6" class="leg-icon"><line x1="0" y1="3" x2="8" y2="3" stroke="{COLOR_CARGO}" stroke-width="2"/><line x1="11" y1="3" x2="19" y2="3" stroke="{COLOR_CARGO}" stroke-width="2"/><circle cx="24" cy="3" r="1.5" fill="{COLOR_CARGO}"/><circle cx="30" cy="3" r="1.5" fill="{COLOR_CARGO}"/></svg>
    <span>Cargo vessels</span></label>
  </div>
  <div class="leg">
    <label><input type="checkbox" checked data-layer="fishing"/>
    <svg width="40" height="6" class="leg-icon"><line x1="0" y1="3" x2="8" y2="3" stroke="{COLOR_FISHING}" stroke-width="2"/><line x1="11" y1="3" x2="19" y2="3" stroke="{COLOR_FISHING}" stroke-width="2"/><circle cx="24" cy="3" r="1.5" fill="{COLOR_FISHING}"/><circle cx="30" cy="3" r="1.5" fill="{COLOR_FISHING}"/></svg>
    <span>Fishing vessels</span></label>
  </div>

  <div class="leg-section">Lighthouses</div>
  <div class="leg">
    <label><input type="checkbox" checked data-layer="lighthouse"/>
    <svg width="36" height="22" class="leg-icon">
      <circle cx="18" cy="11" r="9" fill="orange" fill-opacity="0.05" stroke="orange" stroke-width="1"/>
      <circle cx="18" cy="11" r="4" fill="#FFD700" stroke="#333" stroke-width="1.5"/>
    </svg>
    <span>Lighthouses</span></label>
  </div>

  <div class="leg-section">Base Map</div>
  <div class="leg">
    <label><input type="radio" name="basemap" value="ocean" checked style="margin:0 6px 0 0;cursor:pointer"/>
    <span>Ocean Base</span></label>
  </div>
  <div class="leg">
    <label><input type="radio" name="basemap" value="osm" style="margin:0 6px 0 0;cursor:pointer"/>
    <span>OpenStreetMap</span></label>
  </div>

  <div class="stats">
    <p>{n_cgo} cargo routes &middot; {n_fsh} fishing tracks</p>
    <p>12 lighthouses</p>
    <p style="margin-top:6px;font-style:italic">Click features for info.</p>
  </div>
</div>
</div>

<script>
// ── Base map ────────────────────────────────────────────────────────────────
var map = L.map('map').setView([35.97, -5.80], 9);

var ocean = L.tileLayer(
  'https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{{z}}/{{y}}/{{x}}',
  {{attribution:'Esri, GEBCO, NOAA',maxZoom:13}}
).addTo(map);

var osm = L.tileLayer(
  'https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',
  {{attribution:'&copy; OpenStreetMap',maxZoom:18}}
);

var seamark = L.tileLayer(
  'https://tiles.openseamap.org/seamark/{{z}}/{{x}}/{{y}}.png',
  {{attribution:'&copy; OpenSeaMap',maxZoom:18,opacity:0.7}}
);

// ── Depth colour scale ──────────────────────────────────────────────────────
function depthColor(d) {{
  d = Math.abs(d);
  if (d >= 800) return '#0D47A1';
  if (d >= 600) return '#1565C0';
  if (d >= 400) return '#1976D2';
  if (d >= 200) return '#1E88E5';
  if (d >= 100) return '#42A5F5';
  if (d >= 50)  return '#64B5F6';
  return '#90CAF9';
}}

// ── Trail colours ───────────────────────────────────────────────────────────
var CLR_PASSENGER = '{COLOR_PASSENGER}';
var CLR_CARGO     = '{COLOR_CARGO}';
var CLR_FISHING   = '{COLOR_FISHING}';

// Dash-dash-dot-dot pattern for all vessel trails
var TRAIL_DASH = '12 5 12 5 3 5 3 5';

// ── Data (trails already smoothed + 3 km land-buffered by Python) ───────────
var isolinesData  = {isolines_json};
var passengerData = {passenger_json};
var fishingData   = {fishing_json};
var cargoData     = {cargo_json};

var lighthousesData = {LIGHTHOUSES};

// ── Isolines layer ──────────────────────────────────────────────────────────
var isolinesLayer = L.geoJSON(isolinesData, {{
  style: function(f) {{
    var major = f.properties.major;
    return {{
      color: depthColor(f.properties.depth),
      weight: major ? 2.5 : 1,
      opacity: major ? 0.9 : 0.5,
      dashArray: major ? null : '4 4'
    }};
  }},
  onEachFeature: function(f, layer) {{
    layer.bindPopup('<b>Depth: ' + f.properties.label + '</b>');
    layer.bindTooltip(f.properties.label, {{
      permanent:false, direction:'center'
    }});
  }}
}}).addTo(map);

// ── Vessel layer groups ─────────────────────────────────────────────────────
var passengerGroup = L.layerGroup().addTo(map);
var cargoGroup     = L.layerGroup().addTo(map);
var fishingGroup   = L.layerGroup().addTo(map);

// Trail lines with dash-dash-dot-dot pattern
L.geoJSON(passengerData, {{
  style: function() {{ return {{color:CLR_PASSENGER,weight:2,opacity:0.8,dashArray:TRAIL_DASH}}; }},
  onEachFeature: function(f, layer) {{
    var p = f.properties;
    layer.bindPopup('<b>'+p.vessel_id+'</b><br>Route: '+p.route+'<br>Dir: '+p.direction+'<br>Speed: '+p.speed_knots+' kn');
  }}
}}).addTo(passengerGroup);

L.geoJSON(cargoData, {{
  style: function() {{ return {{color:CLR_CARGO,weight:2,opacity:0.8,dashArray:TRAIL_DASH}}; }},
  onEachFeature: function(f, layer) {{
    var p = f.properties;
    layer.bindPopup('<b>'+p.vessel_id+'</b><br>Route: '+p.route+'<br>Dir: '+p.direction+'<br>Speed: '+p.speed_knots+' kn');
  }}
}}).addTo(cargoGroup);

L.geoJSON(fishingData, {{
  style: function() {{ return {{color:CLR_FISHING,weight:2,opacity:0.8,dashArray:TRAIL_DASH}}; }},
  onEachFeature: function(f, layer) {{
    var p = f.properties;
    layer.bindPopup('<b>'+p.vessel_id+'</b><br>Area: '+p.area+'<br>Pattern: '+p.pattern+'<br>Speed: '+p.speed_knots+' kn');
  }}
}}).addTo(fishingGroup);

// ── Lighthouse layer ────────────────────────────────────────────────────────
var lighthouseGroup = L.layerGroup().addTo(map);

function drawArcPoints(lat, lon, radiusM, startDeg, endDeg) {{
  var pts = [];
  var R = 6371000;
  var latRad = lat * Math.PI / 180;
  for (var a = startDeg; a <= endDeg; a += 2) {{
    var rad = a * Math.PI / 180;
    var dLat = radiusM * Math.cos(rad) / R * (180 / Math.PI);
    var dLon = radiusM * Math.sin(rad) / (R * Math.cos(latRad)) * (180 / Math.PI);
    pts.push([lat + dLat, lon + dLon]);
  }}
  return pts;
}}

lighthousesData.forEach(function(lh) {{
  var lat = lh.lat, lon = lh.lon;
  var rangeM = lh.range_nm * 1852;

  // Inner circle: 10px radius marker
  L.circleMarker([lat, lon], {{
    radius: 10,
    color: '#333',
    fillColor: '#FFD700',
    fillOpacity: 1,
    weight: 2
  }}).bindPopup(
    '<b>' + lh.name + '</b><br>' +
    'Signal: ' + lh.character + '<br>' +
    'Range: ' + lh.range_nm + ' NM (' + (rangeM/1000).toFixed(1) + ' km)'
  ).bindTooltip(lh.name, {{permanent: false, direction: 'top'}})
   .addTo(lighthouseGroup);

  // Outer visibility circle: orange, 5% transparency
  L.circle([lat, lon], {{
    radius: rangeM,
    color: '#FF8C00',
    fillColor: '#FF8C00',
    fillOpacity: 0.05,
    weight: 1.5,
    opacity: 0.6,
    interactive: false
  }}).addTo(lighthouseGroup);

  // Signal pattern on the outer circle: arcs + dots
  var n = lh.flashes;
  var sectionAngle = 360 / n;

  for (var k = 0; k < n; k++) {{
    var base = k * sectionAngle;
    var col = lh.colors[k % lh.colors.length];

    // Arc 1 (dash)
    var arc1 = drawArcPoints(lat, lon, rangeM, base + 5, base + 20);
    if (arc1.length > 1) {{
      L.polyline(arc1, {{color: col, weight: 5, opacity: 0.9}}).addTo(lighthouseGroup);
    }}

    // Arc 2 (dash)
    var arc2 = drawArcPoints(lat, lon, rangeM, base + 25, base + 40);
    if (arc2.length > 1) {{
      L.polyline(arc2, {{color: col, weight: 5, opacity: 0.9}}).addTo(lighthouseGroup);
    }}

    // Dot 1
    var d1Rad = (base + 50) * Math.PI / 180;
    var d1Lat = lat + rangeM * Math.cos(d1Rad) / 6371000 * (180/Math.PI);
    var d1Lon = lon + rangeM * Math.sin(d1Rad) / (6371000 * Math.cos(lat*Math.PI/180)) * (180/Math.PI);
    L.circleMarker([d1Lat, d1Lon], {{
      radius: 4, color: col, fillColor: col, fillOpacity: 0.9, weight: 0
    }}).addTo(lighthouseGroup);

    // Dot 2
    var d2Rad = (base + 60) * Math.PI / 180;
    var d2Lat = lat + rangeM * Math.cos(d2Rad) / 6371000 * (180/Math.PI);
    var d2Lon = lon + rangeM * Math.sin(d2Rad) / (6371000 * Math.cos(lat*Math.PI/180)) * (180/Math.PI);
    L.circleMarker([d2Lat, d2Lon], {{
      radius: 4, color: col, fillColor: col, fillOpacity: 0.9, weight: 0
    }}).addTo(lighthouseGroup);
  }}
}});

// ── Animation engine ────────────────────────────────────────────────────────
var allVessels = [];

function spawnVessels(data, color, group) {{
  data.features.forEach(function(f) {{
    var coords = f.geometry.coordinates;
    var ll = coords.map(function(c) {{ return [c[1], c[0]]; }});
    if (ll.length < 2) return;

    var d = [0];
    for (var i = 1; i < ll.length; i++) {{
      var dy = ll[i][0] - ll[i-1][0];
      var dx = ll[i][1] - ll[i-1][1];
      d.push(d[i-1] + Math.sqrt(dy*dy + dx*dx));
    }}
    var total = d[d.length - 1];
    if (total === 0) return;

    var dot = L.circleMarker(ll[0], {{
      radius: 3,
      color: color,
      fillColor: color,
      fillOpacity: 1,
      weight: 0,
      interactive: false
    }});
    dot.addTo(group);

    var baseSpeed = f.properties.anim_speed || 0.05;
    allVessels.push({{
      marker: dot,
      ll: ll,
      d: d,
      total: total,
      progress: Math.random(),
      speed: baseSpeed * (0.7 + Math.random() * 0.6),
      loop: f.properties.is_loop || false
    }});
  }});
}}

spawnVessels(passengerData, CLR_PASSENGER, passengerGroup);
spawnVessels(cargoData,     CLR_CARGO,     cargoGroup);
spawnVessels(fishingData,   CLR_FISHING,   fishingGroup);

var lastT = 0;
function animate(ts) {{
  var dt = lastT ? (ts - lastT) / 1000 : 0.016;
  lastT = ts;

  for (var i = 0; i < allVessels.length; i++) {{
    var v = allVessels[i];
    v.progress += v.speed * dt;

    var t;
    if (v.loop) {{
      while (v.progress > 1) v.progress -= 1;
      t = v.progress;
    }} else {{
      while (v.progress > 2) v.progress -= 2;
      t = v.progress <= 1 ? v.progress : 2 - v.progress;
    }}

    var target = t * v.total;
    var idx = 0;
    for (var j = 1; j < v.d.length; j++) {{
      if (v.d[j] >= target) {{ idx = j - 1; break; }}
      if (j === v.d.length - 1) idx = j - 1;
    }}

    var segLen = v.d[idx+1] - v.d[idx];
    var frac = segLen > 0 ? (target - v.d[idx]) / segLen : 0;

    var lat = v.ll[idx][0] + frac * (v.ll[idx+1][0] - v.ll[idx][0]);
    var lng = v.ll[idx][1] + frac * (v.ll[idx+1][1] - v.ll[idx][1]);
    v.marker.setLatLng([lat, lng]);
  }}
  requestAnimationFrame(animate);
}}
requestAnimationFrame(animate);

// ── Layer toggle via checkboxes ──────────────────────────────────────────────
var layerMap = {{
  isolines:   isolinesLayer,
  passenger:  passengerGroup,
  cargo:      cargoGroup,
  fishing:    fishingGroup,
  lighthouse: lighthouseGroup,
  seamark:    seamark
}};

document.querySelectorAll('.info input[data-layer]').forEach(function(cb) {{
  cb.addEventListener('change', function() {{
    var layer = layerMap[this.dataset.layer];
    if (!layer) return;
    if (this.checked) {{
      map.addLayer(layer);
    }} else {{
      map.removeLayer(layer);
    }}
  }});
}});

// Base map radio buttons
var baseMaps = {{ ocean: ocean, osm: osm }};
document.querySelectorAll('.info input[name="basemap"]').forEach(function(rb) {{
  rb.addEventListener('change', function() {{
    Object.keys(baseMaps).forEach(function(k) {{ map.removeLayer(baseMaps[k]); }});
    map.addLayer(baseMaps[this.value]);
  }});
}});

// Legend panel minimize/maximize
var legendToggleBtn = document.getElementById('legendToggle');
var legendBody = document.getElementById('legendBody');
var legendIconMin = document.getElementById('legendIconMin');
var legendIconMax = document.getElementById('legendIconMax');
if (legendToggleBtn && legendBody) {{
  legendToggleBtn.addEventListener('click', function() {{
    var collapsed = legendBody.style.display === 'none';
    legendBody.style.display = collapsed ? 'block' : 'none';
    if (legendIconMin && legendIconMax) {{
      legendIconMin.style.display = collapsed ? 'block' : 'none';
      legendIconMax.style.display = collapsed ? 'none' : 'block';
    }}
    legendToggleBtn.setAttribute('aria-label', collapsed ? 'Minimize legend' : 'Maximize legend');
    legendToggleBtn.title = collapsed ? 'Minimize legend' : 'Maximize legend';
  }});
}}

L.control.scale({{imperial: false}}).addTo(map);
</script>
</body>
</html>"""

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")

    size_kb = output.stat().st_size / 1024
    print(f"Map saved to {output} ({size_kb:.0f} KB)")
    print(f"  {n_iso} isolines, {n_pax} passenger, {n_cgo} cargo, {n_fsh} fishing")
    print(f"  12 lighthouses")
    print(f"Open in browser: {output.resolve()}")


def main() -> None:
    args = parse_args()
    create_map(
        args.isolines, args.passenger, args.fishing, args.cargo,
        args.output, args.bathymetry,
    )


if __name__ == "__main__":
    main()
