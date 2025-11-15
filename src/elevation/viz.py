# -*- coding: utf-8 -*-
"""
INTEGRATED VISUALIZATION MODULE - Complete VIZ Pipeline
- 2D visualizations (Elevation/Slope/Aspect/Terrain) + 3D Elevation
"""

from __future__ import annotations
from typing import Dict, Any, Tuple, Optional, List
import numpy as np
import os
import rioxarray as rio
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.colors import ListedColormap, TwoSlopeNorm
import rasterio
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from shapely.geometry import Point, Polygon, MultiPolygon, LinearRing
import plotly.io as pio
import pyproj
import math
import warnings
from shapely.geometry import LinearRing
import shapely

warnings.filterwarnings("ignore", category=UserWarning, module="rasterio")
pio.renderers.default = "browser"

DEFAULT_CONFIG = {
    # ===== Figure 1: Elevation Map =====
    "fig1_figsize": (14, 6),
    "fig1_cmap": "RdBu_r",
    "fig1_percentile_span": (2, 98),
    "fig1_min_span": 0.15,
    "fig1_colors": {
        "footprint": "#1f1f1f",
        "edge": "#e41a1c",
        "bands": ["#ff7f00", "#f781bf", "#a65628"],
        "pooled": "#6a3d9a",
    },
    "fig1_linewidths": {
        "footprint": 2.8,
        "edge": 2.2,
        "bands": 1.8,
        "pooled": 2.2,
    },
    
    # ===== Figure 2: Slope Map =====
    "fig2_figsize": (14, 6),
    "fig2_cmap": "magma",
    "fig2_vmin": 0.0,
    "fig2_vmax_percentile": 98,
    "fig2_vmax_min": 15.0,
    
    # ===== Figure 3: Aspect Map =====
    "fig3_figsize": (14, 6),
    "fig3_cmap": "hsv",
    "fig3_flat_threshold": 1.5,
    
    # ===== Figure 4: Terrain + Histogram =====
    "fig4_figsize": (14, 6),
    "fig4_cmap_labels": {0: "NoData", 1: "Low-Lying", 2: "Moderate", 3: "Steep/Ridge"},
    "fig4_histogram_target_radii": [50.0, 200.0, 500.0],
    "fig4_histogram_colors": ["#4C72B0", "#55A868", "#C44E52"],
    "fig4_histogram_percentiles": (1, 99),
    "fig4_histogram_nbins": 60,
    
    # ===== Figure 5 & 6: 3D Visualization =====
    "fig5_6_speed_mode": True,
    "fig5_6_quantiles": (2, 98),      
    "fig5_6_colorscale_seq": "RdBu_r",
    "fig5_6_max_rings_to_draw": 3,
    "fig5_6_dem_max_size_user": 360,
    "fig5_6_dem_max_size_ref": 420,
    "fig5_6_densify_px": 2.0,
    "fig5_6_simplify_k": 1.5,
    "fig5_6_elevation_colorscale": "RdBu_r",
    "fig5_6_poly_colors": {
        "footprint": "#FFD700",
        "edge": "#00E5FF", 
        "bands": ["#FF5733", "#33FF77", "#3388FF"],
        "pooled": "#FF00FF",
    },
    "fig5_6_poly_linewidths": {
        "footprint": 4,
        "edge": 3,
        "bands": 2,
        "pooled": 3,
    },
}

# ============================================
# HELPER FUNCTIONS
# ============================================

def _draw_poly(ax, poly, lw: float = 2.0, color: str = 'k', ls: str = '-'):
    """Draw polygon outline on matplotlib axis"""
    if (poly is None) or getattr(poly, "is_empty", True):
        return
    geom_type = getattr(poly, "geom_type", None)
    polys = []
    if geom_type == "Polygon":
        polys = [poly]
    elif geom_type == "MultiPolygon":
        polys = list(poly.geoms)
    for p in polys:
        try:
            xs, ys = p.exterior.xy
            ax.plot(xs, ys, linewidth=lw, color=color, linestyle=ls)
        except Exception:
            continue

