# -*- coding: utf-8 -*-
"""
DEM generation & loading (aligned with final pipeline)
-------------------------------------------------------
- discover_usgs_lidar_dataset: Matches dataset using USGS LIDAR boundary resources
- build_dem_for_radius: Generates DEM for a specified radius using PDAL EPT
  (SMRF/Outlier/Range parameters aligned with final script)
- load_dem: Loads DEM into an array and returns metadata
- build_circle_mask: Creates a circular AOI mask on the DEM grid centered at given lat/lon
- build_all_circle_masks: Build masks for multiple radii (50, 100, 120, 200, 300, 500m)
"""

from __future__ import annotations
from typing import Dict, Any, Tuple, List, Optional
import os
import json
import requests
import numpy as np
import rasterio
from rasterio.transform import xy as tfm_xy
import pyproj
import pdal
import geopandas as gpd
from shapely.geometry import Point
from shapely.ops import transform as shp_transform

from .config import (
    DEM_RES_DEFAULT,
    PDAL_THREADS,
    NODATA_SENTINEL,
)

# ===========================
# Helpers (local + aligned with final script)
# ===========================

def _lonlat_to_3857(lon: float, lat: float) -> Tuple[float, float]:
    """Converts WGS84 (lon,lat) to EPSG:3857 (x,y)."""
    t = pyproj.Transformer.from_crs(
        "EPSG:4326", "EPSG:3857", always_xy=True
    ).transform
    return t(lon, lat)


def safe_ll_for_filename(lat: float, lon: float) -> Tuple[str, str]:
    """Converts lat/lon to filename-safe strings (replaces '.' with 'p')."""
    safe_lat = f"{lat:.6f}".replace('.', 'p')
    safe_lon = f"{lon:.6f}".replace('.', 'p')
    return safe_lat, safe_lon


# ===========================
# Dataset Discovery
# ===========================

def discover_usgs_lidar_dataset(
    lat: float,
    lon: float,
    aoi_radius_m: float = 120.0
) -> Optional[Dict[str, str]]:
    """
    Discover USGS LIDAR dataset covering the given lat/lon point.
    Uses USGS LIDAR boundary resources from hobuinc/usgs-lidar GitHub repo.
    
    Args:
        lat: Latitude (WGS84)
        lon: Longitude (WGS84)
        aoi_radius_m: Search radius in meters
    
    Returns:
        {"name": dataset_name_str} or None if not found
    """
    url = (
        "https://raw.githubusercontent.com/hobuinc/usgs-lidar/master/"
        "boundaries/resources.geojson"
    )
    
    try:
        features_json = requests.get(url, timeout=60).json()["features"]
    except Exception as e:
        return None
    
    try:
        # Convert to EPSG:3857 for distance calculations
        gdf = gpd.GeoDataFrame.from_features(
            features_json, crs="EPSG:4326"
        ).to_crs(epsg=3857)
        
        cx, cy = _lonlat_to_3857(lon, lat)
        circle = Point(cx, cy).buffer(float(aoi_radius_m), resolution=128)
        
        hits = gdf[gdf.intersects(circle)]
        if hits.empty:
            return None
        
        # Return first matching dataset (consistent with final script)
        ds_name = hits.iloc[0]["name"]
        return {"name": str(ds_name)}
    
    except Exception:
        return None


# ===========================
# PDAL EPT → DEM
# ===========================

