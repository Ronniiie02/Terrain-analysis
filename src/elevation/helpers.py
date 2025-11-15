# -*- coding: utf-8 -*-
"""
Helper & Utility Functions for Elevation Analysis
--------------------------------------------------
Includes reprojection, statistical filters (IQR, mean),
mask handling, skew proxy estimation, and geometry validation.
All functions retain identical behavior to the original main pipeline.
"""

from __future__ import annotations
from typing import Dict, Any, Tuple, List, Optional
from pathlib import Path
import numpy as np
import pyproj
import pandas as pd
import rasterio
from rasterio.warp import transform as rio_transform
from shapely.geometry import Point, Polygon, MultiPolygon
from shapely.ops import unary_union, transform as shp_transform
from shapely.validation import make_valid
from shapely.geometry import base as shp_base
from shapely.geometry import Point
from shapely.prepared import prep as shp_prep
from shapely import ops as shp_ops


# ---------------------------
# CRS / reprojection helpers
# ---------------------------

def to_3857(x, y):
    """lon/lat -> EPSG:3857 (x,y)"""
    t = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform
    return t(x, y)

def shapely_transform(geom, src_epsg, dst_crs):
    """Transform shapely geom from src_epsg -> dst_crs"""
    t = pyproj.Transformer.from_crs(src_epsg, dst_crs, always_xy=True).transform
    if geom.geom_type == 'Point':
        x, y = t(*geom.coords[0])
        return Point(x, y)
    elif geom.geom_type in ('Polygon', 'MultiPolygon'):
        def _ring(coords): return [t(x, y) for (x, y) in coords]
        if geom.geom_type == 'Polygon':
            ext = _ring(geom.exterior.coords)
            holes = [_ring(r.coords) for r in geom.interiors]
            return Polygon(ext, holes)
        else:
            parts = []
            for p in geom.geoms:
                ext = _ring(p.exterior.coords)
                holes = [_ring(r.coords) for r in p.interiors]
                parts.append(Polygon(ext, holes))
            return unary_union(parts)
    else:
        raise NotImplementedError(f"Unsupported geometry: {geom.geom_type}")

def _reproj_poly(poly, src_crs, dst_crs):
    """Robust polygon reprojection with make_valid and multipolygon union."""
    t = pyproj.Transformer.from_crs(src_crs, dst_crs, always_xy=True).transform

    def _reproj_single(p):
        ext = [t(*xy) for xy in p.exterior.coords]
        holes = [[t(*xy) for xy in r.coords] for r in p.interiors]
        return Polygon(ext, holes)

    poly = make_valid(poly)
    if isinstance(poly, Polygon):
        out = _reproj_single(poly); return make_valid(out)
    elif isinstance(poly, MultiPolygon):
        parts = [_reproj_single(p) for p in poly.geoms]
        out = unary_union(parts); return make_valid(out)
    else:
        raise NotImplementedError(f"Unsupported geometry type: {poly.geom_type}")

def _geom_ok(g):
    """Safe geometry sanity check."""
    try:
        return (g is not None) and (not g.is_empty) and g.is_valid and (g.area > 0.0)
    except Exception:
        return False

# ---------------------------
# Raster helpers
# ---------------------------

def lonlat_to_rowcol(src, lon, lat):
    """lon/lat -> (row, col) in raster (src.crs)."""
    x, y = rio_transform("EPSG:4326", src.crs, [lon], [lat])
    return src.index(x[0], y[0])

def pixel_area(src):
    """Pixel area (abs of res_x * res_y)."""
    rx, ry = src.res
    return abs(rx * ry)

# ---------------------------
# Stats / filters
# ---------------------------

def iqr_filter(a):
    """Return values within 1.5*IQR whiskers (drop NaNs)."""
    v = np.asarray(a, dtype=float)
    v = v[~np.isnan(v)]
    if v.size == 0: return v
    q1, q3 = np.percentile(v, [25, 75])
    iqr = q3 - q1
    lo, hi = q1 - 1.5*iqr, q3 + 1.5*iqr
    keep = (v >= lo) & (v <= hi)
    return v[keep] if keep.any() else v

