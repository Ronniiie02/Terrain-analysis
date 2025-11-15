# api/app.py
import matplotlib
matplotlib.use("Agg")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse  
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
load_dotenv()
from .settings import OUTPUTS_DIR, FRONTEND_DIR  

app = FastAPI(title="Elevation API", version="0.1.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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

if FRONTEND_DIR.exists():
    app.mount("/web", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="web")
    print(f"[INFO] Frontend mounted at /web from {FRONTEND_DIR}")
else:
    print(f"[WARN] FRONTEND DIR NOT FOUND: {FRONTEND_DIR}")

@app.get("/frontend.html")
def serve_frontend():
    return RedirectResponse(url="/web/")

# ✅ 根路径也重定向到 /web/
@app.get("/")
def index():
    return RedirectResponse(url="/web/")

from .routers import runs  
app.include_router(runs.router, prefix="/runs", tags=["runs"])
