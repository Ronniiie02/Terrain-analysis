
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

# === import elevation 包（把 project 根目录加入 sys.path）===
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 你的管线入口：必须导出 PipelineConfig, run_pipeline
from elevation.run_final_pipeline import PipelineConfig, run_pipeline  # noqa

# 内存态运行表（开发模式 --reload 时会被清空）
RUNS: Dict[str, Dict[str, Any]] = {}


# ----------------- 工具 -----------------
# api/services/pipeline_runner.py

def _payload_to_config(payload: Dict[str, Any], run_id: str) -> PipelineConfig:
    """
    把前端 payload 转成 PipelineConfig。
    注意：输出目录初步绑定为 outputs/<run_id>，
    最终以 _run_target 内的 cfg.output_dir = RUNS[run_id]["outputs_dir"] 为准。
    """
    import os
    from api.settings import OUTPUTS_DIR

    cfg = PipelineConfig()

    # 地址 / 经纬度
    if payload.get("address"):
        cfg.address = str(payload["address"])
    if "lat" in payload and "lon" in payload:
        try:
            cfg.lat = float(payload["lat"])
            cfg.lon = float(payload["lon"])
        except Exception:
            pass

    # AOI 半径
    if "aoi_radius_m" in payload:
        try:
            cfg.aoi_radius_m = float(payload["aoi_radius_m"])
            cfg.dem_for_user_aoi = True
        except Exception:
            pass

    # 输出格式
    tf = payload.get("table_format") or payload.get("output_format")
    cfg.output_format = tf if tf in {"csv", "parquet", "both"} else "csv"

    # 其它选项
    if "generate_narrative" in payload:
        cfg.generate_narrative = bool(payload["generate_narrative"])
    if "verbose" in payload:
        cfg.verbose = bool(payload["verbose"])
    if "save_3d" in payload:
        cfg.save_3d = bool(payload["save_3d"])

    # 初步绑定（最终会在 _run_target 再覆盖一遍，确保目录一致）
    cfg.output_dir = os.path.join(str(OUTPUTS_DIR), run_id)

    return cfg


def _outputs_dir_for_runid(run_id: str) -> str:
    return os.path.join(str(OUTPUTS_DIR), run_id)


def _manifest_path(outputs_dir: Optional[str]) -> Optional[str]:
    if not outputs_dir:
        return None
    p = os.path.join(outputs_dir, "manifest.json")
    return p if os.path.exists(p) else None


# ----------------- 后台线程 -----------------
# api/services/pipeline_runner.py

def _run_target(run_id: str, payload: Dict[str, Any]) -> None:
    """后台线程执行管道 - 幂等/缓存复用优先"""
    import os, json, traceback
    from api.settings import OUTPUTS_DIR

    # —— 绑定本次运行目录（由 create_run 预先创建）
    outdir = RUNS[run_id]["outputs_dir"]
    manifest_path = os.path.join(outdir, "manifest.json")

    # 线程启动即再次检查，避免并发重复计算
    if os.path.exists(manifest_path):
        print(f"🔄 [THREAD CACHE HIT] {run_id} manifest exists; reuse immediately.")
        RUNS[run_id].update({
            "status": "done",
            "message": "Results loaded from existing cache",
            "outputs_dir": outdir,
            "manifest_path": manifest_path,
            "result": None,
        })
        return

    # （可选）标记：正在检查/运行
    RUNS[run_id]["status"] = "running"

    try:
        # —— 构造配置
        cfg = _payload_to_config(payload, run_id)

        # 2.3 关键：把“最终目录”直接传给 pipeline（禁止再拼子文件夹）
        # outdir 形如：outputs/<{address}_{radius}>
        cfg.output_dir = RUNS[run_id]["outputs_dir"]

        # —— 真正执行
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
    创建任务 - 增强幂等性检查（同地址+半径秒级复用；不同半径独立目录）
    """
    import os
    import json
    from threading import Thread
    from pathlib import Path

    from api.services.utils_cache import (
        build_predicted_run_id,      # 已修正为使用短地址（逗号前片段）+ 半径
        find_existing_run_smart,     # 智能命中：精确slug/前缀/经纬度容差
        find_existing_run_by_address,
        validate_radius_match,
    )
    from api.settings import OUTPUTS_DIR

    # -------- 提取参数 --------
    address = (payload.get("location_text") or payload.get("address") or "").strip()
    lat = payload.get("lat")
    lon = payload.get("lon")
    radius_m = payload.get("aoi_radius_m") or 500.0

    print("=" * 80)
    print("🔍 POWERFUL CACHE LOOKUP:")
    print(f"  address: {address}")
    print(f"  lat/lon: {lat}, {lon}")
    print(f"  radius: {radius_m}m")

    # -------- 规范 run_id：短地址 + 半径 --------
    canonical_key = address  # build_predicted_run_id 内部会取短地址(逗号前片段)
    run_id = build_predicted_run_id(canonical_key, lat or 0.0, lon or 0.0, radius_m)
    outdir = os.path.join(str(OUTPUTS_DIR), run_id)
    manifest_path = os.path.join(outdir, "manifest.json")

    # 方法0：同目录已有完整结果 → 直接复用（秒级）
    if os.path.exists(manifest_path):
        print(f"✅ [CACHE HIT] {run_id} -> manifest exists, reuse")
        RUNS[run_id] = {
            "status": "done",
            "message": "loaded from cache",
            "result": None,
            "outputs_dir": outdir,
            "manifest_path": manifest_path,
            "params": payload,
        }
        return run_id

    # 方法1：智能检索（精确/前缀/经纬度容差，且半径匹配）
    smart = find_existing_run_smart(str(OUTPUTS_DIR), run_id, lat, lon, radius_m)
    if smart:
        outdir_found, manifest_found, actual_uuid, slug_dir = smart
        print(f"✅ [EXACT/PREFIX/COORD MATCH] Found: {slug_dir}")
        # 直接返回已存在的 slug 目录名；不需要重新排队
        RUNS[slug_dir] = {
            "status": "done",
            "message": "loaded from cache",
            "result": None,
            "outputs_dir": outdir_found,
            "manifest_path": manifest_found,
            "params": payload,
        }
        return slug_dir

    # 方法2：地址相似度匹配（半径严格相等）
    if address:
        similar_run = find_existing_run_by_address(str(OUTPUTS_DIR), address, radius_m, 0.8)
        if similar_run:
            outdir_found, manifest_found, found_run_id = similar_run
            print(f"✅ [SIMILAR ADDRESS] Found: {found_run_id}")
            RUNS[found_run_id] = {
                "status": "done",
                "message": "loaded from cache",
                "result": None,
                "outputs_dir": outdir_found,
                "manifest_path": manifest_found,
                "params": payload,
            }
            return found_run_id

    # 方法3：经纬度兜底（放宽容差），半径仍需匹配
    if lat is not None and lon is not None:
        root = Path(OUTPUTS_DIR)
        coord_eps = 5e-3  # ~500m（只作为最后兜底；真正的分析仍按提交的半径）
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
                    print(f"✅ [COORDINATE FALLBACK] Found: {found_run_id}")
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

    # -------- 缓存未命中：创建新任务（目录=outputs/<run_id>/） --------
    print(f"🆕 [CACHE MISS] Creating new run for {run_id}")
    os.makedirs(outdir, exist_ok=True)

    RUNS[run_id] = {
        "status": "queued",
        "message": None,
        "result": None,
        "outputs_dir": outdir,
        "manifest_path": None,
        "params": payload,
    }

    # 后台执行；run_pipeline 内部须直接使用 cfg.output_dir 作为最终 outdir（不再拼子目录）
    t = Thread(target=_run_target, args=(run_id, payload), daemon=True)
    t.start()

    return run_id


def _recover_if_possible(run_id: str) -> Optional[Dict[str, Any]]:
    """
    当 RUNS 中没有该 run_id（UUID）时，从磁盘恢复：
    - 支持 outputs/<uuid>/**/manifest.json（递归）
    - 把 outputs_dir 设置为 manifest 所在的那个“地址_半径”子目录
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
            _ = json.load(f)  # 读一把确保没损坏
    except Exception:
        return None

    return {
        "run_id": run_id,
        "status": "done",
        "message": "recovered from disk",
        "outputs_dir": os.path.dirname(manifest_path),
    }




