from pathlib import Path
import json
import re
from typing import Optional, Tuple
from difflib import SequenceMatcher

# ========== Utilities ==========
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE
)

def slugify_address(address: str) -> str:
    """Convert address to a safe, compact slug for directory naming."""
    s = re.sub(r"[^A-Za-z0-9]+", "_", address.strip())
    return re.sub(r"_+", "_", s).strip("_")[:80] or "address_unknown"

def build_predicted_run_id(address: str, lat: float, lon: float, radius_m: float) -> str:
    """Generate a predictable run_id using short address + radius."""
    short = short_address_for_dir(address)
    if short:
        slug = slugify_address(short)
    else:
        slug = f"{lat:.5f}_{lon:.5f}"

    radius_label = f"{int(radius_m)}m" if float(radius_m).is_integer() else f"{radius_m:g}m"
    return f"{slug}_{radius_label}"

def _parse_radius_from_runid(run_id: str) -> Optional[float]:
    """Extract radius (in meters) from run_id string (e.g., '_500m')."""
    m = re.search(r'_(\d+(?:\.\d+)?)m$', run_id, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None

def validate_radius_match(req_radius: Optional[float], manifest_radius: Optional[float]) -> bool:
    """Check if two radii are effectively equal (within floating-point tolerance)."""
    if req_radius is None or manifest_radius is None:
        return True  # Skip radius validation if either is missing
    
    return abs(float(req_radius) - float(manifest_radius)) < 1e-6


def short_address_for_dir(address: str) -> str:
    """
    Extract short address for directory naming: take part before first comma.
    Example: "1610 Millstone Rd, Sag Harbor, NY 11963, USA" -> "1610 Millstone Rd"
    Returns empty string if input is empty.
    """
    if not address:
        return ""
    head = address.split(",")[0].strip()
    return head or address.strip()

def find_existing_run(outputs_root: str, run_id: str):
    """Find existing run by exact slug+radius under UUID subdirectories."""
    root = Path(outputs_root)
    req_radius = _parse_radius_from_runid(run_id)

    # 1) Exact match: uuid/*/<slug_radius> with manifest
    for path in root.glob(f"*/{run_id}"):
        man = path / "manifest.json"
        if man.exists():
            try:
                j = json.loads(man.read_text())
                man_r = float(j.get("aoi_radius_m")) if j.get("aoi_radius_m") is not None else None
                if req_radius is None or man_r is None or abs(man_r - req_radius) < 1e-6:
                    return str(path), str(man), path.parent.name
            except Exception:
                return str(path), str(man), path.parent.name

    # 2) Fallback: directory name exactly matches run_id
    for man in root.rglob("manifest.json"):
        try:
            if man.parent.name.lower() != run_id.lower():
                continue
            j = json.loads(man.read_text())
            man_r = float(j.get("aoi_radius_m")) if j.get("aoi_radius_m") is not None else None
            if req_radius is None or man_r is None or abs(man_r - req_radius) < 1e-6:
                return str(man.parent), str(man), man.parent.parent.name
        except Exception:
            continue
    return None


def find_existing_run_by_prefix(outputs_root: str, short_slug_with_radius: str):
    """Find run by prefix match on directory name (short address to full)."""
    root = Path(outputs_root)
    target = short_slug_with_radius.lower()
    for uuid_dir in root.iterdir():
        if not uuid_dir.is_dir():
            continue
        for sub in uuid_dir.iterdir():
            if not sub.is_dir():
                continue
            if sub.name.lower().startswith(target):
                man = sub / "manifest.json"
                if man.exists():
                    return str(sub), str(man), uuid_dir.name, sub.name
    return None


def find_existing_run_smart(outputs_root: str, run_id: str,
                            lat: Optional[float], lon: Optional[float],
                            radius_m: Optional[float],
                            coord_eps: float = 1.5e-3):   # ~167m tolerance
    """
    Smart lookup with multiple fallback strategies:
    1. Exact slug+radius
    2. Prefix match
    3. Coordinate proximity (with exact radius match)
    """
    # 1) Strict slug+radius hit
    hit = find_existing_run(outputs_root, run_id)
    if hit:
        outdir, manifest_path, uuid = hit
        return outdir, manifest_path, uuid, Path(outdir).name

    # 2) Directory name prefix match
    pref = find_existing_run_by_prefix(outputs_root, run_id)
    if pref:
        return pref

    # 3) Coordinate fallback (same radius + within tolerance)
    if lat is None or lon is None or radius_m is None:
        return None
    root = Path(outputs_root)
    for man in root.rglob("manifest.json"):
        try:
            j = json.loads(man.read_text())
            man_lat = j.get("lat"); man_lon = j.get("lon"); man_r = j.get("aoi_radius_m")
            if man_lat is None or man_lon is None or man_r is None:
                continue
            if abs(float(man_r) - float(radius_m)) > 1e-6:
                continue
            if abs(float(man_lat) - float(lat)) <= coord_eps and abs(float(man_lon) - float(lon)) <= coord_eps:
                outdir = str(man.parent); uuid = man.parent.parent.name; slug = man.parent.name
                return outdir, str(man), uuid, slug
        except Exception:
            continue
    return None



def normalize_address(address: str) -> str:
    """
    Normalize address string to reduce formatting variations.
    """
    if not address:
        return ""
    
    # Lowercase and strip
    normalized = address.lower().strip()
    
    # Remove punctuation and extra whitespace
    normalized = re.sub(r'[^\w\s]', ' ', normalized)
    normalized = re.sub(r'\s+', ' ', normalized)
    
    # Standardize common street suffixes
    replacements = {
        r'\bstreet\b': 'st',
        r'\broad\b': 'rd', 
        r'\bavenue\b': 'ave',
        r'\bdrive\b': 'dr',
        r'\bblvd\b': 'blvd',
        r'\bhighway\b': 'hwy',
        r'\blane\b': 'ln',
        r'\bcircle\b': 'cir',
        r'\bcourt\b': 'ct'
    }
    
    for pattern, replacement in replacements.items():
        normalized = re.sub(pattern, replacement, normalized)
    
    # Remove common suffixes (state, zip, country)
    normalized = re.sub(r',\s*(ny|new york|ca|california|us|usa)$', '', normalized)
    normalized = re.sub(r'\s*\d{5}(-\d{4})?$', '', normalized)
    
    return normalized.strip()

def address_similarity(addr1: str, addr2: str) -> float:
    """
    Compute similarity score between two addresses (0 to 1).
    """
    norm1 = normalize_address(addr1)
    norm2 = normalize_address(addr2)
    
    if norm1 == norm2:
        return 1.0
    
    return SequenceMatcher(None, norm1, norm2).ratio()

def find_existing_run_by_address(
    outputs_root: str, 
    address: str, 
    radius_m: float,
    similarity_threshold: float = 0.8
) -> Optional[Tuple[str, str, str]]:
    """
    Find existing run by address similarity.
    Returns: (outdir, manifest_path, run_id) or None
    """
    import json
    from pathlib import Path
    
    root = Path(outputs_root)
    target_addr_norm = normalize_address(address)
    
    for manifest_path in root.rglob("manifest.json"):
        try:
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)
            
            # Check radius match
            manifest_radius = manifest.get("aoi_radius_m")
            if manifest_radius is None or abs(float(manifest_radius) - float(radius_m)) > 1e-6:
                continue
            
            # Check address similarity
            manifest_addr = manifest.get("address") or manifest.get("location_text", "")
            similarity = address_similarity(manifest_addr, address)
            
            if similarity >= similarity_threshold:
                outdir = str(manifest_path.parent)
                run_id = manifest_path.parent.name
                return outdir, str(manifest_path), run_id
                
        except Exception:
            continue
    
    return None