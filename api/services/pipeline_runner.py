# api/services/pipeline_runner.py
import os
import sys
import uuid
import json
import traceback
from threading import Thread
from typing import Dict, Any, Optional

from api.services.utils_cache import (
    build_predicted_run_id, find_existing_run, slugify_address, UUID_RE,
    find_existing_run_smart, validate_radius_match, _parse_radius_from_runid
)
from ..settings import OUTPUTS_DIR, PROJECT_ROOT

# === Import elevation package (add project root to sys.path) ===
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Pipeline entry point: must export PipelineConfig and run_pipeline
from elevation.run_final_pipeline import PipelineConfig, run_pipeline  # noqa

# In-memory run tracking table (cleared on reload in dev mode)
RUNS: Dict[str, Dict[str, Any]] = {}


# ----------------- Utilities -----------------
# api/services/pipeline_runner.py

def _payload_to_config(payload: Dict[str, Any], run_id: str) -> PipelineConfig:
    """
    Convert frontend payload to PipelineConfig.
    Note: Output directory is initially bound to outputs/<run_id>,
    but will be overridden in _run_target using cfg.output_dir = RUNS[run_id]["outputs_dir"].
    """
    import os
    from api.settings import OUTPUTS_DIR

    cfg = PipelineConfig()

    # Address / Latitude-Longitude
    if payload.get("address"):
        cfg.address = str(payload["address"])
    if "lat" in payload and "lon" in payload:
        try:
            cfg.lat = float(payload["lat"])
            cfg.lon = float(payload["lon"])
        except Exception:
            pass

    # AOI radius
    if "aoi_radius_m" in payload:
        try:
            cfg.aoi_radius_m = float(payload["aoi_radius_m"])
            cfg.dem_for_user_aoi = True
        except Exception:
            pass

    # Output format
    tf = payload.get("table_format") or payload.get("output_format")
    cfg.output_format = tf if tf in {"csv", "parquet", "both"} else "csv"

    # Other options
    if "generate_narrative" in payload:
        cfg.generate_narrative = bool(payload["generate_narrative"])
    if "verbose" in payload:
        cfg.verbose = bool(payload["verbose"])
    if "save_3d" in payload:
        cfg.save_3d = bool(payload["save_3d"])

    # Initial binding (will be overridden in _run_target to ensure consistency)
    cfg.output_dir = os.path.join(str(OUTPUTS_DIR), run_id)

    return cfg


def _outputs_dir_for_runid(run_id: str) -> str:
    """Get output directory path for a given run_id."""
    return os.path.join(str(OUTPUTS_DIR), run_id)


def _manifest_path(outputs_dir: Optional[str]) -> Optional[str]:
    """Get manifest.json path if it exists in the given outputs_dir."""
    if not outputs_dir:
        return None
    p = os.path.join(outputs_dir, "manifest.json")
    return p if os.path.exists(p) else None


# ----------------- Background Thread -----------------
# api/services/pipeline_runner.py

def _run_target(run_id: str, payload: Dict[str, Any]) -> None:
    """Background thread to run the pipeline - prioritizes idempotency and cache reuse."""
    import os, json, traceback
    from api.settings import OUTPUTS_DIR

    # Bind output directory for this run (pre-created by create_run)
    outdir = RUNS[run_id]["outputs_dir"]
    manifest_path = os.path.join(outdir, "manifest.json")

    # Check again at thread start to avoid duplicate computation
    if os.path.exists(manifest_path):
        print(f"REUSE [THREAD CACHE HIT] {run_id} manifest exists; reuse immediately.")
        RUNS[run_id].update({
            "status": "done",
            "message": "Results loaded from existing cache",
            "outputs_dir": outdir,
            "manifest_path": manifest_path,
            "result": None,
        })
        return

    # Mark as running (optional)
    RUNS[run_id]["status"] = "running"

    try:
        # Construct config from payload
        cfg = _payload_to_config(payload, run_id)

        # Critical: Pass the final directory directly to pipeline (no subfolder appending)
        cfg.output_dir = RUNS[run_id]["outputs_dir"]

        # Execute the pipeline
        res = run_pipeline(cfg)

        RUNS[run_id].update({
            "status": "done",
            "message": "Analysis completed successfully",
            "result": res,
            "outputs_dir": res.get("outputs_dir") or outdir,
            "manifest_path": os.path.join(outdir, "manifest.json"),
        })

    except Exception as e:
        RUNS[run_id].update({
            "status": "error",
            "message": f"{e}\n{traceback.format_exc()}",
        })