def get_run(run_id: str) -> Dict[str, Any]:
    if not UUID_RE.match(run_id):
        # 改：用 smart，而不是严格版
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
    # 原逻辑（UUID）
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
    """列出该 run 的输出（支持内存缺失时从磁盘恢复）"""
    r = RUNS.get(run_id)
    outdir = r["outputs_dir"] if r and r.get("outputs_dir") else _outputs_dir_for_runid(run_id)
    if not os.path.isdir(outdir):
        return []
    paths = []
    for root, _, files in os.walk(outdir):
        for f in files:
            p = os.path.join(root, f)
            # 统一：路径相对于 outdir
            paths.append(os.path.relpath(p, outdir))
    return sorted(paths)


def read_manifest(run_id: str) -> Optional[Dict[str, Any]]:
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
    按 run_id 定位真实文件路径。
    支持传相对路径或仅文件名；若顶层未找到则递归匹配。
    """
    r = RUNS.get(run_id)
    outdir = r["outputs_dir"] if r and r.get("outputs_dir") else _outputs_dir_for_runid(run_id)

    # 先直接拼接
    abs_path = os.path.join(outdir, filename)
    if os.path.exists(abs_path):
        return abs_path

    # 再递归找同名文件
    base = os.path.basename(filename)
    for root, _, files in os.walk(outdir):
        for f in files:
            if f == base:
                return os.path.join(root, f)

    raise FileNotFoundError(filename)


# ---------- manifest 对前端友好化（全部相对路径） ----------
def _to_rel(outdir: str, p: Optional[str]) -> Optional[str]:
    """把绝对路径 p 变成相对于 outdir 的相对路径；不在 outdir 下则退化成 basename。"""
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
    读取 outputs/<run_id>/manifest.json，并把其中 figs/tables 的路径
    统一转换为相对于 manifest 所在目录(outdir) 的相对路径。
    同时附带 files 列表（相对 outdir 的路径）。
    """
    # 1) 优先用内存中的 outputs_dir（这在 _run_target 里已经被设置为“真正的子目录”）
    r = RUNS.get(run_id)
    outdir = r["outputs_dir"] if r and r.get("outputs_dir") else _outputs_dir_for_runid(run_id)

    # 2) 该 outdir 下直接找 manifest.json
    mp = os.path.join(outdir, "manifest.json")
    if not os.path.exists(mp):
        # 3) 顶层没有，就递归搜寻子目录中的 manifest.json
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
        outdir = os.path.dirname(mp)  # 关键：以 manifest 所在目录为 outdir

    # 4) 读 manifest
    try:
        with open(mp, "r") as f:
            raw = json.load(f)
    except Exception:
        return None

    # 5) figs / tables 路径 → 相对 outdir
    figs = {k: _to_rel(outdir, v) for k, v in (raw.get("figs") or {}).items()}
    tables = {k: _to_rel(outdir, v) for k, v in (raw.get("tables") or {}).items()}

    # 6) files 列表也以 outdir 为根列出
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