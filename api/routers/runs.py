# api/routers/runs.py
from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, field_validator
import re

from ..services import pipeline_runner as pr
from api.services.utils_cache import (
    build_predicted_run_id, find_existing_run, slugify_address,
    find_existing_run_smart, _parse_radius_from_runid   
)
from api.settings import OUTPUTS_DIR

# Try to import geocoding tools; if they fail (e.g., missing API key), disable gracefully
try:
    from elevation.geocode import resolve_location_from_user_input, reverse_geocode
except Exception:  
    resolve_location_from_user_input = None 
    reverse_geocode = None 

router = APIRouter()

class CreateRunRequest(BaseModel):
    # User must provide either: an address OR latitude/longitude
    address: Optional[str] = Field(None, description="Address (optional)")
    lat: Optional[float] = Field(None, description="Latitude (optional)")
    lon: Optional[float] = Field(None, description="Longitude (optional)")

    # Other parameters (match your frontend exactly)
    aoi_radius_m: Optional[float] = Field(500.0, description="Analysis radius in meters")
    output_format: Optional[str] = Field("csv", description="Output format: csv, parquet, or both")
    table_format: Optional[str] = Field(None, description="Legacy field for backward compatibility (csv/parquet/both)")
    verbose: Optional[bool] = True
    generate_narrative: Optional[bool] = True
    save_3d: Optional[bool] = True

    @field_validator("output_format", "table_format")
    @classmethod
    def validate_format(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v_lower = v.lower()
        if v_lower not in {"csv", "parquet", "both"}:
            raise ValueError("output_format must be one of: csv, parquet, or both")
        return v_lower


class RunStatus(BaseModel):
    run_id: str
    status: str
    message: Optional[str] = None
    outputs_dir: Optional[str] = None


# ------------------ Helper Functions ------------------
# ------------------ Helper Functions ------------------
def parse_location_input(req: CreateRunRequest) -> Dict[str, Any]:
    """
    Standardize user input: always geocode first to get a consistent label.
    All address strings (regardless of shorthand or case) are normalized.
    """
    # 1. Prefer latitude/longitude if provided
    if req.lat is not None and req.lon is not None:
        lat_f = float(req.lat)
        lon_f = float(req.lon)
        label = None
        if reverse_geocode is not None:
            try:
                label = reverse_geocode(lat_f, lon_f)
            except Exception:
                pass
        # Fallback to coordinates as label if no address found
        label = label or f"{lat_f:.6f}, {lon_f:.6f}"
        return {"lat": lat_f, "lon": lon_f, "label": label}

    # 2. Address input → force geocoding
    if req.address and req.address.strip():
        raw_addr = req.address.strip()

        # Convert to Title Case to reduce case sensitivity issues
        raw_addr = raw_addr.lower().title()

        # Use unified geocoder (Google first, fallback to OSM)
        if resolve_location_from_user_input is not None:
            lat, lon, label = None, None, None
            try:
                lat, lon, label = resolve_location_from_user_input(raw_addr)
            except Exception:
                pass

            # Always generate a stable label even if geocoding partially fails
            if lat is not None and lon is not None:
                canonical_label = (label or raw_addr).strip()
                # Clean up commas and spacing
                canonical_label = re.sub(r"\s*,\s*", ", ", canonical_label)
                return {"lat": float(lat), "lon": float(lon), "label": canonical_label}

        # If geocoding failed → return original address as label
        return {"lat": None, "lon": None, "label": raw_addr}

    # No valid input
    raise HTTPException(status_code=400, detail="Please provide either an address or latitude/longitude")



# ------------------ API Routes ------------------
@router.post("", response_model=RunStatus)
def create_run_endpoint(body: CreateRunRequest):
    """
    Create a new analysis run.
    Returns immediately with run_id and status (could be 'cached' if result exists).
    """
    coords = parse_location_input(body)
    payload = body.model_dump()

    # Backward compatibility: use table_format if output_format is missing
    if not payload.get("output_format") and payload.get("table_format"):
        payload["output_format"] = payload["table_format"]

    # Inject geocoded coordinates
    if coords["lat"] is not None and coords["lon"] is not None:
        payload.update({"lat": coords["lat"], "lon": coords["lon"]})

    # Save normalized address and original input
    if coords.get("label"):
        payload["address"] = coords["label"]
        if body.address and body.address.strip():
            payload["input_address"] = body.address.strip()
        payload["location_text"] = coords["label"]

    # Create the run (with enhanced idempotency/caching)
    run_id = pr.create_run(payload)
    
    # Check status immediately — return 'done' if cached
    status_data = pr.get_run(run_id)
    
    return RunStatus(
        run_id=run_id,
        status=status_data["status"],
        message=status_data.get("message"),
        outputs_dir=status_data.get("outputs_dir")
    )



@router.get("/{run_id}")
def get_run_status(run_id: str):
    """Get current status of a run (queued, running, done, error, cached)."""
    data = pr.get_run(run_id)
    return JSONResponse(data)



@router.get("/{run_id}/files")
def list_run_files(run_id: str):
    """List all output files generated by this run (relative paths)."""
    return {"files": pr.list_files(run_id)}


@router.get("/{run_id}/manifest")
def get_run_manifest(run_id: str):
    """
    Get metadata about outputs: files, figures, tables.
    Supports both UUID-based runs and slug-based cached runs.
    """
    # First: try standard UUID-based manifest
    m = pr.read_manifest_public(run_id)
    if m:
        return JSONResponse(m)

    # Fallback: treat run_id as a slug (e.g., "123-main-st-500m") and search in outputs
    from api.services.utils_cache import find_existing_run
    from api.settings import OUTPUTS_DIR
    hit = find_existing_run(str(OUTPUTS_DIR), run_id)
    if hit:
        outdir, man, _ = hit
        try:
            import json, pathlib
            j = json.loads(pathlib.Path(man).read_text())
            return JSONResponse(j)
        except Exception:
            pass

    # Handle in-progress or queued jobs
    state = pr.get_run(run_id)
    if state.get("status") in {"queued", "running"}:
        return JSONResponse({
            "status": state["status"],
            "files": [], "figs": {}, "tables": {},
            "error": state.get("error"),
            "message": state.get("message"),
        })
    
    raise HTTPException(status_code=404, detail="Manifest not found")



# Download a specific file from a run
@router.get("/{run_id}/download")
def download_file(run_id: str, filename: str = Query(...)):
    """
    Download a file from a run.
    Supports:
      - UUID-based runs
      - Slug-based cached runs (e.g., address_radius)
    """
    # 1. Try UUID-based file lookup
    try:
        path = pr.get_file_path(run_id, filename)
        return send_file_with_correct_type(path, filename)
    except FileNotFoundError:
        pass

    # 2. Try smart lookup using slug (address + radius)
    smart = find_existing_run_smart(
        str(OUTPUTS_DIR), run_id, None, None, 
        _parse_radius_from_runid(run_id) or None
    )
    if smart:
        outdir, _, _, _ = smart
        import os
        abs_path = os.path.join(outdir, filename)
        
        # If exact path doesn't exist, search recursively for matching filename
        if not os.path.exists(abs_path):
            base = os.path.basename(filename)
            found = None
            for root, _, files in os.walk(outdir):
                for f in files:
                    if f == base:
                        found = os.path.join(root, f)
                        break
                if found: 
                    break
            if not found:
                raise HTTPException(status_code=404, detail=f"File not found: {filename}")
            abs_path = found
        
        return send_file_with_correct_type(abs_path, os.path.basename(abs_path))

    raise HTTPException(status_code=404, detail=f"File not found: {filename}")



# Helper: auto-detect file type and set correct MIME + download behavior
def send_file_with_correct_type(path: str, filename: str):
    """Return FileResponse with proper content type and inline/download behavior."""
    if path.endswith((".html", ".htm")):
        return FileResponse(path, media_type="text/html",
                            headers={"Content-Disposition": "inline"})
    if path.endswith(".png"):
        return FileResponse(path, media_type="image/png", filename=filename)
    if path.endswith((".jpg", ".jpeg")):
        return FileResponse(path, media_type="image/jpeg", filename=filename)
    if path.endswith(".csv"):
        return FileResponse(path, media_type="text/csv", filename=filename)
    if path.endswith(".parquet"):
        return FileResponse(path, media_type="application/octet-stream", filename=filename)
    if path.endswith((".tif", ".tiff")):
        return FileResponse(path, media_type="image/tiff", filename=filename)
    return FileResponse(path, filename=filename)



@router.get("/{run_id}/stderr", response_class=PlainTextResponse)
def get_error_log(run_id: str):
    """Return error message/log only if the run failed."""
    data = pr.get_run(run_id)
    if data.get("status") != "error":
        return ""
    return data.get("message", "Unknown error")