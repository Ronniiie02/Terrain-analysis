# -*- coding: utf-8 -*-
"""
Configuration module for elevation analysis pipeline
---------------------------------------------------
Global defaults for DEM resolution, external services, and analysis parameters.
NOTE: AOI radii (120, 500) are NOT hardcoded here - they are defined at runtime
      in the main orchestration logic based on specific analysis needs.
All values aligned with FINAL pipeline behavior.
"""

import os

# ===========================
# DEM Processing Defaults
# ===========================
DEM_RES_DEFAULT: float = 1.0      # Default DEM resolution (meters)
PDAL_THREADS: int = 8              # PDAL parallelization threads
NODATA_SENTINEL: float = -9999.0   # NoData value in DEMs

# ===========================
# AOI Radii (CONTEXT-SPECIFIC, NOT HARDCODED)
# ===========================
# These are used in specific analysis steps and should be passed as parameters,
# NOT set globally. Only 500m is fixed for area-level statistics.
# Other radii (50, 100, 120, 200, 300, 500) are used for ring metrics and computed dynamically.
AOI_500M_FOR_AREA_STATS: float = 500.0  # Fixed radius for area-level elevation stats

# ===========================
# House & Terrain Analysis
# ===========================
HOUSE_BUFFER_M: float = 3.0        # Buffer around house footprint (meters)
                                   # Used when OSM building not found
PLANE_BAND_INNER_M: float = 5.0    # Inner radius for plane fitting (meters)
PLANE_BAND_OUTER_M: float = 15.0   # Outer radius for plane fitting (meters)

# Ring band definitions (for ring metrics if used)
RING_BANDS_M: tuple[tuple[float, float], ...] = (
    (3.0, 8.0),
    (8.0, 15.0),
    (15.0, 30.0)
)

# Pooled outer statistics limits
POOLED_OUTER_MIN: float = 3.0
POOLED_OUTER_MAX: float = 25.0

# ===========================
# External Services
# ===========================
GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
COUNTRY_BIAS: str = os.getenv("COUNTRY_BIAS", "US")
OVERPASS_UA: str = os.getenv(
    "OVERPASS_UA",
    "uchicago-elevation/0.1 (contact: team@example.com)"
)