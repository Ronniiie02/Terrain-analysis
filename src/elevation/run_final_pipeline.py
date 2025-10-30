# -*- coding: utf-8 -*-
"""
COMPLETE TERRAIN ANALYSIS PIPELINE (Single-File Entry Point)
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
matplotlib.use('Agg')
import rasterio
from pathlib import Path 
import re
from datetime import datetime
import plotly.io as pio
pio.renderers.default = "browser"

from .geocode import resolve_location_from_user_input
from .osm import fetch_building_osm
from .dem import (
    discover_usgs_lidar_dataset,
    build_multiple_dems,
    load_dem,
    build_all_circle_masks,
)
from .terrain import (
    estimate_house_ground_adaptive,
    derive_slope_aspect_curvature,
    classify_terrain_adaptive,
    compute_ring_metrics_unified,
    generate_narrative, 
)

from .viz import (
    figure1_elevation,
    figure2_slope,
    figure3_aspect,
    figure4_terrain_and_hist,
    add_3d_figures_to_pipeline,
    DEFAULT_CONFIG as VIZ_DEFAULT_CONFIG,
)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")) 

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
        self.aoi_radius_m: float = 500.0          # User-selectable!
        self.dem_resolution_m: float = 1.0
        self.dem_nodata: float = -9999.0
        self.pdal_threads: int = 8
        
        # DEM generation - support user AOI + 500m reference
        self.dem_for_user_aoi: bool = True         # Generate DEM for user AOI
        self.reference_radius_m: float = 500.0     # Always generate for reference
        
        # ===== OSM Building =====
        self.osm_buffer_m: float = 120.0           # Search radius (dynamic!)
        self.osm_timeout_s: int = 25
        
        # ===== House Estimation (all parametrized!) =====
        self.house_buffer_m: float = 3.0
        self.plane_band_inner_m: float = 5.0
        self.plane_band_outer_m: float = 15.0
        
        # Adaptive parameters (no hardcodes!)
        self.min_size_edge: int = 10
        self.min_size_ring_3_8: int = 20
        self.min_size_ring_8_15: int = 30
        self.min_size_ring_15_30: int = 50
        self.q_high_tail_threshold: float = 98.0
        self.hard_q_high_tail: float = 99.5
        self.fallback_sample_size: int = 150
        
        # ===== Terrain Classification (parametrized!) =====
        self.slope_low_threshold: float = 2.0      # < 2° = Low-Lying
        self.slope_mod_threshold: float = 5.0      # 2-5° = Moderate
                                                    # >= 5° = Steep
        
        # ===== Ring Definitions (parametrized!) =====
        self.ring_bands_m: Tuple[Tuple[float, float], ...] = (
            (3.0, 8.0),
            (8.0, 15.0),
            (15.0, 30.0)
        )
        
        self.pad_inner_m: float = 0.8
        self.pad_outer_m: float = 2.5
        
        self.pooled_outer_min: float = 3.0
        self.pooled_outer_max: float = 25.0
        
        # ===== Ring Metrics Radii (parametrized!) =====
        self.ring_metrics_radii: List[float] = [50.0, 100.0, 200.0, 300.0, 500.0]
        self.ring_analysis_radii: List[float] = [10.0, 50.0, 100.0, 200.0, 300.0, 500.0]
        
        # ===== Visualization =====
        self.viz_config: Dict[str, Any] = VIZ_DEFAULT_CONFIG.copy()
        
        # ===== Output =====
        self.output_dir: str = "./outputs"
        self.output_format: str = "csv"             # parquet, csv, or both
        self.generate_narrative: bool = True
        self.verbose: bool = True
        self.save_3d: bool = True
        
        # Derivative fields
        self.dataset_name: Optional[str] = None
        self.outdir: Optional[str] = None


# ============================================
# HELPER FUNCTIONS
# ============================================
def _slugify_address(address: str, fallback: str = "") -> str:
    if not address:
        return (fallback or "address_unknown")
    s = re.sub(r"[^A-Za-z0-9]+", "_", address.strip())
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = (fallback or "address_unknown")
    return s[:80] 

def _lonlat_to_3857(lon: float, lat: float) -> Tuple[float, float]:
    """Convert WGS84 to Web Mercator"""
    import pyproj
    t = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform
    return t(lon, lat)

def _lonlat_to_crs(lon: float, lat: float, dst_crs) -> Tuple[float, float]:
    import pyproj
    tr = pyproj.Transformer.from_crs("EPSG:4326", dst_crs, always_xy=True).transform
    return tr(lon, lat)

def house_rc_from_lonlat(meta: Dict, lon: float, lat: float) -> Tuple[int, int]:
    """Find raster cell (row, col) for given (lon, lat)"""
    dst_crs = meta.get("crs")
    cx, cy = _lonlat_to_crs(lon, lat, dst_crs)
    xs, ys = meta["xs"], meta["ys"]
    d2 = (xs - cx)**2 + (ys - cy)**2
    r, c = np.unravel_index(np.nanargmin(d2), xs.shape)
    return int(r), int(c)


def build_ring_masks(
    meta: Dict,
    radii: List[float],
    lon: float,
    lat: float
) -> Dict[float, np.ndarray]:
    """Build circular masks for multiple radii"""
    dst_crs = meta.get("crs")
    cx, cy = _lonlat_to_crs(lon, lat, dst_crs)
    xs, ys = meta["xs"], meta["ys"]
    dist = np.sqrt((xs - cx)**2 + (ys - cy)**2)
    masks = {}
    for r in radii:
        rr = float(r)
        m = (dist <= rr) & np.isfinite(xs) & np.isfinite(ys)
        masks[rr] = m
    return masks


def cardinal_direction(deg: float) -> str:
    """Convert bearing to cardinal direction"""
    if not np.isfinite(deg):
        return "Unknown"
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[int((deg + 22.5) / 45) % 8]


def compute_flow_convergence(
    aspect_array: np.ndarray,
    house_rc: Tuple[int, int],
    ring_mask: np.ndarray
) -> float:
    """Calculate flow convergence percentage"""
    rows, cols = np.indices(aspect_array.shape)
    rows_sel = rows[ring_mask]
    cols_sel = cols[ring_mask]
    aspects_sel = aspect_array[ring_mask]
    hr, hc = house_rc
    conv, total = 0, 0
    
    for r, c, a in zip(rows_sel, cols_sel, aspects_sel):
        if not np.isfinite(a):
            continue
        total += 1
        dr, dc = hr - r, hc - c
        bearing_to_house = (np.degrees(np.arctan2(dc, -dr)) + 360) % 360
        diff = abs(a - bearing_to_house)
        if min(diff, 360 - diff) <= 45:
            conv += 1
    
    return (conv / total * 100.0) if total > 0 else 0.0


def compute_ring_metrics_simple(
    dem: np.ndarray,
    slope: np.ndarray,
    aspect: np.ndarray,
    house_rc: Tuple[int, int],
    house_elev: float,
    ring_mask: np.ndarray,
    slope_low_threshold: float = 2.0,
    slope_mod_threshold: float = 5.0,
) -> Optional[Dict]:
    """Compute comprehensive ring metrics (all parametrized!)"""
    if ring_mask.sum() == 0:
        return None
    
    elevs = dem[ring_mask]
    med = float(np.nanmedian(elevs))
    delta = float(house_elev - med)
    pct_higher = float((elevs > house_elev).sum() / elevs.size * 100.0)
    pct_lower = float((elevs < house_elev).sum() / elevs.size * 100.0)
    
    s = slope[ring_mask]
    slope_mean = float(np.nanmean(s))
    slope_median = float(np.nanmedian(s))
    p25, p75 = float(np.nanpercentile(s, 25)), float(np.nanpercentile(s, 75))
    pct_flat = float((s < slope_low_threshold).sum() / s.size * 100.0)
    pct_gentle = float(((s >= slope_low_threshold) & (s < slope_mod_threshold)).sum() / s.size * 100.0)
    pct_steep = float((s >= slope_mod_threshold).sum() / s.size * 100.0)
    
    conv = float(compute_flow_convergence(aspect, house_rc, ring_mask))
    
    asp = aspect[ring_mask]
    vals = asp[np.isfinite(asp)]
    if vals.size > 0:
        sin_m = np.nanmean(np.sin(np.deg2rad(vals)))
        cos_m = np.nanmean(np.cos(np.deg2rad(vals)))
        dom = (np.degrees(np.arctan2(sin_m, cos_m)) + 360) % 360
    else:
        dom = np.nan
    
    return {
        'n_pixels': int(ring_mask.sum()),
        'ring_median': med,
        'delta_median': delta,
        'pct_higher': pct_higher,
        'pct_lower': pct_lower,
        'slope_mean': slope_mean,
        'slope_median': slope_median,
        'slope_p25': p25,
        'slope_p75': p75,
        'pct_flat': pct_flat,
        'pct_gentle': pct_gentle,
        'pct_steep': pct_steep,
        'convergence_%': conv,
        'dominant_aspect': float(dom) if np.isfinite(dom) else np.nan,
    }



def save_table(
    df: pd.DataFrame,
    base_path: str,
    formats: List[str] = ["csv"]
) -> Dict[str, str]:
    """保存表格；返回 {<文件名>: <绝对路径>}，避免键冲突并匹配前端期待"""
    results: Dict[str, str] = {}

    for fmt in formats:
        if fmt == "parquet":
            p = base_path + ".parquet"
            try:
                import pyarrow as pa, pyarrow.parquet as pq, pyarrow.fs as pafs
                table = pa.Table.from_pandas(df)
                fs = pafs.LocalFileSystem()
                pq.write_table(table, p, filesystem=fs)
                results[Path(p).name] = p   # ← 关键：用文件名当 key
            except Exception:
                p_csv = base_path + ".csv"
                df.to_csv(p_csv, index=False)
                results[Path(p_csv).name] = p_csv
        elif fmt == "csv":
            p = base_path + ".csv"
            df.to_csv(p, index=False)
            results[Path(p).name] = p      # ← 关键：用文件名当 key
        # 其它格式略

    return results


# ============================================
# DEM GENERATION WITH DSM SUPPORT
# ============================================

def build_pdal_grid_with_dsm(ept_url: str, polygon_wkt: str, res: float, out_path: str, 
                           kind: str = "dtm", nodata: float = -9999, threads: int = 8):
    """Build PDAL pipeline for DTM or DSM generation"""
    base = [
        {
            "type": "readers.ept", 
            "filename": ept_url, 
            "threads": threads,
            "polygon": polygon_wkt, 
            "resolution": res
        },
        {
            "type": "filters.outlier", 
            "method": "statistical", 
            "mean_k": 8, 
            "multiplier": 2.5
        },
    ]
    
    if kind == "dtm":
        # For DTM: ground classification and ground points only
        base.insert(1, {
            "type": "filters.smrf", 
            "window": 32.0, 
            "slope": 0.2, 
            "threshold": 0.45, 
            "scalar": 1.25
        })
        base.append({
            "type": "filters.range", 
            "limits": "Classification[2:2]"  # Ground points only
        })
        output_type = "mean"
    else:
        # For DSM: all points (surface model)
        output_type = "max"
    
    pipeline = base + [
        {
            "type": "writers.gdal",
            "filename": out_path,
            "resolution": res,
            "output_type": output_type,
            "gdaldriver": "GTiff",
            "nodata": nodata,
            "gdalopts": "COMPRESS=LZW,TILED=YES,BIGTIFF=YES"
        }
    ]
    
    return {"pipeline": pipeline}


def _preflight_ept_bounds(dataset_name: str, lon: float, lat: float, radius_m: float) -> dict:
    """
    用 EPT 的 bounds 估算 AOI 最大可用半径；若目标半径超出，返回 ok=False 和建议的 max_radius_m
    """
    import json
    import requests
    import pyproj

    ept_url = f"https://s3-us-west-2.amazonaws.com/usgs-lidar-public/{dataset_name}/ept.json"
    try:
        meta = requests.get(ept_url, timeout=10).json()
    except Exception:
        # 取不到元数据就让 PDAL 去失败；不要在预检阶段阻断
        return {"ok": True}

    # CRS：优先用 WKT；否则拼接 authority/horizontal（常见：EPSG + 代码）
    srs = meta.get("srs", {}) or {}
    srs_wkt = srs.get("wkt")
    if not srs_wkt:
        auth = srs.get("authority")
        horiz = srs.get("horizontal") or srs.get("epsg")
        if auth and horiz:
            srs_wkt = f"EPSG:{horiz}"
        else:
            srs_wkt = "EPSG:3857"

    bounds = meta.get("boundsConforming") or meta.get("bounds")
    if not bounds:
        return {"ok": True}

    # 兼容两种结构：[[minx,miny,minz],[maxx,maxy,maxz]] 或 [minx,miny,minz,maxx,maxy,maxz]
    if isinstance(bounds[0], (list, tuple)):
        (minx, miny, _minz), (maxx, maxy, _maxz) = bounds
    else:
        minx, miny, _minz, maxx, maxy, _maxz = bounds

    # 把 WGS84 的 lon/lat 转到 EPT 的坐标系
    to_ept = pyproj.Transformer.from_crs("EPSG:4326", srs_wkt, always_xy=True).transform
    cx, cy = to_ept(lon, lat)  # 注意：to_ept 是函数

    # 离边界盒四边的最小距离就是“最大可用半径”
    max_r = min(cx - minx, maxx - cx, cy - miny, maxy - cy)
    if max_r <= 0:
        return {
            "ok": False,
            "error_code": "AOI_OUTSIDE_EPT_BOUNDS",
            "message": "Location is outside the LiDAR dataset grid.",
            "max_radius_m": 0.0
        }

    if radius_m > max_r:
        return {
            "ok": False,
            "error_code": "AOI_OUTSIDE_EPT_BOUNDS",
            "message": "AOI exceeds LiDAR grid extent for this dataset.",
            "max_radius_m": float(max_r)
        }

    return {"ok": True, "max_radius_m": float(max_r)}





def generate_dem_with_dsm(dataset_name: str, lat: float, lon: float, 
                         dem_specs: Dict[str, Dict], nodata: float = -9999, 
                         threads: int = 8, verbose: bool = True):
    """Generate both DTM and DSM for each specification + 缓存复用"""
    import pdal
    from shapely.geometry import Point
    import pyproj
    
    # 缓存关键：用「经纬度+半径+分辨率」生成唯一标识（保留6位小数，避免精度冲突）
    def get_cache_key(lat, lon, radius, res):
        return f"cache_{lat:.6f}_{lon:.6f}_{radius:.0f}m_{res:.1f}m"
    
    # 缓存目录（独立于任务输出，方便复用）
    CACHE_DIR = os.path.join(PROJECT_ROOT, "dem_cache")
    os.makedirs(CACHE_DIR, exist_ok=True)
    
    # Convert to meters (EPSG:3857)
    def to_3857(x, y):
        t = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform
        return t(x, y)
    
    cx3857, cy3857 = to_3857(lon, lat)
    ept_url = f"https://s3-us-west-2.amazonaws.com/usgs-lidar-public/{dataset_name}/ept.json"
    
    results = {}
    
    for dem_key, spec in dem_specs.items():
        radius_m = spec["radius_m"]
        resolution_m = spec["resolution_m"]
        # —— EPT 边界预检：先判断 AOI 是否会越界
        pre = _preflight_ept_bounds(dataset_name, lon, lat, radius_m)
        if not pre.get("ok", True):
            # 抛出带 code 的异常，上层会封装到 /runs/:id 状态中
            raise RuntimeError(json.dumps({
                "error_code": pre.get("error_code", "AOI_OUTSIDE_EPT_BOUNDS"),
                "message": pre.get("message", "AOI is outside of LiDAR grid."),
                "max_radius_m": pre.get("max_radius_m", 0.0),
                "dataset": dataset_name,
                "radius_m": radius_m,
                "resolution_m": resolution_m
            }))
        cache_key = get_cache_key(lat, lon, radius_m, resolution_m)
        cache_dtm_path = os.path.join(CACHE_DIR, f"{cache_key}_dtm.tif")
        cache_dsm_path = os.path.join(CACHE_DIR, f"{cache_key}_dsm.tif")
        
        # 检查缓存：如果已存在，直接复用，跳过PDAL生成
        if os.path.exists(cache_dtm_path) and os.path.exists(cache_dsm_path):
            if verbose:
                print(f"  🚀 复用缓存的 {radius_m}m DEM (DTM+DSM)")
            results[dem_key] = {
                "dtm_path": cache_dtm_path,
                "dsm_path": cache_dsm_path,
                "radius_m": radius_m,
                "resolution_m": resolution_m
            }
            continue
        
        # 缓存不存在，生成DEM（原逻辑不变）
        circle_geom = Point(cx3857, cy3857).buffer(float(radius_m), resolution=128)
        poly_wkt = circle_geom.wkt
        
        try:# 生成DTM（保存到缓存目录）
            if not os.path.exists(cache_dtm_path):
                if verbose:
                    print(f"Building {radius_m}m DTM → {cache_dtm_path}")
                pipeline_json = build_pdal_grid_with_dsm(
                    ept_url, poly_wkt, resolution_m, cache_dtm_path, "dtm", nodata, threads
                )
                pdal.Pipeline(json.dumps(pipeline_json)).execute()
            
            # 生成DSM（保存到缓存目录）
            if not os.path.exists(cache_dsm_path):
                if verbose:
                    print(f"Building {radius_m}m DSM → {cache_dsm_path}")
                pipeline_json = build_pdal_grid_with_dsm(
                    ept_url, poly_wkt, resolution_m, cache_dsm_path, "dsm", nodata, threads
                )
                pdal.Pipeline(json.dumps(pipeline_json)).execute()

        except Exception as e:
            msg = str(e)
            if "Grid width out of range" in msg or "writers.gdal" in msg:
                raise RuntimeError(json.dumps({
                    "error_code": "AOI_OUTSIDE_EPT_BOUNDS",
                    "message": "AOI exceeds LiDAR grid extent (PDAL writers.gdal).",
                    "max_radius_m": pre.get("max_radius_m"),   # 预检若得到则透传
                    "dataset": dataset_name,
                    "radius_m": radius_m,
                    "resolution_m": resolution_m
                }))
            raise
        # 结果指向缓存文件
        results[dem_key] = {
            "dtm_path": cache_dtm_path,
            "dsm_path": cache_dsm_path,
            "radius_m": radius_m,
            "resolution_m": resolution_m
        }
    
    return results


# ============================================
# MAIN PIPELINE
# ============================================

def run_pipeline(config: PipelineConfig) -> Dict[str, Any]:
    """
    Complete terrain analysis pipeline.
    All parameters in config - ZERO hardcodes!
    """
    # --- 地理编码和坐标验证 ---
    lat_ok = isinstance(config.lat, (int, float)) and np.isfinite(config.lat)
    lon_ok = isinstance(config.lon, (int, float)) and np.isfinite(config.lon)

    # 地理编码处理
    if config.address and not (lat_ok and lon_ok):
        lat, lon, faddr = resolve_location_from_user_input(config.address)
        if lat is None or lon is None:
            raise RuntimeError(json.dumps({
                "error_code": "GEOCODING_FAILED",
                "message": f"无法解析地址: {config.address}",
                "address": config.address
            }))
        config.lat, config.lon = float(lat), float(lon)
        config.address = faddr

    # 最终坐标验证
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

    # --- 创建输出目录（在重定位之后，确保使用最终坐标）---
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
    
    # Create output directory
    config.outdir = config.output_dir
    os.makedirs(config.outdir, exist_ok=True)
    
    # ===== STEP 2: DEM Generation (DTM + DSM) =====
    if config.verbose:
        print("\n" + "=" * 90)
        print("STEP 2: DEM GENERATION (DTM + DSM)")
        print("=" * 90)
    # 在 STEP 2 的 dem_specs 定义前添加：
    dem_specs = {}

    if config.aoi_radius_m <= 100:
        user_resolution = 2.0
    else:
        user_resolution = 1.0

# 500m参考DEM（固定1m分辨率）
    dem_specs["dem_500m"] = {
        "out_path": os.path.join(config.outdir, "dem_500m_dtm.tif"),
        "radius_m": config.reference_radius_m,
        "resolution_m": 1.0,  # 参考DEM保持高精度
    }
    if config.aoi_radius_m != config.reference_radius_m:
# 用户半径DEM（动态分辨率）
        user_radius_key = f"dem_{int(config.aoi_radius_m)}m"
        dem_specs[user_radius_key] = {
            "out_path": os.path.join(config.outdir, f"dem_{int(config.aoi_radius_m)}m_dtm.tif"),
            "radius_m": config.aoi_radius_m,
            "resolution_m": user_resolution,  # 动态分辨率生效
        }
    else:
        user_radius_key = "dem_500m"

    if config.verbose:
        print(f"DEM specifications:")
        print(f"  - 500m reference: {dem_specs['dem_500m']['resolution_m']}m resolution")
        print(f"  - {config.aoi_radius_m}m user: {dem_specs[user_radius_key]['resolution_m']}m resolution")
    # Generate both DTM and DSM
    try:
        dem_results = generate_dem_with_dsm(
            dataset_name=config.dataset_name,
            lat=config.lat,
            lon=config.lon,
            dem_specs=dem_specs,
            nodata=config.dem_nodata,
            threads=config.pdal_threads,
            verbose=config.verbose
        )
    except RuntimeError as e:
        # 透传我们抛出的结构化错误（JSON 字符串），让上游 /runs 能拿到 error_code 等
        try:
            err = json.loads(str(e))
        except Exception:
            err = {"error_code":"PIPELINE_ERROR","message":str(e)}
        # 直接再抛一次，让调用方（pipeline_runner）记成 error 状态并把 err 带回
        raise RuntimeError(json.dumps(err))

    if config.verbose:
        print(f"Generated {len(dem_results)} DEM pairs (DTM + DSM)")
    
    # ===== STEP 3: Load Primary DEM =====
    if config.verbose:
        print("\n" + "=" * 90)
        print("STEP 3: LOADING DEM & BUILDING MASKS")
        print("=" * 90)

    user_dem_result = dem_results[user_radius_key]
    user_dem_path = user_dem_result["dtm_path"]
    dem_user, meta_user = load_dem(user_dem_path, nodata=config.dem_nodata)
    mask_user = build_ring_masks(meta_user, [config.aoi_radius_m], config.lon, config.lat)[config.aoi_radius_m]
    
    # 加载500m参考DEM（右图用）
    ref_dem_result = dem_results["dem_500m"] 
    ref_dem_path = ref_dem_result["dtm_path"]
    dem_500, meta_500 = load_dem(ref_dem_path, nodata=config.dem_nodata)
    mask_500 = build_ring_masks(meta_500, [config.reference_radius_m], config.lon, config.lat)[config.reference_radius_m]
    dem_arr = dem_500["arr"]  # 关键：定义 dem_arr（后续分析用）
    meta = meta_500  # 关键：定义 meta（用于构建ring_masks_all）

    if config.verbose:
        print(f"User DEM ({config.aoi_radius_m}m): {dem_user['arr'].shape}")
        print(f"Reference DEM (500m): {dem_500['arr'].shape}")

    # Build masks for all radii
    all_radii = sorted(set(config.ring_analysis_radii + [config.aoi_radius_m, config.reference_radius_m]))
    ring_masks_all = build_ring_masks(meta, all_radii, config.lon, config.lat)
    # Get mask for user AOI
    mask_user_aoi = ring_masks_all.get(config.aoi_radius_m, ring_masks_all[config.reference_radius_m])
    
    if config.verbose:
        print(f"DEM shape: {dem_arr.shape}")
        print(f"Primary mask ({config.aoi_radius_m}m): {mask_user_aoi.sum()} pixels")
    
    # ===== STEP 4: Fetch OSM Building =====
    # ===== STEP 4: FETCHING OSM BUILDING =====
    if config.verbose:
        print("\n" + "=" * 90)
        print("STEP 4: FETCHING OSM BUILDING")
        print("=" * 90)

    # 复用 STEP 0 的重定位结果；否则再抓 OSM
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

    # 半径闸门：太远就弃用
    def _distance_m_safe(sel, lon0, lat0):
        try:
            d = float(sel.get("distance_m", float("nan")))
            if not np.isfinite(d):
                raise ValueError
            return d
        except Exception:
            import pyproj
            from shapely.ops import transform as shp_transform
            from shapely.geometry import Point
            aeqd = pyproj.CRS.from_proj4(f"+proj=aeqd +lat_0={lat0} +lon_0={lon0} +datum=WGS84 +units=m +no_defs")
            to_local = pyproj.Transformer.from_crs("EPSG:4326", aeqd, always_xy=True).transform
            g_local = shp_transform(to_local, sel["geometry"])
            p_local = shp_transform(to_local, Point(lon0, lat0))
            return p_local.distance(g_local)

    if osm_result.get("success") and osm_result.get("selected"):
        sel = osm_result["selected"]
        # 1) 若 footprint 包含中心，直接接受（不看距离）
        if sel.get("contains_query", False):
            pass  # keep
        else:
            dist_m = _distance_m_safe(sel, config.lon, config.lat)
            thr = float(config.aoi_radius_m) * 1.2  # 放宽到 1.2×AOI
            if dist_m > thr:
                if config.verbose:
                    print(f"⚠ OSM building {dist_m:.1f} m away > 1.2×AOI ({thr:.1f} m), discard.")
                osm_result = {"success": False, "selected": None, "error": "too_far"}


    # 重投影 + 构建绘图几何（用与 DEM 一致的 CRS）
    from shapely.geometry import Point
    from shapely.ops import transform as shp_transform
    import pyproj

    dst_crs = (meta_user.get("crs") if 'meta_user' in locals() else None) or meta_500.get("crs")
    cx_dst, cy_dst = _lonlat_to_crs(config.lon, config.lat, dst_crs)
    center_pt = Point(cx_dst, cy_dst)

    if osm_result.get("success") and osm_result.get("selected"):
        to_raster = pyproj.Transformer.from_crs("EPSG:4326", dst_crs, always_xy=True).transform
        osm_poly = shp_transform(to_raster, osm_result["selected"]["geometry"])
        house_area_raster = osm_poly
    else:
        osm_poly = None
        house_area_raster = center_pt.buffer(config.house_buffer_m, resolution=64)

    pad_ring_raster = house_area_raster.buffer(config.pad_outer_m, resolution=96).difference(house_area_raster.buffer(config.pad_inner_m, resolution=96))
    bands_raster = [house_area_raster.buffer(rmax, resolution=96).difference(
                      house_area_raster.buffer(rmin, resolution=96))
                    for (rmin, rmax) in config.ring_bands_m]
    pooled_raster = house_area_raster.buffer(config.pooled_outer_max, resolution=96).difference(
                      house_area_raster.buffer(config.pooled_outer_min, resolution=96))
    
    # ===== STEP 5: Compute Terrain Derivatives =====
    if config.verbose:
        print("\n" + "=" * 90)
        print("STEP 5: COMPUTING TERRAIN DERIVATIVES")
        print("=" * 90)
    
    deriv = derive_slope_aspect_curvature(
        dem_arr,
        res_x=config.dem_resolution_m,
        res_y=config.dem_resolution_m,
    )
    slope_500 = deriv['slope']
    aspect_500 = deriv['aspect']
    curv_500 = deriv['curvature']   
    user_dem_resolution = user_dem_result["resolution_m"]

    deriv_user = derive_slope_aspect_curvature(
        dem_user["arr"],
        res_x=user_dem_resolution,
        res_y=user_dem_resolution,
    )
    slope_user = deriv_user['slope']
    aspect_user = deriv_user['aspect']
    curv_user = deriv_user['curvature']

    if config.verbose:
        print("✓ Computed slope, aspect, curvature")
    
    # ===== STEP 6: Estimate House Elevation =====
    if config.verbose:
        print("\n" + "=" * 90)
        print("STEP 6: ESTIMATING HOUSE ELEVATION")
        print("=" * 90)
    
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

    # 左图星标：用 meta_user["crs"] 转 lon/lat
    x_user, y_user = _lonlat_to_crs(config.lon, config.lat, meta_user.get("crs"))

    # 右图星标：用 meta_500["crs"] 转 lon/lat
    x_ref, y_ref   = _lonlat_to_crs(config.lon, config.lat, meta_500.get("crs"))    
    # Figure 1: Elevation
    try:
        figure1_elevation(
            out_tif_user=user_dem_path,                 # 1. 用户DEM路径
            dem_500=dem_500["arr"],                      # 2. 500m参考DEM数组
            circle_mask_500=mask_500,                    # 3. 500m参考DEM的掩码（修正）
            xs_flat_500=meta_500["xs"].ravel(),          # 4. 500m参考DEM的x坐标（修正）
            ys_flat_500=meta_500["ys"].ravel(),          # 5. 500m参考DEM的y坐标（修正）
            cx3857_user=x_user,                          # 6. 用户中心x
            cy3857_user=y_user,                          # 7. 用户中心y
            cx3857_500=x_ref,                            # 8. 500m参考中心x
            cy3857_500=y_ref,                            # 9. 500m参考中心y
            aoi_radius_user=config.aoi_radius_m,         # 10. 用户半径
            house_area_raster=house_area_raster,
            pad_ring_raster=pad_ring_raster,
            bands_raster=bands_raster,
            pooled_raster=pooled_raster,
            center_elev=house_ground,                    # 对应center_elev
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
    
    # Figure 5 & 6: 3D - 使用DSM进行3D可视化
    # Figure 5 & 6: 3D - Top = DEM (DTM), Bottom = Satellite/DSM
    if config.save_3d:
        try:
            # 1) 取用户半径 与 500m 参考的 DTM/DSM 两套路径
            if config.dem_for_user_aoi and config.aoi_radius_m != config.reference_radius_m:
                user_key = f"dem_{int(config.aoi_radius_m)}m"
            else:
                user_key = "dem_500m"

            user_dtm_path = dem_results[user_key]["dtm_path"]   # ← DEM/DTM（上排要用这个）
            user_dsm_path = dem_results[user_key]["dsm_path"]   # ← DSM（下排 NAIP 贴这个；没有也可传 None）

            ref_dtm_path  = dem_results["dem_500m"]["dtm_path"] # ← DEM/DTM（上排右图）
            ref_dsm_path  = dem_results["dem_500m"]["dsm_path"] # ← DSM（下排右图）

            # 防御性打印（跑一次就能肉眼确认传参是否正确）
            if config.verbose:
                print("[3D] DEM user:", os.path.basename(user_dtm_path))
                print("[3D] DEM 500m:", os.path.basename(ref_dtm_path))
                print("[3D] DSM user:", os.path.basename(user_dsm_path))
                print("[3D] DSM 500m:", os.path.basename(ref_dsm_path))

            # 如果 OSM 不可用，兜底 footprint/rings（保持你原逻辑）
            if osm_poly is None and config.verbose:
                print("  ⚠ OSM未找到建筑，使用默认圆形 footprint 并保持环带差集")
                house_area_raster = center_pt.buffer(config.house_buffer_m, resolution=64)

            pad_ring_raster = house_area_raster.buffer(config.pad_outer_m, resolution=96)\
                            .difference(house_area_raster.buffer(config.pad_inner_m, resolution=96))

            bands_raster = [house_area_raster.buffer(rmax, resolution=96)
                            .difference(house_area_raster.buffer(rmin, resolution=96))
                            for (rmin, rmax) in config.ring_bands_m]

            pooled_raster = house_area_raster.buffer(config.pooled_outer_max, resolution=96)\
                            .difference(house_area_raster.buffer(config.pooled_outer_min, resolution=96))

            # 2) 关键：上排传 DTM 到 dem_path_*，下排传 DSM 到 dsm_path_*
            fig5_user, fig6_user, fig5_ref, fig6_ref = add_3d_figures_to_pipeline(
                dem_path_user=user_dtm_path,               # ✅ 上排用 DEM/DTM
                dem_path_500=ref_dtm_path,                 # ✅ 上排用 DEM/DTM
                dsm_path_user=user_dsm_path,               # ✅ 下排 Satellite/DSM（可为 None）
                dsm_path_500=ref_dsm_path,                 # ✅ 下排 Satellite/DSM（可为 None）
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
                print(f"  ✅ 3D组合图已生成：{combo_path}")
            if config.verbose:
                print("✓ figure5_6_3d_combo.html")

        except Exception as e:
            if config.verbose:
                print(f"✗ 3D visualizations: {e}")

    
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
                'Radius (m)': r,
                'Pixels': m['n_pixels'],
                'ΔElev_median (m)': round(m['delta_median'], 3),   # 恢复原列名
                '% Higher': round(m['pct_higher'], 1),
                '% Lower': round(m['pct_lower'], 1),
                'Slope_mean (°)': round(m['slope_mean'], 2),
                'Slope_median (°)': round(m['slope_median'], 2),
                'Slope_P25 (°)': round(m['slope_p25'], 2),
                'Slope_P75 (°)': round(m['slope_p75'], 2),
                '% Flat <2°': round(m['pct_flat'], 1),
                '% Gentle 2–5°': round(m['pct_gentle'], 1),
                '% Steep ≥5°': round(m['pct_steep'], 1),
                'Convergence (%)': round(m['convergence_%'], 1),
                'Dominant Aspect (°)': round(m['dominant_aspect'], 1) if np.isfinite(m['dominant_aspect']) else np.nan,
                'Dominant Aspect (cardinal)': cardinal_direction(m['dominant_aspect']),
            })
    
    summary_df = pd.DataFrame(ring_rows).reset_index(drop=True)
    summary_df.attrs["ring_mask_10m"] = ring_masks_all.get(10.0, None)
    if config.verbose:
        print(f"Computed metrics for {len(ring_rows)} radii")
    
    # ===== STEP 10: Area Statistics =====
    if config.verbose:
        print("\n" + "=" * 90)
        print("STEP 10: AREA STATISTICS")
        print("=" * 90)

    def _compute_area_stats(dem_arr: np.ndarray, mask: np.ndarray, house_ground: float, label: str) -> Tuple[pd.DataFrame, float]:
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
            'Metric': [
                'House Elevation',
                f'Lowest Elevation in {label}',
                f'Highest Elevation in {label}',
                f'Median Elevation in {label}',
                f'Elevation Percentile Rank in {label}',
            ],
            'Value': [
                f"{house_ground:.2f} m",
                f"{min_elev:.2f} m" if np.isfinite(min_elev) else "NaN",
                f"{max_elev:.2f} m" if np.isfinite(max_elev) else "NaN",
                f"{med_elev:.2f} m" if np.isfinite(med_elev) else "NaN",
                f"{pr:.2f}%" if np.isfinite(pr) else "NaN",
            ],
            'Interpretation': [
                'Reference elevation',
                f"{(house_ground - min_elev):.2f} m above lowest" if np.isfinite(min_elev) else "NaN",
                f"{(max_elev - house_ground):.2f} m below highest" if np.isfinite(max_elev) else "NaN",
                f"{'Above' if house_ground > med_elev else 'Below'} median" if np.isfinite(med_elev) else "NaN",
                "Above regional median" if np.isfinite(pr) and pr > 50 else ("Below regional median" if np.isfinite(pr) else "NaN"),
            ]
        })
        return df, float(pr) if np.isfinite(pr) else np.nan

    # 计算两份：500m 固定 + 用户 AOI
    label_user = f"{int(config.aoi_radius_m)}m" if float(config.aoi_radius_m).is_integer() else f"{config.aoi_radius_m}m"
    area_df_500m, pr_500m   = _compute_area_stats(dem_arr, mask_500,     house_ground, "500m")
    area_df_user, pr_user_aoi = _compute_area_stats(dem_arr, mask_user_aoi, house_ground, label_user)

    # 延续既有语义：
    # - percentile_rank（旧字段）= 用户 AOI
    # - 另外新增 percentile_rank_500m 方便区分
    pr = pr_user_aoi
    if config.verbose:
        print(f"House percentile (user AOI {label_user}): {pr_user_aoi:.1f}%")
        print(f"House percentile (500m): {pr_500m:.1f}%")

    
    # ===== STEP 11: Save Outputs =====
    if config.verbose:
        print("\n" + "=" * 90)
        print("STEP 11: SAVING OUTPUTS")
        print("=" * 90)
    
    try:
        # Determine formats
        formats_to_save = []
        if config.output_format == "parquet":
            formats_to_save = ["parquet"]
        elif config.output_format == "csv":
            formats_to_save = ["csv"]
        else:  # both
            formats_to_save = ["parquet", "csv"]
        
        # Filter available formats
        available_formats = []
        for fmt in formats_to_save:
            if fmt == "parquet":
                try:
                    import pyarrow
                    available_formats.append("parquet")
                    if config.verbose:
                        print(f"  Supported format: parquet (pyarrow available)")
                except ImportError:
                    if config.verbose:
                        print(f"  ❌ Parquet not supported (pyarrow missing), fallback to csv")
            else:
                available_formats.append("csv")
                if config.verbose:
                    print(f"  Supported format: csv")
        
        if not available_formats:
            available_formats = ["csv"]
            if config.verbose:
                print(f"  Default format: csv")
        
        # Save tables
        saved_tables = {}
        table_base_dir = os.path.basename(config.outdir)  # 子目录名（如26p409538_-81p784861_500m）
        
        # 保存summary_multiscale表格
        if config.verbose:
            print(f"  Saving summary_multiscale table...")
        summary_multiscale_paths = save_table(
            summary_df,
            os.path.join(config.outdir, "summary_multiscale"),
            available_formats
        )
        # 转换为相对路径（前端需要）
        for fmt, abs_path in summary_multiscale_paths.items():
            rel_path = os.path.join(table_base_dir, fmt)
            saved_tables[f"summary_multiscale.{fmt.split('.')[-1]}"] = rel_path
            if config.verbose:
                print(f"    ✅ {fmt}: {abs_path}")
        
        # 转换为相对路径（前端需要）
        if config.verbose:
            print(f"  Saving summary_area_level (two versions)...")
        area_500_base = os.path.join(config.outdir, "summary_area_level")
        area_500_paths = save_table(area_df_500m, area_500_base, available_formats)
        for fname, abs_path in area_500_paths.items():
            rel_path = os.path.join(table_base_dir, fname)   # e.g. summary_area_level.csv
            saved_tables[f"summary_area_level.{fname.split('.')[-1]}"] = rel_path
            if config.verbose:
                print(f"    ✅ {fname} (500m default): {abs_path}")
        
        area_user_base = os.path.join(config.outdir, "summary_area_level_user")
        area_user_paths = save_table(area_df_user, area_user_base, available_formats)
        for fname, abs_path in area_user_paths.items():
            rel_path = os.path.join(table_base_dir, fname)   # e.g. summary_area_level_user.csv
            saved_tables[f"summary_area_level_user.{fname.split('.')[-1]}"] = rel_path
            if config.verbose:
                print(f"    ✅ {fname} (user AOI {label_user}): {abs_path}")
        
        if config.verbose:
            print(f"  Saved {len(saved_tables)} tables total")
        
        # Save narrative
        narrative_abs_path = os.path.join(config.outdir, "narrative.txt")
        narrative_rel_path = os.path.join(table_base_dir, "narrative.txt")  # 相对路径
        if config.generate_narrative:
            if config.verbose:
                print(f"  Saving narrative...")

            try:
                narrative_text = generate_narrative(
                    summary_df=summary_df,
                    dem=dem_arr,
                    slope=slope_500,
                    aspect=aspect_500,
                    house_rc=house_rc,
                )
                with open(narrative_abs_path, "w", encoding="utf-8") as f:
                    f.write(narrative_text.strip() + "\n")
                if config.verbose:
                    print(f"    ✅ narrative.txt: {narrative_abs_path}")
            except Exception as e:
                if config.verbose:
                    print(f"    ⚠ narrative generation failed, writing empty placeholder: {e}")
                with open(narrative_abs_path, "w", encoding="utf-8") as f:
                    f.write("")  # 出错时写空，前端会显示 '-- EMPTY NARRATIVE --'
        else:
            narrative_rel_path = ""
            if config.verbose:
                print(f"  Skipping narrative (generate_narrative=False)")
        
        # ===== STEP 12: Generate Manifest (关键修复) =====
        if config.verbose:
            print(f"  Generating manifest.json...")
        
        # 1. 确定manifest保存路径：run_id根目录（outputs/{run_id}/manifest.json）
        run_root_dir = config.outdir 
        # 确保run_root_dir存在（容错）
        os.makedirs(run_root_dir, exist_ok=True)
        manifest_path = os.path.join(run_root_dir, "manifest.json")
        if config.verbose:
            print(f"    Manifest path: {manifest_path}")
        
        figs_rel_paths = {
            "figure1_elevation": os.path.join(table_base_dir, "figure1_elevation.png"),
            "figure2_slope": os.path.join(table_base_dir, "figure2_slope.png"),
            "figure3_aspect": os.path.join(table_base_dir, "figure3_aspect.png"),
            "figure4_terrain_and_hist": os.path.join(table_base_dir, "figure4_terrain_and_hist.png"),
            "figure5_6_3d_combo": os.path.join(table_base_dir, "figure5_6_3d_combo.html")
        }
        # 表格文件（从saved_tables中提取）
        table_rel_paths = list(saved_tables.values())
        # 叙事文件
        narrative_files = [narrative_rel_path] if config.generate_narrative and os.path.exists(narrative_abs_path) else []
        
        # 2.4 新增：收集DTM/DSM路径（核心修复）
        dem_files = []
        for dem_key, dem_info in dem_results.items():
    # 提取DTM文件名，构建相对路径（与其他文件保持一致的目录结构）
            dtm_filename = os.path.basename(dem_info["dtm_path"])
            dtm_rel_path = os.path.join(table_base_dir, dtm_filename)
            dem_files.append(dtm_rel_path)
    
    # 提取DSM文件名，构建相对路径
            dsm_filename = os.path.basename(dem_info["dsm_path"])
            dsm_rel_path = os.path.join(table_base_dir, dsm_filename)
            dem_files.append(dsm_rel_path)
        # 合并所有文件到files字段（去重+过滤空路径）
        all_files = list(set(list(figs_rel_paths.values()) + table_rel_paths + narrative_files + dem_files))
        all_files = [f for f in all_files if f.strip()]
        
        existing_files = []
        for rel in all_files:
            abs_path = os.path.join(config.output_dir, rel.split('/', 1)[-1]) if rel.startswith(os.path.basename(config.outdir)) else os.path.join(config.output_dir, rel)
            if os.path.exists(abs_path):
                existing_files.append(rel)
        all_files = existing_files
        
        # 3. 构建完整manifest（匹配前端预期字段）
        manifest = {
            "run_id": table_base_dir,  # 补充run_id字段
            "dataset": config.dataset_name,
            "lat": float(config.lat),
            "lon": float(config.lon),
            "aoi_radius_m": float(config.aoi_radius_m),
            "house_ground_m": float(house_ground),
            "percentile_rank": float(pr) if np.isfinite(pr) else None, 
            "percentile_rank_500m": float(pr_500m) if np.isfinite(pr_500m) else None,
            "address": config.address,
            "tables": saved_tables,  # 相对路径的表格
            "figs": figs_rel_paths,  # 修正key+相对路径的图片
            "files": all_files  # 补充前端需要的files数组
        }
        
        # 4. 保存manifest到run_id根目录（添加异常捕获）
        try:
            with open(manifest_path, "w") as f:
                json.dump(manifest, f, indent=2)
            if config.verbose:
                print(f"    ✅ manifest.json saved successfully")
                print(f"    Manifest contains {len(all_files)} output files")
        except Exception as manifest_err:
            print(f"    ❌ Failed to save manifest: {str(manifest_err)}")
            raise  # 抛出异常，让用户看到
        
        if config.verbose:
            print("\n" + "=" * 90)
            print("✅ PIPELINE COMPLETE")
            print("=" * 90)
            print(f"Run root dir: {run_root_dir}")
            print(f"Subdir with outputs: {config.outdir}")
            print(f"Manifest path: {manifest_path}")
        
    except Exception as e:
        # 捕获所有异常，打印详细信息
        print(f"\n    ❌ ERROR IN SAVING OUTPUTS: {str(e)}")
        print(f"    Traceback (simplified): {e.__traceback__.tb_frame.f_code.co_filename}:{e.__traceback__.tb_lineno}")
        raise  # 重新抛出，确保用户看到错误

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
    }


# ============================================
# ENTRY POINT
# ============================================

if __name__ == "__main__":
    # Create default config
    config = PipelineConfig()
    
    # Override with user input (example)
    config.lat = 41.8781
    config.lon = -87.6298
    config.aoi_radius_m = 500.0  # User-selectable!
    config.dem_for_user_aoi = True
    config.output_format = "both"
    config.generate_narrative = True
    config.save_3d = True
    
    # Run pipeline
    results = run_pipeline(config)
    
    # Print summary
    print(f"\n[SUMMARY]")
    print(f"House elevation: {results['house_ground_m']:.2f} m")
    print(f"Percentile rank: {results['percentile_rank']:.1f}%")
    print(f"Output dir: {results['outputs_dir']}")
