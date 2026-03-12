#!/usr/bin/env bash
set -e

echo "=== Building map data ==="
python fetch_bathymetry.py
python generate_isolines.py --min-depth -1300
python fetch_vessels.py
python create_map.py

echo "=== Copying to deploy folder ==="
cp web/index.html deploy/public/index.html

echo "=== Done ==="
echo "Run: cd deploy && vercel"