def build_dem_for_radius(
    dataset_name: str,
    lat: float,
    lon: float,
    radius_m: float,
    resolution_m: float = 1.0,
    out_tif: str = None,
    threads: int = 8,
    nodata: float = -9999.0,
) -> int:
    """
    Build DEM via PDAL pipeline (EPT reader + SMRF + Outlier + Classification filter + GDAL writer).
    
    Pipeline steps:
    1. readers.ept: Read from USGS LIDAR EPT point cloud
    2. filters.smrf: Surface Material Removal Filter
    3. filters.outlier: Statistical outlier removal
    4. filters.range: Keep only ground classification (2:2)
    5. writers.gdal: Write DEM GeoTIFF with mean aggregation
    
    Args:
        dataset_name: USGS LIDAR dataset name (e.g., "FL_LeeCounty_2007")
        lat, lon: Point location (WGS84)
        radius_m: Circular AOI radius (meters)
        resolution_m: Output DEM pixel size (meters)
        out_tif: Output GeoTIFF path
        threads: PDAL thread count
        nodata: NoData sentinel value
    
    Returns:
        Point count processed (or 0 if file already exists)
    """
    if os.path.exists(out_tif):
        # Skip if already exists (consistent with final script)
        return 0
    
    # Convert to EPSG:3857 for AOI geometry
    cx, cy = _lonlat_to_3857(lon, lat)
    circle_geom = Point(cx, cy).buffer(radius_m, resolution=128)
    
    # Build PDAL pipeline
    ept_url = (
        f"https://s3-us-west-2.amazonaws.com/usgs-lidar-public/"
        f"{dataset_name}/ept.json"
    )
    
    pipeline_spec = {
        "pipeline": [
            {
                "type": "readers.ept",
                "filename": ept_url,
                "threads": threads,
                "polygon": circle_geom.wkt,
                "resolution": resolution_m
            },
            {
                "type": "filters.smrf",
                "window": 32.0,
                "slope": 0.2,
                "threshold": 0.45,
                "scalar": 1.25
            },
            {
                "type": "filters.outlier",
                "method": "statistical",
                "mean_k": 8,
                "multiplier": 2.5
            },
            {
                "type": "filters.range",
                "limits": "Classification[2:2]"  # Ground only
            },
            {
                "type": "writers.gdal",
                "filename": out_tif,
                "resolution": resolution_m,
                "output_type": "mean",
                "gdaldriver": "GTiff",
                "nodata": nodata,
                "gdalopts": "COMPRESS=LZW,TILED=YES"
            }
        ]
    }
    
    # Execute PDAL pipeline
    count = pdal.Pipeline(json.dumps(pipeline_spec)).execute()
    return int(count)


# ===========================
# DEM Management
# ===========================

# ✅ ATOMIC FUNCTION (原子操作):
def ensure_dem(
    dataset_name: str,
    lat: float,
    lon: float,
    out_tif_path: str,          # 任意路径,没有"120m"/"500m"
    radius_m: float,             # 任意半径: 50, 100, 120, 200, 300, 500...
    resolution_m: float = 1.0,
    nodata: float = -9999.0,
    threads: int = 8,
) -> Dict[str, Any]:
    """生成或重用单个DEM - 支持任意半径"""
    if not os.path.exists(out_tif_path):
        build_dem_for_radius(
            dataset_name=dataset_name,
            lat=lat,
            lon=lon,
            radius_m=radius_m,      # ✅ 动态半径
            resolution_m=resolution_m,
            out_tif=out_tif_path,
            threads=threads,
            nodata=nodata,
        )
    
    return {
        "dataset": dataset_name,
        "tif_path": out_tif_path,
        "radius_m": radius_m,      # ✅ 记录实际使用的半径
        "resolution_m": resolution_m,
        "nodata": nodata,
    }


# ✅ CONVENIENCE FUNCTION (便利函数):
def build_multiple_dems(
    dataset_name: str,
    lat: float,
    lon: float,
    dem_specs: Dict[str, Dict[str, float]],  # 字典定义所有DEM规格
    nodata: float = -9999.0,
    threads: int = 8,
) -> Dict[str, Dict[str, Any]]:
    """
    生成多个DEM - 完全通用!
    
    Example in pipeline:
        dem_specs = {
            "dem_120m": {"out_path": "./dem_100m.tif", "radius_m": 120, "resolution_m": 1.0},
            "dem_500m": {"out_path": "./dem_500m.tif", "radius_m": 500, "resolution_m": 1.0},
            "dem_50m":  {"out_path": "./dem_50m.tif",  "radius_m": 50,  "resolution_m": 1.0},
            "dem_custom": {"out_path": "./dem_custom.tif", "radius_m": 250, "resolution_m": 0.5},
        }
        dems = build_multiple_dems(dataset_name, lat, lon, dem_specs)
    """
    results = {}
    
    for dem_name, spec in dem_specs.items():
        result = ensure_dem(
            dataset_name=dataset_name,
            lat=lat,
            lon=lon,
            out_tif_path=spec["out_path"],
            radius_m=spec["radius_m"],              # ✅ 任意半径
            resolution_m=spec.get("resolution_m", 1.0),
            nodata=nodata,
            threads=threads,
        )
        results[dem_name] = result
    
    return results


