# api/settings.py
from pathlib import Path

# 目录
API_DIR      = Path(__file__).resolve().parent          # .../api
PROJECT_ROOT = API_DIR.parent                            # .../Uchicago-elevation
FRONTEND_DIR = PROJECT_ROOT / "frontend"
FRONTEND_FILE = FRONTEND_DIR / "frontend.html"
OUTPUTS_DIR  = PROJECT_ROOT / "outputs"

# 其他可扩展的全局设置放这里（如日志级别、CORS 白名单等）