def _read_dem_as_grid(dem_tif, max_size=800):
    with rasterio.open(dem_tif) as src:
        z = src.read(1).astype(float)
        if src.nodata is not None:
            z[z == src.nodata] = np.nan
        H, W = z.shape
        step = max(1, int(math.ceil(max(H, W) / max_size)))
        rows = np.arange(0, H, step)
        cols = np.arange(0, W, step)
        z_ds = z[::step, ::step]
        xs, ys = rasterio.transform.xy(
            src.transform,
            np.repeat(rows, len(cols)),
            np.tile(cols, len(rows)),
            offset="center"
        )
        return (
            np.array(xs).reshape(len(rows), len(cols)),
            np.array(ys).reshape(len(rows), len(cols)),
            z_ds,
            src.crs,
            max(src.res)  
        )

def _robust_minmax(arrays, q=(2,98)):
            vals = []
            for a in arrays:
                if a is None:
                    continue
                v = np.asarray(a, dtype=float)
                v = v[np.isfinite(v)]
                if v.size:
                    vals.append(v)
            if not vals:
                return (0.0, 1.0)
            v = np.concatenate(vals)
            lo, hi = np.percentile(v, q)
            if lo == hi:
                lo -= 0.1; hi += 0.1
            return float(lo), float(hi)


def _reproj_geom(geom, src_crs, dst_crs):
    if (geom is None) or geom.is_empty:
        return None
    if str(src_crs) == str(dst_crs):
        return geom
    tr = pyproj.Transformer.from_crs(src_crs, dst_crs, always_xy=True).transform
    def _one(poly):
        ext = [tr(*xy) for xy in poly.exterior.coords]
        holes = [[tr(*xy) for xy in r.coords] for r in poly.interiors]
        return Polygon(ext, holes)
    if geom.geom_type == "Polygon":
        return _one(geom)
    elif geom.geom_type == "MultiPolygon":
        return MultiPolygon([_one(p) for p in geom.geoms])
    else:
        return None

def _simplify_for_speed(poly, pixel_size, simplify_k):
    if (poly is None) or poly.is_empty:
        return poly
    tol = float(simplify_k) * float(pixel_size)
    try:
        return shapely.simplify(poly, tolerance=tol, preserve_topology=True)
    except Exception:
        return poly

def add_polygon_outline(fig, polygon, dem_tif, color="#FF00FF", lw=3, poly_crs="EPSG:3857",
                        densify_px=2.0, z_offset=1.5, fallback_z=None, simplify_k=1.5, speed_mode=True):
    if (polygon is None) or polygon.is_empty:
        return
    with rasterio.open(dem_tif) as src:
        poly_src = _reproj_geom(polygon, poly_crs, src.crs)
        if (poly_src is None) or poly_src.is_empty:
            return

        pixel_size = max(src.res)
        poly_src = _simplify_for_speed(poly_src, pixel_size, simplify_k)

        dem_band = src.read(1).astype(float)
        if src.nodata is not None:
            dem_band[dem_band == src.nodata] = np.nan
        dem_med = float(np.nanmedian(dem_band)) if np.isfinite(dem_band).any() else 0.0

        step_len = max(src.res) * float(densify_px)
        polys = [poly_src] if poly_src.geom_type == "Polygon" else list(poly_src.geoms)
        for poly in polys:
            try:
                ring = LinearRing(poly.exterior.coords)
            except Exception:
                continue
            n = max(int(ring.length / max(step_len, 1e-6)) + 1, 64)
            if speed_mode:
                n = min(n, 1200)

            ts = np.linspace(0.0, 1.0, n)
            dists = ts * ring.length
            pts = [ring.interpolate(d) for d in dists]
            xy = [(p.x, p.y) for p in pts]

            vals = np.array([v[0] for v in src.sample(xy, indexes=1)], dtype=float)
            if (src.nodata is not None):
                vals = np.where(vals == src.nodata, np.nan, vals)

            if not np.isfinite(vals).any():
                base = dem_med if (fallback_z is None or not np.isfinite(fallback_z)) else float(fallback_z)
                z = np.full(len(xy), base + float(z_offset), dtype=float)
            else:
                base = np.nanmedian(vals)
                z = np.nan_to_num(vals, nan=base) + float(z_offset)

            xs = np.fromiter((p[0] for p in xy), float, count=len(xy))
            ys = np.fromiter((p[1] for p in xy), float, count=len(xy))
            fig.add_trace(go.Scatter3d(
                x=xs, y=ys, z=z,
                mode="lines",
                line=dict(color=color, width=int(lw)),
                showlegend=False
            ))

