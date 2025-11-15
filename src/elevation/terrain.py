# -*- coding: utf-8 -*-
"""
Terrain analytics core - IMPROVED VERSION (parametrized)
- House-ground adaptive estimator
- Adaptive terrain classification
- Slope/Aspect/Curvature derivation
- Unified ring metrics & narrative
"""

from __future__ import annotations
from typing import Dict, Any, Tuple, Optional, List
import numpy as np
import rasterio
from rasterio import mask as rio_mask
import rasterio.errors
import pyproj
import scipy.ndimage as ndi
from shapely.geometry import Point, Polygon, MultiPolygon
from scipy.ndimage import convolve
import pandas as pd
# project helpers
from .helpers import _reproj_poly

__all__ = [
    "estimate_house_ground_adaptive",
    "classify_terrain_adaptive",
    "derive_slope_aspect_curvature",
    "compute_ring_metrics_unified",
    "generate_narrative",
]


# ---------------------------
# House-ground robust estimator
# ---------------------------

def estimate_house_ground_adaptive(
    src: rasterio.io.DatasetReader,
    house_poly,
    edge_poly,
    bands_polys,
    cx: float,
    cy: float,
    ndv: float,
    fallback_quantile: float = 0.40,
    hi_skew: float = 0.25,
    hi_tail_mult: float = 1.5,
    # ===== NEW: Parametrized thresholds =====
    min_size_ring_8_15: int = 30,
    min_size_edge: int = 10,
    min_size_ring_3_8: int = 20,
    min_size_ring_15_30: int = 50,
    fallback_annuli: List[Tuple[float, float]] = None,
    min_vals_annulus: int = 8,
    fallback_search_radius_px: int = 25,
    min_sample_size: int = 8,
    fallback_sample_size: int = 1,
    q_high_tail_threshold: float = 98.0,
    hard_q_high_tail: float = 60.0,
    soft_k_high_tail: float = 1.2,
    soft_k_normal: float = 1.8,
    q_estimate_min: float = 0.30,
    q_estimate_max: float = 0.45,
) -> Tuple[float, Dict[str, Any]]:
    """
    Robust estimator of house-ground elevation.
    完整复现最终脚本的行为：先优先 ring pools，再按高偏/长上尾修剪，
    若数据不足，按 3857 同心环回退，最后像素邻域回退。
    
    NOW FULLY PARAMETRIZED - all hardcoded thresholds are now function arguments!
    """
    
    # Set defaults if not provided
    if fallback_annuli is None:
        fallback_annuli = [(2.0, 25.0), (10.0, 35.0)]

    def _read_vals(poly):
        if (poly is None) or getattr(poly, "is_empty", True) or (not getattr(poly, "is_valid", False)):
            return np.array([], dtype=float)
        try:
            arr, _ = rio_mask.mask(src, [poly], crop=True, filled=True, nodata=ndv)
        except (rasterio.errors.WindowError, ValueError, IndexError):
            return np.array([], dtype=float)
        v = arr[0]
        v = v[(v != ndv) & np.isfinite(v)]
        return v.astype(float)

    def _annulus_vals(center_x, center_y, r_in, r_out):
        circ_out = Point(center_x, center_y).buffer(float(r_out), resolution=64)
        circ_in  = Point(center_x, center_y).buffer(float(r_in),  resolution=64)
        ring     = circ_out.difference(circ_in)
        try:
            ring_src = _reproj_poly(ring, "EPSG:3857", src.crs)
        except Exception:
            return np.array([], dtype=float)
        return _read_vals(ring_src)

    def _nearest_valid_from_center():
        x_src, y_src = pyproj.Transformer.from_crs("EPSG:3857", src.crs, always_xy=True).transform(cx, cy)
        r0, c0 = rasterio.transform.rowcol(src.transform, x_src, y_src)
        dem = src.read(1).astype(float)
        dem[dem == ndv] = np.nan
        H, W = dem.shape
        for rad in range(0, fallback_search_radius_px + 1):
            rmin, rmax = max(0, r0 - rad), min(H-1, r0 + rad)
            cmin, cmax = max(0, c0 - rad), min(W-1, c0 + rad)
            window = dem[rmin:rmax+1, cmin:cmax+1]
            if np.isfinite(window).any():
                rr, cc = np.where(np.isfinite(window))
                vals = window[rr, cc]
                return float(np.nanmedian(vals))
        return np.nan

    # ---- read ring pools ----
    edge_vals  = _read_vals(edge_poly)
    ring_3_8   = _read_vals(bands_polys[0]) if len(bands_polys) > 0 and bands_polys[0] else np.array([], dtype=float)
    ring_8_15  = _read_vals(bands_polys[1]) if len(bands_polys) > 1 and bands_polys[1] else np.array([], dtype=float)
    ring_15_30 = _read_vals(bands_polys[2]) if len(bands_polys) > 2 and bands_polys[2] else np.array([], dtype=float)

    pools = []
    if ring_8_15.size  >= min_size_ring_8_15: pools.append(ring_8_15)
    if edge_vals.size  >= min_size_edge: pools.append(edge_vals)
    if ring_3_8.size   >= min_size_ring_3_8: pools.append(ring_3_8)
    if ring_15_30.size >= min_size_ring_15_30: pools.append(ring_15_30)

    # 3857 ring
    if not pools:
        for rin, rout in fallback_annuli:
            vals = _annulus_vals(cx, cy, rin, rout)
            if vals.size >= min_vals_annulus:
                pools = [vals]
                break
        else:
            nv = _nearest_valid_from_center()
            if np.isfinite(nv):
                return float(nv), {"status":"nearest_valid_pixel", "n_base":1, "n_used":1, "method":"single_pixel"}
            return np.nan, {"status":"insufficient", "n":0}

    base_parts = [p for p in pools if isinstance(p, np.ndarray) and p.size > 0]
    if not base_parts:
        return np.nan, {"status":"insufficient", "n":0}

    base = np.concatenate(base_parts)
    base = base[np.isfinite(base)]
    if base.size < min_sample_size:
        fallback = np.concatenate([x for x in [edge_vals, ring_3_8, ring_8_15, ring_15_30] if x.size > 0])
        fallback = fallback[np.isfinite(fallback)]
        if fallback.size >= fallback_sample_size:
            return float(np.nanmedian(fallback)), {"status":"fallback_small", "n":int(fallback.size)}
        nv = _nearest_valid_from_center()
        if np.isfinite(nv):
            return float(nv), {"status":"nearest_valid_pixel", "n_base":1, "n_used":1, "method":"single_pixel"}
        return np.nan, {"status":"insufficient", "n":0}

    # IQR 
    v0 = base.copy()
    q1, q3 = np.percentile(v0, [25, 75]); iqr0 = max(q3 - q1, 1e-9)
    v0 = v0[(v0 >= q1 - 1.5 * iqr0) & (v0 <= q3 + 1.5 * iqr0)]
    if v0.size < min_sample_size:
        v0 = base

    def _robust_skew_proxy(v):
        q25, med, q75 = np.percentile(v, [25, 50, 75])
        iqr = max(q75 - q25, 1e-9)
        return ((q75 + q25) - 2.0 * med) / iqr, med, q25, q75, iqr

    def _trim_upper_tail(v, med, iqr, hard_q=None, soft_k=1.2):
        v = np.asarray(v, dtype=float)
        thr = np.percentile(v, hard_q) if hard_q is not None else (med + soft_k * iqr)
        return v[v <= thr]

    skew, med, q25, q75, iqr = _robust_skew_proxy(v0)
    q98 = np.percentile(v0, q_high_tail_threshold)
    high_tail = (skew > hi_skew) and ((q98 - med) > hi_tail_mult * iqr)

    if high_tail:
        v1 = _trim_upper_tail(v0, med, iqr, hard_q=hard_q_high_tail, soft_k=soft_k_high_tail)
        q = max(q_estimate_min, min(q_estimate_max, fallback_quantile))
        est = float(np.quantile(v1, q)) if v1.size >= min_sample_size else float(np.quantile(v0, q))
        method = f"adaptive_P{int(q*100)}_trim{int(hard_q_high_tail)}"
    else:
        v1 = _trim_upper_tail(v0, med, iqr, hard_q=None, soft_k=soft_k_normal)
        est = float(np.median(v1)) if v1.size >= min_sample_size else float(np.median(v0))
        method = f"median_IQRtrim_k{soft_k_normal}"

    info = {"status":"ok","n_base":int(base.size),"n_used":int(v1.size),
            "skew_proxy":float(skew),"iqr":float(iqr),"q25":float(q25),
            "q75":float(q75),"q98":float(q98),"method":method}
    return est, info