def create_run(payload: Dict[str, Any]) -> str:
    """
    Create a new run with enhanced idempotency:
    - Reuse within seconds for same address + radius
    - Separate directories for different radii
    """
    import os
    import json
    from threading import Thread
    from pathlib import Path

    from api.services.utils_cache import (
        build_predicted_run_id,      # Uses short address (pre-comma) + radius
        find_existing_run_smart,     # Smart match: exact slug/prefix/lat-lon tolerance
        find_existing_run_by_address,
        validate_radius_match,
    )
    from api.settings import OUTPUTS_DIR

    # Extract parameters
    address = (payload.get("location_text") or payload.get("address") or "").strip()
    lat = payload.get("lat")
    lon = payload.get("lon")
    radius_m = payload.get("aoi_radius_m") or 500.0

    print("=" * 80)
    print("POWERFUL CACHE LOOKUP:")
    print(f"  address: {address}")
    print(f"  lat/lon: {lat}, {lon}")
    print(f"  radius: {radius_m}m")

    # Normalize run_id: short address + radius
    canonical_key = address
    run_id = build_predicted_run_id(canonical_key, lat or 0.0, lon or 0.0, radius_m)
    outdir = os.path.join(str(OUTPUTS_DIR), run_id)
    manifest_path = os.path.join(outdir, "manifest.json")

    # Method 0: Exact directory already has complete result - reuse instantly
    if os.path.exists(manifest_path):
        print(f"CACHE HIT {run_id} -> manifest exists, reuse")
        RUNS[run_id] = {
            "status": "done",
            "message": "loaded from cache",
            "result": None,
            "outputs_dir": outdir,
            "manifest_path": manifest_path,
            "params": payload,
        }
        return run_id

    # Method 1: Smart lookup (exact/prefix/lat-lon tolerance + radius match)
    smart = find_existing_run_smart(str(OUTPUTS_DIR), run_id, lat, lon, radius_m)
    if smart:
        outdir_found, manifest_found, actual_uuid, slug_dir = smart
        print(f"EXACT/PREFIX/COORD MATCH Found: {slug_dir}")
        RUNS[slug_dir] = {
            "status": "done",
            "message": "loaded from cache",
            "result": None,
            "outputs_dir": outdir_found,
            "manifest_path": manifest_found,
            "params": payload,
        }
        return slug_dir

    # Method 2: Address similarity match (strict radius equality)
    if address:
        similar_run = find_existing_run_by_address(str(OUTPUTS_DIR), address, radius_m, 0.8)
        if similar_run:
            outdir_found, manifest_found, found_run_id = similar_run
            print(f"SIMILAR ADDRESS Found: {found_run_id}")
            RUNS[found_run_id] = {
                "status": "done",
                "message": "loaded from cache",
                "result": None,
                "outputs_dir": outdir_found,
                "manifest_path": manifest_found,
                "params": payload,
            }
            return found_run_id

    # Method 3: Coordinate fallback (loose tolerance), radius must still match
    if lat is not None and lon is not None:
        root = Path(OUTPUTS_DIR)
        coord_eps = 5e-3  # ~500m (last resort; actual analysis uses submitted radius)
        for mp in root.rglob("manifest.json"):
            try:
                with open(mp, "r") as f:
                    m = json.load(f)
                man_lat = m.get("lat"); man_lon = m.get("lon"); man_r = m.get("aoi_radius_m")
                if man_lat is None or man_lon is None or not validate_radius_match(radius_m, man_r):
                    continue
                if abs(float(man_lat) - float(lat)) <= coord_eps and abs(float(man_lon) - float(lon)) <= coord_eps:
                    outdir_found = str(mp.parent)
                    found_run_id = mp.parent.name
                    print(f"COORDINATE FALLBACK Found: {found_run_id}")
                    RUNS[found_run_id] = {
                        "status": "done",
                        "message": "loaded from cache",
                        "result": None,
                        "outputs_dir": outdir_found,
                        "manifest_path": str(mp),
                        "params": payload,
                    }
                    return found_run_id
            except Exception:
                continue

    # Cache miss: Create new run (directory = outputs/<run_id>/)
    print(f"CACHE MISS Creating new run for {run_id}")
    os.makedirs(outdir, exist_ok=True)

    RUNS[run_id] = {
        "status": "queued",
        "message": None,
        "result": None,
        "outputs_dir": outdir,
        "manifest_path": None,
        "params": payload,
    }

    # Run in background; pipeline must use cfg.output_dir as final output dir
    t = Thread(target=_run_target, args=(run_id, payload), daemon=True)
    t.start()

    return run_id