def iqr_clip_arr(arr, mask=None, whisker=1.5):
    """Clip array values outside whisker*IQR (within mask if provided)."""
    a = np.array(arr, dtype=float, copy=True)
    sel = np.isfinite(a)
    if mask is not None: sel &= (mask.astype(bool))
    vals = a[sel]
    if vals.size == 0: return a
    q1, q3 = np.percentile(vals, [25, 75])
    iqr = q3 - q1
    lo, hi = q1 - whisker * iqr, q3 + whisker * iqr
    a[sel & (a < lo)] = lo
    a[sel & (a > hi)] = hi
    return a

from scipy.ndimage import convolve as _convolve

def nan_mean_filter(arr, size=5, mode='reflect', valid_mask=None):
    """Mean filter that ignores NaN (and optionally requires valid_mask)."""
    arr = np.asarray(arr, dtype=float)
    if valid_mask is None:
        valid_mask = np.isfinite(arr)
    else:
        valid_mask = valid_mask.astype(bool) & np.isfinite(arr)
    k = np.ones((size, size), dtype=float)
    sum_vals = _convolve(np.where(valid_mask, arr, 0.0), k, mode=mode)
    cnt_vals = _convolve(valid_mask.astype(float), k, mode=mode)
    out = np.divide(sum_vals, cnt_vals, out=np.full_like(sum_vals, np.nan, dtype=float), where=(cnt_vals > 0))
    return out

# ---------------------------
# Small utilities (optional)
# ---------------------------

def _is_number(x):
    try: float(x); return True
    except: return False

def _parse_latlon_string(s):
    if not isinstance(s, str): return None
    parts = [p.strip() for p in s.split(",")]
    if len(parts) != 2: return None
    if _is_number(parts[0]) and _is_number(parts[1]):
        return float(parts[0]), float(parts[1])
    return None

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

_RISK_BUILDING_POLY = None

def set_risk_building_poly(poly) -> None:
    """
    Set building polygon (in same CRS as meta_500['crs']) for terrain risk computation.
    If None, compute_global_composite_risk_map will fall back to single-point mode.
    """
    global _RISK_BUILDING_POLY
    _RISK_BUILDING_POLY = poly

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
    from .terrain import compute_flow_convergence

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
    from pathlib import Path
    
    results: Dict[str, str] = {}

    for fmt in formats:
        if fmt == "parquet":
            p = base_path + ".parquet"
            try:
                import pyarrow as pa, pyarrow.parquet as pq, pyarrow.fs as pafs
                table = pa.Table.from_pandas(df)
                fs = pafs.LocalFileSystem()
                pq.write_table(table, p, filesystem=fs)
                results[Path(p).name] = p  
            except Exception:
                p_csv = base_path + ".csv"
                df.to_csv(p_csv, index=False)
                results[Path(p_csv).name] = p_csv
        elif fmt == "csv":
            p = base_path + ".csv"
            df.to_csv(p, index=False)
            results[Path(p).name] = p    

    return results
    
   