# ============================================
# FIGURE 1: ELEVATION MAP (2-panel comparison)
# ============================================

def figure1_elevation(
    out_tif_user: str,
    dem_500: np.ndarray,
    circle_mask_500: np.ndarray,
    xs_flat_500: np.ndarray,
    ys_flat_500: np.ndarray,
    cx3857_user: float,
    cy3857_user: float,
    cx3857_500: float,
    cy3857_500: float,
    aoi_radius_user: int,
    house_area_raster,
    pad_ring_raster,
    bands_raster: List,
    pooled_raster,
    center_elev: float,
    pooled_outer_min: float,
    pooled_outer_max: float,
    ring_bands_m: Tuple[Tuple[float, float], ...],
    save_path: Optional[str] = None,
    config: Dict[str, Any] = None,
) -> plt.Figure:
    """Figure 1: Elevation Map (User AOI vs 500m Reference)"""
    if config is None:
        config = DEFAULT_CONFIG
    
    figsize = config.get("fig1_figsize")
    cmap_div = config.get("fig1_cmap")
    percentiles = config.get("fig1_percentile_span")
    min_span = config.get("fig1_min_span")
    col_dict = config.get("fig1_colors")
    lw_dict = config.get("fig1_linewidths")
    
    # Load and process user AOI DEM
    dtm_user = rio.open_rasterio(out_tif_user, masked=True).squeeze()
    dem_user = dtm_user.values.astype(float)
    circle_mask_user = np.isfinite(dem_user)
    
    # Calculate dynamic span
    delta_user = np.where(circle_mask_user, dem_user - center_elev, np.nan)
    p_low_user, p_high_user = np.nanpercentile(delta_user, percentiles)
    
    delta_500 = np.where(circle_mask_500, dem_500 - center_elev, np.nan)
    p_low_500, p_high_500 = np.nanpercentile(delta_500, percentiles)
    
    span = max(abs(p_low_user), abs(p_high_user), abs(p_low_500), abs(p_high_500), min_span)
    norm_rel = TwoSlopeNorm(vmin=center_elev - span, vcenter=center_elev, vmax=center_elev + span)
    
    fig = plt.figure(figsize=figsize)
    gs = fig.add_gridspec(1, 2, wspace=0.12)
    
    # Left panel: User AOI
    ax1 = fig.add_subplot(gs[0, 0])
    img1 = dtm_user.plot(ax=ax1, cmap=cmap_div, norm=norm_rel, add_colorbar=False)
    cbar1 = fig.colorbar(img1, ax=ax1, shrink=0.8, pad=0.05)
    cbar1.set_label("Elevation (m)", rotation=270, labelpad=15)
    ax1.set_aspect('equal')
    ax1.set_title(f"Elevation - {aoi_radius_user:g}m DEM", fontsize=14, fontweight='bold')
    
    _draw_poly(ax1, house_area_raster, lw=lw_dict["footprint"], color=col_dict["footprint"])
    _draw_poly(ax1, pad_ring_raster, lw=lw_dict["edge"], color=col_dict["edge"])
    for i, ((rmin, rmax), rp) in enumerate(zip(ring_bands_m, bands_raster)):
        _draw_poly(ax1, rp, lw=lw_dict["bands"], color=col_dict["bands"][i % len(col_dict["bands"])])
    _draw_poly(ax1, pooled_raster, lw=lw_dict["pooled"], color=col_dict["pooled"])
    
    ax1.scatter(cx3857_user, cy3857_user, marker='*', s=200, edgecolor='black', facecolor='yellow', linewidth=1, zorder=5)
    ax1.legend(handles=[
        Line2D([0], [0], color=col_dict["footprint"], lw=lw_dict["footprint"], label='Footprint (OSM)'),
        Line2D([0], [0], color=col_dict["edge"], lw=lw_dict["edge"], label='Edge 0.8–2.5 m'),
        Line2D([0], [0], color=col_dict["bands"][0], lw=lw_dict["bands"], label='3–8 m'),
        Line2D([0], [0], color=col_dict["bands"][1], lw=lw_dict["bands"], label='8–15 m'),
        Line2D([0], [0], color=col_dict["bands"][2], lw=lw_dict["bands"], label='15–30 m'),
        Line2D([0], [0], color=col_dict["pooled"], lw=lw_dict["pooled"], label=f'Pooled {pooled_outer_min:.0f}–{pooled_outer_max:.0f} m'),
    ], loc='upper left', framealpha=0.5, fontsize=6.5)
    ax1.axis('off')
    
    # Right panel: 500m reference
    ax2 = fig.add_subplot(gs[0, 1])
    img2 = ax2.imshow(
        np.where(circle_mask_500, dem_500, np.nan),
        cmap=cmap_div, norm=norm_rel,
        extent=(xs_flat_500.min(), xs_flat_500.max(), ys_flat_500.min(), ys_flat_500.max()),
        origin='upper'
    )
    cbar2 = fig.colorbar(img2, ax=ax2, shrink=0.8, pad=0.05)
    cbar2.set_label("Elevation (m)", rotation=270, labelpad=15)
    ax2.set_aspect('equal')
    ax2.set_title("Elevation - 500m DEM", fontsize=14, fontweight='bold')
    ax2.scatter(cx3857_500, cy3857_500, marker='*', s=250, edgecolor='black', facecolor='yellow', linewidth=1.5, zorder=5)
    ax2.axis('off')
    
    fig.suptitle("Elevation Maps", fontsize=16, fontweight='bold')
    
    if save_path:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        fig.savefig(save_path, dpi=200, bbox_inches="tight")
        plt.close(fig)
    
    return fig


