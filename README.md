# Flood Risk Rating of Properties: Terrain Analysis Pipeline

**Repository:** https://github.com/Ronniiie02/Terrain-analysis  
**Project:** Flood Risk Rating of Properties – Leveraging Geospatial Analysis & Digital Elevation Models  
**Partner:** Tokio Marine Highland (TMH) · University of Chicago MS in Applied Data Science Capstone

---

## 1. Project Overview

Property-scale flood risk is driven by **micro-topography** — tiny elevation differences that determine whether water drains away from a property or pools around it. Traditional tools (FEMA maps, coarse DEMs, zonal statistics) often miss these fine-scale patterns and cannot reliably distinguish a safe house on a ridge from a vulnerable house in a shallow bowl within the same flood zone.

This repository implements a **LiDAR-based geospatial analysis pipeline** and an **interactive web UI** that:

- Downloads and builds **1 m bare-earth Digital Elevation Models (DEMs)** around a target property using USGS 3DEP data.
- Extracts **terrain metrics** such as:
  - Relative elevation (Δelevation vs. surroundings)
  - Slope and flatness
  - Flow convergence and dominant downslope direction
- Estimates a physically-interpretable **terrain-risk score (0–1)** for the property, reflecting how likely water is to **collect** rather than **drain**.
- Serves the results through a **FastAPI backend** and **modern HTML/Tailwind/Leaflet dashboard** for underwriting and risk analytics.

The system is designed as an **interpretable, physics-based decision-support tool**, not a black-box ML predictor of losses or premiums. It complements existing models and FEMA zones by focusing on **local drainage behavior**.

---

## 2. Key Features

- **Automated DEM generation (USGS 3DEP)**
  - 1 m bare-earth DTM (ground only) from LiDAR point clouds.
  - PDAL-based pipeline to filter noise, classify ground points, and rasterize elevation.

- **Building-aware property localization**
  - Dual-source geocoding (Google / Nominatim, configurable in code).
  - OpenStreetMap (OSM) building footprints via Overpass API.
  - Smart relocation to the nearest/containing building footprint when the raw address lands on a road or wrong parcel.

- **Adaptive house-ground estimation**
  - Uses concentric **rings** around the building (e.g., 3–8 m, 8–15 m, 15–30 m).
  - IQR-based outlier removal + skew-aware quantiles.
  - Produces a robust estimate of **true ground elevation at the house**.

- **Multi-scale ring analysis (10–500 m)**
  - Relative elevation (Δelevation) at multiple radii.
  - Slope statistics and flat/gentle/steep proportions.
  - Flow convergence and dominant downslope direction by radius.

- **Global composite terrain-risk score**
  - Combines:
    - Global elevation rank (500 m region)
    - Local depression vs. regional median
    - Flatness (slow drainage)
    - Flow convergence onto the parcel
  - Transparent linear weighting + gamma non-linearity for low-lying areas.

- **Interactive visualization**
  - 2D maps (elevation, slope, aspect, terrain zones).
  - Histogram-based elevation distributions.
  - Multiscale ring metrics table.
  - Dual 3D terrain view (user AOI vs. 500 m reference).
  - Narrative text report for non-technical stakeholders.

- **Backend + Frontend integration**
  - FastAPI backend exposing a `/runs`-style API to trigger the full pipeline.
  - HTML/Tailwind/Leaflet/Chart.js-based UI for interactive address-level exploration.

---

## 3. Repository Structure

