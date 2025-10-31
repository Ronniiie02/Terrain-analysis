const BASE = "http://127.0.0.1:8000";
const $  = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
let overlayGroup = null;
let aoiCircle = null;
let satMap = null;
let satMarker = null;
const GOOGLE_KEY = ""; // 可选前端反向地理编码

/* ---------- 星标 SVG & 工厂 ---------- */
const STAR_SVG = `
<svg viewBox="0 0 24 24" width="28" height="28" fill="#FFD60A" stroke="#222" stroke-width="1.1">
  <path d="M12 2l2.9 6.6 7.1.6-5.3 4.6 1.6 6.7L12 16.9 5.7 20.5l1.6-6.7L2 9.2l7.1-.6z"/>
</svg>`;
function createStarIcon(size=28){
  return L.divIcon({
    className: "",
    html: `<div class="star-marker" title="TARGET"><div class="halo"></div>${STAR_SVG}</div>`,
    iconSize: [size, size],
    iconAnchor: [size/2, size/2],
  });
}

/* ---------- 地图 ---------- */
function initMap() {
  const el = $("#satMap");
  // 初始中心：如果还没有坐标，就先看世界
  satMap = L.map(el, { zoomControl: true }).setView([0, 0], 2);

  // ---- 底图（Street + Satellite）----
  // OSM（街道）
  const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '© OpenStreetMap'
  });

  // Esri 卫星
  const esriWorldImagery = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    {
      maxZoom: 20,
      attribution: "Tiles © Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community"
    }
  );

  // （可选）Esri 地名/边界标签叠加（半透明）
  const esriPlaces = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
    {
      maxZoom: 20,
      opacity: 0.8,
      attribution: "Esri Boundaries & Places"
    }
  );

  // 默认显示：卫星 + 标签
  esriWorldImagery.addTo(satMap);
  esriPlaces.addTo(satMap);

  // 图层控制器（左上角/右上角都行）
  L.control
    .layers(
      {
        "🛰 Satellite (Esri)": esriWorldImagery,
        "🗺 Street (OSM)": osm
      },
      {
        "🏷 Labels (Esri)": esriPlaces
      },
      { collapsed: false }
    )
    .addTo(satMap);

  // 用于放置星标+圈的图层组（你已有逻辑会往这里加）
  overlayGroup = L.layerGroup().addTo(satMap);
}


function placeTarget(lat, lon, radiusM = null) {
  if (!satMap) initMap();

  const latNum = Number(lat), lonNum = Number(lon);
  if (!Number.isFinite(latNum) || !Number.isFinite(lonNum)) return;

  // 清理上次
  if (overlayGroup) {
    overlayGroup.clearLayers();
  } else {
    if (satMarker) satMap.removeLayer(satMarker);
    if (aoiCircle) { satMap.removeLayer(aoiCircle); aoiCircle = null; }
  }

  const icon = createStarIcon(28);
  satMarker = L.marker([latNum, lonNum], { icon, zIndexOffset: 1000 }).addTo(overlayGroup);
  satMarker.bindTooltip("Target", { permanent:false, direction:"top", offset:[0,-12] });
  if (satMarker.bringToFront) satMarker.bringToFront();

  if (Number.isFinite(radiusM) && radiusM > 0) {
    aoiCircle = L.circle([latNum, lonNum], {
      radius: radiusM,
      color: "#FFD60A",
      weight: 1.5,
      fillColor: "#FFD60A",
      fillOpacity: 0.08
    }).addTo(overlayGroup);

    // 视图：圈优先，限制最大缩放以便看清建筑
    satMap.fitBounds(aoiCircle.getBounds(), { maxZoom: 19, padding:[20,20] });
  } else {
    satMap.setView([latNum, lonNum], 18);
  }

  // 再次刷新，确保图块/标注不偏移
  setTimeout(()=> satMap.invalidateSize(), 100);
}


