"""
Generate bathymetric contour lines (isolines) from a bathymetry GeoTIFF.

Reads the raster, generates contour lines at specified depth intervals,
and outputs GeoJSON for use in the web map.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
from scipy.ndimage import gaussian_filter
from shapely.geometry import LineString, mapping
from skimage.measure import find_contours


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate bathymetric isolines from a raster"
    )
    p.add_argument(
        "--input",
        type=Path,
        default=Path("data/bathymetry.tif"),
        help="Input bathymetry GeoTIFF",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("derived/isolines.geojson"),
        help="Output GeoJSON file",
    )
    p.add_argument(
        "--interval",
        type=float,
        default=50,
        help="Contour interval in meters (default: 50)",
    )
    p.add_argument(
        "--min-depth",
        type=float,
        default=-1000,
        help="Deepest contour level in meters (default: -1000)",
    )
    p.add_argument(
        "--max-depth",
        type=float,
        default=0,
        help="Shallowest contour level in meters (default: 0)",
    )
    p.add_argument(
        "--smooth",
        type=float,
        default=1.0,
        help="Gaussian smoothing sigma in pixels (default: 1.0, 0 to disable)",
    )
    p.add_argument(
        "--simplify",
        type=float,
        default=0.002,
        help="Line simplification tolerance in degrees (default: 0.002)",
    )
    p.add_argument(
        "--min-vertices",
        type=int,
        default=5,
        help="Minimum vertices per contour line (default: 5)",
    )
    return p.parse_args()


def pixel_to_geo(
    contour: np.ndarray, transform: rasterio.Affine
) -> list[tuple[float, float]]:
    """Convert pixel coordinates (row, col) to geographic (lon, lat)."""
    coords = []
    for row, col in contour:
        x = transform.c + col * transform.a + row * transform.b
        y = transform.f + col * transform.d + row * transform.e
        coords.append((x, y))
    return coords


def generate_isolines(
    input_path: Path,
    interval: float,
    min_depth: float,
    max_depth: float,
    smooth_sigma: float,
    simplify_tolerance: float,
    min_vertices: int,
) -> dict:
    """Generate contour lines from bathymetry raster and return GeoJSON."""
    with rasterio.open(input_path) as src:
        data = src.read(1).astype(np.float64)
        transform = src.transform
        nodata = src.nodata

        if nodata is not None:
            data[data == nodata] = np.nan

    valid = data[~np.isnan(data)]
    if valid.size == 0:
        raise RuntimeError("No valid data in bathymetry raster")

    print(f"Data range: {valid.min():.1f} to {valid.max():.1f} m")

    # EMODnet may use positive-depth convention; normalise to negative = below sea level
    median = np.nanmedian(valid)
    if median > 0 and valid.min() >= 0:
        print("Converting positive-depth values to negative elevation convention")
        data = -data

    if smooth_sigma > 0:
        mask = np.isnan(data)
        filled = np.where(mask, 0, data)
        data = gaussian_filter(filled, sigma=smooth_sigma)
        data[mask] = np.nan

    # Build contour levels (descending from 0 toward min_depth)
    levels = np.arange(max_depth, min_depth - interval, -interval)
    levels = levels[levels >= min_depth]
    print(
        f"Generating contours at {len(levels)} levels: "
        f"{levels.max():.0f} m to {levels.min():.0f} m"
    )

    features: list[dict] = []
    for level in levels:
        contours = find_contours(data, level)
        for contour in contours:
            if len(contour) < min_vertices:
                continue

            geo_coords = pixel_to_geo(contour, transform)
            line = LineString(geo_coords)

            if simplify_tolerance > 0:
                line = line.simplify(simplify_tolerance, preserve_topology=True)

            if line.is_empty or len(line.coords) < 2:
                continue

            is_major = bool(abs(level) % 200 < 1e-6 or level == 0)
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "depth": float(level),
                        "label": f"{int(level)} m",
                        "major": is_major,
                    },
                    "geometry": mapping(line),
                }
            )

    print(f"Generated {len(features)} contour line segments")
    return {"type": "FeatureCollection", "features": features}


def main() -> None:
    args = parse_args()

    if not args.input.exists():
        raise FileNotFoundError(
            f"Bathymetry file not found: {args.input}\n"
            "Run fetch_bathymetry.py first."
        )

    geojson = generate_isolines(
        args.input,
        args.interval,
        args.min_depth,
        args.max_depth,
        args.smooth,
        args.simplify,
        args.min_vertices,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(geojson, f)

    size_kb = args.output.stat().st_size / 1024
    print(f"Saved to {args.output} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