# ---------------------------
# Adaptive terrain classification
# ---------------------------

def classify_terrain_adaptive(
    dem: np.ndarray,
    slope_deg: np.ndarray,
    curv: np.ndarray,
    circle_mask: np.ndarray,
    base_flat: float = 1.7,
    base_gentle: float = 10.0,
    base_vsteep: float = 30.0,
    use_multiscale: bool = True,
    pix_scales: Tuple[int, ...] = (3, 7, 15),
    k_curv: float = 2.0,
    min_patch_pixels: int = 9,
    # ===== NEW: Parametrized slope thresholds =====
    slope_threshold_convex_concav: float = 15.0,
    slope_threshold_concav_steep: float = 5.0,
):
    """
    Adaptive terrain classification with PARAMETRIZED slope thresholds.
    """
    finite = np.isfinite(dem) & np.isfinite(slope_deg) & np.isfinite(curv)

    if np.any(finite):
        s = slope_deg[finite]
        q15, q60, q95 = np.nanpercentile(s, [15, 60, 95])
        t_flat   = max(base_flat,   float(q15))
        t_gentle = max(base_gentle, float(q60))
        t_vsteep = max(base_vsteep, float(q95))
    else:
        t_flat, t_gentle, t_vsteep = base_flat, base_gentle, base_vsteep

    if np.any(finite):
        c = curv[finite]
        med_c = float(np.nanmedian(c))
        mad_c = float(np.nanmedian(np.abs(c - med_c)))
        scale_curv = 1.4826 * mad_c if mad_c > 0 else float(np.nanpercentile(np.abs(c), 90))
        Tmin = float(np.nanpercentile(np.abs(c), 60))
        Tmax = float(np.nanpercentile(np.abs(c), 98))
        curv_T = float(np.clip(k_curv * scale_curv, Tmin, Tmax))
    else:
        curv_T = 1.0

    if use_multiscale:
        votes_convex = np.zeros_like(curv, dtype=np.int16)
        votes_concav = np.zeros_like(curv, dtype=np.int16)
        for w in pix_scales:
            curv_s = curv if w <= 1 else ndi.uniform_filter(curv, size=int(w), mode="nearest")
            votes_convex += (curv_s < -curv_T)
            votes_concav += (curv_s >  curv_T)
        is_convex = votes_convex > votes_concav
        is_concav = votes_concav > votes_convex
        is_plane  = ~(is_convex | is_concav)
    else:
        is_convex = curv < -curv_T
        is_concav = curv >  curv_T
        is_plane  = ~(is_convex | is_concav)

    zone = np.zeros_like(dem, dtype=np.uint8)
    z = zone; f = finite; s = slope_deg

    z[f & is_plane & (s < t_flat)] = 1
    z[f & is_plane & (s >= t_flat) & (s < t_gentle)] = 2
    z[f & is_plane & (s >= t_gentle) & (s < t_vsteep)] = 3

    # ===== NOW PARAMETRIZED =====
    z[f & (s < slope_threshold_convex_concav) & is_convex] = 4
    z[f & (s < slope_threshold_convex_concav) & is_concav] = 5
    z[f & (s >= slope_threshold_concav_steep) & is_concav] = 6
    z[f & (s >= t_vsteep)] = 7

    if np.any(z > 0):
        def mode_filter(arr):
            def _mode(b):
                b = b.ravel()
                vals, cnts = np.unique(b[b > 0], return_counts=True)
                return vals[np.argmax(cnts)] if cnts.size else 0
            return ndi.generic_filter(arr, _mode, size=3, mode="nearest")

        z2 = mode_filter(z)
        labeled, ncomp = ndi.label(z2 > 0)
        if ncomp > 0:
            sizes = ndi.sum(np.ones_like(z2, dtype=np.int32), labeled, index=np.arange(1, ncomp+1))
            small_ids = (np.arange(1, ncomp+1))[sizes < min_patch_pixels]
            small_mask = np.isin(labeled, small_ids)
            z3 = mode_filter(z2)
            z2[small_mask] = z3[small_mask]
        z = z2

    info = dict(t_flat=float(t_flat), t_gentle=float(t_gentle), t_vsteep=float(t_vsteep),
                curv_T=float(curv_T), scales=list(pix_scales),
                slope_threshold_convex_concav=float(slope_threshold_convex_concav),
                slope_threshold_concav_steep=float(slope_threshold_concav_steep))
    return z, info