/* ---------- Modal & 历史回退 ---------- */
let modalOpen = false;
function openImage(img){
  if (!img || !img.src) return;
  $("#modalImg").src = img.src;
  $("#modal").classList.add("active");
  $("#modal").setAttribute("aria-hidden","false");
  modalOpen = true;
  try { history.pushState({modal:true}, "", "#preview"); } catch(e){}
  window.addEventListener("keydown", escListener);
}
function closeModal(){
  if (!modalOpen) return;
  $("#modal").classList.remove("active");
  $("#modal").setAttribute("aria-hidden","true");
  $("#modalImg").src = "";
  modalOpen = false;
  window.removeEventListener("keydown", escListener);
  try { if (history.state && history.state.modal) history.back(); } catch(e){}
}
$("#modal").addEventListener("click", (e)=>{ if (e.target === $("#modal")) closeModal(); });
function escListener(e){ if (e.key === "Escape") closeModal(); }
window.addEventListener("popstate",(e)=>{
  if (modalOpen) {
    $("#modal").classList.remove("active");
    $("#modal").setAttribute("aria-hidden","true");
    $("#modalImg").src = "";
    modalOpen = false;
    window.removeEventListener("keydown", escListener);
  }
});

/* ---------- 提示/状态 ---------- */
function showNotification(message, type = 'info') {
  const notification = $('#notification');
  const notificationIcon = $('#notificationIcon');
  const notificationText = $('#notificationText');
  
  // Set the message
  notificationText.textContent = message;
  
  // Set the type and icon
  notification.className = 'notification';
  notification.classList.add(type);
  
  notificationIcon.className = 'notification-icon';
  notificationIcon.classList.add(type);
  
  // Set the appropriate icon
  if (type === 'success') {
    notificationIcon.innerHTML = '<i class="fa fa-check-circle"></i>';
  } else if (type === 'error') {
    notificationIcon.innerHTML = '<i class="fa fa-exclamation-circle"></i>';
  } else if (type === 'warning') {
    notificationIcon.innerHTML = '<i class="fa fa-exclamation-triangle"></i>';
  } else {
    notificationIcon.innerHTML = '<i class="fa fa-info-circle"></i>';
  }
  
  // Show the notification
  notification.classList.add('show');
  
  // Hide it after 3 seconds
  setTimeout(() => {
    notification.classList.remove('show');
  }, 3000);
}

function updateProgress(p){ 
  $("#progressBar").style.width = p + "%"; 
  // Animate the glow effect
  if (p > 0 && p < 100) {
    $("#progressBar").style.boxShadow = "0 0 5px #00e5ff, 0 0 10px #00e5ff";
  } else {
    $("#progressBar").style.boxShadow = "none";
  }
}

function handleOutOfGridError(runStatus) {
  // 期望结构：runStatus.error = { error_code, message, max_radius_m, dataset, radius_m, ... }
  const err = runStatus && runStatus.error ? runStatus.error : {};
  const maxR = Number(err.max_radius_m);
  const curR = Number($("#radius").value);

  // 计算建议值（向下取整到最近的选项）
  const options = [50, 100, 200, 300, 500];
  const safe = options.filter(v => Number.isFinite(maxR) ? v <= maxR : true);
  const suggested = safe.length ? safe[safe.length - 1] : 50;

  // 视觉状态
  setBadge("err", "OUT OF GRID");
  updateSystemStatus("warn", "ADJUST RADIUS");
  $("#status").textContent = "ERROR";
  $("#hint").textContent = "AOI exceeds LiDAR dataset extent. Please reduce radius.";

  // 高亮半径选择器
  const radiusSel = $("#radius");
  radiusSel.classList.add("ring-2", "ring-offset-2");
  radiusSel.style.boxShadow = "0 0 12px rgba(255,214,10,.6)";
  setTimeout(()=>{
    radiusSel.classList.remove("ring-2","ring-offset-2");
    radiusSel.style.boxShadow = "";
  }, 1800);

  // 写出详细提示
  const maxText = Number.isFinite(maxR) ? `${Math.floor(maxR)} m` : "dataset bounds";
  const curText = Number.isFinite(curR) ? `${curR} m` : "--";
  const suggestText = Number.isFinite(maxR) ? `${suggested} m` : "smaller value";

  // toast
  showNotification(
    `AOI OUT OF GRID\nCurrent: ${curText}\nMax allowed: ${maxText}\nTry: ${suggestText}`,
    "warning"
  );

  // 可选：自动把下拉选到建议值（如果你希望“自动修复”）
  if (Number.isFinite(maxR)) {
    $("#radius").value = String(suggested);
  }

  // 页面关键位也提示一下
  $("#narrative").textContent = 
    `⚠ The requested analysis radius (${curText}) exceeds the LiDAR dataset extent (max ≈ ${maxText}). ` +
    `Please reduce the radius (e.g., ${suggestText}) and run again.`;
}

