# -*- coding: utf-8 -*-
"""
OSM building fetcher (Overpass API)
"""

import os
import time
import math
import pyproj
import requests
from shapely.geometry import Point, Polygon, MultiPolygon
from shapely.ops import transform as shp_transform
from shapely.validation import make_valid
from requests.exceptions import RequestException, Timeout

__all__ = [
    "geodesic_area_m2",
    "distance_m_local", 
    "fetch_building_osm",
]

OVERLAP_UA = "flood-risk/2.2 (analysis tool)"


def geodesic_area_m2(geom):
    """
    Calculate the true surface area on the WGS84 ellipsoid.
    Works for both Polygon and MultiPolygon.
    """
    geod = pyproj.Geod(ellps="WGS84")

    def poly_area(poly):
        lon, lat = poly.exterior.xy
        a_exterior, _ = geod.polygon_area_perimeter(lon, lat)
        area = abs(a_exterior)

        # subtract area of interior rings (holes)
        for ring in poly.interiors:
            lon_i, lat_i = ring.xy
            a_hole, _ = geod.polygon_area_perimeter(lon_i, lat_i)
            area -= abs(a_hole)

        return area

    if isinstance(geom, Polygon):
        return poly_area(geom)
    elif isinstance(geom, MultiPolygon):
        return sum(poly_area(p) for p in geom.geoms)

    return 0.0


def distance_m_local(geom, pt_lon, pt_lat):
    """
    Compute distance in meters from a lon/lat point
    using a local Azimuthal Equidistant projection (AEQD).
    This avoids inaccuracies from great-circle approximations
    when measuring short local distances.
    """
    aeqd = pyproj.CRS.from_proj4(
        f"+proj=aeqd +lat_0={pt_lat} +lon_0={pt_lon} +datum=WGS84 +units=m +no_defs"
    )
    wgs84 = pyproj.CRS.from_epsg(4326)

    to_local = pyproj.Transformer.from_crs(
        wgs84, aeqd, always_xy=True
    ).transform
    
    geom_local = shp_transform(to_local, geom)
    point_local = shp_transform(to_local, Point(pt_lon, pt_lat))
    
    return point_local.distance(geom_local)


def fetch_building_osm(lat, lon, buffer_m=120.0, timeout_s=25):
    """
    Query building footprints from the OSM Overpass API.
    
    Args:
        lat, lon: Query point (WGS84)
        buffer_m: Search radius in meters
        timeout_s: Request timeout in seconds
    
    Returns:
        {
            'success': bool,
            'selected': selected_building_dict or None,
            'all': list of all buildings found,
            'n_buildings': int,
            'method': selection method ('contains_query_point', 'nearest_to_query'),
            'error': str or None
        }
    """
    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter", 
        "https://overpass.openstreetmap.ru/api/interpreter",
        "https://overpass.nchc.org.tw/api/interpreter",
    ]

    # Convert approximate meter buffer to degrees
    buffer_deg = max(buffer_m, 80.0) / 111000.0
    headers = {"User-Agent": OVERLAP_UA}
    
    query = f"""
    [bbox:{lat - buffer_deg},{lon - buffer_deg},{lat + buffer_deg},{lon + buffer_deg}]
    [out:json][timeout:{timeout_s}];
    (
      way["building"];
      relation["building"];
    );
    out geom;
    """

    last_err = None
    
    for ep in endpoints:
        for attempt, sleep_s in enumerate([0.0, 1.0, 2.0, 3.5], start=1):
            if sleep_s > 0:
                time.sleep(sleep_s)

            try:
                r = requests.post(ep, data=query, timeout=timeout_s, headers=headers)
                r.raise_for_status()
                data = r.json()
                elements = data.get('elements', [])

                if not elements:
                    return {
                        'success': False, 
                        'selected': None, 
                        'n_buildings': 0, 
                        'error': 'No buildings found'
                    }

                buildings = []
                pt = Point(lon, lat)

                # Convert each building returned by OSM into a usable polygon
                for elem in elements:
                    try:
                        if elem['type'] == 'way':
                            coords = [(nd['lon'], nd['lat']) for nd in elem.get('geometry', [])]
                            if len(coords) >= 3:
                                poly = Polygon(coords)
                                buildings.append({
                                    'geometry': poly,
                                    'area_m2': geodesic_area_m2(poly),
                                    'distance_m': distance_m_local(poly, lon, lat),
                                    'contains_query': poly.contains(pt)
                                })

                        elif elem['type'] == 'relation':
                            members = elem.get('members', [])
                            gmem = None

                            # find outer ring
                            for m in members:
                                if m.get('role') == 'outer' and 'geometry' in m:
                                    gmem = m
                                    break
                            # fallback
                            if gmem is None and members:
                                gmem = members[0] if 'geometry' in members[0] else None

                            if gmem and 'geometry' in gmem:
                                coords = [(nd['lon'], nd['lat']) for nd in gmem['geometry']]
                                if len(coords) >= 3:
                                    poly = Polygon(coords)
                                    buildings.append({
                                        'geometry': poly,
                                        'area_m2': geodesic_area_m2(poly),
                                        'distance_m': distance_m_local(poly, lon, lat),
                                        'contains_query': poly.contains(pt)
                                    })
                    except Exception:
                        continue

                if not buildings:
                    return {
                        'success': False, 
                        'selected': None, 
                        'n_buildings': 0, 
                        'error': 'No valid polygons'
                    }

                # Priority 1: buildings containing the query point
                contains = [b for b in buildings if b['contains_query']]
                if contains:
                    selected = max(contains, key=lambda b: b['area_m2'])
                    method = "contains_query_point"
                else:
                    # Priority 2: nearest footprint
                    selected = min(buildings, key=lambda b: b['distance_m'])
                    method = "nearest_to_query"

                return {
                    'success': True,
                    'selected': selected,
                    'all': buildings,
                    'n_buildings': len(buildings),
                    'method': method,
                    'error': None
                }

            except (Timeout, RequestException) as e:
                last_err = str(e)
                continue

    return {
        'success': False,
        'selected': None,
        'n_buildings': 0,
        'error': f'Overpass failed: {last_err or "unknown"}'
    }


