# api/settings.py
from pathlib import Path

API_DIR      = Path(__file__).resolve().parent  
PROJECT_ROOT = API_DIR.parent                         
FRONTEND_DIR = PROJECT_ROOT / "frontend"
FRONTEND_FILE = FRONTEND_DIR / "index.html"  
OUTPUTS_DIR  = PROJECT_ROOT / "outputs"