function setBadge(type, text, spin=false){
  $("#statusBadge").className = "badge " + type;
  $("#statusBadge").innerHTML = (spin?'<div class="loader"></div>':'') + text;
}

function updateSystemStatus(type, text) {
  const systemStatus = $('#systemStatus');
  const statusIcon = systemStatus.querySelector('.status-icon');
  const statusText = systemStatus.querySelector('.status-text');
  
  systemStatus.className = 'system-status ' + type;
  statusIcon.className = 'status-icon ' + type;
  statusText.className = 'status-text ' + type;
  statusText.textContent = text;
}

/* ---------- CSV/表格 ---------- */
function csvToTable(csv, table){
  const lines = csv.trim().split("\n");
  if (!lines.length) return;
  const headers = lines[0].split(",").map(h=>h.trim());
  const thead = table.querySelector("thead tr");
  thead.innerHTML = headers.map(h=>`<th>${h}</th>`).join("");
  const tbody = table.querySelector("tbody"); tbody.innerHTML = "";
  for (let i=1;i<lines.length;i++){
    const cells = lines[i].split(",").map(c=>c.trim());
    const tr = document.createElement("tr");
    tr.innerHTML = cells.map(c=>`<td>${c}</td>`).join("");
    tbody.appendChild(tr);
  }
}
async function fetchText(url){ const r = await fetch(url); if(!r.ok) throw new Error(`HTTP ${r.status}`); return r.text(); }
function findFileByRegex(files, patterns){ for(const pat of patterns){ const f = files.find(x=>pat.test(x)); if(f) return f; } return null; }

// 从对象或杂糅了堆栈的长字符串中，尽可能提取结构化 {error_code, message, ...}
function extractStructuredError(v) {
  // 常见形态：
  // 1) v.error 是对象（最佳）
  // 2) v.error 是 JSON 字符串
  // 3) v.message 里夹了一个 {"error_code": ...} 的 JSON
  // 4) 全是普通字符串
  const out = { error_code: undefined, message: undefined };

  // 1) 直接对象
  if (v && typeof v.error === "object" && v.error !== null) {
    return { ...v.error };
  }

  // 2) error 是字符串尝试 JSON.parse
  if (v && typeof v.error === "string") {
    try { return JSON.parse(v.error); } catch (e) {}
  }

  // 3) 在 v.message 里用正则挖出 JSON
  const msg = (v && typeof v.message === "string") ? v.message : "";
  if (msg) {
    const m = msg.match(/\{[\s\S]*?"error_code"[\s\S]*?\}/);
    if (m) {
      try { return JSON.parse(m[0]); } catch (e) {}
    }
    out.message = msg; // 退化：只有普通字符串
  }
  return out;
}