# ============================================
# FIGURE 2: SLOPE MAP (2-panel comparison)
# ============================================

def figure2_slope(
    slope_user: Optional[np.ndarray],
    slope_500: np.ndarray,
    circle_mask_user: Optional[np.ndarray],
    circle_mask_500: np.ndarray,
    xs_flat_user: Optional[np.ndarray],
    ys_flat_user: Optional[np.ndarray],
    xs_flat_500: np.ndarray,
    ys_flat_500: np.ndarray,
    src_crs_user,
    lon: float,
    lat: float,
    aoi_radius_user: int,
    save_path: Optional[str] = None,
    config: Dict[str, Any] = None,
) -> plt.Figure:
    """Figure 2: Slope Map (2-panel comparison)"""
    if config is None:
        config = DEFAULT_CONFIG
    
    figsize = config.get("fig2_figsize")
    cmap = config.get("fig2_cmap")
    vmin = config.get("fig2_vmin")
    vmax_percentile = config.get("fig2_vmax_percentile")
    vmax_min = config.get("fig2_vmax_min")
    
    # Dynamic vmax calculation
    svals = []
    if slope_user is not None:
        svals.append(np.where(circle_mask_user, slope_user, np.nan))
    svals.append(np.where(circle_mask_500, slope_500, np.nan))
    concat = np.concatenate([v[np.isfinite(v)] for v in svals if v is not None]) if svals else np.array([15.0])
    smax = float(np.nanpercentile(concat, vmax_percentile)) if concat.size else vmax_min
    smax = max(vmax_min, smax)
    
    fig = plt.figure(figsize=figsize)
    gs = fig.add_gridspec(1, 2, wspace=0.12)
    
    # Left panel: User AOI
    ax1 = fig.add_subplot(gs[0, 0])
    if slope_user is not None and np.isfinite(slope_user).any():
        img1 = ax1.imshow(
            np.where(circle_mask_user, slope_user, np.nan),
            cmap=cmap, vmin=vmin, vmax=smax, origin="upper",
            extent=(np.min(xs_flat_user), np.max(xs_flat_user), np.min(ys_flat_user), np.max(ys_flat_user))
        )
        cbar1 = fig.colorbar(img1, ax=ax1, shrink=0.8, pad=0.05)
        cbar1.set_label("Slope (degrees)", rotation=270, labelpad=12)
        ax1.set_title(f"Slope - {aoi_radius_user:g}m DEM", fontsize=14, fontweight='bold')
        xh, yh = pyproj.Transformer.from_crs("EPSG:4326", src_crs_user, always_xy=True).transform(lon, lat)
        ax1.scatter(xh, yh, marker='*', s=120, edgecolor='k', facecolor='yellow', zorder=5)
    else:
        ax1.text(0.5, 0.5, "DEM unavailable", ha='center', va='center', transform=ax1.transAxes)
    ax1.axis('off')
    
    # Right panel: 500m reference
    ax2 = fig.add_subplot(gs[0, 1])
    img2 = ax2.imshow(
        np.where(circle_mask_500, slope_500, np.nan),
        cmap=cmap, vmin=vmin, vmax=smax, origin="upper",
        extent=(xs_flat_500.min(), xs_flat_500.max(), ys_flat_500.min(), ys_flat_500.max())
    )
    cbar2 = fig.colorbar(img2, ax=ax2, shrink=0.8, pad=0.05)
    cbar2.set_label("Slope (degrees)", rotation=270, labelpad=12)
    ax2.set_title("Slope - 500m DEM", fontsize=14, fontweight='bold')
    ax2.scatter(np.mean([xs_flat_500.min(), xs_flat_500.max()]),
                np.mean([ys_flat_500.min(), ys_flat_500.max()]),
                marker='*', s=160, edgecolor='k', facecolor='yellow', zorder=5)
    ax2.axis('off')
    
    fig.suptitle("Slope Maps", fontsize=16, fontweight='bold')
    
    if save_path:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        fig.savefig(save_path, dpi=200, bbox_inches="tight")
        plt.close(fig)
    
    return fig