def _recover_if_possible(run_id: str) -> Optional[Dict[str, Any]]:
    """
    Recover run state from disk when not in RUNS (e.g., after server restart):
    - Supports outputs/<uuid>/**/manifest.json (recursive search)
    - Sets outputs_dir to the actual address_radius subdirectory containing manifest
    """
    root = _outputs_dir_for_runid(run_id)
    if not os.path.isdir(root):
        return None

    manifest_path = None
    for dirpath, _, files in os.walk(root):
        if "manifest.json" in files:
            manifest_path = os.path.join(dirpath, "manifest.json")
            break
    if not manifest_path:
        return None

    try:
        with open(manifest_path, "r") as f:
            _ = json.load(f)  # Validate file integrity
    except Exception:
        return None

    return {
        "run_id": run_id,
        "status": "done",
        "message": "recovered from disk",
        "outputs_dir": os.path.dirname(manifest_path),
    }


def get_run(run_id: str) -> Dict[str, Any]:
    """Get run status; supports smart lookup if run_id is not a UUID."""
    if not UUID_RE.match(run_id):
        # Use smart lookup instead of strict match
        smart = find_existing_run_smart(str(OUTPUTS_DIR), run_id, None, None, _parse_radius_from_runid(run_id) or None)
        if smart:
            outdir, manifest_path, actual_uuid, _ = smart
            rmem = RUNS.get(actual_uuid) or _recover_if_possible(actual_uuid) or {}
            if not rmem:
                return {
                    "run_id": actual_uuid,
                    "status": "done",
                    "message": "loaded from cache",
                    "outputs_dir": outdir,
                }
            return {
                "run_id": actual_uuid,
                "status": rmem.get("status", "done"),
                "message": rmem.get("message", "loaded from cache"),
                "outputs_dir": rmem.get("outputs_dir", outdir),
            }
    # Original UUID logic
    r = RUNS.get(run_id)
    if not r:
        rec = _recover_if_possible(run_id)
        if rec:
            return rec
        return {
            "run_id": run_id,
            "status": "error",
            "message": "run_id not found (probably server restarted). Please submit again.",
            "outputs_dir": None,
        }
    return {
        "run_id": run_id,
        "status": r["status"],
        "message": r.get("message"),
        "outputs_dir": r.get("outputs_dir"),
    }


def list_files(run_id: str):
    """List all output files for a run (recovers from disk if not in memory)."""
    r = RUNS.get(run_id)
    outdir = r["outputs_dir"] if r and r.get("outputs_dir") else _outputs_dir_for_runid(run_id)
    if not os.path.isdir(outdir):
        return []
    paths = []
    for root, _, files in os.walk(outdir):
        for f in files:
            p = os.path.join(root, f)
            # Normalize: paths relative to outdir
            paths.append(os.path.relpath(p, outdir))
    return sorted(paths)