// 把结构化错误映射成「面向用户的短句」；不泄露技术细节/堆栈
function formatUserFacingError(err) {
  const code = err.error_code || "";
  const raw = (err.message || "").toString();

  if (code === "AOI_OUTSIDE_EPT_BOUNDS") {
    const maxR = Number(err.max_radius_m);
    const options = [50,100,200,300,500];
    const safe = options.filter(v => Number.isFinite(maxR) ? v <= maxR : true);
    const suggested = safe.length ? safe[safe.length - 1] : 50;
    const maxText = Number.isFinite(maxR) ? `${Math.floor(maxR)} m` : "dataset bounds";
    return {
      title: "AOI OUT OF GRID",
      hint:  `AOI exceeds dataset extent. Max ≈ ${maxText}. Try ${suggested} m or smaller.`,
      toast: `AOI out of grid · Max ≈ ${maxText} · Suggest ${suggested} m`
    };
  }

  // 其他错误：尽量给简短、人话的消息
  // 去掉 PDAL/内部实现细节，保留一句人类可读
  const clean = raw
    .replace(/pdal|writers\.gdal|Traceback[\s\S]*/gi, "") // 去掉实现名与堆栈
    .replace(/\s+/g, " ")                                  // 压成一行
    .trim();

  return {
    title: "ANALYSIS FAILED",
    hint:  clean || "An unexpected error occurred. Please try again.",
    toast: clean || "Analysis failed."
  };
}

/* ---------- Snapshots ---------- */
function renderSnapshot(el, stats){
  const html = `
    <div class="snapshot-chip">
      <div class="t">House</div>
      <div class="v">${stats.house ?? "--"}</div>
    </div>
    <div class="snapshot-chip">
      <div class="t">Min</div>
      <div class="v">${stats.min ?? "--"}</div>
    </div>
    <div class="snapshot-chip">
      <div class="t">Median</div>
      <div class="v">${stats.median ?? "--"}</div>
    </div>
    <div class="snapshot-chip">
      <div class="t">Max</div>
      <div class="v">${stats.max ?? "--"}</div>
    </div>
    <div class="snapshot-chip">
      <div class="t">Percentile</div>
      <div class="v">${stats.pct ?? "--"}</div>
    </div>
  `;
  el.innerHTML = html;
}
function parseAreaCSVToStats(csv){
  const lines = csv.trim().split("\n"); if (lines.length < 2) return {};
  const idxValue = 1;
  const pick = (kw) => {
    const row = lines.find(l => l.toLowerCase().includes(kw));
    if (!row) return null;
    const arr = row.split(","); return (arr[idxValue]||"").trim();
  };
  return { house: pick("house elevation"), min: pick("lowest"), max: pick("highest"), median: pick("median"), pct: pick("percentile") };
}

/* ---------- 反向地理编码（可选） ---------- */
async function reverseGeocode(lat, lon){
  if (GOOGLE_KEY) {
    try{
      const r = await fetch(`https://maps.googleapis.com/maps/api/geocode/json?latlng=${lat},${lon}&key=${GOOGLE_KEY}`);
      const j = await r.json();
      if (j.status === "OK" && j.results && j.results[0]) return j.results[0].formatted_address;
    }catch(e){}
  }
  try{
    const r2 = await fetch(`https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lon}`);
    const j2 = await r2.json();
    if (j2 && j2.display_name) return j2.display_name;
  }catch(e){}
  return null;
}

/* ---------- 清理 ---------- */
function clearUI(){
  $("#mLocation").textContent="--";
  $("#mGround").textContent="--.--";
  $("#mFiles").textContent="--";
  $("#mTables").textContent="--";
  $("#userAOIRadius").textContent="--";
  $("#narrative").textContent="-- PROCESSING --";
  $$(".gallery img").forEach(i=>i.removeAttribute("src"));
  $("#fig3d").removeAttribute("src");
  $("#fileList").innerHTML="";
  $("#tblSummary tbody").innerHTML="";
  $("#tblSummary thead tr").innerHTML="<th>Summary Multiscale</th>";
  $("#snap500").innerHTML=""; $("#snapUser").innerHTML="";
  $("#status").textContent = "INITIALIZING";
  $("#hint").textContent   = "ESTABLISHING CONNECTION";
  setBadge("warn","PROCESSING",true);
}