# ============================================
# FIGURE 3: ASPECT MAP (2-panel comparison)
# ============================================

def figure3_aspect(
    aspect_user: Optional[np.ndarray],
    slope_user: Optional[np.ndarray],
    aspect_500: np.ndarray,
    slope_500: np.ndarray,
    circle_mask_user: Optional[np.ndarray],
    circle_mask_500: np.ndarray,
    xs_flat_user: Optional[np.ndarray],
    ys_flat_user: Optional[np.ndarray],
    xs_flat_500: np.ndarray,
    ys_flat_500: np.ndarray,
    aoi_radius_user: int,
    save_path: Optional[str] = None,
    config: Dict[str, Any] = None,
) -> plt.Figure:
    """Figure 3: Aspect Map (2-panel comparison, flat areas masked)"""
    if config is None:
        config = DEFAULT_CONFIG
    
    figsize = config.get("fig3_figsize")
    cmap = config.get("fig3_cmap")
    flat_threshold = config.get("fig3_flat_threshold")
    
    fig = plt.figure(figsize=figsize)
    gs = fig.add_gridspec(1, 2, wspace=0.12)
    
    # Left panel: User AOI
    ax1 = fig.add_subplot(gs[0, 0])
    if aspect_user is not None and slope_user is not None:
        flat_mask_user = np.isfinite(slope_user) & (slope_user < flat_threshold)
        asp_vis_user = np.where(circle_mask_user & ~flat_mask_user, aspect_user, np.nan)
        img1 = ax1.imshow(
            asp_vis_user, cmap=cmap, vmin=0, vmax=360, origin="upper",
            extent=(np.min(xs_flat_user), np.max(xs_flat_user), np.min(ys_flat_user), np.max(ys_flat_user))
        )
        cbar1 = fig.colorbar(img1, ax=ax1, shrink=0.8, pad=0.05)
        cbar1.set_label("Aspect (°, 0=N, 90=E)", rotation=270, labelpad=12)
        ax1.set_title(f"Aspect - {aoi_radius_user:g}m DEM", fontsize=14, fontweight='bold')
    else:
        ax1.text(0.5, 0.5, "DEM unavailable", ha='center', va='center', transform=ax1.transAxes)
    ax1.axis('off')
    
    # Right panel: 500m reference
    ax2 = fig.add_subplot(gs[0, 1])
    flat_mask_500 = np.isfinite(slope_500) & (slope_500 < flat_threshold)
    asp_vis_500 = np.where(circle_mask_500 & ~flat_mask_500, aspect_500, np.nan)
    img2 = ax2.imshow(
        asp_vis_500, cmap=cmap, vmin=0, vmax=360, origin="upper",
        extent=(xs_flat_500.min(), xs_flat_500.max(), ys_flat_500.min(), ys_flat_500.max())
    )
    cbar2 = fig.colorbar(img2, ax=ax2, shrink=0.8, pad=0.05)
    cbar2.set_label("Aspect (°, 0=N, 90=E)", rotation=270, labelpad=12)
    ax2.set_title("Aspect - 500m DEM", fontsize=14, fontweight='bold')
    ax2.axis('off')
    
    fig.suptitle("Aspect Maps", fontsize=16, fontweight='bold')
    
    if save_path:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        fig.savefig(save_path, dpi=200, bbox_inches="tight")
        plt.close(fig)
    
    return fig