# ---------------------------
# Slope / Aspect / Curvature
# ---------------------------

def derive_slope_aspect_curvature(
    dem: np.ndarray,
    res_x: float = 1.0,
    res_y: float = 1.0,
) -> Dict[str, np.ndarray]:
    """
    与最终脚本一致的公式（np.gradient + arctan/hypot）。
    允许传入像元分辨率；未传时按 1m 处理（上游请传真实分辨率）。
    """
    dem = np.asarray(dem, dtype=float)
    dem = np.where(np.isfinite(dem), dem, np.nan)

    dz_dy, dz_dx = np.gradient(dem, res_y, res_x)
    slope = np.degrees(np.arctan(np.hypot(dz_dx, dz_dy)))
    aspect = (90.0 - np.degrees(np.arctan2(dz_dy, -dz_dx))) % 360.0

    d2z_dy2, _ = np.gradient(dz_dy, res_y, res_x)
    _, d2z_dx2 = np.gradient(dz_dx, res_y, res_x)
    curvature = d2z_dx2 + d2z_dy2

    # flat 像元的坡向归零（与 500m 逻辑一致）
    flat = (slope == 0) | ~np.isfinite(slope)
    aspect = aspect.copy()
    aspect[flat] = 0.0

    return {"slope": slope, "aspect": aspect, "curvature": curvature}