def read_manifest(run_id: str) -> Optional[Dict[str, Any]]:
    """Read raw manifest.json from the run's output directory."""
    r = RUNS.get(run_id)
    outdir = r["outputs_dir"] if r and r.get("outputs_dir") else _outputs_dir_for_runid(run_id)
    mp = os.path.join(outdir, "manifest.json")
    if not os.path.exists(mp):
        return None
    try:
        with open(mp, "r") as f:
            return json.load(f)
    except Exception:
        return None


def get_file_path(run_id: str, filename: str) -> str:
    """
    Resolve full file path by run_id.
    Accepts relative path or filename; searches recursively if not found at top level.
    """
    r = RUNS.get(run_id)
    outdir = r["outputs_dir"] if r and r.get("outputs_dir") else _outputs_dir_for_runid(run_id)

    # Try direct join first
    abs_path = os.path.join(outdir, filename)
    if os.path.exists(abs_path):
        return abs_path

    # Then search recursively for matching filename
    base = os.path.basename(filename)
    for root, _, files in os.walk(outdir):
        for f in files:
            if f == base:
                return os.path.join(root, f)

    raise FileNotFoundError(filename)


# ---------- Make manifest frontend-friendly (all paths relative) ----------
def _to_rel(outdir: str, p: Optional[str]) -> Optional[str]:
    """Convert absolute path p to relative to outdir; fallback to basename if outside."""
    if not p:
        return p
    try:
        ap = os.path.abspath(p)
        ao = os.path.abspath(outdir)
        rel = os.path.relpath(ap, ao)
        if rel.startswith(".."):
            return os.path.basename(ap)
        return rel
    except Exception:
        return os.path.basename(p)


def read_manifest_public(run_id: str) -> Optional[Dict[str, Any]]:
    """
    Read outputs/<run_id>/manifest.json and normalize paths:
    - Convert figs/tables paths to relative to manifest directory
    - Include full file list relative to output directory
    """
    # 1) Prefer in-memory outputs_dir (set correctly in _run_target)
    r = RUNS.get(run_id)
    outdir = r["outputs_dir"] if r and r.get("outputs_dir") else _outputs_dir_for_runid(run_id)

    # 2) Look for manifest.json directly in outdir
    mp = os.path.join(outdir, "manifest.json")
    if not os.path.exists(mp):
        # 3) If not found, search recursively under run_id root
        root = _outputs_dir_for_runid(run_id)
        found = None
        if os.path.isdir(root):
            for dirpath, _, files in os.walk(root):
                if "manifest.json" in files:
                    found = os.path.join(dirpath, "manifest.json")
                    break
        if not found:
            return None
        mp = found
        outdir = os.path.dirname(mp)  # Use manifest's directory as base

    # 4) Load manifest
    try:
        with open(mp, "r") as f:
            raw = json.load(f)
    except Exception:
        return None

    # 5) Normalize figs/tables paths relative to outdir
    figs = {k: _to_rel(outdir, v) for k, v in (raw.get("figs") or {}).items()}
    tables = {k: _to_rel(outdir, v) for k, v in (raw.get("tables") or {}).items()}

    # 6) List all files relative to outdir
    files = []
    for dirpath, _, fs in os.walk(outdir):
        for fname in fs:
            files.append(os.path.relpath(os.path.join(dirpath, fname), outdir))
    files.sort()

    return {
        "run_id": run_id,
        "dataset": raw.get("dataset"),
        "lat": raw.get("lat"),
        "lon": raw.get("lon"),
        "aoi_radius_m": raw.get("aoi_radius_m"),
        "address": raw.get("address") or raw.get("location_text"),
        "house_ground_m": raw.get("house_ground_m"),
        "percentile_rank": raw.get("percentile_rank"),
        "files": files,
        "figs": figs,
        "tables": tables,
    }