```text
Uchicago-elevation/
├── api/                         # FastAPI backend (REST API)
│   ├── app.py                   # Main FastAPI application
│   ├── routers/                 # API route definitions (e.g. /runs)
│   ├── schemas.py               # Pydantic schemas for requests/responses
│   ├── services/                # Pipeline orchestration, cache utilities
│   └── settings.py              # API & environment configuration
│
├── frontend/                    # Web UI (static assets)
│   ├── index.html               # Main dashboard page
│   ├── css/
│   │   ├── styles.css           # Layout & custom styling
│   │   └── utilities.css        # Utility classes/helpers
│   └── js/
│       ├── api.js               # API calls to backend (e.g. trigger runs)
│       ├── main.js              # Page initialization & event wiring
│       ├── map.js               # Leaflet map logic, circles, markers
│       ├── tailwind-config.js   # Tailwind config/theme in the browser
│       └── ui.js                # DOM updates, charts, panels
│
├── outputs/                     # Pipeline outputs (one subfolder per run)
│   ├── <run_id>/
│   │   ├── figure1_elevation.png
│   │   ├── figure2_slope.png
│   │   ├── figure3_aspect.png
│   │   ├── figure4_terrain_and_hist.png
│   │   ├── figure5_6_3d_combo.html
│   │   ├── figure_terrain_risk_map.png
│   │   ├── summary_multiscale.csv / .parquet
│   │   ├── summary_area_level.csv
│   │   ├── summary_area_level_user.csv
│   │   ├── narrative.txt
│   │   └── manifest.json        # Central index for this run’s files
│   └── ...
│
├── src/
│   ├── elevation/               # Core Python package (terrain engine)
│   │   ├── __init__.py
│   │   ├── analytics.py         # Higher-level analytics, utilities
│   │   ├── config.py            # Shared config, constants
│   │   ├── dem.py               # USGS dataset discovery & DEM building
│   │   ├── geocode.py           # Address → lat/lon (Google / Nominatim)
│   │   ├── helpers.py           # Utility functions (coords, masks, IQR, etc.)
│   │   ├── osm.py               # OSM building fetch + relocation logic
│   │   ├── run_final_pipeline.py# Full end-to-end pipeline entry point
│   │   ├── terrain.py           # Terrain metrics, rings, risk score, narrative
│   │   └── viz.py               # Figure generation & 3D visualization
│   └── uchicago_elevation.egg-info/  # Package metadata
│
├── tests/
│   └── scripts/
│       ├── flood_pipeline.py    # Example/legacy runner script
│       └── test_pipeline.ipynb  # Notebook for verifying the pipeline
│
├── pyproject.toml               # Project metadata & dependencies
├── .gitignore
└── Uchicago-elevation.code-workspace
4. Installation
4.1. Prerequisites
Python 3.10+ (recommend 3.11/3.12)

pip or uv / poetry for dependency management

Recommended system libraries:

GDAL / PROJ / GEOS (often installed via Homebrew on macOS)

PDAL (if you plan to rebuild DEMs from raw LiDAR; some code paths assume PDAL is available)

A modern browser (Chrome/Edge/Firefox) for the frontend.

On macOS with Homebrew (example):

bash
复制代码
brew install gdal proj geos pdal
4.2. Clone the repository
bash
复制代码
git clone https://github.com/Ronniiie02/Terrain-analysis.git
cd Terrain-analysis   # or Uchicago-elevation if this is your local folder
4.3. Create and activate a virtual environment
bash
复制代码
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
4.4. Install Python dependencies
This project uses pyproject.toml. You can install dependencies with pip using pip install . or pip install -e . for editable mode:

bash
复制代码
pip install -e .
If you prefer, you can also open pyproject.toml and install individual libraries manually, but pip install -e . is the intended workflow.

5. Running the Backend API
The backend is a FastAPI application located in api/app.py. It orchestrates the full terrain pipeline via services in api/services/pipeline_runner.py and the elevation package.

From the project root:

bash
复制代码
uvicorn api.app:app --reload --host 127.0.0.1 --port 8000
You should then see logs indicating that the API is running at:

Backend base URL: http://127.0.0.1:8000

If FastAPI includes automatically generated docs (depending on the configuration in app.py), you can open:

Interactive docs: http://127.0.0.1:8000/docs

Alternative docs: http://127.0.0.1:8000/redoc

Note: The backend reads/writes to the outputs/ directory. Make sure this folder is writable.

6. Running the Frontend UI
The frontend is a static application served from frontend/index.html with JS modules under frontend/js and CSS under frontend/css.

Option 1 – Simple local static server (recommended for development)
From the project root:

bash
复制代码
cd frontend
python -m http.server 5173
Then open:

http://127.0.0.1:5173/index.html

Make sure the frontend’s API URLs in frontend/js/api.js point to your running backend (default: http://127.0.0.1:8000).

Option 2 – Open the HTML directly
For quick visual checks, you can open frontend/index.html directly in the browser. However, some browsers restrict fetch() from file:// URLs; using a small HTTP server (Option 1) is more robust.

7. Core Pipeline Workflow
The main end-to-end analysis logic lives in:

src/elevation/run_final_pipeline.py
with the main function: run_pipeline(config: PipelineConfig)

7.1. Geocoding / Coordinate resolution
If an address is provided and no lat/lon:

Resolve via resolve_location_from_user_input() in geocode.py.

Validate final coordinates.

7.2. Building-aware smart relocation
Uses validate_and_relocate_building() from osm.py:

Fetch OSM building footprints in a reference radius (default 500 m).

Prefer buildings that contain the geocoded point; else choose the nearest.

If necessary, relocate the “analysis center” to the selected building centroid.

If OSM is missing, create a fallback circular building.

7.3. LiDAR dataset discovery & DEM generation
discover_usgs_lidar_dataset() selects the matching USGS 3DEP dataset.

build_multiple_dems() (in dem.py) builds:

A 500 m reference DEM (1 m resolution).

A user AOI DEM (e.g., 100–500 m radius, same resolution).

DEMs are stored as GeoTIFFs in the outputs/<run_id>/ directory.

7.4. Terrain derivatives
derive_slope_aspect_curvature() (in terrain.py) computes:

Slope (degrees)

Aspect (degrees)

Curvature

Applied to both user AOI DEM and 500 m reference DEM.

7.5. House ground elevation estimation
estimate_house_ground_adaptive():

Builds ring geometries around the building footprint:

Edge ring, near bands (3–8 m, 8–15 m, 15–30 m), pooled outer bands.

Samples DEM values within these rings.

Applies IQR filtering and skew-aware quantiles for robust estimation.

Output:

house_ground (float, meters)

Diagnostic info about the method and sample sizes

7.6. Terrain classification
classify_terrain_adaptive():

Uses slope thresholds (e.g., 2° and 5°) to label flat/gentle/steep zones.

Produces a compact zone map for the 500 m area.

7.7. Visualization
figure1_elevation()
Elevation map, DEM overlays, rings, building footprint.

figure2_slope()
Slope maps for user AOI and 500 m reference.

figure3_aspect()
Aspect (direction of slope) maps.

figure4_terrain_and_hist()
Terrain classification + elevation distributions.

add_3d_figures_to_pipeline()
Dual 3D HTML view of user AOI & 500 m DEM.

7.8. Global composite terrain-risk map
compute_global_composite_risk_map():

Inputs:

500 m DEM

500 m slope and aspect

House-ground elevation vs. area median

Computes components:

Global elevation risk

Local depression risk

Flatness risk

Flow convergence risk

Aggregates with configurable weights & gamma non-linearity.

Returns:

target_risk (0–1)

global_percentile (relative to 500 m area)

Also saves:

figure_terrain_risk_map.png

7.9. Multi-scale ring metrics & area statistics
For each radius (e.g., 50, 100, 200, 300, 500 m):

compute_ring_metrics_simple() computes:

Δelevation

Slope statistics

Flat/gentle/steep percentages

Flow convergence and dominant aspect

Results are saved in:

summary_multiscale.csv / .parquet

Area-level summary tables:

summary_area_level.csv (500 m)

summary_area_level_user.csv (user radius)

7.10. Narrative generation
generate_narrative():

Uses ring metrics, area stats, and terrain-risk to produce a natural-language description of:

How the property sits relative to surrounding terrain.

Drainage directions and flow convergence.

Interpreted terrain-risk score (0–1, percentile).

Saved as:

narrative.txt

7.11. Manifest
A single manifest.json is generated per run:

Contains:

run_id, coordinates, AOI radius

House elevation, percentile ranks

Terrain-risk score & percentile

Paths to figures, tables, narrative, DEM files

The frontend uses this manifest to load results.

8. Using the API from the Frontend
The UI communicates with the backend through a small API layer (frontend/js/api.js).

Typical flow:

User inputs address or coordinates in the frontend.

Frontend sends a POST request to the backend (e.g., /runs) with:

Address or lat/lon

Desired AOI radius

Backend starts the pipeline, creates a new run_id, and returns:

run_id

Status / initial metadata

Frontend requests run results:

Loads manifest.json and associated figures/tables.

Frontend updates UI:

Maps, charts, tables, narrative text.

The exact paths and payloads are defined in api/routers/runs.py and api/services/pipeline_runner.py. See those files for the current API contract.

9. Example: Running the Pipeline Directly in Python
You can run the pipeline programmatically (without the API) using run_pipeline:

python
复制代码
from elevation.run_final_pipeline import PipelineConfig, run_pipeline

config = PipelineConfig()
config.lat = 41.8781
config.lon = -87.6298
config.aoi_radius_m = 500.0
config.dem_for_user_aoi = True
config.output_dir = "./outputs/Chicago_example"
config.output_format = "both"     # "csv", "parquet", or "both"
config.generate_narrative = True
config.save_3d = True

results = run_pipeline(config)

print("House elevation:", results["house_ground_m"], "m")
print("Percentile rank:", results["percentile_rank"], "%")
print("Terrain risk score:", results["terrain_risk_score"])
print("Outputs dir:", results["outputs_dir"])
This will produce the full set of figures, tables, narrative, and manifest in ./outputs/Chicago_example.

10. Interpretation of Key Outputs
10.1. House elevation (house_ground_m)
Robust estimate of the ground directly beneath the building footprint.

Based on cleaned DEM + ring-based sampling.

10.2. Elevation percentile rank
Share of surrounding terrain (within AOI) that is lower than the house.

Example: 13.7% → the house is lower than most of the surrounding area.

10.3. ΔElevation (ΔElev_median in summary_multiscale)
House elevation minus the median elevation of the ring.

Negative: the house sits in a local depression.

Positive: the house is higher than its surroundings.

10.4. Terrain-risk score (terrain_risk_score)
0 → best-draining within the 500 m context.

1 → most flood-prone within the 500 m context.

Combines:

Global elevation

Local depression

Flatness

Flow convergence

Best interpreted together with its percentile in the 500 m area.

10.5. Narrative (narrative.txt)
Non-technical explanation of:

Terrain setting (ridge, slope, basin).

Drainage directions and flow convergence.

How the risk score was obtained and what it implies.

11. Limitations & Scope
11.1. What this tool does not do
Does not predict insurance claims or losses.

Does not incorporate rainfall intensity, sewer capacity, soil permeability, or building design.

Does not replace FEMA maps or proprietary hazard models.

11.2. What this tool does provide
A high-resolution, physically interpretable terrain lens on flood susceptibility.

A reproducible analytics layer that can be combined with:

FEMA zones

TMH proprietary models

Historical claims and exposure data

Property-level micro-topography insights to support underwriting and portfolio analysis.

12. Academic & Industry Context
This repository implements the methods described in the capstone paper:

Flood Risk Rating of Properties: Leveraging Geospatial Analysis & Digital Elevation Model
Yiyang Yao, Jialong Guo, Qi Yang, Chenxi Liu
Advisor: Wendy Klusendorf · Instructor: Anil Chaturvedi, PhD
Master of Science in Applied Data Science
Division of Physical Sciences, University of Chicago, November 2025.

Key concepts reflected in the code and UI:

Use of LiDAR-based DEMs for flood-relevant topography.

Sensitivity of flood modeling to DEM resolution (1 m vs. coarser grids).

Importance of micro-topography (depressions, flow paths, local storage).

Transparent, physics-based scoring vs. opaque black-box models.

13. Acknowledgments
This project is the result of collaboration between:

Tokio Marine Highland (TMH) – domain expertise, problem framing, and use-case context in private flood insurance.

University of Chicago, MS Applied Data Science Program – academic supervision and evaluation.

Faculty and advisors:

Wendy Klusendorf (Advisor)

Anil Chaturvedi, PhD (Instructor)

We also acknowledge:

The USGS 3D Elevation Program (3DEP) for making high-quality LiDAR and DEM data publicly available.

The OpenStreetMap community for building footprint data.