# ---------------------------
# Unified ring metrics + narrative
# ---------------------------

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


def compute_ring_metrics_unified(
    dem: np.ndarray,
    slope: np.ndarray,
    aspect: np.ndarray,
    house_rc: Tuple[int, int],
    house_elev: float,
    ring_mask: np.ndarray,
    rows_grid: np.ndarray,
    cols_grid: np.ndarray,
    # ===== NEW: Parametrized slope thresholds =====
    slope_flat_threshold: float = 2.0,
    slope_gentle_threshold: float = 5.0,
) -> Optional[Dict[str, Any]]:
    """
    Compute ring metrics with PARAMETRIZED slope classification thresholds.
    """
    if ring_mask.sum() == 0:
        return None

    ring_elevs   = dem[ring_mask]
    ring_median  = float(np.nanmedian(ring_elevs))
    delta_median = float(house_elev - ring_median)
    pct_higher   = float((ring_elevs > house_elev).sum() / ring_elevs.size * 100.0)
    pct_lower    = float((ring_elevs < house_elev).sum() / ring_elevs.size * 100.0)

    ring_slopes   = slope[ring_mask]
    slope_mean    = float(np.nanmean(ring_slopes))
    slope_median  = float(np.nanmedian(ring_slopes))
    slope_p25     = float(np.nanpercentile(ring_slopes, 25))
    slope_p75     = float(np.nanpercentile(ring_slopes, 75))
    # ===== NOW PARAMETRIZED =====
    pct_flat      = float((ring_slopes < slope_flat_threshold).sum() / ring_slopes.size * 100.0)
    pct_gentle    = float(((ring_slopes >= slope_flat_threshold) & (ring_slopes < slope_gentle_threshold)).sum() / ring_slopes.size * 100.0)
    pct_steep     = float((ring_slopes >= slope_gentle_threshold).sum() / ring_slopes.size * 100.0)

    convergence_score = float(compute_flow_convergence(aspect, house_rc, ring_mask, rows_grid, cols_grid))

    ring_aspects = aspect[ring_mask]
    vals = ring_aspects[np.isfinite(ring_aspects)]
    if vals.size > 0:
        sin_mean = float(np.nanmean(np.sin(np.deg2rad(vals))))
        cos_mean = float(np.nanmean(np.cos(np.deg2rad(vals))))
        dominant_aspect = (np.degrees(np.arctan2(sin_mean, cos_mean)) + 360) % 360
    else:
        dominant_aspect = np.nan

    return {
        'n_pixels'        : int(ring_mask.sum()),
        'ring_median'     : ring_median,
        'delta_median'    : delta_median,
        'pct_higher'      : pct_higher,
        'pct_lower'       : pct_lower,
        'slope_mean'      : slope_mean,
        'slope_median'    : slope_median,
        'slope_p25'       : slope_p25,
        'slope_p75'       : slope_p75,
        'pct_flat'        : pct_flat,
        'pct_gentle'      : pct_gentle,
        'pct_steep'       : pct_steep,
        'convergence_%'   : convergence_score,
        'dominant_aspect' : float(dominant_aspect) if np.isfinite(dominant_aspect) else np.nan,
    }


