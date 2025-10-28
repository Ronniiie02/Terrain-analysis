# -*- coding: utf-8 -*-
"""
Helper & Utility Functions for Elevation Analysis
--------------------------------------------------
Includes reprojection, statistical filters (IQR, mean),
mask handling, skew proxy estimation, and geometry validation.
All functions retain identical behavior to the original main pipeline.
"""

from __future__ import annotations
import numpy as np
import pyproj
import rasterio
from rasterio.warp import transform as rio_transform
from shapely.geometry import Point, Polygon, MultiPolygon
from shapely.ops import unary_union, transform as shp_transform
from shapely.validation import make_valid

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