def compute_global_composite_risk_map(
    dem_arr_500: np.ndarray,
    slope_500: np.ndarray,
    aspect_500: np.ndarray,
    meta_500: Dict[str, Any],
    lat: float,
    lon: float,
    gamma: float,
    res_window_m: float,  
    weights: Dict[str, float],
    out_png_path: str,
    house_ground: float,
    area_median_elev: float,
) -> Dict[str, Any]:
    """
    Building-level terrain composite risk over 500m DEM (new version)

    Core design:
    - Primarily based on house_ground: The goal is to make risk strongly positively correlated with "house_ground relative to the median in the 500m area"
    - global_score = (1 - rank(house_ground in 500m elevs)) ** gamma
      * The lower the house_ground (smaller quantile), the higher the global_score → high risk
    - delta_risk = normalize( -(house_ground - area_median_elev) )
      * The lower house_ground is below area median, the closer delta_risk is to 1
      * Normalization interval comes from the distribution of -(elev - area_median_elev) across all pixels
    - slope_risk = 1 - rank(aggregated_slope), aggregated_slope comes from robust aggregation of ring pools
      * Rings (3–8, 8–15, 15–30m + edge ring) reuse the pool idea from estimate_house_ground_adaptive
      * Take upper tail quantile ("upper mode") to amplify the contribution of gentle/low slopes to risk
    - aspect_sin_risk / aspect_cos_risk generated from aggregated aspect values in rings, normalized using min/max of sin/cos across the whole image
    - building_raw weighted using original weights, normalized by min/max of flood_risk_raw across the whole image → target_risk
    - global_percentile = P(all_risk_norm <= target_risk) (larger means more dangerous)

    Fallback logic:
    - If ring pools cannot obtain enough samples, slope/aspect fallback to median within footprint;
    - If footprint itself cannot get pixels, fallback to "nearest pixel risk" point mode.
    """

    import matplotlib.pyplot as plt
    import pyproj
    import rasterio
    from rasterio import features as rio_features
    from shapely.geometry import Point, mapping
    from shapely.prepared import prep as shp_prep
    from shapely import ops as shp_ops

    # =========================================================
    # Basic flattening: Flatten DEM / slope / aspect + coordinates
    # =========================================================
    dem = dem_arr_500.astype(float)
    slope = slope_500.astype(float)
    aspect = aspect_500.astype(float)

    xs = meta_500["xs"]  # 2D meshgrid of x
    ys = meta_500["ys"]  # 2D meshgrid of y
    crs = meta_500.get("crs")
    transform = meta_500.get("transform")
    ndv = meta_500.get("ndv", meta_500.get("nodata", np.nan))

    # Longitude and latitude
    to_ll = pyproj.Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
    lons, lats = to_ll(xs, ys)

    xs_flat = xs.ravel()
    ys_flat = ys.ravel()

    flat = dict(
        lon=lons.ravel(),
        lat=lats.ravel(),
        x=xs_flat,
        y=ys_flat,
        elev=dem.ravel(),
        slope=slope.ravel(),
        aspect=aspect.ravel(),
    )
    df = pd.DataFrame(flat).replace([np.inf, -np.inf], np.nan).dropna()

    # Use only valid pixels
    elev_all = df["elev"].values
    slope_all = df["slope"].values
    aspect_all = df["aspect"].values

    # =========================================================
    # 1. Pixel-level risk components (new definition: centered on area median)
    # =========================================================

    # --- global_score: Global "relative height" ---
    # elev_pct: Quantile of the pixel in the 500m elev distribution
    df["elev_pct"] = df["elev"].rank(pct=True)
    df["global_score"] = (1.0 - df["elev_pct"]) ** float(gamma)

    # --- delta_risk: "Low-lying degree" relative to area_median_elev ---
    # For each pixel: delta_raw = -(elev - area_median_elev), lower means larger
    delta_raw_all = -(df["elev"].values - float(area_median_elev))
    if np.isfinite(delta_raw_all).any():
        p1, p99 = np.percentile(delta_raw_all[np.isfinite(delta_raw_all)], [1, 99])
    else:
        p1, p99 = 0.0, 1.0
    delta_raw_clip = np.clip(delta_raw_all, p1, p99)
    dmin, dmax = float(np.nanmin(delta_raw_clip)), float(np.nanmax(delta_raw_clip))
    df["delta_risk"] = (delta_raw_clip - dmin) / (dmax - dmin + 1e-12)

    # --- slope_risk: Flatter is more dangerous (small slope = large risk) ---
    df["slope_risk"] = 1.0 - df["slope"].rank(pct=True)

    # --- aspect risk: Use |sin| / |cos| (direction-related) ---
    asp_rad_all = np.deg2rad(df["aspect"].values)
    sin_abs_all = np.abs(np.sin(asp_rad_all))
    cos_abs_all = np.abs(np.cos(asp_rad_all))
    sin_lo, sin_hi = float(sin_abs_all.min()), float(sin_abs_all.max())
    cos_lo, cos_hi = float(cos_abs_all.min()), float(cos_abs_all.max())

    df["aspect_sin_risk"] = (sin_abs_all - sin_lo) / (sin_hi - sin_lo + 1e-12)
    df["aspect_cos_risk"] = (cos_abs_all - cos_lo) / (cos_hi - cos_lo + 1e-12)

    # =========================================================
    # 2. Full image raw / norm (later building risk uses this min/max for normalization)
    # =========================================================
    w = weights
    df["flood_risk_raw"] = (
        w["global_"] * df["global_score"]
        + w["delta"] * df["delta_risk"]
        + w["slope"] * df["slope_risk"]
        + w["asin"] * df["aspect_sin_risk"]
        + w["acos"] * df["aspect_cos_risk"]
    )
    mn, mx = float(df["flood_risk_raw"].min()), float(df["flood_risk_raw"].max())
    df["flood_risk_norm"] = (df["flood_risk_raw"] - mn) / (mx - mn + 1e-12)
    all_risk_norm = df["flood_risk_norm"].values

    # =========================================================
    # 3. One-dimensional robust statistics tool (replicating estimate_house_ground_adaptive style)
    # =========================================================
    MIN_SAMPLE = 8
    FALLBACK_QUANTILE = 0.40
    HI_SKEW = 0.25
    HI_TAIL_MULT = 1.5
    Q_HIGH_TAIL_THRESH = 98.0
    HARD_Q_HIGH_TAIL = 60.0
    SOFT_K_HIGH_TAIL = 1.2
    SOFT_K_NORMAL = 1.8
    Q_ESTIMATE_MIN = 0.30
    Q_ESTIMATE_MAX = 0.45

    def _robust_1d(vals: np.ndarray, mode: str = "median") -> float:
        """
        Perform robust aggregation on a one-dimensional array:
        - First use IQR to remove extremes, then determine if there is a long tail based on skew + upper tail
        - mode="upper": Use upper tail quantile P70+, emphasizing "if there is a high-risk tail, the overall is biased high"
        - mode="median": Use median (for directional variables)
        """
        v = np.asarray(vals, dtype=float)
        v = v[np.isfinite(v)]
        if v.size == 0:
            return np.nan

        # Initial IQR clipping
        q1, q3 = np.percentile(v, [25, 75])
        iqr0 = max(q3 - q1, 1e-9)
        v0 = v[(v >= q1 - 1.5 * iqr0) & (v <= q3 + 1.5 * iqr0)]
        if v0.size < MIN_SAMPLE:
            v0 = v

        q25, med, q75 = np.percentile(v0, [25, 50, 75])
        iqr = max(q75 - q25, 1e-9)
        skew = ((q75 + q25) - 2.0 * med) / iqr
        q98 = np.percentile(v0, Q_HIGH_TAIL_THRESH)
        high_tail = (skew > HI_SKEW) and ((q98 - med) > HI_TAIL_MULT * iqr)

        def _trim_upper_tail(v_in, med_in, iqr_in, hard_q=None, soft_k=1.2):
            v_in = np.asarray(v_in, dtype=float)
            if hard_q is not None:
                thr = np.percentile(v_in, hard_q)
            else:
                thr = med_in + soft_k * iqr_in
            return v_in[v_in <= thr]

        if mode == "upper":
            # High-risk variables: Use upper tail quantile (at least P70)
            v1 = _trim_upper_tail(v0, med, iqr, hard_q=HARD_Q_HIGH_TAIL, soft_k=SOFT_K_HIGH_TAIL)
            if v1.size < MIN_SAMPLE:
                v1 = v0
            q = max(Q_ESTIMATE_MIN, min(Q_ESTIMATE_MAX, FALLBACK_QUANTILE))
            q_eff = max(q, 0.70)
            return float(np.quantile(v1, q_eff))

        # Default median path
        if high_tail:
            v1 = _trim_upper_tail(v0, med, iqr, hard_q=HARD_Q_HIGH_TAIL, soft_k=SOFT_K_HIGH_TAIL)
            q = max(Q_ESTIMATE_MIN, min(Q_ESTIMATE_MAX, FALLBACK_QUANTILE))
            if v1.size >= MIN_SAMPLE:
                est = float(np.quantile(v1, q))
            else:
                est = float(np.quantile(v0, q))
        else:
            v1 = _trim_upper_tail(v0, med, iqr, hard_q=None, soft_k=SOFT_K_NORMAL)
            if v1.size >= MIN_SAMPLE:
                est = float(np.median(v1))
            else:
                est = float(np.median(v0))

        return est

    # =========================================================
    # 4. Ring sampling logic (reusing ring idea from house_ground)
    # =========================================================

    def _vals_in_poly(arr: np.ndarray, poly) -> np.ndarray:
        """Sample array pixel values within the given polygon, excluding nodata / non-finite values."""
        if poly is None or getattr(poly, "is_empty", True):
            return np.array([], dtype=float)
        if transform is None:
            return np.array([], dtype=float)
        try:
            mask = rio_features.geometry_mask(
                [mapping(poly)],
                transform=transform,
                invert=True,
                out_shape=arr.shape,
            )
        except Exception:
            return np.array([], dtype=float)
        vals = arr[mask]
        vals = vals[np.isfinite(vals)]
        if np.isfinite(ndv):
            vals = vals[vals != ndv]
        return vals.astype(float)

    def _build_ring_polys_for_pools(building_poly):
        """
        Build ring pools according to the idea from estimate_house_ground_adaptive:
        - edge ring: [0.8, 2.5] m
        - bands: [3, 8], [8, 15], [15, 30] m
        - If not enough samples, fallback annuli: (2,25), (10,35) m
        Returns: List of polygons for pooling
        """
        # Parameters aligned with estimation function
        pad_inner_m = 0.8
        pad_outer_m = 2.5
        ring_specs = [(3.0, 8.0), (8.0, 15.0), (15.0, 30.0)]

        min_size_ring_8_15 = 30
        min_size_edge = 10
        min_size_ring_3_8 = 20
        min_size_ring_15_30 = 50

        fallback_annuli = [(2.0, 25.0), (10.0, 35.0)]
        min_vals_annulus = 8

        # 1) Build edge + band polygons
        edge_poly = building_poly.buffer(pad_outer_m, resolution=96).difference(
            building_poly.buffer(pad_inner_m, resolution=96)
        )
        bands_polys = [
            building_poly.buffer(rmax, resolution=96).difference(
                building_poly.buffer(rmin, resolution=96)
            )
            for (rmin, rmax) in ring_specs
        ]

        # 2) Use DEM sample count to decide which rings enter pools
        edge_vals_elev = _vals_in_poly(dem, edge_poly)
        ring_vals_elev = [ _vals_in_poly(dem, p) for p in bands_polys ]

        ring_3_8_elev, ring_8_15_elev, ring_15_30_elev = ring_vals_elev

        pools_polys = []
        if ring_8_15_elev.size >= min_size_ring_8_15:
            pools_polys.append(bands_polys[1])
        if edge_vals_elev.size >= min_size_edge:
            pools_polys.append(edge_poly)
        if ring_3_8_elev.size >= min_size_ring_3_8:
            pools_polys.append(bands_polys[0])
        if ring_15_30_elev.size >= min_size_ring_15_30:
            pools_polys.append(bands_polys[2])

        # 3) If main rings not enough samples, fallback to annulus
        if not pools_polys:
            centroid = building_poly.centroid
            cx, cy = float(centroid.x), float(centroid.y)

            for rin, rout in fallback_annuli:
                circ_out = Point(cx, cy).buffer(float(rout), resolution=64)
                circ_in = Point(cx, cy).buffer(float(rin), resolution=64)
                ring_poly = circ_out.difference(circ_in)
                vals = _vals_in_poly(dem, ring_poly)
                if vals.size >= min_vals_annulus:
                    pools_polys = [ring_poly]
                    break

        return pools_polys

    def _collect_vals_from_pools(arr: np.ndarray, pools_polys) -> np.ndarray:
        if not pools_polys:
            return np.array([], dtype=float)
        parts = [ _vals_in_poly(arr, poly) for poly in pools_polys ]
        parts = [p for p in parts if p.size > 0]
        if not parts:
            return np.array([], dtype=float)
        v = np.concatenate(parts)
        return v[np.isfinite(v)].astype(float)

    # =========================================================
    # 5. Building footprint mode: Multi-variable ring aggregation + house_ground driven risk
    # =========================================================
    global _RISK_BUILDING_POLY
    building_poly = _RISK_BUILDING_POLY

    target_lon = float(lon)
    target_lat = float(lat)
    target_risk = float("nan")
    global_percentile = float("nan")
    higher_pct = float("nan")
    used_building_mode = False

    if building_poly is not None:
        poly_prep = shp_prep(building_poly)
        x_arr = df["x"].values
        y_arr = df["y"].values

        inside = np.fromiter(
            (poly_prep.contains(Point(x, y)) for x, y in zip(x_arr, y_arr)),
            dtype=bool,
            count=len(x_arr),
        )

        if inside.any():
            used_building_mode = True
            building_df = df.loc[inside].copy()

            # 5.1 Build ring pools (for robust aggregation of slope / aspect / elev)
            ring_pools_polys = _build_ring_polys_for_pools(building_poly)

            # elev ring samples (for debug / risk intuition only, final elev aggregation forces house_ground)
            elev_ring_vals = _collect_vals_from_pools(dem, ring_pools_polys)

            # slope samples
            slope_ring_vals = _collect_vals_from_pools(slope, ring_pools_polys)
            if slope_ring_vals.size >= MIN_SAMPLE:
                slope_est = _robust_1d(slope_ring_vals, mode="upper")
            else:
                # Fallback to footprint median
                slope_est = float(np.nanmedian(building_df["slope"].values))

            # aspect samples
            aspect_ring_vals = _collect_vals_from_pools(aspect, ring_pools_polys)
            if aspect_ring_vals.size >= MIN_SAMPLE:
                aspect_est = _robust_1d(aspect_ring_vals, mode="median")
            else:
                aspect_est = float(np.nanmedian(building_df["aspect"].values))

            # 5.2 house_ground drives global / delta
            # global_score_house: Quantile of house_ground in 500m elev distribution
            elevs_all = elev_all
            elev_pct_house = (elevs_all < float(house_ground)).sum() / max(len(elevs_all), 1)
            global_score_house = (1.0 - elev_pct_house) ** float(gamma)

            # delta_risk_house: Using p1/p99 of -(elev - area_median_elev) across the full image for normalization interval
            delta_raw_house = -(float(house_ground) - float(area_median_elev))
            delta_raw_house_clip = np.clip(delta_raw_house, dmin, dmax)
            delta_risk_house = (delta_raw_house_clip - dmin) / (dmax - dmin + 1e-12)

            # 5.3 slope_risk_house: Using quantile of aggregated_slope in full image
            slope_rank_house = (slope_all < float(slope_est)).sum() / max(len(slope_all), 1)
            slope_risk_house = 1.0 - slope_rank_house

            # 5.4 aspect_risk_house: Using sin/cos of aggregated_aspect + min/max normalization across full image
            asp_rad_house = np.deg2rad(float(aspect_est))
            sin_abs_house = abs(np.sin(asp_rad_house))
            cos_abs_house = abs(np.cos(asp_rad_house))

            aspect_sin_risk_house = (sin_abs_house - sin_lo) / (sin_hi - sin_lo + 1e-12)
            aspect_cos_risk_house = (cos_abs_house - cos_lo) / (cos_hi - cos_lo + 1e-12)

            # 5.5 Building-level raw risk + normalization
            building_raw = (
                w["global_"] * global_score_house
                + w["delta"] * delta_risk_house
                + w["slope"] * slope_risk_house
                + w["asin"] * aspect_sin_risk_house
                + w["acos"] * aspect_cos_risk_house
            )

            building_norm = (building_raw - mn) / (mx - mn + 1e-12)
            building_norm = float(np.clip(building_norm, 0.0, 1.0))
            target_risk = building_norm

            # Global percentile (larger means more dangerous)
            global_percentile = float((all_risk_norm <= building_norm).sum() / len(all_risk_norm) * 100.0)
            higher_pct = max(0.0, min(100.0, 100.0 - global_percentile))

            # Geometric center of building footprint (converted back to WGS84 longitude / latitude)
            poly_ll = shp_ops.transform(to_ll, building_poly)
            centroid_ll = poly_ll.centroid
            target_lon = float(centroid_ll.x)
            target_lat = float(centroid_ll.y)

            # 5.6 Visualization: Show risk map + footprint + center point
            plt.figure(figsize=(7, 6))
            sc = plt.scatter(df["lon"], df["lat"], c=df["flood_risk_norm"], s=2, cmap="inferno")

            bx, by = poly_ll.exterior.xy
            plt.plot(bx, by, linewidth=2.0, color="cyan", label="Building footprint")

            plt.scatter(
                target_lon,
                target_lat,
                c="lime",
                s=80,
                marker="X",
                edgecolor="yellow",
                label="Building center",
            )

            plt.colorbar(sc, label="Terrain Risk (0–1)")
            plt.title("Spatial Distribution of Terrain Risk")
            plt.xlabel("Longitude")
            plt.ylabel("Latitude")
            plt.axis("equal")
            plt.legend()
            plt.tight_layout()
            try:
                plt.savefig(out_png_path, dpi=200)
            finally:
                plt.close()

    # =========================================================
    # 6. Fallback: No footprint / no inside pixels → nearest pixel risk
    # =========================================================
    if not used_building_mode:
        def haversine(lat1, lon1, lat2, lon2):
            R = 6371000.0
            lat1, lat2 = np.radians(lat1), np.radians(lat2)
            dlat = lat2 - lat1
            dlon = np.radians(lon2 - lon1)
            a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
            return 2 * R * np.arcsin(np.sqrt(a))

        df["dist_m"] = haversine(lat, lon, df["lat"], df["lon"])
        idx_target = df["dist_m"].idxmin()
        target_lon = float(df.at[idx_target, "lon"])
        target_lat = float(df.at[idx_target, "lat"])
        target_risk = float(df.at[idx_target, "flood_risk_norm"])

        risk_rank = df["flood_risk_norm"].rank(pct=True)
        global_percentile = float(risk_rank.loc[idx_target] * 100.0)
        higher_pct = max(0.0, min(100.0, 100.0 - global_percentile))

        plt.figure(figsize=(7, 6))
        sc = plt.scatter(df["lon"], df["lat"], c=df["flood_risk_norm"], s=2, cmap="inferno")
        plt.scatter(
            target_lon,
            target_lat,
            c="lime",
            s=80,
            marker="X",
            edgecolor="yellow",
            label="Nearest pixel",
        )
        plt.colorbar(sc, label="Terrain Risk (0–1)")
        plt.title("Spatial Distribution of Terrain Risk")
        plt.xlabel("Longitude")
        plt.ylabel("Latitude")
        plt.axis("equal")
        plt.legend()
        plt.tight_layout()
        try:
            plt.savefig(out_png_path, dpi=200)
        finally:
            plt.close()

    # =========================================================
    # 7. Return values: Keep fields completely consistent with the old version
    # =========================================================
    return dict(
        target_lon=target_lon,
        target_lat=target_lat,
        target_risk=target_risk,
        global_percentile=global_percentile,
        higher_pct=higher_pct,
        out_png_path=out_png_path,
    )