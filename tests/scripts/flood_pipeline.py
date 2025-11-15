# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from elevation.pipeline import run_pipeline
from elevation.geocode import resolve_location_from_user_input

def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/flood_pipeline.py '<address | lat,lon>' [outputs_dir]")
        sys.exit(1)

    user_text = sys.argv[1].strip()
    outputs_dir = sys.argv[2].strip() if len(sys.argv) >= 3 else None

    lat, lon, label = resolve_location_from_user_input(user_text)
    if lat is None:
        sys.exit("ERROR: location could not be resolved.")

    print("="*90)
    print("COMPLETE TERRAIN RISK ANALYSIS PIPELINE (one-click)")
    print("="*90)
    print(f"Target location: {label}  ({lat:.6f},{lon:.6f})")

    res = run_pipeline(lat, lon, outputs_dir=outputs_dir)
    print("\nOutputs written to:", res["outputs_dir"])
    for k, p in res["figures"].items():
        print(" -", os.path.basename(p))
    for k, p in res["tables"].items():
        print(" -", os.path.basename(p))

if __name__ == "__main__":
    main()
