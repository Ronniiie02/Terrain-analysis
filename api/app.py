# api/app.py
import matplotlib
matplotlib.use("Agg")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .settings import OUTPUTS_DIR, FRONTEND_DIR, FRONTEND_FILE

app = FastAPI(title="Elevation API", version="0.1.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # 开发期全开；生产建议收紧
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"ok": True}

@app.on_event("startup")
async def startup():
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[INFO] Outputs directory ensured: {OUTPUTS_DIR}")

# 静态前端
if FRONTEND_DIR.exists():
    app.mount("/web", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="web")
    print(f"[INFO] Frontend mounted at /web from {FRONTEND_DIR}")
else:
    print(f"[WARN] FRONTEND DIR NOT FOUND: {FRONTEND_DIR}")

@app.get("/frontend.html")
def serve_frontend():
    if not FRONTEND_FILE.exists():
        raise HTTPException(status_code=404, detail=f"frontend.html not found at {FRONTEND_FILE}")
    return FileResponse(FRONTEND_FILE)

@app.get("/")
def index():
    if not FRONTEND_FILE.exists():
        return {"msg": "frontend.html not found. Put it under <repo>/frontend/frontend.html"}
    return FileResponse(FRONTEND_FILE)

# !!! 非常关键：最后再引入 routers，避免初始化时的循环
from .routers import runs  # noqa: E402
app.include_router(runs.router, prefix="/runs", tags=["runs"])
