# -*- coding: utf-8 -*-
"""
Geocoding helpers (robust)
- Prefer Google if GOOGLE_API_KEY is present
- Fallback to OSM Nominatim with proper headers & country bias
"""

from __future__ import annotations
from typing import Optional, Tuple
import os
import requests

__all__ = [
    "GOOGLE_API_KEY",
    "GEOCODE_URL",
    "COUNTRY_BIAS",
    "geocode_address",
    "nominatim_geocode",
    "resolve_location_from_user_input",
]

# ❶ 仅环境变量，不要硬编码默认 key
GOOGLE_API_KEY: Optional[str] = os.getenv("GOOGLE_API_KEY", "AIzaSyBV79lYlTdh2ev6jOOE6q-aZGVWdQVJv-8")
GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
COUNTRY_BIAS = "US" 

UA = {"User-Agent": "terrain-risk/2.1 (contact: yao3@uchicago.edu)"}

def _is_number(x) -> bool:
    try:
        float(x)
        return True
    except Exception:
        return False

def _parse_latlon_string(s: str) -> Optional[Tuple[float, float]]:
    if not isinstance(s, str):
        return None
    parts = [p.strip() for p in s.split(",")]
    if len(parts) != 2:
        return None
    if _is_number(parts[0]) and _is_number(parts[1]):
        return float(parts[0]), float(parts[1])
    return None

def geocode_address(address: str, api_key: Optional[str], country_bias: Optional[str] = None):
    if not api_key:
        print("[GEOCODE:GOOGLE] skipped: no GOOGLE_API_KEY")
        return None, None, None
    params = {"address": address, "key": api_key}
    if country_bias:
        params["components"] = f"country:{country_bias}"
    try:
        r = requests.get(GEOCODE_URL, params=params, headers=UA, timeout=15)
        ct = r.headers.get("content-type","")
        data = r.json() if "json" in ct else {}
        api_status = data.get("status")
        if r.status_code != 200 or api_status != "OK":
            print(f"[GEOCODE:GOOGLE] http={r.status_code} api_status={api_status} err={data.get('error_message')} resp={str(data)[:200]}")
            return None, None, None
        res0 = data["results"][0]
        loc = res0["geometry"]["location"]
        return float(loc["lat"]), float(loc["lng"]), res0.get("formatted_address")
    except Exception as e:
        print(f"[GEOCODE:GOOGLE] EXCEPTION {type(e).__name__}: {e}")
        return None, None, None


def nominatim_geocode(address: str, country_bias: Optional[str] = None):
    base = "https://nominatim.openstreetmap.org/search"
    params = {"format": "json", "q": address, "limit": 1, "addressdetails": 0}
    if (country_bias or "").upper() == "US":
        params["countrycodes"] = "us"
    try:
        r = requests.get(base, params=params, headers=UA, timeout=15)
        if r.status_code != 200:
            print(f"[GEOCODE:NOMINATIM] http={r.status_code} body={r.text[:200]}")
            return None, None, None
        js = r.json()
        if not js:
            print("[GEOCODE:NOMINATIM] 200 but empty result")
            return None, None, None
        j0 = js[0]
        return float(j0["lat"]), float(j0["lon"]), j0.get("display_name")
    except Exception as e:
        print(f"[GEOCODE:NOMINATIM] EXCEPTION {type(e).__name__}: {e}")
        return None, None, None

def resolve_location_from_user_input(user_text: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """先解析 'lat,lon'，再试 Google（如有 KEY），最后 Nominatim 兜底。"""
    parsed = _parse_latlon_string(user_text)
    if parsed:
        return parsed[0], parsed[1], f"{parsed[0]:.6f}, {parsed[1]:.6f}"

    # 先 Google（如果配置了 KEY）
    lat, lon, faddr = geocode_address(user_text, GOOGLE_API_KEY, country_bias=COUNTRY_BIAS)
    if lat is not None and lon is not None:
        return lat, lon, (faddr or user_text)

    # 再 OSM 兜底
    lat, lon, faddr = nominatim_geocode(user_text, country_bias=COUNTRY_BIAS)
    if lat is not None and lon is not None:
        return lat, lon, (faddr or user_text)

    return None, None, None
