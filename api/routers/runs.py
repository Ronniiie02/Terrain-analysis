
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


# 尝试可选地址解析（没有也不报错）
try:
    # 新增：引入 reverse_geocode，用于 lat/lon -> 地址
    from elevation.geocode import resolve_location_from_user_input, reverse_geocode  # type: ignore
except Exception:  # 模块不存在时退化
    resolve_location_from_user_input = None  # type: ignore
    reverse_geocode = None  # type: ignore

router = APIRouter()

# ------------------ Pydantic 模型 ------------------
class CreateRunRequest(BaseModel):
    # 任选其一：address 或 lat/lon
    address: Optional[str] = Field(None, description="地址（可选）")
    lat: Optional[float] = Field(None, description="纬度（可选）")
    lon: Optional[float] = Field(None, description="经度（可选）")

    # 其它参数（与你的前端一致）
    aoi_radius_m: Optional[float] = Field(500.0, description="分析半径")
    output_format: Optional[str] = Field("csv", description="csv/parquet/both")
    table_format: Optional[str] = Field(None, description="兼容旧字段（csv/parquet/both）")
    verbose: Optional[bool] = True
    generate_narrative: Optional[bool] = True
    save_3d: Optional[bool] = True

    @field_validator("output_format", "table_format")
    @classmethod
    def _fmt_ok(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v2 = v.lower()
        if v2 not in {"csv", "parquet", "both"}:
            raise ValueError("output_format 只能是 csv/parquet/both")
        return v2


class RunStatus(BaseModel):
    run_id: str
    status: str
    message: Optional[str] = None
    outputs_dir: Optional[str] = None


# ------------------ 工具函数 ------------------
# ------------------ 工具函数 ------------------
def _pick_coords_or_address(req: CreateRunRequest) -> Dict[str, Any]:
    """
    统一解析用户输入：强制先地理编码，得到规范化 label。
    所有地址字符串（无论输入简写/大小写）都统一标准化。
    """
    # 1️⃣ 经纬度优先
    if req.lat is not None and req.lon is not None:
        lat_f = float(req.lat)
        lon_f = float(req.lon)
        label = None
        if reverse_geocode is not None:
            try:
                label = reverse_geocode(lat_f, lon_f)
            except Exception:
                pass
        label = label or f"{lat_f:.6f}, {lon_f:.6f}"
        return {"lat": lat_f, "lon": lon_f, "label": label}

    # 2️⃣ 地址输入 → 强制地理编码
    if req.address and req.address.strip():
        raw_addr = req.address.strip()

        # ✅ 强制小写转 Title Case，消除大小写差异
        raw_addr = raw_addr.lower().title()

        # ✅ 调用统一解析器（Google 优先，OSM 兜底）
        if resolve_location_from_user_input is not None:
            lat, lon, label = None, None, None
            try:
                lat, lon, label = resolve_location_from_user_input(raw_addr)
            except Exception:
                pass

            # ✅ 无论解析成功与否，都生成一个稳定 label
            if lat is not None and lon is not None:
                canonical_label = (label or raw_addr).strip()
                # 进一步标准化去空格、逗号
                canonical_label = re.sub(r"\s*,\s*", ", ", canonical_label)
                return {"lat": float(lat), "lon": float(lon), "label": canonical_label}

        # ❌ 没解析成功 → fallback
        return {"lat": None, "lon": None, "label": raw_addr}

    raise HTTPException(status_code=400, detail="请提供地址或经纬度")




# ------------------ 路由 ------------------
@router.post("", response_model=RunStatus)
def create_run_endpoint(body: CreateRunRequest):
    coords = _pick_coords_or_address(body)
    payload = body.model_dump()

    # 兼容处理
    if not payload.get("output_format") and payload.get("table_format"):
        payload["output_format"] = payload["table_format"]

    # 注入解析结果
    if coords["lat"] is not None and coords["lon"] is not None:
        payload.update({"lat": coords["lat"], "lon": coords["lon"]})

    if coords.get("label"):
        payload["address"] = coords["label"]
        if body.address and body.address.strip():
            payload["input_address"] = body.address.strip()
        payload["location_text"] = coords["label"]

    # 调用增强的幂等性检查
    run_id = pr.create_run(payload)
    
    # 立即检查状态，如果是缓存命中则返回done
    status_data = pr.get_run(run_id)
    
    return RunStatus(
        run_id=run_id,
        status=status_data["status"],
        message=status_data.get("message"),
        outputs_dir=status_data.get("outputs_dir")
    )





@router.get("/{run_id}")
def get_run_status(run_id: str):
    data = pr.get_run(run_id)
    return JSONResponse(data)



@router.get("/{run_id}/files")
def list_run_files(run_id: str):
    """列出该 run 的所有输出（相对路径）"""
    return {"files": pr.list_files(run_id)}


@router.get("/{run_id}/manifest")
def get_run_manifest(run_id: str):
    # 先走原逻辑（给 UUID 任务用）
    m = pr.read_manifest_public(run_id)
    if m:
        return JSONResponse(m)

    # ✅ 兜底：把 run_id 当作 slug，到 outputs 下找
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

    # 原有队列/运行中兼容
    state = pr.get_run(run_id)
    if state.get("status") in {"queued", "running"}:
        return JSONResponse({
            "status": state["status"],
            "files": [], "figs": {}, "tables": {},
            "error": state.get("error"),
            "message": state.get("message"),
        })
    raise HTTPException(status_code=404, detail="manifest not found")



# api/routers/runs.py -> download_file
@router.get("/{run_id}/download")
def download_file(run_id: str, filename: str = Query(...)):
    # 1) 先尝试把 run_id 当 UUID 走
    try:
        path = pr.get_file_path(run_id, filename)
        return _file_response_auto(path, filename)
    except FileNotFoundError:
        pass

    # 2) 把 run_id 当 slug（地址_半径）→ 映射到真实目录再找文件
    smart = find_existing_run_smart(str(OUTPUTS_DIR), run_id, None, None, _parse_radius_from_runid(run_id) or None)
    if smart:
        outdir, _, _, _ = smart
        import os
        abs_path = os.path.join(outdir, filename)
        if not os.path.exists(abs_path):
            base = os.path.basename(filename)
            found = None
            for root, _, files in os.walk(outdir):
                for f in files:
                    if f == base:
                        found = os.path.join(root, f)
                        break
                if found: break
            if not found:
                raise HTTPException(status_code=404, detail=f"file not found: {filename}")
            abs_path = found
        return _file_response_auto(abs_path, os.path.basename(abs_path))

    raise HTTPException(status_code=404, detail=f"file not found: {filename}")



# 小工具：自动识别 MIME，和你原来的返回一致
from fastapi.responses import FileResponse
def _file_response_auto(path: str, filename: str):
    if path.endswith((".html",".htm")):
        return FileResponse(path, media_type="text/html",
                            headers={"Content-Disposition":"inline"})
    if path.endswith(".png"):
        return FileResponse(path, media_type="image/png", filename=filename)
    if path.endswith((".jpg",".jpeg")):
        return FileResponse(path, media_type="image/jpeg", filename=filename)
    if path.endswith(".csv"):
        return FileResponse(path, media_type="text/csv", filename=filename)
    if path.endswith(".parquet"):
        return FileResponse(path, media_type="application/octet-stream", filename=filename)
    if path.endswith((".tif",".tiff")):
        return FileResponse(path, media_type="image/tiff", filename=filename)
    return FileResponse(path, filename=filename)



@router.get("/{run_id}/stderr", response_class=PlainTextResponse)
def stderr(run_id: str):
    """获取错误日志（仅当任务失败时）"""
    data = pr.get_run(run_id)
    if data.get("status") != "error":
        return ""
    return data.get("message", "未知错误")