/* ---------- 句子抽取：避免小数点截断 ---------- */
function extractFirstSentence(txt){
  if (!txt) return "";
  const s = txt.trim();
  const stops = [". ", "! ", "? "];
  let cut = -1;
  for (const stop of stops){
    const i = s.indexOf(stop);
    if (i !== -1) cut = (cut === -1) ? i+2 : Math.min(cut, i+2);
  }
  return (cut === -1) ? s : s.slice(0, cut);
}

/* ---------- 加载结果（含 Delta 文本全显） ---------- */
async function loadOutputs(runId){
  try{
    const r = await fetch(`${BASE}/runs/${runId}/manifest`);
    if(!r.ok) throw new Error(`Failed to load manifest: HTTP ${r.status}`);
    const manifest = await r.json();
    const files = manifest.files || [];

    // 地址 / 高程 / 坐标
    const manifestAddr = manifest.address && String(manifest.address).trim();
    const addrInput = $("#addr").value.trim();
    const address = manifestAddr || addrInput || (
      (Number.isFinite(manifest.lat) && Number.isFinite(manifest.lon))
        ? `${Number(manifest.lat).toFixed(6)}, ${Number(manifest.lon).toFixed(6)}`
        : "--"
    );
    $("#mLocation").textContent = address;
    // loadOutputs 里替换这一段
    // ground elevation
    if (Number.isFinite(Number(manifest.house_ground_m))) {
      $("#mGround").textContent = Number(manifest.house_ground_m).toFixed(2);
    }

    // coords -> number, then place marker
    const latNum = Number(manifest.lat);
    const lonNum = Number(manifest.lon);
    const aoiR   = Number(manifest.aoi_radius_m);
    if (Number.isFinite(latNum) && Number.isFinite(lonNum)) {
      $("#mFiles").textContent = `${latNum.toFixed(6)}, ${lonNum.toFixed(6)}`;
      placeTarget(latNum, lonNum, Number.isFinite(aoiR) ? aoiR : null);       // 如果实现了半径圈：placeTarget(latNum, lonNum, Number(manifest.aoi_radius_m));
      $("#lat").value = latNum.toFixed(6);
      $("#lon").value = lonNum.toFixed(6);
    }


    // Figures
    const figs = manifest.figs || {};
    for (const [key, filename] of Object.entries(figs)) {
      const url = `${BASE}/runs/${runId}/download?filename=${encodeURIComponent(filename)}`;
      if (key === "figure1_elevation") $("#fig1").src = url;
      else if (key === "figure2_slope") $("#fig2").src = url;
      else if (key === "figure3_aspect") $("#fig3").src = url;
      else if (key === "figure4_terrain_and_hist") $("#fig4").src = url;
      else if (key === "figure5_6_3d_combo") {
        const ifr = $("#fig3d");
        ifr.src = url;
        setTimeout(()=>{ ifr.style.height = ifr.parentElement.clientHeight + "px"; }, 160);
      }
    }

    // Multiscale
    const tableSummary = (manifest.tables||{})["summary_multiscale.csv"] || findFileByRegex(files, [/summary[_-]?multiscale\.csv$/i]);
    if (tableSummary){
      const url = `${BASE}/runs/${runId}/download?filename=${encodeURIComponent(tableSummary)}`;
      const csv = await fetchText(url);
      csvToTable(csv, $("#tblSummary"));
    }

    // Snapshots
    $("#userAOIRadius").textContent = Math.round(manifest.aoi_radius_m || 500);
    const area500 = (manifest.tables||{})["summary_area_level.csv"] || findFileByRegex(files, [/summary[_-]?area[_-]?level\.csv$/i]);
    if (area500){ const url = `${BASE}/runs/${runId}/download?filename=${encodeURIComponent(area500)}`; const csv = await fetchText(url); renderSnapshot($("#snap500"), parseAreaCSVToStats(csv)); }
    const areaUser = (manifest.tables||{})["summary_area_level_user.csv"] || findFileByRegex(files, [/summary[_-]?area[_-]?level[_-]?user\.csv$/i]);
    if (areaUser){ const url = `${BASE}/runs/${runId}/download?filename=${encodeURIComponent(areaUser)}`; const csv = await fetchText(url); renderSnapshot($("#snapUser"), parseAreaCSVToStats(csv)); }

    // Narrative + Delta（第一句话完整显示到通栏卡片）
    const narPath = findFileByRegex(files, [/narrative\.txt$/i]);
    let deltaSet = false;
    if (narPath){
      const txt = (await fetchText(`${BASE}/runs/${runId}/download?filename=${encodeURIComponent(narPath)}`)).trim();
      $("#narrative").textContent = txt || "-- EMPTY NARRATIVE --";
      const firstSentence = extractFirstSentence(txt);
      if (firstSentence){
        $("#mTables").textContent = firstSentence.trim();
        deltaSet = true;
      }
    }
    if (!deltaSet && tableSummary){
      const url = `${BASE}/runs/${runId}/download?filename=${encodeURIComponent(tableSummary)}`;
      const csv = await fetchText(url);
      const lines = csv.trim().split("\n");
      const headers = lines[0].split(",").map(h=>h.trim());
      const idxR = headers.findIndex(h=>/^radius\s*\(m\)$/i.test(h));
      const idxD = headers.findIndex(h=>/Δ?elev.*median/i.test(h));
      if (idxR>=0 && idxD>=0){
        for (let i=1;i<lines.length;i++){
          const cells = lines[i].split(",").map(s=>s.trim());
          if (Math.abs(parseFloat(cells[idxR]) - 500) < 1e-6){
            $("#mTables").textContent = `At 500 m, the home sits ${cells[idxD]} relative to the area median elevation.`;
            break;
          }
        }
      }
    }

    // Files
    const fileListEl = $("#fileList");
    fileListEl.innerHTML = (files||[]).map(f=>{
      const url = `${BASE}/runs/${runId}/download?filename=${encodeURIComponent(f)}`;
      return `<a href="${url}" target="_blank" class="file-link" title="${f}">
        <i class="fa fa-file-text-o"></i> ${f}
      </a>`;
    }).join("");

  } catch (err) {
    showNotification("FAILED TO LOAD ANALYSIS RESULTS", "error");
    console.error(err);
  }
};

