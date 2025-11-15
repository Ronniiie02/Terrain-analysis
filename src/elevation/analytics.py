# -*- coding: utf-8 -*-
"""
Analytics module
- Build multiscale summary DataFrame
- Generate narrative (reusing terrain.generate_narrative)
- Area-level statistics (min/max/median/percentile rank)
- Surrounding terrain summary (50/100/200/300/500m) with identical prints
"""

from __future__ import annotations
from typing import Dict, Any, Tuple, List, Optional
import numpy as np
import pandas as pd

from .terrain import (
    compute_ring_metrics_unified,
    generate_narrative,
    cardinal_direction,
)

__all__ = [
    "ring_metrics_for_radii",
    "build_multiscale_summary_df",
    "generate_narrative_from_df",
    "area_level_summary",
    "build_surrounding_terrain_table",
]


def ring_metrics_for_radii(
    dem: np.ndarray,
    slope: np.ndarray,
    aspect: np.ndarray,
    house_rc: Tuple[int, int],
    house_elev: float,
    ring_masks: Dict[float, np.ndarray],
) -> List[Dict[str, Any]]:
    """Compute ring metrics for standard radii (50, 200, 500) if available."""
    rows_grid, cols_grid = np.indices(dem.shape)
    out = []
    for radius in [50.0, 200.0, 500.0]:
        mask = ring_masks.get(radius, None)
        if mask is None:
            continue
        m = compute_ring_metrics_unified(
            dem=dem, slope=slope, aspect=aspect,
            house_rc=house_rc, house_elev=house_elev,
            ring_mask=mask, rows_grid=rows_grid, cols_grid=cols_grid
        )
        if m:
            out.append({'radius_m': radius, 'metrics': m})
    return out


def build_multiscale_summary_df(ring_analyses: List[Dict[str, Any]]) -> pd.DataFrame:
    """Return the summary table used in the final script's display() before narrative."""
    rows = []
    for x in ring_analyses:
        m = x['metrics']
        rows.append({
            'Radius (m)': x['radius_m'],
            'Pixels': m['n_pixels'],
            'ΔElev_median (m)': round(m['delta_median'], 3),
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
    if not rows:
        return pd.DataFrame(columns=[
            'Radius (m)','Pixels','ΔElev_median (m)','% Higher','% Lower',
            'Slope_mean (°)','Slope_median (°)','Slope_P25 (°)','Slope_P75 (°)',
            '% Flat <2°','% Gentle 2–5°','% Steep ≥5°',
            'Convergence (%)','Dominant Aspect (°)','Dominant Aspect (cardinal)'
        ])
    return pd.DataFrame(rows)


def generate_narrative_from_df(
    summary_df: pd.DataFrame,
    dem: np.ndarray,
    slope: np.ndarray,
    aspect: np.ndarray,
    house_rc: Tuple[int, int],
    ring_mask_10m: Optional[np.ndarray] = None,
) -> str:
    """
    Wrapper to attach ring_mask_10m via DataFrame attrs so terrain.generate_narrative
    produces identical text as the final single-file pipeline.
    """
    if ring_mask_10m is not None:
        summary_df = summary_df.copy()
        summary_df.attrs['ring_mask_10m'] = ring_mask_10m
    return generate_narrative(summary_df, dem, slope, aspect, house_rc)


def area_level_summary(
    elevations_500m: np.ndarray,
    house_elev: float,
) -> Tuple[pd.DataFrame, float, str]:
    """
    Compute area-level table (min/max/median/percentile rank) and textual risk_desc,
    matching STEP 10 prints.
    """
    elevs = elevations_500m[np.isfinite(elevations_500m)]
    if elevs.size == 0:
        return pd.DataFrame(), np.nan, "No data"

    min_elev = float(np.nanmin(elevs))
    max_elev = float(np.nanmax(elevs))
    median_elev = float(np.nanmedian(elevs))

    percentile_rank = (elevs < house_elev).sum() / elevs.size * 100.0
    if percentile_rank < 10:
        risk_desc = "Among the lowest 10% of elevations"
    elif percentile_rank < 25:
        risk_desc = "Below the 25th percentile"
    elif percentile_rank < 50:
        risk_desc = "Below regional median"
    elif percentile_rank < 75:
        risk_desc = "Above regional median"
    elif percentile_rank < 90:
        risk_desc = "Above the 75th percentile"
    else:
        risk_desc = "Among the highest 10% of elevations"

    summary_df = pd.DataFrame({
        'Metric': ['House Elevation','Regional Min','Regional Max','Regional Median','Percentile Rank'],
        'Value':  [f"{house_elev:.3f} m", f"{min_elev:.3f} m", f"{max_elev:.3f} m", f"{median_elev:.3f} m", f"{percentile_rank:.1f}%"],
        'Interpretation': [
            'Reference elevation',
            f"{house_elev - min_elev:.2f} m above lowest",
            f"{max_elev - house_elev:.2f} m below highest",
            f"{'Above' if house_elev > median_elev else 'Below'} median",
            risk_desc
        ]
    })
    return summary_df, percentile_rank, risk_desc


def build_surrounding_terrain_table(
    ring_analyses: List[Dict[str, Any]],
    dem_500: np.ndarray,
    house_elev: float,
    ring_masks: Dict[float, np.ndarray],
    extra_radii: List[float] = [100.0, 300.0],
) -> pd.DataFrame:
    """
    Reproduce STEP 10 'SURROUNDING TERRAIN SUMMARY (Cumulative Circles - Unified)'.
    It includes computed radii from ring_analyses and the additional 100/300m.
    """
    rows = []
    
    # Already computed radii
    for a in ring_analyses:
        m = a['metrics']
        r = float(a['radius_m'])
        rows.append({
            "Radius (m)": int(r),
            "Median Elev (m)": m['ring_median'],
            "Δ House−Ring (m)": m['delta_median'],
            "% Higher Than House": m['pct_higher'],
            "Pixels": m['n_pixels']
        })
    
    # Extra radii
    for r in extra_radii:
        if any(abs(float(a['radius_m']) - r) < 1e-6 for a in ring_analyses):
            continue
        mask = ring_masks.get(r, None)
        if mask is None or mask.sum() == 0:
            continue
        vals = dem_500[mask]
        vals = vals[np.isfinite(vals)]
        if vals.size == 0:
            continue
        ring_median = float(np.nanmedian(vals))
        delta_vs_ring = float(house_elev - ring_median)
        pct_higher = float((vals > house_elev).sum() / vals.size * 100.0)

        rows.append({
            "Radius (m)": int(r),
            "Median Elev (m)": ring_median,
            "Δ House−Ring (m)": delta_vs_ring,
            "% Higher Than House": pct_higher,
            "Pixels": int(mask.sum())
        })

    rows = sorted(rows, key=lambda x: x["Radius (m)"])
    return pd.DataFrame(rows)