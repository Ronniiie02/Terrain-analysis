# -*- coding: utf-8 -*-
"""
COMPLETE TERRAIN ANALYSIS PIPELINE
=============================================================
Full end-to-end execution
All parameters exposed in config dict.
"""

from __future__ import annotations
from typing import Dict, Any, List, Optional, Tuple
import os
import json
import numpy as np
import pandas as pd
import matplotlib
from pathlib import Path 
matplotlib.use('Agg')
import rasterio

from .helpers import (
    _lonlat_to_3857,
    _lonlat_to_crs,
    house_rc_from_lonlat,
    build_ring_masks,
    compute_ring_metrics_simple,
    save_table,
    compute_global_composite_risk_map,
    set_risk_building_poly, 
)

from .geocode import resolve_location_from_user_input
from .osm import fetch_building_osm
from .dem import (
    discover_usgs_lidar_dataset,
    build_multiple_dems,
    load_dem,
)
from .terrain import (
    estimate_house_ground_adaptive,
    derive_slope_aspect_curvature,
    classify_terrain_adaptive,
    generate_narrative, 
    cardinal_direction,
    compute_flow_convergence,
)

from .viz import (
    figure1_elevation,
    figure2_slope,
    figure3_aspect,
    figure4_terrain_and_hist,
    add_3d_figures_to_pipeline,
    DEFAULT_CONFIG as VIZ_DEFAULT_CONFIG,
)

# ============================================
# COMPLETE CONFIGURATION (NO HARDCODES!)
# ============================================

class PipelineConfig:
    """All pipeline parameters - fully exposed"""
    
    def __init__(self):
        # ===== User Input =====
        self.lat: Optional[float] = None
        self.lon: Optional[float] = None
        self.address: str = ""
        
        # ===== AOI & DEM =====
        self.aoi_radius_m: float = 500.0          # User AOI radius in meters
        self.dem_resolution_m: float = 1.0        # DEM resolution in meters
        self.dem_nodata: float = -9999.0          # DEM NoData sentinel
        self.pdal_threads: int = 8                # Threads for PDAL processing
        
        # DEM generation - support user AOI + 500m reference
        self.dem_for_user_aoi: bool = True        # Generate DEM for user AOI
        self.reference_radius_m: float = 500.0    # Always generate reference DEM (500m)
        
        # ===== OSM Building =====
        self.osm_buffer_m: float = 120.0          # Search radius for OSM footprints (meters)
        self.osm_timeout_s: int = 25              # Overpass timeout
        
        # ===== House Estimation (all parametrized!) =====
        self.house_buffer_m: float = 3.0          # Fallback circular building radius (m)
        self.plane_band_inner_m: float = 5.0      # Inner radius for plane fitting (m)
        self.plane_band_outer_m: float = 15.0     # Outer radius for plane fitting (m)
        
        # Adaptive parameters (no hardcodes!)
        self.min_size_edge: int = 10
        self.min_size_ring_3_8: int = 20
        self.min_size_ring_8_15: int = 30
        self.min_size_ring_15_30: int = 50
        self.q_high_tail_threshold: float = 98.0
        self.hard_q_high_tail: float = 99.5
        self.fallback_sample_size: int = 150
        
        # ===== Terrain Classification (parametrized!) =====
        self.slope_low_threshold: float = 2.0     # < 2° = flat / low slope
        self.slope_mod_threshold: float = 5.0     # 2–5° = gentle; >= 5° = steep
        
        # ===== Ring Definitions (parametrized!) =====
        self.ring_bands_m: Tuple[Tuple[float, float], ...] = (
            (3.0, 8.0),
            (8.0, 15.0),
            (15.0, 30.0)
        )
        
        self.pad_inner_m: float = 0.8             # Inner buffer for edge ring
        self.pad_outer_m: float = 2.5             # Outer buffer for edge ring
        
        self.pooled_outer_min: float = 3.0        # Inner radius for pooled outer ring
        self.pooled_outer_max: float = 25.0       # Outer radius for pooled outer ring
        
        # ===== Ring Metrics Radii (parametrized!) =====
        self.ring_metrics_radii: List[float] = [50.0, 100.0, 200.0, 300.0, 500.0]
        self.ring_analysis_radii: List[float] = [10.0, 50.0, 100.0, 200.0, 300.0, 500.0]
        
        # ===== Visualization =====
        self.viz_config: Dict[str, Any] = VIZ_DEFAULT_CONFIG.copy()
        
        # ===== Global Composite Risk (single-point over 500m DEM) =====
        self.global_gamma: float = 4.0
        self.global_res_window_m: float = 10.0
        self.global_weights: Dict[str, float] = dict(
            global_=0.50,
            delta=0.20,
            slope=0.20,
            asin=0.05,
            acos=0.05
        )

        # ===== Output =====
        self.output_dir: str = "./outputs"
        self.output_format: str = "csv"           # 'csv', 'parquet', or 'both'
        self.generate_narrative: bool = True
        self.verbose: bool = True
        self.save_3d: bool = True
        
        # Derived fields (set during runtime)
        self.dataset_name: Optional[str] = None
        self.outdir: Optional[str] = None

# ============================================
# MAIN PIPELINE
# ============================================