/* ---------- 事件：经纬度失焦自动反查地址 ---------- */
$("#lat").addEventListener("blur", async ()=>{
  const lat=parseFloat($("#lat").value), lon=parseFloat($("#lon").value);
  if (Number.isFinite(lat) && Number.isFinite(lon)) {
    const addr = await reverseGeocode(lat, lon);
    if (addr) { 
      $("#addr").value = addr; 
      showNotification("Auto-filled address from coordinates.", "success");
    }
  }
});
$("#lon").addEventListener("blur", async ()=>{
  const lat=parseFloat($("#lat").value), lon=parseFloat($("#lon").value);
  if (Number.isFinite(lat) && Number.isFinite(lon)) {
    const addr = await reverseGeocode(lat, lon);
    if (addr) { 
      $("#addr").value = addr; 
      showNotification("Auto-filled address from coordinates.", "success");
    }
  }
});

/* ---------- Run ---------- */
$("#runBtn").onclick = async () => {
  clearUI();
  $("#status").textContent = "INITIALIZING";
  setBadge("warn","PROCESSING",true);
  $("#hint").textContent = "ESTABLISHING CONNECTION";
  updateProgress(0);

  const addr = $("#addr").value.trim();
  const latStr = $("#lat").value.trim();
  const lonStr = $("#lon").value.trim();
  const radius = parseFloat($("#radius").value);
  $("#userAOIRadius").textContent = Math.round(radius);

  let payload = { aoi_radius_m: radius, output_format:"csv", verbose:true, generate_narrative:true };

  if (addr) {
    payload.address = addr; $("#mLocation").textContent = addr;
  } else if (!Number.isNaN(parseFloat(latStr)) && !Number.isNaN(parseFloat(lonStr))) {
    payload.lat = parseFloat(latStr); payload.lon = parseFloat(lonStr);
    $("#mLocation").textContent = `${payload.lat.toFixed(6)}, ${payload.lon.toFixed(6)}`;
  } else {
    setBadge("err","BAD INPUT"); 
    showNotification("Enter address or lat/lon", "error"); 
    updateProgress(0); 
    return;
  }
  if (!addr && !Number.isNaN(+latStr) && !Number.isNaN(+lonStr)) {
    placeTarget(+latStr, +lonStr, radius);
  }
  try{
    const r = await fetch(`${BASE}/runs`, { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify(payload) });
    if (!r.ok){ 
      setBadge("err","FAILED"); 
      showNotification("INITIALIZATION FAILED", "error"); 
      return; 
    }
    const data = await r.json();
    $("#rid").value = data.run_id;
    
    // ✅ 如果后端说已命中缓存，直接加载结果，不要轮询
    if (data.status === "done" && data.message === "reused from cache") {
      updateProgress(100);
      setBadge("ok","COMPLETE");
      $("#status").textContent = "DONE";
      $("#hint").textContent = "LOADED FROM CACHE";
      showNotification("Loaded from cache ✅", "success");
      await loadOutputs(data.run_id);
      return; // ⬅️ 关键：不要进入轮询
    }
        
    $("#rid").setAttribute("title", data.run_id); 
    $("#status").textContent = "QUEUED"; 
    setBadge("warn","WAITING"); 
    $("#hint").textContent="AWAITING JOB SLOT"; 
    updateProgress(10);

    let progress = 10;
    const progressInterval = setInterval(()=>{ 
      if (progress<90){ 
        progress += Math.random()*2; 
        updateProgress(progress);
      } 
    }, 500);

    const statusTimer = setInterval(async()=>{
      try{
        const s = await fetch(`${BASE}/runs/${data.run_id}`);
        const v = await s.json();
        $("#status").textContent = v.status.toUpperCase();

        if (v.status === "running") { 
          setBadge("warn","ANALYZING",true); 
          $("#hint").textContent = "PROCESSING LIDAR DATA"; 
        }

        if (v.status === "done") {
          clearInterval(statusTimer); 
          clearInterval(progressInterval);
          updateProgress(100); 
          setBadge("ok","COMPLETE"); 
          $("#hint").textContent="ANALYSIS COMPLETE"; 
          showNotification("ANALYSIS COMPLETE", "success");
          await loadOutputs(data.run_id);
        }

        if (v.status === "error") {
          clearInterval(statusTimer);
          clearInterval(progressInterval);
          updateProgress(0);

          // ✅ 只保留新逻辑
          const err = extractStructuredError(v);
          const ui  = formatUserFacingError(err);

          if (err.error_code === "AOI_OUTSIDE_EPT_BOUNDS") {
            handleOutOfGridError({ error: err });
          } else {
            setBadge("err", "FAILED");
            $("#status").textContent = "ERROR";
            $("#hint").textContent = ui.hint;      // 简短提示
            showNotification(ui.toast, "error");   // 简短 toast
            $("#narrative").textContent = `⚠ ${ui.hint}`;
            console.error("Backend error (raw):", v); // 仅控制台
          }
        }
      } catch(err){
        clearInterval(statusTimer); 
        clearInterval(progressInterval);
        setBadge("err","CONNECTION LOST"); 
        updateProgress(0);
      }
    }, 1400);
  } catch (err) {
    // 外层 try 的兜底
    console.error(err);
    setBadge("err","FAILED");
    $("#status").textContent = "ERROR";
    $("#hint").textContent = "REQUEST FAILED";
    updateProgress(0);
    showNotification("REQUEST FAILED", "error");
  }
}; // <— 结束 $("#runBtn").onclick = async () => { ... }