# ============================================
# FIGURE 4: TERRAIN CLASSIFICATION + HISTOGRAM
# ============================================

def figure4_terrain_and_hist(
    zone_compact: np.ndarray,
    circle_mask_500: np.ndarray,
    dem_500: np.ndarray,
    star_rc: Tuple[int, int],
    house_ground_med: float,
    ring_masks: Dict[float, np.ndarray],
    aoi_radius_user: int = 500,
    save_path: Optional[str] = None,
    config: Dict[str, Any] = None,
) -> plt.Figure:
    """Figure 4: Terrain Classification (3-class) + Elevation Distribution"""
    if config is None:
        config = DEFAULT_CONFIG
    
    figsize = config.get("fig4_figsize")
    cmap_labels = config.get("fig4_cmap_labels")
    target_radii = config.get("fig4_histogram_target_radii")
    colors_cycle = config.get("fig4_histogram_colors")
    hist_percentiles = config.get("fig4_histogram_percentiles")
    nbins = config.get("fig4_histogram_nbins")
    
    # Create colormap dynamically
    cmap_compact = ListedColormap(["#000000", "#9E9E9E", "#4E79A7", "#F28E2B"])
    
    fig = plt.figure(figsize=figsize)
    gs = fig.add_gridspec(1, 2, wspace=0.15)
    
    # Left: Terrain zones
    ax_t = fig.add_subplot(gs[0, 0])
    zone_display = zone_compact.astype(float)
    zone_display[~circle_mask_500] = np.nan
    img = ax_t.imshow(zone_display, cmap=cmap_compact, vmin=0, vmax=3, origin="upper")
    ax_t.set_title("Terrain Zones Classification", fontweight="bold", fontsize=12)
    legend_order = [0, 1, 2, 3]
    handles = [
        Patch(facecolor=cmap_compact(i / 3), edgecolor="k", linewidth=0.5,
              label=cmap_labels.get(i, f"Class {i}"))
        for i in legend_order
    ]
    ax_t.legend(handles=handles, loc='upper left', framealpha=0.9, fontsize=9, ncol=1)
    ax_t.scatter(star_rc[1], star_rc[0], marker='*', s=300, color='yellow', edgecolor='black', linewidth=1.5, zorder=10)
    ax_t.axis('off')
    
    # Right: Elevation histogram
    ax_h = fig.add_subplot(gs[0, 1])
    ring_elev_dict = {}
    
    for r in target_radii:
        m = ring_masks.get(r, None)
        if m is None:
            continue
        vals = dem_500[m]
        vals = vals[np.isfinite(vals)]
        if vals.size > 0:
            ring_elev_dict[r] = vals
    
    if ring_elev_dict:
        concat_all = np.concatenate(list(ring_elev_dict.values()))
        p1, p99 = np.nanpercentile(concat_all, hist_percentiles)
        if p1 == p99:
            p1 -= 0.1
            p99 += 0.1
        bins = np.linspace(p1, p99, nbins)
        
        for idx, r in enumerate(target_radii):
            vals = ring_elev_dict.get(r, None)
            if vals is None:
                continue
            col = colors_cycle[idx % len(colors_cycle)]
            ax_h.hist(vals, bins=bins, density=True, histtype='step', linewidth=2.0, color=col,
                      label=f'{int(r)}m (n={vals.size:,})')
            ax_h.hist(vals, bins=bins, density=True, histtype='stepfilled', alpha=0.10, color=col)
        
        ax_h.axvline(house_ground_med, color='black', linestyle='-', linewidth=2.0, label='House elevation')
        ax_h.set_title('Elevation Distributions', fontsize=12, fontweight='bold')
        ax_h.set_xlabel('Elevation (m)')
        ax_h.set_ylabel('Density')
        ax_h.grid(True, alpha=0.25, linestyle=':')
        h, l = ax_h.get_legend_handles_labels()
        uniq = dict(zip(l, h))
        ax_h.legend(uniq.values(), uniq.keys(), loc="upper right", fontsize=9, framealpha=0.9, ncol=1)
    else:
        ax_h.text(0.5, 0.5, "No valid elevation data", ha='center', va='center', transform=ax_h.transAxes)
        ax_h.axis('off')
    
    fig.suptitle("Terrain & Elevation Summary", fontsize=16, fontweight='bold')
    
    if save_path:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        fig.savefig(save_path, dpi=200, bbox_inches="tight")
        plt.close(fig)
    
    return fig

