# -*- coding: utf-8 -*-
"""
OSM building fetcher (Overpass API)
-----------------------------------
Functions mirror the final one-file pipeline 1:1:
- geodesic_area_m2: Calculate true surface area on WGS84 ellipsoid
- distance_m_local: Distance in meters using local AEQD projection
- fetch_building_osm: Multi-endpoint + backoff, EXACT return schema (buffer_m is REQUIRED and dynamic!)
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

# 与原始代码完全相同的User-Agent
OVERLAP_UA = "flood-risk/2.2 (analysis tool)"

def geodesic_area_m2(geom):
    """
    Calculate true surface area on WGS84 ellipsoid.
    Works with Polygon or MultiPolygon.
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


def distance_m_local(geom, pt_lon, pt_lat):
    """
    Compute distance in meters from a lon/lat point
    using local Azimuthal Equidistant projection (AEQD).
    """
    aeqd = pyproj.CRS.from_proj4(f"+proj=aeqd +lat_0={pt_lat} +lon_0={pt_lon} +datum=WGS84 +units=m +no_defs")
    wgs84 = pyproj.CRS.from_epsg(4326)
    to_local = pyproj.Transformer.from_crs(wgs84, aeqd, always_xy=True).transform
    
    geom_local = shp_transform(to_local, geom)
    point_local = shp_transform(to_local, Point(pt_lon, pt_lat))
    
    return point_local.distance(geom_local)


def fetch_building_osm(lat, lon, buffer_m=120.0, timeout_s=25):
    """
    Query building footprints from OSM Overpass API
    
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
            'method': str (selection method) or None,
            'error': str or None
        }
    """
    # 与原始代码完全相同的端点列表
    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter", 
        "https://overpass.openstreetmap.ru/api/interpreter",
        "https://overpass.nchc.org.tw/api/interpreter",
    ]

    # 与原始代码完全相同的buffer计算
    buffer_deg = max(buffer_m, 80.0) / 111000.0
    headers = {"User-Agent": OVERLAP_UA}
    
    # 与原始代码完全相同的查询
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
    
    # 与原始代码完全相同的重试逻辑
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

                # 与原始代码完全相同的元素处理逻辑
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
                            for m in members:
                                if m.get('role') == 'outer' and 'geometry' in m:
                                    gmem = m
                                    break
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

                # 与原始代码完全相同的选择逻辑
                contains = [b for b in buildings if b['contains_query']]
                if contains:
                    selected = max(contains, key=lambda b: b['area_m2'])
                    method = "contains_query_point"
                else:
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

    # 与原始代码完全相同的错误返回
    return {
        'success': False,
        'selected': None,
        'n_buildings': 0,
        'error': f'Overpass failed: {last_err or "unknown"}'
    }


from shapely.ops import transform as shp_transform
from shapely.geometry import Point, Polygon, MultiPolygon
import pyproj

def _geodesic_area_m2(geom):
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
    """AEQD 局部投影量距，避免经纬度直线误差"""
    aeqd = pyproj.CRS.from_proj4(f"+proj=aeqd +lat_0={pt_lat} +lon_0={pt_lon} +datum=WGS84 +units=m +no_defs")
    wgs84 = pyproj.CRS.from_epsg(4326)
    to_local = pyproj.Transformer.from_crs(wgs84, aeqd, always_xy=True).transform
    geom_local = shp_transform(to_local, geom)
    point_local = shp_transform(to_local, Point(pt_lon, pt_lat))
    return point_local.distance(geom_local)

def validate_and_relocate_building(
    lat: float,
    lon: float,
    aoi_radius_user: float,
    aoi_radius_ref: float = 500.0,
    # 新默认：更宽松、更通用（全国适用）
    accept_within_user_phase1: float = 1.2,   # Phase1 500m 候选若距离 <= 1.2×AOI，则接受
    accept_within_user_phase2: float = 0.9,   # Phase2 用户半径内最近若距离 <= 0.9×AOI，则接受
    user_search_expand: float = 1.5,          # Phase2 搜索半径放大倍数
    user_search_floor_m: float = 150.0,       # Phase2 搜索半径下限（m）
    micro_shift_m: float = 30.0,              # “微小位移”阈值（仅做标记/日志）
    timeout_s: int = 30,
):
    """
    更宽松、以“包含点”为优先的重定位策略（全美通用）：
      1) 在参考半径（默认500m）里搜 OSM：
         - 若存在 footprint 包含原点 → 直接接受（不看距离）。
         - 否则，若与原点距离 <= 1.2×AOI → 接受。
         - 否则进入 Phase 2。
      2) 在用户 AOI 附近扩大半径再搜（max(1.5×AOI, 150m)）：
         - 若存在 footprint 包含原点 → 接受。
         - 否则，最近的若距离 <= 0.9×AOI → 接受。
         - 否则 → 重定位到 Phase1 选中建筑的质心。
    返回值增加:
      - relocation_distance_m
      - micro_shift_ok（< micro_shift_m）
    """
    # --- Phase 1: 500m 参考范围 ---
    r500 = fetch_building_osm(lat, lon, buffer_m=aoi_radius_ref * 1.1, timeout_s=timeout_s)
    best = None
    reason = "no_relocation_needed"
    new_lat, new_lon = lat, lon
    relocation_distance_m = 0.0

    def _dist(sel, lon0, lat0):
        try:
            d = float(sel.get("distance_m", float("nan")))
            if not math.isfinite(d):
                raise ValueError
            return d
        except Exception:
            return distance_m_local(sel["geometry"], lon0, lat0)

    if r500.get("success") and r500.get("selected"):
        # 1) 若任何 footprint 包含原点，优先接受最大面积的那个
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
                # --- Phase 2: 用户半径加大搜 ---
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
                            # 两边都太远 → 重定位到 Phase1 的候选
                            best = sel
                            c = sel["geometry"].centroid
                            new_lat, new_lon = c.y, c.x
                            relocation_distance_m = d500
                            reason = "relocated_to_ref_centroid"
                else:
                    # 用户范围里没找到 → 重定位到 Phase1 的候选
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
    """创建备用建筑几何"""
    def to_3857(x, y):
        t = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform
        return t(x, y)
    
    center_3857 = to_3857(center_lon, center_lat)
    corrected_building = Point(center_3857).buffer(building_buffer_m, resolution=32)
    
    # 转回4326用于显示
    transformer = pyproj.Transformer.from_crs('EPSG:3857', 'EPSG:4326', always_xy=True)
    corrected_building_4326 = shp_transform(transformer.transform, corrected_building)
    
    return corrected_building, corrected_building_4326


if __name__ == "__main__":
    # 测试代码
    test_lat, test_lon = 40.7128, -74.0060  
    print("Testing OSM building fetch...")
    result = fetch_building_osm(test_lat, test_lon, buffer_m=100.0)
    print(f"Success: {result['success']}")
    if result['success']:
        print(f"Buildings found: {result['n_buildings']}")
        print(f"Selected building area: {result['selected']['area_m2']:.2f} m²")