# ===========================
# DEM Loading
# ===========================

def load_dem(
    path: str,
    nodata: float = -9999.0,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Load DEM from GeoTIFF and return array + metadata.
    
    Returns:
        (full_dict, metadata_dict) where:
        - full_dict contains: arr, transform, crs, res, shape, xs, ys, cx3857, cy3857
        - metadata_dict contains: transform, crs, res, shape, xs, ys, cx3857, cy3857
    """
    with rasterio.open(path) as src:
        arr = src.read(1).astype(float)
        
        # Handle NoData
        if src.nodata is not None:
            arr[arr == src.nodata] = np.nan
        else:
            arr[arr == nodata] = np.nan
        
        H, W = arr.shape
        rows, cols = np.indices((H, W))
        
        # Get pixel coordinates (center)
        xs_flat, ys_flat = rasterio.transform.xy(
            src.transform, rows.ravel(), cols.ravel(), offset="center"
        )
        xs = np.asarray(xs_flat, dtype=float).reshape(H, W)
        ys = np.asarray(ys_flat, dtype=float).reshape(H, W)
        
        # Center coordinates (in source CRS, typically EPSG:3857)
        cx3857 = float(np.nanmean(xs))
        cy3857 = float(np.nanmean(ys))
        
        meta = {
            "transform": src.transform,
            "crs": src.crs,
            "res": src.res,
            "shape": (H, W),
            "xs": xs,
            "ys": ys,
            "cx3857": cx3857,
            "cy3857": cy3857,
        }
        
        out = {"arr": arr, **meta}
        return out, meta


# ===========================
# Circular AOI Masking
# ===========================

def build_circle_mask(
    xs_grid: np.ndarray,
    ys_grid: np.ndarray,
    lon: float,
    lat: float,
    radius_m: float,
    src_crs: str = "EPSG:3857"
) -> np.ndarray:
    """
    Create circular mask for given radius on DEM grid.
    
    Args:
        xs_grid, ys_grid: Coordinate grids from load_dem metadata
        lon, lat: Center point (WGS84)
        radius_m: Radius in meters
        src_crs: Source CRS (default EPSG:3857)
    
    Returns:
        Boolean mask array
    """
    cx, cy = _lonlat_to_3857(lon, lat)
    dist = np.sqrt((xs_grid - cx)**2 + (ys_grid - cy)**2)
    return dist <= float(radius_m)


def build_all_circle_masks(
    xs_grid: np.ndarray,
    ys_grid: np.ndarray,
    lon: float,
    lat: float,
    radii_m: Optional[List[float]] = None
) -> Dict[float, np.ndarray]:
    """
    Build circular masks for multiple radii (50, 100, 120, 200, 300, 500m).
    
    Args:
        xs_grid, ys_grid: Coordinate grids
        lon, lat: Center point (WGS84)
        radii_m: List of radii to compute (default: standard set)
    
    Returns:
        {radius: mask} dictionary
    """
    if radii_m is None:
        radii_m = [50.0, 100.0, 120.0, 200.0, 300.0, 500.0]
    
    cx, cy = _lonlat_to_3857(lon, lat)
    xs_grid = np.asarray(xs_grid, dtype=float)
    ys_grid = np.asarray(ys_grid, dtype=float)
    dist = np.sqrt((xs_grid - cx)**2 + (ys_grid - cy)**2)
    
    masks = {}
    for r in radii_m:
        masks[float(r)] = dist <= float(r)
    
    return masks