/* ---------- System init ---------- */
async function initializeSystem(){
  try{
    // Simulate loading time for the loading screen
    await new Promise(resolve => setTimeout(resolve, 2000));
    
    const res = await fetch(`${BASE}/health`);
    if (res.ok){ 
      updateSystemStatus("ok", "SYSTEM READY");
      $("#runBtn").disabled=false; 
    }
    else throw new Error();
  }catch{
    updateSystemStatus("err", "CONNECTION FAILED");
    $("#runBtn").disabled = true; 
    showNotification("SYSTEM CONNECTION FAILED", "error");
  } finally {
    // Hide the loading screen
    const loadingScreen = document.getElementById('loadingScreen');
    loadingScreen.style.opacity = '0';
    setTimeout(() => {
      loadingScreen.style.display = 'none';
    }, 500);
  }
}
document.addEventListener("DOMContentLoaded", ()=>{
  $("#runBtn").disabled=true;
  initializeSystem();
  initMap();
});

document.addEventListener("DOMContentLoaded", () => {
  const copyBtn = document.querySelector("#copyRid");
  if (copyBtn) {
    copyBtn.addEventListener("click", async () => {
      const v = (document.querySelector("#rid")?.value ?? "");
      try {
        await navigator.clipboard.writeText(v);
        showNotification("RUN ID copied.", "success");
      } catch {
        showNotification("Copy failed.", "error");
      }
    });
  }
});
/* ---------- 3D 全屏 ---------- */
function open3DFull(){
  const iframe = $("#fig3d"); if(!iframe.src) return;
  const modal = $("#modal"); const content = modal.querySelector(".modal-content");
  const img = content.querySelector("img"); img.style.display="none";
  const big = document.createElement("iframe");
  big.src = iframe.src; big.style.width="90vw"; big.style.height="85vh"; big.style.border="none";
  content.appendChild(big); modal.classList.add("active"); modal.setAttribute("aria-hidden","false");
  modalOpen = true; try{ history.pushState({modal:true}, "", "#preview3d"); }catch(e){}
  window.addEventListener("keydown", escListener);
  const closeOnce = () => {
    content.removeChild(big); img.style.display="";
    closeModal();
    modal.querySelector(".modal-close").removeEventListener("click", closeOnce);
  };
  modal.querySelector(".modal-close").addEventListener("click", closeOnce);
}