# ===========================================================
# Secondary helper functions (rewritten with English comments)
# ===========================================================

def _geodesic_area_m2(geom):
    """
    Same as geodesic_area_m2 but simplified for internal fallback use.
    Computes area on the WGS84 ellipsoid.
    """
    geod = pyproj.Geod(ellps="WGS84")

    def poly_area(poly):
        lon, lat = poly.exterior.xy
        a_exterior, _ = geod.polygon_area_perimeter(lon, lat)
        area = abs(a_exterior)

        for ring in poly.interiors:
            lon_i, lat_i = ring.xy
            a_hole, _ = geod.polygon_area_perimeter(lon_i, lat_i)
            area -= abs(a_hole)

        return area

    if isinstance(geom, Polygon):
        return poly_area(geom)
    elif isinstance(geom, MultiPolygon):
        return sum(poly_area(p) for p in geom.geoms)

    return 0.0


def _distance_m_local(geom, pt_lon, pt_lat):
    """
    Local AEQD projection to compute meter-level distances
    between a lon/lat point and a footprint geometry.
    This avoids distortion from geographic coordinates.
    """
    aeqd = pyproj.CRS.from_proj4(
        f"+proj=aeqd +lat_0={pt_lat} +lon_0={pt_lon} +datum=WGS84 +units=m +no_defs"
    )
    wgs84 = pyproj.CRS.from_epsg(4326)

    to_local = pyproj.Transformer.from_crs(
        wgs84, aeqd, always_xy=True
    ).transform

    geom_local = shp_transform(to_local, geom)
    point_local = shp_transform(to_local, Point(pt_lon, pt_lat))

    return point_local.distance(geom_local)