# ============================================
# FIGURE 5 & 6: 3D VISUALIZATION PIPELINE 
# ============================================

def add_3d_figures_to_pipeline(
    dem_path_user: str,           
    dem_path_500: str,           
    house_area_raster,
    pad_ring_raster,
    bands_raster: list,
    pooled_raster,
    house_ground_med: float,
    aoi_radius_user: int,
    src_crs_user,
    ring_bands_m: tuple,
    outdir=None,
    config=None,
    verbose=True,
):
    """
    3D Visualization
    """
    try:
        SPEED_MODE = config.get("fig5_6_speed_mode", True) if config else True
        MAX_RINGS_TO_DRAW = config.get("fig5_6_max_rings_to_draw", 3) if config else 3
        MAX_SIZE_USER = config.get("fig5_6_dem_max_size_user", 360) if config else 360
        MAX_SIZE_REF  = config.get("fig5_6_dem_max_size_ref", 420) if config else 420
        DENSIFY_PX = config.get("fig5_6_densify_px", 2.0) if config else 2.0
        SIMPLIFY_K = config.get("fig5_6_simplify_k", 1.5) if config else 1.5
        COLOR_SEQ = (config or {}).get("fig5_6_elevation_colorscale", "RdBu_r")

        # Polygon color/linewidth config
        poly_colors = config.get("fig5_6_poly_colors", {}) if config else {}
        COL_FOOT = poly_colors.get("footprint", "#FFD700")
        COL_EDGE = poly_colors.get("edge", "#00E5FF")
        COL_BANDS = poly_colors.get("bands", ["#FF5733", "#33FF77", "#3388FF"])
        COL_POOL = poly_colors.get("pooled", "#FF00FF")
        if SPEED_MODE:
            LW_FOOT, LW_EDGE, LW_BAND, LW_POOL = (4, 3, 2, 3)
        else:
            LW_FOOT, LW_EDGE, LW_BAND, LW_POOL = (6, 5, 3, 4)

        # ---- Load DEM arrays for range normalization ----
        with rasterio.open(dem_path_user) as su:
            zu = su.read(1).astype(float)
            if su.nodata is not None:
                zu[zu == su.nodata] = np.nan
        with rasterio.open(dem_path_500) as s5:
            z5 = s5.read(1).astype(float)
            if s5.nodata is not None:
                z5[z5 == s5.nodata] = np.nan

        q_low, q_high = (config or {}).get("fig5_6_quantiles", (2, 98))
        CMIN, CMAX = _robust_minmax([zu, z5], (q_low, q_high))

        # ---- Draw DEM surface ----
        def _render_dem_surface(dem_tif, title, max_size):
            xs, ys, z, _, _ = _read_dem_as_grid(dem_tif, max_size=max_size)
            surf = go.Surface(
                x=xs, y=ys, z=z,
                surfacecolor=z,
                colorscale=COLOR_SEQ,
                cmin=CMIN, cmax=CMAX,
                showscale=True, colorbar=dict(title="Elevation (m)")
            )
            fig = go.Figure(surf)
            fig.update_layout(
                title=title, scene_aspectmode="data",
                margin=dict(l=0, r=0, t=40, b=0), showlegend=False
            )
            return fig

        fig_user = _render_dem_surface(dem_path_user, f"Elevation DEM - {aoi_radius_user:g}m", MAX_SIZE_USER)
        fig_ref  = _render_dem_surface(dem_path_500, "Elevation DEM - 500m", MAX_SIZE_REF)

        # ---- Combine left/right ----
        combo = make_subplots(
            rows=1, cols=2,
            specs=[[{"type": "scene"}, {"type": "scene"}]],
            subplot_titles=(f"Elevation DEM - {aoi_radius_user:g}m", "Elevation DEM - 500m"),
        )
        for tr in fig_user.data: combo.add_trace(tr, row=1, col=1)
        for tr in fig_ref.data:  combo.add_trace(tr, row=1, col=2)

        # ---- Overlay polygons (footprint, rings, pooled) ----
        def _overlay(fig_obj, poly, base_tif, color, lw, col_idx):
            tmp = go.Figure()
            add_polygon_outline(
                tmp, poly, base_tif,
                color=color, lw=lw,
                poly_crs=src_crs_user,
                densify_px=DENSIFY_PX,
                z_offset=1.2 if SPEED_MODE else 1.5,
                fallback_z=house_ground_med,
                simplify_k=SIMPLIFY_K,
                speed_mode=SPEED_MODE
            )
            for tr in tmp.data:
                tr.showlegend = False
                fig_obj.add_trace(tr, row=1, col=col_idx)

        for (col_idx, tif) in [(1, dem_path_user), (2, dem_path_500)]:
            _overlay(combo, house_area_raster, tif, COL_FOOT, LW_FOOT, col_idx)
            _overlay(combo, pad_ring_raster,   tif, COL_EDGE, LW_EDGE, col_idx)
            rings_drawn = 0
            for rp, colr in zip(bands_raster, COL_BANDS):
                if rp is None: continue
                _overlay(combo, rp, tif, colr, LW_BAND, col_idx)
                rings_drawn += 1
                if rings_drawn >= MAX_RINGS_TO_DRAW: break
            _overlay(combo, pooled_raster, tif, COL_POOL, LW_POOL, col_idx)

        combo.update_layout(
            title=dict(text="3D Terrain Visualization",
                       x=0.5, xanchor='center', font=dict(size=16)),
            scene =dict(aspectmode="data"),
            scene2=dict(aspectmode="data"),
            margin=dict(l=0, r=60, t=70, b=0),
            showlegend=False,
            height=750 if SPEED_MODE else 900,
        )

        # ---- Save ----
        if outdir:
            out_path = os.path.join(outdir, "figure5_6_3d_combo.html")
            combo.write_html(out_path)
            if verbose:
                print(f"✓ Saved {out_path}")

        return (fig_user, None, fig_ref, None)

    except Exception as e:
        print(f"⚠ 3D visualization failed: {e}")
        import traceback; traceback.print_exc()
        return None, None, None, None


__all__ = [
    'DEFAULT_CONFIG',
    'figure1_elevation',
    'figure2_slope',
    'figure3_aspect',
    'figure4_terrain_and_hist',
    'add_3d_figures_to_pipeline',
]