def cardinal_direction(deg: float) -> str:
    """Convert bearing to cardinal direction"""
    if not np.isfinite(deg):
        return "Unknown"
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[int((deg + 22.5) / 45) % 8]


def _estimate_local_curvature(dem: np.ndarray, mask: np.ndarray) -> float:
    ker = np.array([[0,1,0],[1,-4,1],[0,1,0]], dtype=float)
    sub = np.where(mask, dem, np.nan)
    val = np.nan_to_num(sub, nan=0.0)
    wgt = np.where(np.isfinite(sub), 1.0, 0.0)
    num = convolve(val, ker, mode="nearest")
    den = convolve(wgt, np.abs(ker), mode="nearest")
    curv = np.where(den > 0, num / np.maximum(den, 1e-6), np.nan)
    return float(np.nanmedian(curv))


def generate_narrative(
    summary_df: pd.DataFrame,
    dem: np.ndarray,
    slope: np.ndarray,
    aspect: np.ndarray,
    house_rc: Tuple[int, int],
    *,
    # Optional context (use if available)
    global_risk: Optional[Dict[str, float]] = None,   # {target_lon, target_lat, target_risk, global_percentile, higher_pct}
    house_elev_m: Optional[float] = None,             # not printed (kept for compatibility)
    area_percentiles: Optional[Dict[str, float]] = None,
    area_labels: Optional[Dict[str, str]] = None,
) -> str:
    """
    Returns exactly TWO paragraphs:
      P1: Terrain description at 500m + local (10m) slope/aspect/curvature + multi-scale slope + 500m convergence.
      P2: Global composite risk sentence (score + percentile + intuitive 'less flood-prone than X%').
    """

    # ---------- helpers ----------
    def _get_row(r: float):
        hit = summary_df[summary_df["Radius (m)"] == r]
        return hit.iloc[0].to_dict() if len(hit) else None

    def _cardinal(deg: float) -> str:
        if not np.isfinite(deg):
            return "Unknown"
        dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"]
        idx = int((deg % 360) / 22.5 + 0.5) % 16
        return dirs[idx]

    def _median_in_mask(arr: np.ndarray, mask: np.ndarray) -> float:
        vals = arr[mask]
        return float(np.nanmedian(vals)) if np.isfinite(vals).any() else np.nan

    def _local_curvature_masked(dem_: np.ndarray, mask_: np.ndarray) -> float:
        """Small, safe curvature estimate over masked pixels (fallback if external util not present)."""
        if not mask_.any():
            return np.nan
        # Sobel-ish second derivatives (very light-weight)
        dzdy, dzdx = np.gradient(dem_)
        d2y, _ = np.gradient(dzdy)
        _, d2x = np.gradient(dzdx)
        curv = d2x + d2y
        vals = curv[mask_]
        return float(np.nanmedian(vals)) if np.isfinite(vals).any() else np.nan

    # ---------- pick scale rows ----------
    r50  = _get_row(50.0)
    r200 = _get_row(200.0)
    r500 = _get_row(500.0)

    # ---------- 10m mask from attrs (if provided) ----------
    mask10 = summary_df.attrs.get("ring_mask_10m", None)
    if mask10 is None:
        mask10 = np.zeros_like(dem, dtype=bool)
        r, c = house_rc
        if 0 <= r < dem.shape[0] and 0 <= c < dem.shape[1]:
            # tiny 3x3 around house as a very small fallback "local" window
            r0, r1 = max(0, r-1), min(dem.shape[0], r+2)
            c0, c1 = max(0, c-1), min(dem.shape[1], c+2)
            mask10[r0:r1, c0:c1] = True

    # ---------- local metrics (10m) ----------
    local_slope_med = _median_in_mask(slope, mask10)

    aspects10 = np.where(mask10, aspect, np.nan)
    valid10 = aspects10[np.isfinite(aspects10)]
    if valid10.size > 0:
        sin_m = float(np.nanmean(np.sin(np.deg2rad(valid10))))
        cos_m = float(np.nanmean(np.cos(np.deg2rad(valid10))))
        dom10 = (np.degrees(np.arctan2(sin_m, cos_m)) + 360) % 360
        dom10_card = _cardinal(dom10)
    else:
        dom10, dom10_card = np.nan, "Unknown"

    local_curv_med = _local_curvature_masked(dem, mask10)

    # ---------- Paragraph 1: terrain description ----------
    p1_parts = []

    # 500 m Δelev relative to area median (fallback to 200m if 500m missing)
    if r500 is not None and np.isfinite(r500.get("ΔElev_median (m)", np.nan)):
        p1_parts.append(f"At 500 m, the home sits {r500['ΔElev_median (m)']:+.3f} m relative to the area median elevation.")
    elif r200 is not None and np.isfinite(r200.get("ΔElev_median (m)", np.nan)):
        p1_parts.append(f"At 200 m, the home sits {r200['ΔElev_median (m)']:+.3f} m relative to the area median elevation.")

    # 10 m slope with qualitative label
    if np.isfinite(local_slope_med):
        label = ("flat-to-gentle" if local_slope_med < 3
                 else "a gentle incline" if local_slope_med < 6
                 else "a noticeable incline")
        p1_parts.append(f"Within 10 m, median slope is ~{local_slope_med:.1f}°, indicating {label}.")

    # local drainage direction from aspect
    if np.isfinite(dom10):
        p1_parts.append(f"Nearby ground tilts toward {dom10_card} (~{dom10:.0f}°), a likely drainage direction.")

    # curvature tone
    if np.isfinite(local_curv_med):
        if local_curv_med < -1e-4:
            p1_parts.append("Local curvature suggests a slightly concave surface (minor water collection tendency).")
        elif local_curv_med > 1e-4:
            p1_parts.append("Local curvature suggests a slightly convex surface (ridge-like).")
        else:
            p1_parts.append("Local curvature is near zero (essentially flat shape).")

    # multi-scale slope summary (only include scales that exist)
    scales = []
    if r50  is not None and np.isfinite(r50.get("Slope_median (°)", np.nan)):
        scales.append(f"50 m: {r50['Slope_median (°)']:.1f}°")
    if r200 is not None and np.isfinite(r200.get("Slope_median (°)", np.nan)):
        scales.append(f"200 m: {r200['Slope_median (°)']:.1f}°")
    if r500 is not None and np.isfinite(r500.get("Slope_median (°)", np.nan)):
        scales.append(f"500 m: {r500['Slope_median (°)']:.1f}°")
    if scales:
        p1_parts.append("Median slope by scale — " + "; ".join(scales) + ".")

    # 500 m convergence/down-slope toward home + dominant aspect
    if r500 is not None:
        conv = r500.get("Convergence (%)", np.nan)
        dom  = r500.get("Dominant Aspect (°)", np.nan)
        if np.isfinite(conv) and np.isfinite(dom):
            p1_parts.append(
                f"At 500 m, ~{conv:.0f}% of surrounding surfaces point downslope toward the home; "
                f"dominant downslope direction is {_cardinal(dom)} (~{dom:.0f}°)."
            )

    paragraph_1 = " ".join(p1_parts).strip()

    # ---------- Paragraph 2: global composite risk ----------
    paragraph_2 = ""
    if isinstance(global_risk, dict):
        try:
            tgt_r  = float(global_risk.get("target_risk", np.nan))
            pct    = float(global_risk.get("global_percentile", np.nan))
            higher = float(global_risk.get("higher_pct", np.nan))
            if np.isfinite(tgt_r) and np.isfinite(pct) and np.isfinite(higher):
                paragraph_2 = (
                    f"The terrain risk model estimates a terrain risk score of {tgt_r:.3f} for this location, "
                    f"which ranks at the {pct:.1f}th percentile across the entire area, "
                    f"meaning it is higher and less flood-prone than about {higher:.1f}% of the terrain in this DEM region."
                )
        except Exception:
            paragraph_2 = ""

    # Ensure we only return exactly two paragraphs (second may be empty if inputs missing)
    if paragraph_2:
        return f"{paragraph_1}\n\n{paragraph_2}"
    else:
        return paragraph_1
