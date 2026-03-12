"""
Generate vessel traffic data for the Strait of Gibraltar.

Creates realistic vessel route GeoJSON based on known shipping lanes
and fishing areas.  Uses bathymetry data to ensure routes stay over water.

For production use, replace with real AIS data from:
  - Global Fishing Watch API:  https://globalfishingwatch.org/our-apis/
  - EMODnet Human Activities:  https://emodnet.ec.europa.eu/en/human-activities
  - Marine Traffic API:        https://www.marinetraffic.com/en/ais-api-services
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import numpy as np
import rasterio
from shapely.geometry import LineString, mapping

# ── Known ferry routes (lon, lat waypoints) ──────────────────────────────────

FERRY_ROUTES = {
    "Algeciras - Tangier Med": [
        (-5.4400, 36.1200),
        (-5.4500, 36.0600),
        (-5.4700, 36.0000),
        (-5.4900, 35.9400),
        (-5.5050, 35.8800),
    ],
    "Algeciras - Ceuta": [
        (-5.4400, 36.1200),
        (-5.4100, 36.0600),
        (-5.3700, 36.0000),
        (-5.3300, 35.9400),
        (-5.3100, 35.8900),
    ],
    "Tarifa - Tangier": [
        (-5.6050, 36.0050),
        (-5.6200, 35.9700),
        (-5.6500, 35.9200),
        (-5.6900, 35.8600),
        (-5.7500, 35.7900),
    ],
    "Gibraltar - Tangier Med": [
        (-5.3600, 36.1300),
        (-5.3800, 36.0700),
        (-5.4100, 36.0000),
        (-5.4600, 35.9300),
        (-5.5000, 35.8800),
    ],
}

# ── IMO Traffic Separation Scheme ────────────────────────────────────────────

TSS_EASTBOUND = [
    (-7.2000, 35.9000),
    (-6.5000, 35.9100),
    (-5.7800, 35.9250),
    (-5.6500, 35.9300),
    (-5.5000, 35.9500),
    (-5.3500, 35.9700),
    (-5.2000, 35.9850),
    (-4.8000, 36.0000),
]

TSS_WESTBOUND = [
    (-4.8000, 35.9600),
    (-5.2000, 35.9400),
    (-5.3500, 35.9200),
    (-5.5000, 35.9000),
    (-5.6500, 35.8850),
    (-5.7800, 35.8750),
    (-6.5000, 35.8600),
    (-7.2000, 35.8500),
]

# ── Cargo routes ─────────────────────────────────────────────────────────────

CARGO_ROUTES = {
    "Atlantic - Med (north)": [
        (-7.2000, 35.9400),
        (-6.5000, 35.9450),
        (-5.7800, 35.9500),
        (-5.4800, 35.9600),
        (-5.2000, 35.9800),
        (-4.8000, 36.0000),
    ],
    "Atlantic - Med (south)": [
        (-7.2000, 35.9000),
        (-6.5000, 35.9050),
        (-5.7800, 35.9100),
        (-5.4800, 35.9200),
        (-5.2000, 35.9400),
        (-4.8000, 35.9500),
    ],
    "Med - Atlantic (north)": [
        (-4.8000, 35.9800),
        (-5.2000, 35.9700),
        (-5.4800, 35.9500),
        (-5.7800, 35.9300),
        (-6.5000, 35.9200),
        (-7.2000, 35.9100),
    ],
    "Med - Atlantic (south)": [
        (-4.8000, 35.9400),
        (-5.2000, 35.9200),
        (-5.4800, 35.9000),
        (-5.7800, 35.8800),
        (-6.5000, 35.8700),
        (-7.2000, 35.8600),
    ],
    "Algeciras approach (E)": [
        (-5.3000, 35.9600),
        (-5.3500, 35.9900),
        (-5.3900, 36.0300),
        (-5.4200, 36.0700),
        (-5.4400, 36.1100),
    ],
    "Algeciras approach (W)": [
        (-5.5500, 35.9400),
        (-5.5200, 35.9700),
        (-5.4900, 36.0100),
        (-5.4600, 36.0600),
        (-5.4400, 36.1100),
    ],
}

# ── Fishing areas (tighter radii, centres firmly over water) ─────────────────

FISHING_AREAS = {
    "Bay of Algeciras": {"center": (-5.38, 36.05), "radius": 0.025},
    "Tarifa Waters": {"center": (-5.60, 35.96), "radius": 0.025},
    "Northern Morocco": {"center": (-5.42, 35.87), "radius": 0.020},
    "Mid-Strait": {"center": (-5.50, 35.93), "radius": 0.030},
    "Western Approach": {"center": (-5.70, 35.91), "radius": 0.025},
    "Eastern Approach": {"center": (-5.25, 35.96), "radius": 0.025},
}


# ── Water validation using bathymetry ────────────────────────────────────────


class WaterValidator:
    """Sample bathymetry raster to keep vessel routes over water."""

    def __init__(self, bathymetry_path: Path | None = None):
        self.active = False
        if bathymetry_path and bathymetry_path.exists():
            with rasterio.open(bathymetry_path) as src:
                self.data = src.read(1)
                self.transform = src.transform
                self.shape = (src.height, src.width)
            self.active = True
            print(f"  Water validator loaded ({bathymetry_path})")
        else:
            print("  No bathymetry file — skipping water validation")

    def is_water(self, lon: float, lat: float) -> bool:
        if not self.active:
            return True
        col = int((lon - self.transform.c) / self.transform.a)
        row = int((lat - self.transform.f) / self.transform.e)
        if 0 <= row < self.shape[0] and 0 <= col < self.shape[1]:
            return float(self.data[row, col]) < 0
        return True

    def filter_coords(self, coords: list[tuple]) -> list[tuple]:
        """Remove coordinates that fall on land."""
        if not self.active:
            return coords
        return [(lon, lat) for lon, lat in coords if self.is_water(lon, lat)]


# ── Helpers ──────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate vessel traffic data for the Strait of Gibraltar"
    )
    p.add_argument(
        "--passenger-output",
        type=Path,
        default=Path("derived/passenger_vessels.geojson"),
    )
    p.add_argument(
        "--fishing-output",
        type=Path,
        default=Path("derived/fishing_vessels.geojson"),
    )
    p.add_argument(
        "--cargo-output",
        type=Path,
        default=Path("derived/cargo_vessels.geojson"),
    )
    p.add_argument(
        "--bathymetry",
        type=Path,
        default=Path("data/bathymetry.tif"),
        help="Bathymetry raster for water validation",
    )
    p.add_argument("--n-passenger", type=int, default=16)
    p.add_argument("--n-fishing", type=int, default=24)
    p.add_argument("--n-cargo", type=int, default=12)
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args()


def _jitter(waypoints: list[tuple], spread: float = 0.005) -> list[tuple]:
    return [
        (lon + random.gauss(0, spread), lat + random.gauss(0, spread))
        for lon, lat in waypoints
    ]


# ── Passenger routes ─────────────────────────────────────────────────────────


def generate_passenger_routes(n: int, water: WaterValidator) -> dict:
    route_names = list(FERRY_ROUTES.keys())
    features: list[dict] = []

    for i in range(n):
        name = route_names[i % len(route_names)]
        base = list(FERRY_ROUTES[name])
        if i % 2 == 1:
            base = list(reversed(base))
            direction = "return"
        else:
            direction = "outbound"

        coords = water.filter_coords(_jitter(base, spread=0.003))
        if len(coords) < 2:
            continue

        features.append(
            {
                "type": "Feature",
                "properties": {
                    "vessel_type": "passenger",
                    "route": name,
                    "direction": direction,
                    "vessel_id": f"PAX-{i + 1:03d}",
                    "speed_knots": round(random.uniform(18, 28), 1),
                    "is_loop": False,
                    "anim_speed": 0.08,
                },
                "geometry": mapping(LineString(coords)),
            }
        )

    # TSS cruise ships
    for i in range(max(2, n // 4)):
        base = TSS_EASTBOUND if i % 2 == 0 else TSS_WESTBOUND
        direction = "eastbound" if i % 2 == 0 else "westbound"
        coords = water.filter_coords(_jitter(base, spread=0.004))
        if len(coords) < 2:
            continue

        features.append(
            {
                "type": "Feature",
                "properties": {
                    "vessel_type": "passenger",
                    "route": f"TSS transit ({direction})",
                    "direction": direction,
                    "vessel_id": f"CRZ-{i + 1:03d}",
                    "speed_knots": round(random.uniform(15, 22), 1),
                    "is_loop": False,
                    "anim_speed": 0.06,
                },
                "geometry": mapping(LineString(coords)),
            }
        )

    return {"type": "FeatureCollection", "features": features}


# ── Cargo routes ─────────────────────────────────────────────────────────────


def generate_cargo_routes(n: int, water: WaterValidator) -> dict:
    route_names = list(CARGO_ROUTES.keys())
    features: list[dict] = []

    for i in range(n):
        name = route_names[i % len(route_names)]
        base = list(CARGO_ROUTES[name])
        if i % 2 == 1:
            base = list(reversed(base))
            direction = "return"
        else:
            direction = "outbound"

        coords = water.filter_coords(_jitter(base, spread=0.004))
        if len(coords) < 2:
            continue

        features.append(
            {
                "type": "Feature",
                "properties": {
                    "vessel_type": "cargo",
                    "route": name,
                    "direction": direction,
                    "vessel_id": f"CGO-{i + 1:03d}",
                    "speed_knots": round(random.uniform(10, 18), 1),
                    "is_loop": False,
                    "anim_speed": 0.05,
                },
                "geometry": mapping(LineString(coords)),
            }
        )

    return {"type": "FeatureCollection", "features": features}


# ── Fishing tracks ───────────────────────────────────────────────────────────


def _fishing_circle(
    center: tuple[float, float], radius: float
) -> list[tuple[float, float]]:
    n = 30
    angles = np.linspace(0, 2 * np.pi, n)
    rx = radius * (1 + 0.2 * np.random.randn())
    ry = radius * (1 + 0.2 * np.random.randn())
    rot = random.uniform(0, np.pi)
    coords = []
    for a in angles:
        x = center[0] + rx * np.cos(a) * np.cos(rot) - ry * np.sin(a) * np.sin(rot)
        y = center[1] + rx * np.cos(a) * np.sin(rot) + ry * np.sin(a) * np.cos(rot)
        coords.append(
            (x + random.gauss(0, radius * 0.02), y + random.gauss(0, radius * 0.02))
        )
    coords.append(coords[0])
    return coords


def _fishing_zigzag(
    center: tuple[float, float], extent: float
) -> list[tuple[float, float]]:
    n_legs = random.randint(5, 8)
    coords = []
    for i in range(n_legs):
        x = center[0] - extent + (i / n_legs) * 2 * extent
        y = (
            center[1] - extent * random.uniform(0.2, 0.5)
            if i % 2 == 0
            else center[1] + extent * random.uniform(0.2, 0.5)
        )
        coords.append(
            (x + random.gauss(0, extent * 0.02), y + random.gauss(0, extent * 0.02))
        )
    return coords


def generate_fishing_tracks(n: int, water: WaterValidator) -> dict:
    areas = list(FISHING_AREAS.items())
    features: list[dict] = []

    for i in range(n):
        area_name, area = areas[i % len(areas)]
        center = (
            area["center"][0] + random.gauss(0, area["radius"] * 0.2),
            area["center"][1] + random.gauss(0, area["radius"] * 0.2),
        )
        r = area["radius"] * random.uniform(0.4, 0.8)

        if i % 3 == 0:
            coords = _fishing_zigzag(center, r)
            pattern = "trawling"
            is_loop = False
        else:
            coords = _fishing_circle(center, r)
            pattern = "circling"
            is_loop = True

        coords = water.filter_coords(coords)
        if len(coords) < 3:
            continue

        if is_loop and coords[0] != coords[-1]:
            coords.append(coords[0])

        features.append(
            {
                "type": "Feature",
                "properties": {
                    "vessel_type": "fishing",
                    "area": area_name,
                    "vessel_id": f"FSH-{i + 1:03d}",
                    "speed_knots": round(random.uniform(3, 8), 1),
                    "pattern": pattern,
                    "is_loop": is_loop,
                    "anim_speed": 0.03,
                },
                "geometry": mapping(LineString(coords)),
            }
        )

    return {"type": "FeatureCollection", "features": features}


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    args = parse_args()
    random.seed(args.seed)
    np.random.seed(args.seed)

    water = WaterValidator(args.bathymetry)

    print(f"Generating {args.n_passenger} passenger vessel routes...")
    passenger = generate_passenger_routes(args.n_passenger, water)
    args.passenger_output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.passenger_output, "w") as f:
        json.dump(passenger, f)
    print(f"  Saved {len(passenger['features'])} routes to {args.passenger_output}")

    print(f"Generating {args.n_cargo} cargo vessel routes...")
    cargo = generate_cargo_routes(args.n_cargo, water)
    args.cargo_output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.cargo_output, "w") as f:
        json.dump(cargo, f)
    print(f"  Saved {len(cargo['features'])} routes to {args.cargo_output}")

    print(f"Generating {args.n_fishing} fishing vessel tracks...")
    fishing = generate_fishing_tracks(args.n_fishing, water)
    args.fishing_output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.fishing_output, "w") as f:
        json.dump(fishing, f)
    print(f"  Saved {len(fishing['features'])} tracks to {args.fishing_output}")


if __name__ == "__main__":
    main()