def validate_and_relocate_building(
    lat: float,
    lon: float,
    aoi_radius_user: float,
    aoi_radius_ref: float = 500.0,
    accept_within_user_phase1: float = 1.2,   # Accept Phase 1 footprint when distance <= 1.2 × AOI
    accept_within_user_phase2: float = 0.9,   # Accept Phase 2 footprint when distance <= 0.9 × AOI
    user_search_expand: float = 1.5,          # Phase 2 search radius expansion factor
    user_search_floor_m: float = 150.0,       # Minimum search radius for Phase 2
    micro_shift_m: float = 30.0,              # Threshold for marking a “micro relocation”
    timeout_s: int = 30,
):
    """
    Multi-phase OSM building validation and centroid relocation.

    Workflow:
    -----------------------------------
    Phase 1 (Reference radius: 500m):
        - Fetch all footprints in 500m radius.
        - If any footprint contains the query point → Accept directly.
        - Else if the closest footprint is within 1.2 × AOI radius → Accept.
        - Otherwise → proceed to Phase 2.

    Phase 2 (User AOI expanded):
        - Use max(1.5 × AOI, 150m) as the new radius.
        - If any footprint contains the query point → Accept.
        - Else if closest footprint is within 0.9 × AOI → Accept.
        - Otherwise → relocate to the centroid of the Phase 1 selected footprint.

    Returned fields:
        - relocation_distance_m
        - micro_shift_ok (True if relocation < micro_shift_m)
    """
    # Phase 1: reference search (default 500m)
    r500 = fetch_building_osm(lat, lon, buffer_m=aoi_radius_ref * 1.1, timeout_s=timeout_s)

    best = None
    reason = "no_relocation_needed"
    new_lat, new_lon = lat, lon
    relocation_distance_m = 0.0

    def _dist(sel, lon0, lat0):
        """Helper: safely compute geometric distance."""
        try:
            d = float(sel.get("distance_m", float("nan")))
            if not math.isfinite(d):
                raise ValueError
            return d
        except Exception:
            return distance_m_local(sel["geometry"], lon0, lat0)

    if r500.get("success") and r500.get("selected"):

        contains_500 = [b for b in (r500.get("all") or []) if b.get("contains_query")]
        if contains_500:
            best = max(contains_500, key=lambda b: b["area_m2"])
            reason = "contains_query_in_ref"

        else:
            sel = r500["selected"]
            d500 = _dist(sel, lon, lat)

            if d500 <= aoi_radius_user * float(accept_within_user_phase1):
                best = sel
                reason = "within_1p2x_user_in_ref"

            else:
                # Phase 2: expanded user AOI
                user_buf = max(aoi_radius_user * float(user_search_expand), float(user_search_floor_m))
                ruser = fetch_building_osm(lat, lon, buffer_m=user_buf, timeout_s=timeout_s)

                if ruser.get("success") and ruser.get("selected"):

                    contains_user = [b for b in (ruser.get("all") or []) if b.get("contains_query")]
                    if contains_user:
                        best = max(contains_user, key=lambda b: b["area_m2"])
                        reason = "contains_query_in_user"

                    else:
                        sel_u = ruser["selected"]
                        du = _dist(sel_u, lon, lat)

                        if du <= aoi_radius_user * float(accept_within_user_phase2):
                            best = sel_u
                            reason = "within_0p9x_user_in_user"

                        else:
                            best = sel
                            c = sel["geometry"].centroid
                            new_lat, new_lon = c.y, c.x
                            relocation_distance_m = d500
                            reason = "relocated_to_ref_centroid"
                else:
                    best = sel
                    c = sel["geometry"].centroid
                    new_lat, new_lon = c.y, c.x
                    relocation_distance_m = d500
                    reason = "relocated_no_buildings_in_user"
    else:
        reason = "no_buildings_found"

    return dict(
        success=(best is not None),
        building=best,
        relocated_lat=new_lat,
        relocated_lon=new_lon,
        relocation_reason=reason,
        original_lat=lat,
        original_lon=lon,
        relocation_distance_m=float(relocation_distance_m),
        micro_shift_ok=bool(relocation_distance_m < float(micro_shift_m)),
    )


def create_fallback_building(center_lon, center_lat, building_buffer_m=5.0):
    """
    Create a synthetic circular fallback building geometry
    around a query location (used when OSM footprints fail).
    """
    def to_3857(x, y):
        t = pyproj.Transformer.from_crs(
            "EPSG:4326", "EPSG:3857", always_xy=True
        ).transform
        return t(x, y)
    
    center_3857 = to_3857(center_lon, center_lat)

    # Circular buffer in Web Mercator (meters)
    corrected_building = Point(center_3857).buffer(building_buffer_m, resolution=32)
    
    transformer = pyproj.Transformer.from_crs(
        'EPSG:3857', 'EPSG:4326', always_xy=True
    )

    corrected_building_4326 = shp_transform(transformer.transform, corrected_building)
    
    return corrected_building, corrected_building_4326


if __name__ == "__main__":
    test_lat, test_lon = 40.7128, -74.0060  
    print("Testing OSM building fetch...")
    result = fetch_building_osm(test_lat, test_lon, buffer_m=100.0)
    print(f"Success: {result['success']}")
    if result['success']:
        print(f"Buildings found: {result['n_buildings']}")
        print(f"Selected building area: {result['selected']['area_m2']:.2f} m²")