/* ---------- 响应式修正 ---------- */
window.addEventListener("resize", ()=>{
  const iframe = $("#fig3d");
  if (iframe.src) iframe.style.height = iframe.parentElement.clientHeight + "px";
});

/* ---------- GSAP Animations ---------- */
document.addEventListener("DOMContentLoaded", () => {
  // Register ScrollTrigger plugin
  gsap.registerPlugin(ScrollTrigger);
  
  // Animate cards on scroll
  gsap.utils.toArray('.glass-card').forEach((card, i) => {
    gsap.from(card, {
      y: 50,
      opacity: 0,
      duration: 0.8,
      ease: "power2.out",
      scrollTrigger: {
        trigger: card,
        start: "top 80%",
        toggleActions: "play none none none"
      },
      delay: i * 0.1
    });
  });
  
  // Animate metric cards
  gsap.utils.toArray('.metric-card').forEach((card, i) => {
    gsap.from(card, {
      scale: 0.9,
      opacity: 0,
      duration: 0.6,
      ease: "back.out(1.7)",
      scrollTrigger: {
        trigger: card,
        start: "top 85%",
        toggleActions: "play none none none"
      },
      delay: i * 0.15
    });
  });
  
  // Animate figure containers
  gsap.utils.toArray('.figure-container').forEach((container, i) => {
    gsap.from(container, {
      x: i % 2 === 0 ? -50 : 50,
      opacity: 0,
      duration: 0.7,
      ease: "power3.out",
      scrollTrigger: {
        trigger: container,
        start: "top 80%",
        toggleActions: "play none none none"
      }
    });
  });
});