def run_pipeline(config: PipelineConfig) -> Dict[str, Any]:
    """
    Complete terrain analysis pipeline.
    All parameters in config - zero hardcodes.
    """
    # --- Geocoding and coordinate validation ---
    lat_ok = isinstance(config.lat, (int, float)) and np.isfinite(config.lat)
    lon_ok = isinstance(config.lon, (int, float)) and np.isfinite(config.lon)

    # If address is provided but lat/lon are missing → geocode first
    if config.address and not (lat_ok and lon_ok):
        lat, lon, faddr = resolve_location_from_user_input(config.address)
        if lat is None or lon is None:
            # Keep error message text as originally written (Chinese)
            raise RuntimeError(json.dumps({
                "error_code": "GEOCODING_FAILED",
                "message": f"Unable to resolve address: {config.address}",
                "address": config.address
            }))
        config.lat, config.lon = float(lat), float(lon)
        config.address = faddr

    # Final sanity check on coordinates
    lat_ok = isinstance(config.lat, (int, float)) and np.isfinite(config.lat)
    lon_ok = isinstance(config.lon, (int, float)) and np.isfinite(config.lon)
    if not (lat_ok and lon_ok):
        raise RuntimeError("No valid coordinates. Provide lat/lon or a geocodable address.")

    # ===== STEP 0: Smart relocation BEFORE dataset discovery / DEM build =====
    from .osm import validate_and_relocate_building

    reloc = validate_and_relocate_building(
        lat=config.lat,
        lon=config.lon,
        aoi_radius_user=config.aoi_radius_m,
        aoi_radius_ref=config.reference_radius_m,
    )

    # If relocation is successful and coordinates changed, adopt relocated point
    if reloc["success"] and (reloc["relocated_lat"] != config.lat or reloc["relocated_lon"] != config.lon):
        if config.verbose:
            print("\n[SMART RELOCATION]")
            print(f"  Original: ({config.lat:.6f}, {config.lon:.6f})")
            print(f"  New     : ({reloc['relocated_lat']:.6f}, {reloc['relocated_lon']:.6f})")
            print(f"  Reason  : {reloc['relocation_reason']}")
        config.lat, config.lon = float(reloc["relocated_lat"]), float(reloc["relocated_lon"])
        selected_building_from_relocation = reloc["building"]
    else:
        selected_building_from_relocation = None

    # --- Create output directory AFTER potential relocation, so it reflects final location ---
    config.outdir = config.output_dir
    os.makedirs(config.outdir, exist_ok=True)

    if config.verbose:
        print(f"📁 Output directory: {config.outdir}")
        print(f"📍 Radius: {config.aoi_radius_m}m")

    if config.verbose:
        print("=" * 90)
        print(f"TERRAIN ANALYSIS PIPELINE - AOI RADIUS: {config.aoi_radius_m}m")
        print("=" * 90)
        print(f"Target location: {config.lat:.6f}, {config.lon:.6f}")
        print(f"Output directory: {config.outdir}")
    
    # ===== STEP 1: Dataset Discovery =====
    if config.verbose:
        print("\n" + "=" * 90)
        print("STEP 1: LIDAR DATASET DISCOVERY")
        print("=" * 90)
    
    dataset_info = discover_usgs_lidar_dataset(
        lat=config.lat,
        lon=config.lon,
        aoi_radius_m=config.aoi_radius_m
    )
    
    if not dataset_info or "name" not in dataset_info:
        raise RuntimeError("No LIDAR dataset found")
    
    config.dataset_name = dataset_info["name"]
    if config.verbose:
        print(f"Dataset: {config.dataset_name}")
    
    # ===== STEP 2: DEM Generation (DTM + DSM) =====
    if config.verbose:
        print("\n" + "=" * 90)
        print("STEP 2: DEM GENERATION")
        print("=" * 90)

    dem_specs = {}

    # 500m reference DEM (fixed 1m resolution)
    dem_specs["dem_500m"] = {
        "out_path": os.path.join(config.outdir, "dem_500m_dtm.tif"),
        "radius_m": config.reference_radius_m,
        "resolution_m": 1.0,
    }

    # User AOI DEM (optional, can be same as 500m)
    if config.aoi_radius_m != config.reference_radius_m:
        user_radius_key = f"dem_{int(config.aoi_radius_m)}m"
        dem_specs[user_radius_key] = {
            "out_path": os.path.join(
                config.outdir,
                f"dem_{int(config.aoi_radius_m)}m_dtm.tif"
            ),
            "radius_m": config.aoi_radius_m,
            "resolution_m": 1.0,
        }
    else:
        user_radius_key = "dem_500m"

    if config.verbose:
        print(f"DEM specifications:")
        print(f"  - 500m reference: {dem_specs['dem_500m']['resolution_m']}m resolution")
        print(f"  - {config.aoi_radius_m}m user: {dem_specs[user_radius_key]['resolution_m']}m resolution")

    try:
        dem_results = build_multiple_dems(
            dataset_name=config.dataset_name,
            lat=config.lat,
            lon=config.lon,
            dem_specs=dem_specs,
            nodata=config.dem_nodata,
            threads=config.pdal_threads,
        )
    except RuntimeError as e:
        # If our code raised a structured JSON error, propagate it as-is
        try:
            err = json.loads(str(e))
        except Exception:
            err = {"error_code": "PIPELINE_ERROR", "message": str(e)}
        raise RuntimeError(json.dumps(err))

    if config.verbose:
        print(f"Generated {len(dem_results)} DEM")
    
    # ===== STEP 3: Load Primary DEM =====
    if config.verbose:
        print("\n" + "=" * 90)
        print("STEP 3: LOADING DEM & BUILDING MASKS")
        print("=" * 90)

    # Load user AOI DEM
    user_dem_result = dem_results[user_radius_key]
    user_dem_path = user_dem_result.get("tif_path", user_dem_result.get("dtm_path"))
    dem_user, meta_user = load_dem(user_dem_path, nodata=config.dem_nodata)
    mask_user = build_ring_masks(
        meta_user, [config.aoi_radius_m], config.lon, config.lat
    )[config.aoi_radius_m]
    
    # Load 500m reference DEM
    ref_dem_result = dem_results["dem_500m"]
    ref_dem_path = ref_dem_result.get("tif_path", ref_dem_result.get("dtm_path"))
    dem_500, meta_500 = load_dem(ref_dem_path, nodata=config.dem_nodata)
    mask_500 = build_ring_masks(
        meta_500, [config.reference_radius_m], config.lon, config.lat
    )[config.reference_radius_m]

    # For convenience: use the 500m DEM as the main array/meta
    dem_arr = dem_500["arr"]
    meta = meta_500

    if config.verbose:
        print(f"User DEM ({config.aoi_radius_m}m): {dem_user['arr'].shape}")
        print(f"Reference DEM (500m): {dem_500['arr'].shape}")

    # Build masks for all analysis radii
    all_radii = sorted(
        set(config.ring_analysis_radii + [config.aoi_radius_m, config.reference_radius_m])
    )
    ring_masks_all = build_ring_masks(meta, all_radii, config.lon, config.lat)

    # Primary AOI mask (user radius; fallback to 500m if missing)
    mask_user_aoi = ring_masks_all.get(
        config.aoi_radius_m, ring_masks_all[config.reference_radius_m]
    )
    
    if config.verbose:
        print(f"DEM shape: {dem_arr.shape}")
        print(f"Primary mask ({config.aoi_radius_m}m): {mask_user_aoi.sum()} pixels")
    
    # ===== STEP 4: Fetch OSM Building =====
    if config.verbose:
        print("\n" + "=" * 90)
        print("STEP 4: FETCHING OSM BUILDING")
        print("=" * 90)

    # Reuse relocation result from STEP 0 if available; otherwise query OSM
    if selected_building_from_relocation is not None:
        osm_result = {
            "success": True,
            "selected": selected_building_from_relocation,
            "method": "relocated_pipeline",
            "n_buildings": None,
            "error": None,
        }
    else:
        osm_result = fetch_building_osm(
            lat=config.lat,
            lon=config.lon,
            buffer_m=config.osm_buffer_m,
            timeout_s=config.osm_timeout_s
        )

    # Distance gate: discard footprints that are too far from AOI
    def _distance_m_safe(sel, lon0, lat0):
        """Safely compute footprint distance from center in meters."""
        try:
            d = float(sel.get("distance_m", float("nan")))
            if not np.isfinite(d):
                raise ValueError
            return d
        except Exception:
            import pyproj
            from shapely.ops import transform as shp_transform
            from shapely.geometry import Point
            aeqd = pyproj.CRS.from_proj4(
                f"+proj=aeqd +lat_0={lat0} +lon_0={lon0} +datum=WGS84 +units=m +no_defs"
            )
            to_local = pyproj.Transformer.from_crs(
                "EPSG:4326", aeqd, always_xy=True
            ).transform
            g_local = shp_transform(to_local, sel["geometry"])
            p_local = shp_transform(to_local, Point(lon0, lat0))
            return p_local.distance(g_local)

    if osm_result.get("success") and osm_result.get("selected"):
        sel = osm_result["selected"]
        # Rule 1: if footprint contains the query point, always accept (ignore distance)
        if sel.get("contains_query", False):
            pass
        else:
            # Rule 2: if distance > 1.2 × AOI, discard OSM
            dist_m = _distance_m_safe(sel, config.lon, config.lat)
            thr = float(config.aoi_radius_m) * 1.2
            if dist_m > thr:
                if config.verbose:
                    print(f"⚠ OSM building {dist_m:.1f} m away > 1.2×AOI ({thr:.1f} m), discard.")
                osm_result = {"success": False, "selected": None, "error": "too_far"}

    # Reproject building footprint to raster CRS (DEM CRS)
    from shapely.geometry import Point
    from shapely.ops import transform as shp_transform
    import pyproj

    dst_crs = (meta_user.get("crs") if "meta_user" in locals() else None) or meta_500.get("crs")
    cx_dst, cy_dst = _lonlat_to_crs(config.lon, config.lat, dst_crs)
    center_pt = Point(cx_dst, cy_dst)

    if osm_result.get("success") and osm_result.get("selected"):
        to_raster = pyproj.Transformer.from_crs(
            "EPSG:4326", dst_crs, always_xy=True
        ).transform
        osm_poly = shp_transform(to_raster, osm_result["selected"]["geometry"])
        house_area_raster = osm_poly
    else:
        # Fallback: simple circular footprint around the query point
        osm_poly = None
        house_area_raster = center_pt.buffer(config.house_buffer_m, resolution=64)

    # Build ring polygons in raster CRS
    pad_ring_raster = house_area_raster.buffer(
        config.pad_outer_m, resolution=96
    ).difference(house_area_raster.buffer(config.pad_inner_m, resolution=96))

    bands_raster = [
        house_area_raster.buffer(rmax, resolution=96).difference(
            house_area_raster.buffer(rmin, resolution=96)
        )
        for (rmin, rmax) in config.ring_bands_m
    ]

    pooled_raster = house_area_raster.buffer(
        config.pooled_outer_max, resolution=96
    ).difference(
        house_area_raster.buffer(config.pooled_outer_min, resolution=96)
    )
    
    # ===== STEP 5: Compute Terrain Derivatives =====
    if config.verbose:
        print("\n" + "=" * 90)
        print("STEP 5: COMPUTING TERRAIN DERIVATIVES")
        print("=" * 90)
    
    # Derivatives for 500m DEM
    deriv = derive_slope_aspect_curvature(
        dem_arr,
        res_x=1.0,
        res_y=1.0,
    )
    slope_500 = deriv["slope"]
    aspect_500 = deriv["aspect"]
    curv_500 = deriv["curvature"]   

    # Derivatives for user AOI DEM
    deriv_user = derive_slope_aspect_curvature(
        dem_user["arr"],
        res_x=1.0,
        res_y=1.0,
    )
    slope_user = deriv_user["slope"]
    aspect_user = deriv_user["aspect"]
    curv_user = deriv_user["curvature"]

    if config.verbose:
        print("✓ Computed slope, aspect, curvature")
    
    # ===== STEP 6: Estimate House Elevation =====
    if config.verbose:
        print("\n" + "=" * 90)
        print("STEP 6: ESTIMATING HOUSE ELEVATION")
        print("=" * 90)
    
    # Convert lon/lat to Web Mercator coordinates for plane fitting logic
    cx3857, cy3857 = _lonlat_to_3857(config.lon, config.lat)
    
    with rasterio.open(user_dem_path) as src:
        house_ground, house_info = estimate_house_ground_adaptive(
            src=src,
            house_poly=house_area_raster,
            edge_poly=pad_ring_raster,
            bands_polys=bands_raster,
            cx=cx3857,
            cy=cy3857,
            ndv=config.dem_nodata,
            min_size_edge=config.min_size_edge,
            min_size_ring_3_8=config.min_size_ring_3_8,
            min_size_ring_8_15=config.min_size_ring_8_15,
            min_size_ring_15_30=config.min_size_ring_15_30,
            q_high_tail_threshold=config.q_high_tail_threshold,
            hard_q_high_tail=config.hard_q_high_tail,
            fallback_sample_size=config.fallback_sample_size,
        )

    if config.verbose:
        print(f"House elevation: {house_ground:.2f} m (method: {house_info.get('method', 'N/A')})")

    # ===== STEP 7: Classify Terrain =====
    if config.verbose:
        print("\n" + "=" * 90)
        print("STEP 7: TERRAIN CLASSIFICATION")
        print("=" * 90)
    
    zone, zone_info = classify_terrain_adaptive(
        dem=dem_arr,
        slope_deg=slope_500,
        curv=curv_500,
        circle_mask=mask_user_aoi,
        base_flat=config.slope_low_threshold,
        base_gentle=config.slope_mod_threshold,
    )

    if config.verbose:
        print("✓ Terrain classified")
    
    # ===== STEP 8: Generate Visualizations =====
    if config.verbose:
        print("\n" + "=" * 90)
        print("STEP 8: GENERATING VISUALIZATIONS")
        print("=" * 90)

    house_rc = house_rc_from_lonlat(meta, config.lon, config.lat)

    # Star marker for user DEM (using meta_user CRS)
    x_user, y_user = _lonlat_to_crs(config.lon, config.lat, meta_user.get("crs"))

    # Star marker for 500m DEM (using meta_500 CRS)
    x_ref, y_ref = _lonlat_to_crs(config.lon, config.lat, meta_500.get("crs"))

    # Figure 1: Elevation
    try:
        figure1_elevation(
            out_tif_user=user_dem_path,
            dem_500=dem_500["arr"],
            circle_mask_500=mask_500,
            xs_flat_500=meta_500["xs"].ravel(),
            ys_flat_500=meta_500["ys"].ravel(),
            cx3857_user=x_user,
            cy3857_user=y_user,
            cx3857_500=x_ref,
            cy3857_500=y_ref,
            aoi_radius_user=config.aoi_radius_m,
            house_area_raster=house_area_raster,
            pad_ring_raster=pad_ring_raster,
            bands_raster=bands_raster,
            pooled_raster=pooled_raster,
            center_elev=house_ground,
            pooled_outer_min=config.pooled_outer_min,
            pooled_outer_max=config.pooled_outer_max,
            ring_bands_m=config.ring_bands_m,
            save_path=os.path.join(config.outdir, "figure1_elevation.png"),
            config=config.viz_config,
        )
        if config.verbose:
            print("✓ figure1_elevation.png")
    except Exception as e:
        if config.verbose:
            print(f"✗ figure1_elevation: {e}")
    
    # Figure 2: Slope
    try:
        figure2_slope(
            slope_user=slope_user,
            slope_500=slope_500,
            circle_mask_user=mask_user,
            circle_mask_500=mask_500,
            xs_flat_user=meta_user["xs"].ravel(),
            ys_flat_user=meta_user["ys"].ravel(),
            xs_flat_500=meta_500["xs"].ravel(),
            ys_flat_500=meta_500["ys"].ravel(),
            src_crs_user=meta_user.get("crs"),
            lon=config.lon,
            lat=config.lat,
            aoi_radius_user=config.aoi_radius_m,
            save_path=os.path.join(config.outdir, "figure2_slope.png"),
            config=config.viz_config,
        )
        if config.verbose:
            print("✓ figure2_slope.png")
    except Exception as e:
        if config.verbose:
            print(f"✗ figure2_slope: {e}")
    
    # Figure 3: Aspect
    try:
        figure3_aspect(
            aspect_user=aspect_user,
            slope_user=slope_user,
            aspect_500=aspect_500,
            slope_500=slope_500,
            circle_mask_user=mask_user,
            circle_mask_500=mask_500,
            xs_flat_user=meta_user["xs"].ravel(),
            ys_flat_user=meta_user["ys"].ravel(),
            xs_flat_500=meta_500["xs"].ravel(),
            ys_flat_500=meta_500["ys"].ravel(),
            aoi_radius_user=config.aoi_radius_m,
            save_path=os.path.join(config.outdir, "figure3_aspect.png"),
            config=config.viz_config,
        )
        if config.verbose:
            print("✓ figure3_aspect.png")
    except Exception as e:
        if config.verbose:
            print(f"✗ figure3_aspect: {e}")
    
    # Figure 4: Terrain + Histogram
    try:
        figure4_terrain_and_hist(
            zone_compact=zone,
            circle_mask_500=mask_500,
            dem_500=dem_500["arr"],
            star_rc=house_rc,
            house_ground_med=house_ground,
            ring_masks=ring_masks_all,
            aoi_radius_user=config.aoi_radius_m,
            save_path=os.path.join(config.outdir, "figure4_terrain_and_hist.png"),
            config=config.viz_config,
        )
        if config.verbose:
            print("✓ figure4_terrain_and_hist.png")
    except Exception as e:
        if config.verbose:
            print(f"✗ figure4_terrain_and_hist: {e}")
    
    # Figure 5 & 6: 3D Visualizations (DEM + rings)
    if config.save_3d:
        try:
            # 1) Determine which DEM paths to use for user AOI and 500m reference
            if config.dem_for_user_aoi and config.aoi_radius_m != config.reference_radius_m:
                user_key = f"dem_{int(config.aoi_radius_m)}m"
            else:
                user_key = "dem_500m"

            user_dtm_path = dem_results[user_key].get(
                "tif_path", dem_results[user_key].get("dtm_path")
            )
            ref_dtm_path = dem_results["dem_500m"].get(
                "tif_path", dem_results["dem_500m"].get("dtm_path")
            )

            # Helpful debugging output to confirm DEM selection
            if config.verbose:
                print("[3D] DEM user:", os.path.basename(user_dtm_path))
                print("[3D] DEM 500m:", os.path.basename(ref_dtm_path))

            # If OSM is unavailable, keep default circular footprint and ring structure
            if osm_poly is None and config.verbose:
                print("  ⚠ OSM did not find the building. Use the default circular footprint and keep the ring difference set.")
                house_area_raster = center_pt.buffer(config.house_buffer_m, resolution=64)

            pad_ring_raster = house_area_raster.buffer(
                config.pad_outer_m, resolution=96
            ).difference(house_area_raster.buffer(config.pad_inner_m, resolution=96))

            bands_raster = [
                house_area_raster.buffer(rmax, resolution=96).difference(
                    house_area_raster.buffer(rmin, resolution=96)
                )
                for (rmin, rmax) in config.ring_bands_m
            ]

            pooled_raster = house_area_raster.buffer(
                config.pooled_outer_max, resolution=96
            ).difference(
                house_area_raster.buffer(config.pooled_outer_min, resolution=96)
            )

            _ = add_3d_figures_to_pipeline(
                dem_path_user=user_dtm_path,
                dem_path_500=ref_dtm_path,
                aoi_radius_user=config.aoi_radius_m,
                house_ground_med=house_ground,
                house_area_raster=house_area_raster,
                pad_ring_raster=pad_ring_raster,
                bands_raster=bands_raster,
                pooled_raster=pooled_raster,
                ring_bands_m=config.ring_bands_m,
                outdir=config.outdir,
                src_crs_user=meta_user.get("crs", "EPSG:3857"),
                verbose=config.verbose,
                config=config.viz_config,
            )

            combo_path = os.path.join(config.outdir, "figure5_6_3d_combo.html")
            if os.path.exists(combo_path) and config.verbose:
                print(f"  ✅ 3D visualization generated：{combo_path}")
            if config.verbose:
                print("✓ figure5_6_3d_combo.html")

        except Exception as e:
            if config.verbose:
                print(f"✗ 3D visualizations: {e}")

    # ===== STEP 8.5: Global Composite Terrain Risk (building-level over 500m) =====
    if config.verbose:
        print("\n" + "=" * 90)
        print("STEP 8.5: GLOBAL COMPOSITE TERRAIN RISK (building-level over 500m)")
        print("=" * 90)

    # Make building polygon globally available for risk function
    set_risk_building_poly(house_area_raster)

    # Compute median elevation for the 500m region (used by risk function)
    area_median_elev_500m = float(
        np.nanmedian(dem_arr[mask_500 & np.isfinite(dem_arr)])
    )

    global_map_path = os.path.join(config.outdir, "figure_terrain_risk_map.png")
    global_risk_info = compute_global_composite_risk_map(
        dem_arr_500=dem_500["arr"],
        slope_500=slope_500,
        aspect_500=aspect_500,
        meta_500=meta_500,
        lat=float(config.lat),
        lon=float(config.lon),
        gamma=float(config.global_gamma),
        res_window_m=float(config.global_res_window_m),
        weights=config.global_weights,
        out_png_path=global_map_path,
        house_ground=float(house_ground),
        area_median_elev=area_median_elev_500m,
    )

    if config.verbose:
        print(f"✓ figure_terrain_risk_map.png")

    # ===== STEP 9: Ring Metrics =====
    if config.verbose:
        print("\n" + "=" * 90)
        print("STEP 9: COMPUTING RING METRICS")
        print("=" * 90)
    
    ring_rows = []
    for r in config.ring_metrics_radii:
        mask_ring = ring_masks_all.get(r, None)
        if mask_ring is None:
            continue
        
        m = compute_ring_metrics_simple(
            dem=dem_arr,
            slope=slope_500,
            aspect=aspect_500,
            house_rc=house_rc,
            house_elev=house_ground,
            ring_mask=mask_ring,
            slope_low_threshold=config.slope_low_threshold,
            slope_mod_threshold=config.slope_mod_threshold,
        )
        
        if m:
            ring_rows.append({
                "Radius (m)": r,
                "Pixels": m["n_pixels"],
                "ΔElev_median (m)": round(m["delta_median"], 3),
                "Slope_mean (°)": round(m["slope_mean"], 2),
                "Slope_median (°)": round(m["slope_median"], 2),
                "Slope_P25 (°)": round(m["slope_p25"], 2),
                "Slope_P75 (°)": round(m["slope_p75"], 2),
                "% Flat <2°": round(m["pct_flat"], 1),
                "% Gentle 2–5°": round(m["pct_gentle"], 1),
                "% Steep ≥5°": round(m["pct_steep"], 1),
                "Convergence (%)": round(m["convergence_%"], 1),
                "Dominant Aspect (°)": (
                    round(m["dominant_aspect"], 1)
                    if np.isfinite(m["dominant_aspect"])
                    else np.nan
                ),
                "Dominant Aspect (cardinal)": cardinal_direction(m["dominant_aspect"]),
            })
    
    summary_df = pd.DataFrame(ring_rows).reset_index(drop=True)
    # Attach 10m ring mask for narrative/utilities (stored in attrs, not as a column)
    summary_df.attrs["ring_mask_10m"] = ring_masks_all.get(10.0, None)

    if config.verbose:
        print(f"Computed metrics for {len(ring_rows)} radii")
    
    # ===== STEP 10: Area Statistics =====
    if config.verbose:
        print("\n" + "=" * 90)
        print("STEP 10: AREA STATISTICS")
        print("=" * 90)

    def _compute_area_stats(
        dem_arr: np.ndarray,
        mask: np.ndarray,
        house_ground: float,
        label: str
    ) -> Tuple[pd.DataFrame, float]:
        """
        Compute basic elevation statistics for an area mask and house elevation.
        Returns a DataFrame (for export) and percentile rank for the house.
        """
        valid = mask & np.isfinite(dem_arr)
        elevs = dem_arr[valid]

        if elevs.size > 0:
            min_elev = float(np.nanmin(elevs))
            max_elev = float(np.nanmax(elevs))
            med_elev = float(np.nanmedian(elevs))
            pr = (elevs < house_ground).sum() / elevs.size * 100.0
        else:
            min_elev = max_elev = med_elev = pr = np.nan

        df = pd.DataFrame({
            "Metric": [
                "House Elevation",
                f"Lowest Elevation in {label}",
                f"Highest Elevation in {label}",
                f"Median Elevation in {label}",
                f"Elevation Percentile Rank in {label}",
            ],
            "Value": [
                f"{house_ground:.2f} m",
                f"{min_elev:.2f} m" if np.isfinite(min_elev) else "NaN",
                f"{max_elev:.2f} m" if np.isfinite(max_elev) else "NaN",
                f"{med_elev:.2f} m" if np.isfinite(med_elev) else "NaN",
                f"{pr:.2f}%" if np.isfinite(pr) else "NaN",
            ],
            "Interpretation": [
                "Reference elevation",
                (
                    f"{(house_ground - min_elev):.2f} m above lowest"
                    if np.isfinite(min_elev)
                    else "NaN"
                ),
                (
                    f"{(max_elev - house_ground):.2f} m below highest"
                    if np.isfinite(max_elev)
                    else "NaN"
                ),
                (
                    "Above median"
                    if np.isfinite(med_elev) and house_ground > med_elev
                    else ("Below median" if np.isfinite(med_elev) else "NaN")
                ),
                (
                    "Above regional median"
                    if np.isfinite(pr) and pr > 50
                    else ("Below regional median" if np.isfinite(pr) else "NaN")
                ),
            ],
        })
        return df, float(pr) if np.isfinite(pr) else np.nan

    # Compute area stats for both: 500m fixed and user AOI
    label_user = (
        f"{int(config.aoi_radius_m)}m"
        if float(config.aoi_radius_m).is_integer()
        else f"{config.aoi_radius_m}m"
    )
    area_df_500m, pr_500m = _compute_area_stats(
        dem_arr, mask_500, house_ground, "500m"
    )
    area_df_user, pr_user_aoi = _compute_area_stats(
        dem_arr, mask_user_aoi, house_ground, label_user
    )

    # Keep backward-compatible meaning:
    # - percentile_rank (old field) = user AOI percentile
    # - percentile_rank_500m = separate 500m percentile
    pr = pr_user_aoi

    def _fmt_pct(x):
        return f"{x:.1f}%" if np.isfinite(x) else "NaN"

    if config.verbose:
        print(f"House percentile (user AOI {label_user}): {_fmt_pct(pr_user_aoi)}")
        print(f"House percentile (500m): {_fmt_pct(pr_500m)}")

    
    # ===== STEP 11: Save Outputs =====
    if config.verbose:
        print("\n" + "=" * 90)
        print("STEP 11: SAVING OUTPUTS")
        print("=" * 90)
    
    try:
        # Determine which formats to save
        formats_to_save = []
        if config.output_format == "parquet":
            formats_to_save = ["parquet"]
        elif config.output_format == "csv":
            formats_to_save = ["csv"]
        else:  # 'both'
            formats_to_save = ["parquet", "csv"]
        
        # Filter formats based on available dependencies
        available_formats = []
        for fmt in formats_to_save:
            if fmt == "parquet":
                try:
                    import pyarrow  # noqa: F401
                    available_formats.append("parquet")
                    if config.verbose:
                        print("  Supported format: parquet (pyarrow available)")
                except ImportError:
                    if config.verbose:
                        print("  ❌ Parquet not supported (pyarrow missing), fallback to csv")
            else:
                available_formats.append("csv")
                if config.verbose:
                    print("  Supported format: csv")
        
        if not available_formats:
            available_formats = ["csv"]
            if config.verbose:
                print("  Default format: csv")
        
        # Save table outputs
        saved_tables = {}
        table_base_dir = os.path.basename(config.outdir)  # e.g. run_id subdir
        
        # Save multi-scale summary
        if config.verbose:
            print("  Saving summary_multiscale table...")
        summary_multiscale_paths = save_table(
            summary_df,
            os.path.join(config.outdir, "summary_multiscale"),
            available_formats
        )
        # Convert to relative paths (for frontend)
        for fmt, abs_path in summary_multiscale_paths.items():
            rel_path = os.path.join(table_base_dir, fmt)
            saved_tables[f"summary_multiscale.{fmt.split('.')[-1]}"] = rel_path
            if config.verbose:
                print(f"    ✅ {fmt}: {abs_path}")
        
        # Save area-level stats (500m)
        if config.verbose:
            print("  Saving summary_area_level (two versions)...")
        area_500_base = os.path.join(config.outdir, "summary_area_level")
        area_500_paths = save_table(area_df_500m, area_500_base, available_formats)
        for fname, abs_path in area_500_paths.items():
            rel_path = os.path.join(table_base_dir, fname)
            saved_tables[f"summary_area_level.{fname.split('.')[-1]}"] = rel_path
            if config.verbose:
                print(f"    ✅ {fname} (500m default): {abs_path}")
        
        # Save area-level stats (user AOI)
        area_user_base = os.path.join(config.outdir, "summary_area_level_user")
        area_user_paths = save_table(area_df_user, area_user_base, available_formats)
        for fname, abs_path in area_user_paths.items():
            rel_path = os.path.join(table_base_dir, fname)
            saved_tables[f"summary_area_level_user.{fname.split('.')[-1]}"] = rel_path
            if config.verbose:
                print(f"    ✅ {fname} (user AOI {label_user}): {abs_path}")
        
        if config.verbose:
            print(f"  Saved {len(saved_tables)} tables total")
        
        # Save narrative text (if enabled)
        narrative_abs_path = os.path.join(config.outdir, "narrative.txt")
        narrative_rel_path = os.path.join(table_base_dir, "narrative.txt")
        if config.generate_narrative:
            if config.verbose:
                print("  Saving narrative...")
            try:
                narrative_text = generate_narrative(
                    summary_df=summary_df,
                    dem=dem_arr,
                    slope=slope_500,
                    aspect=aspect_500,
                    house_rc=house_rc,
                    global_risk=global_risk_info,
                    house_elev_m=house_ground,
                    area_percentiles={"user": pr_user_aoi, "ref_500m": pr_500m},
                    area_labels={"user": f"{int(config.aoi_radius_m)}m", "ref_500m": "500m"},
                )
                with open(narrative_abs_path, "w", encoding="utf-8") as f:
                    f.write(narrative_text.strip() + "\n")
                if config.verbose:
                    print(f"    ✅ narrative.txt: {narrative_abs_path}")
            except Exception as e:
                if config.verbose:
                    print(f"    ⚠ narrative generation failed, writing empty placeholder: {e}")
                # If narrative fails, write an empty file so frontend shows a placeholder
                with open(narrative_abs_path, "w", encoding="utf-8") as f:
                    f.write("")
        else:
            narrative_rel_path = ""
            if config.verbose:
                print("  Skipping narrative (generate_narrative=False)")
        
        # ===== STEP 12: Generate Manifest =====
        if config.verbose:
            print("  Generating manifest.json...")
        
        # 1. Manifest should be saved at run root directory (outputs/{run_id}/manifest.json)
        run_root_dir = config.outdir
        os.makedirs(run_root_dir, exist_ok=True)
        manifest_path = os.path.join(run_root_dir, "manifest.json")
        if config.verbose:
            print(f"    Manifest path: {manifest_path}")
        
        # Relative figure paths (for frontend routing)
        figs_rel_paths = {
            "figure1_elevation": os.path.join(table_base_dir, "figure1_elevation.png"),
            "figure2_slope": os.path.join(table_base_dir, "figure2_slope.png"),
            "figure3_aspect": os.path.join(table_base_dir, "figure3_aspect.png"),
            "figure4_terrain_and_hist": os.path.join(table_base_dir, "figure4_terrain_and_hist.png"),
            "figure5_6_3d_combo": os.path.join(table_base_dir, "figure5_6_3d_combo.html"),
            "figure_terrain_risk_map": os.path.join(table_base_dir, "figure_terrain_risk_map.png"),
        }

        # Table files from saved_tables
        table_rel_paths = list(saved_tables.values())

        # Narrative file
        narrative_files = (
            [narrative_rel_path]
            if config.generate_narrative and os.path.exists(narrative_abs_path)
            else []
        )
        
        # Collect DEM file names as relative paths
        dem_files = []
        for dem_key, dem_info in dem_results.items():
            dtm_filename = os.path.basename(
                dem_info.get("tif_path", dem_info.get("dtm_path", ""))
            )
            if dtm_filename:
                dtm_rel_path = os.path.join(table_base_dir, dtm_filename)
                dem_files.append(dtm_rel_path)
    
        # Aggregate all files and filter empties
        all_files = list(
            set(list(figs_rel_paths.values()) + table_rel_paths + narrative_files + dem_files)
        )
        all_files = [f for f in all_files if f.strip()]
        
        # Only keep entries that actually exist on disk
        existing_files = []
        for rel in all_files:
            if rel.startswith(os.path.basename(config.outdir)):
                # Strip run_id prefix to construct absolute path correctly
                abs_path = os.path.join(config.output_dir, rel.split("/", 1)[-1])
            else:
                abs_path = os.path.join(config.output_dir, rel)
            if os.path.exists(abs_path):
                existing_files.append(rel)
        all_files = existing_files
        
        # Build manifest dict expected by the frontend
        manifest = {
            "run_id": table_base_dir,
            "dataset": config.dataset_name,
            "lat": float(config.lat),
            "lon": float(config.lon),
            "aoi_radius_m": float(config.aoi_radius_m),
            "house_ground_m": float(house_ground),
            "percentile_rank": float(pr) if np.isfinite(pr) else None,
            "percentile_rank_500m": float(pr_500m) if np.isfinite(pr_500m) else None,
            "terrain_risk_score": (
                float(global_risk_info.get("target_risk"))
                if isinstance(global_risk_info, dict)
                and global_risk_info.get("target_risk") is not None
                else None
            ),
            "terrain_risk_percentile": (
                float(global_risk_info.get("global_percentile"))
                if isinstance(global_risk_info, dict)
                and global_risk_info.get("global_percentile") is not None
                else None
            ),
            "address": config.address,
            "tables": saved_tables,
            "figs": figs_rel_paths,
            "files": all_files,
        }
        
        # Save manifest.json at run root directory
        try:
            with open(manifest_path, "w") as f:
                json.dump(manifest, f, indent=2)
            if config.verbose:
                print("    ✅ manifest.json saved successfully")
                print(f"    Manifest contains {len(all_files)} output files")
        except Exception as manifest_err:
            print(f"    ❌ Failed to save manifest: {str(manifest_err)}")
            raise
        
        if config.verbose:
            print("\n" + "=" * 90)
            print("✅ PIPELINE COMPLETE")
            print("=" * 90)
            print(f"Run root dir: {run_root_dir}")
            print(f"Subdir with outputs: {config.outdir}")
            print(f"Manifest path: {manifest_path}")
        
    except Exception as e:
        # Global catch for output errors, with a simplified traceback hint
        print(f"\n    ❌ ERROR IN SAVING OUTPUTS: {str(e)}")
        print(
            f"    Traceback (simplified): "
            f"{e.__traceback__.tb_frame.f_code.co_filename}:{e.__traceback__.tb_lineno}"
        )
        raise

    return {
        "outputs_dir": config.outdir,
        "run_root_dir": run_root_dir,
        "house_ground_m": float(house_ground),
        "percentile_rank": float(pr) if np.isfinite(pr) else None,
        "dataset": config.dataset_name,
        "aoi_radius_m": float(config.aoi_radius_m),
        "summary_multiscale": summary_df,
        "summary_area_level": area_df_500m,
        "summary_area_level_500m": area_df_500m,
        "summary_area_level_user": area_df_user,
        "terrain_risk_score": (
            float(global_risk_info.get("target_risk"))
            if isinstance(global_risk_info, dict)
            else None
        ),
        "terrain_risk_percentile": (
            float(global_risk_info.get("global_percentile"))
            if isinstance(global_risk_info, dict)
            else None
        ),
    }


# ============================================
# ENTRY POINT
# ============================================

if __name__ == "__main__":
    # Create default config
    config = PipelineConfig()
    
    # Example override with manual inputs
    config.lat = 41.8781
    config.lon = -87.6298
    config.aoi_radius_m = 500.0        # User-selectable radius
    config.dem_for_user_aoi = True
    config.output_format = "both"      # Save both CSV and Parquet if possible
    config.generate_narrative = True
    config.save_3d = True
    
    # Run pipeline
    results = run_pipeline(config)
    
    # Print short summary
    print("\n[SUMMARY]")
    print(f"House elevation: {results['house_ground_m']:.2f} m")
    print(f"Percentile rank: {results['percentile_rank']:.1f}%")
    print(f"Output dir: {results['outputs_dir']}")
