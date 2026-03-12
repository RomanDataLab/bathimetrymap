"""
Fetch bathymetry data for the Strait of Gibraltar.

Sources:
  emodnet  - EMODnet Bathymetry WCS (default, no credentials needed)
             https://emodnet.ec.europa.eu/en/bathymetry
  etopo    - NOAA ETOPO 2022 via OPeNDAP (requires netCDF4 package)
             https://www.ncei.noaa.gov/products/etopo-global-relief-model
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_bounds
import requests

# Strait of Gibraltar default bounds
NORTH = 36.25
SOUTH = 35.75
EAST = -4.59
WEST = -7.47

EMODNET_WCS = "https://ows.emodnet-bathymetry.eu/wcs"
ETOPO_OPENDAP = (
    "https://www.ncei.noaa.gov/thredds-ocean/dodsC/"
    "ncei/bathymetry/ETOPO_2022_v1_60s_N90W180_bed.nc"
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Download bathymetry data for the Strait of Gibraltar"
    )
    p.add_argument("--north", type=float, default=NORTH)
    p.add_argument("--south", type=float, default=SOUTH)
    p.add_argument("--east", type=float, default=EAST)
    p.add_argument("--west", type=float, default=WEST)
    p.add_argument(
        "--source",
        choices=("emodnet", "etopo"),
        default="emodnet",
        help="Data source: 'emodnet' (default) or 'etopo' (requires netCDF4)",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("data/bathymetry.tif"),
        help="Output GeoTIFF path",
    )
    return p.parse_args()


def fetch_emodnet(
    north: float, south: float, east: float, west: float, output: Path
) -> None:
    """Download bathymetry from EMODnet Bathymetry WCS as GeoTIFF."""
    print("Fetching bathymetry from EMODnet Bathymetry WCS...")
    print(f"  Bounds: {south:.4f}-{north:.4f}N, {west:.4f}-{east:.4f}E")

    params = [
        ("service", "WCS"),
        ("version", "2.0.1"),
        ("request", "GetCoverage"),
        ("CoverageId", "emodnet:mean"),
        ("subset", f"Long({west},{east})"),
        ("subset", f"Lat({south},{north})"),
        ("format", "image/tiff"),
    ]

    resp = requests.get(EMODNET_WCS, params=params, timeout=300, stream=True)
    resp.raise_for_status()

    content_type = resp.headers.get("content-type", "")
    if "xml" in content_type or "html" in content_type:
        raise RuntimeError(
            f"EMODnet returned an error response (content-type: {content_type}).\n"
            f"Response body: {resp.text[:500]}\n\n"
            "Try --source etopo as a fallback."
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "wb") as f:
        for chunk in resp.iter_content(1 << 20):
            f.write(chunk)

    _print_raster_info(output)


def fetch_etopo(
    north: float, south: float, east: float, west: float, output: Path
) -> None:
    """Download bathymetry from NOAA ETOPO 2022 via OPeNDAP."""
    try:
        import netCDF4
    except ImportError:
        raise RuntimeError(
            "netCDF4 is required for the ETOPO source.\n"
            "Install with: pip install netCDF4"
        )

    print("Fetching bathymetry from NOAA ETOPO 2022 via OPeNDAP...")
    print(f"  Bounds: {south:.4f}-{north:.4f}N, {west:.4f}-{east:.4f}E")
    print(f"  Endpoint: {ETOPO_OPENDAP}")

    ds = netCDF4.Dataset(ETOPO_OPENDAP)
    lat = ds.variables["lat"][:]
    lon = ds.variables["lon"][:]

    lat_idx = np.where((lat >= south) & (lat <= north))[0]
    lon_idx = np.where((lon >= west) & (lon <= east))[0]

    if len(lat_idx) == 0 or len(lon_idx) == 0:
        ds.close()
        raise RuntimeError("No data found for the specified bounds.")

    print(f"  Downloading {len(lat_idx)}x{len(lon_idx)} grid cells...")
    elevation = ds.variables["z"][
        lat_idx[0] : lat_idx[-1] + 1,
        lon_idx[0] : lon_idx[-1] + 1,
    ]
    lat_sub = lat[lat_idx]
    lon_sub = lon[lon_idx]
    ds.close()

    if hasattr(elevation, "filled"):
        elevation = elevation.filled(np.nan)

    arr = np.asarray(elevation, dtype=np.float32)

    # ETOPO stores lat ascending; GeoTIFF convention is top-to-bottom
    if lat_sub[0] < lat_sub[-1]:
        arr = arr[::-1]
        lat_sub = lat_sub[::-1]

    transform = from_bounds(
        float(lon_sub.min()),
        float(lat_sub.min()),
        float(lon_sub.max()),
        float(lat_sub.max()),
        len(lon_sub),
        len(lat_sub),
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        output,
        "w",
        driver="GTiff",
        height=len(lat_sub),
        width=len(lon_sub),
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
        nodata=-9999,
    ) as dst:
        dst.write(arr, 1)

    _print_raster_info(output)


def _print_raster_info(path: Path) -> None:
    """Print summary of the saved raster."""
    with rasterio.open(path) as src:
        data = src.read(1)
        nodata = src.nodata
        if nodata is not None:
            valid = data[data != nodata]
        else:
            valid = data[~np.isnan(data)]
        print(f"  Size: {src.width} x {src.height} pixels")
        print(f"  CRS: {src.crs}")
        if valid.size > 0:
            print(f"  Depth range: {valid.min():.1f} to {valid.max():.1f} m")
    print(f"  Saved to {path}")


def main() -> None:
    args = parse_args()
    if args.source == "emodnet":
        fetch_emodnet(args.north, args.south, args.east, args.west, args.output)
    else:
        fetch_etopo(args.north, args.south, args.east, args.west, args.output)


if __name__ == "__main__":
    main()
