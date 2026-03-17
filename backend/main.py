"""
PixelForge Pro — FastAPI Backend
Professional AI Post-Production Suite
Includes Groq AI (Llama-3.3-70b-versatile) integration for intelligent video analysis.

Start:
    Windows: run.bat
    Linux/Mac: bash run.sh
    Manual: uvicorn main:app --reload --port 8000
"""
import os, uuid, time, asyncio, shutil, traceback, base64, re, json as _json
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, BackgroundTasks, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import processors as P

try:
    import httpx as _httpx
    _HTTPX_OK = True
except ImportError:
    _HTTPX_OK = False

AI_CONFIG: Dict = {}

app = FastAPI(
    title="PixelForge Pro",
    description="Professional AI Video Enhancement Suite",
    version="6.0.0",
    docs_url="/docs",
)

app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

BASE     = Path(__file__).parent
UPLOADS  = BASE / "uploads";    UPLOADS.mkdir(exist_ok=True)
OUTPUTS  = BASE / "outputs";    OUTPUTS.mkdir(exist_ok=True)
THUMBS   = BASE / "thumbnails"; THUMBS.mkdir(exist_ok=True)
FRAMES   = BASE / "frames";     FRAMES.mkdir(exist_ok=True)
FRONTEND = BASE.parent / "frontend"

JOBS: Dict[str, dict] = {}
T0 = time.time()

# ── Models ─────────────────────────────────────────────────────────────────────

class EnhanceRequest(BaseModel):
    job_id:           str
    enhancements:     List[str]
    resolution:       str = "1080p"
    format:           str = "mp4"
    quality:          str = "high"
    color_strength:   str = "medium"
    denoise_strength: str = "medium"

class TrimRequest(BaseModel):
    start: float = Field(ge=0)
    end:   float = Field(gt=0)

class CompressRequest(BaseModel):
    target_mb: Optional[float] = Field(None, gt=0)
    crf: int = Field(28, ge=18, le=51)

class ConvertRequest(BaseModel):
    format: str = "mp4"

class SpeedRequest(BaseModel):
    factor: float = Field(2.0, ge=0.25, le=8.0)

class ChromaRequest(BaseModel):
    color:      str   = "green"
    similarity: float = Field(0.10, ge=0.01, le=0.5)
    blend:      float = Field(0.00, ge=0.0, le=0.1)

class RegionRequest(BaseModel):
    x:      float = Field(0.7,  ge=0.0, le=0.99)
    y:      float = Field(0.85, ge=0.0, le=0.99)
    width:  float = Field(0.25, ge=0.01, le=1.0)
    height: float = Field(0.13, ge=0.01, le=1.0)
    effect: str   = "blur"

class WatermarkRequest(BaseModel):
    text:     str   = "PixelForge Pro"
    position: str   = "bottomright"
    opacity:  float = Field(0.7, ge=0.1, le=1.0)

class ColorGradeRequest(BaseModel):
    job_id:        str
    # Basic adjustments
    exposure:      float = Field(0.0,   ge=-3.0,  le=3.0)
    contrast:      float = Field(0.0,   ge=-1.0,  le=1.0)
    saturation:    float = Field(1.0,   ge=0.0,   le=3.0)
    vibrance:      float = Field(0.0,   ge=-1.0,  le=1.0)
    highlights:    float = Field(0.0,   ge=-1.0,  le=1.0)
    shadows:       float = Field(0.0,   ge=-1.0,  le=1.0)
    whites:        float = Field(0.0,   ge=-1.0,  le=1.0)
    blacks:        float = Field(0.0,   ge=-1.0,  le=1.0)
    temperature:   float = Field(6500,  ge=2000,  le=12000)
    tint:          float = Field(0.0,   ge=-100,  le=100)
    # Color wheels
    shadows_r:     float = Field(0.0,   ge=-1.0,  le=1.0)
    shadows_g:     float = Field(0.0,   ge=-1.0,  le=1.0)
    shadows_b:     float = Field(0.0,   ge=-1.0,  le=1.0)
    midtones_r:    float = Field(0.0,   ge=-1.0,  le=1.0)
    midtones_g:    float = Field(0.0,   ge=-1.0,  le=1.0)
    midtones_b:    float = Field(0.0,   ge=-1.0,  le=1.0)
    highlights_r:  float = Field(0.0,   ge=-1.0,  le=1.0)
    highlights_g:  float = Field(0.0,   ge=-1.0,  le=1.0)
    highlights_b:  float = Field(0.0,   ge=-1.0,  le=1.0)
    # Look preset
    look:          str   = "none"
    format:        str   = "mp4"

class FrameExtractRequest(BaseModel):
    job_id: str
    time:   float = Field(0.0, ge=0.0)
    count:  int   = Field(1, ge=1, le=100)

class GroqAnalyzeRequest(BaseModel):
    job_id:       str
    groq_api_key: str
    frame_b64:    Optional[str] = None
    query:        Optional[str] = None
    analysis_type: str = "full"  # full | color | technical | narrative

class GroqChatRequest(BaseModel):
    groq_api_key: str
    messages:     List[Dict[str, str]]
    video_context: Optional[Dict[str, Any]] = None

class SetApiKeyRequest(BaseModel):
    groq_api_key: str

# ── Helpers ────────────────────────────────────────────────────────────────────

def _job(job_id: str) -> dict:
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404, f"Job not found: {job_id}")
    return j

def _new_job(name: str, path: Path, info: dict) -> str:
    jid = uuid.uuid4().hex[:12]
    JOBS[jid] = {
        "id": jid, "name": name, "path": str(path),
        "info": info, "status": "ready", "progress": 0,
        "result": None, "error": None,
        "created": datetime.utcnow().isoformat(),
        "output": None
    }
    return jid

def _update(jid: str, **kw):
    if jid in JOBS:
        JOBS[jid].update(kw)

async def _run_job(jid: str, coro):
    try:
        _update(jid, status="running", progress=5)
        result = await coro
        _update(jid, status="done", progress=100, result=result)
    except Exception as e:
        _update(jid, status="error", error=str(e))
        traceback.print_exc()

# ── Upload ─────────────────────────────────────────────────────────────────────

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    jid  = uuid.uuid4().hex[:12]
    ext  = Path(file.filename).suffix.lower() or ".mp4"
    dest = UPLOADS / f"{jid}{ext}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    info = await P.run_probe_async(dest)
    vid  = next((s for s in info.get("streams",[]) if s.get("codec_type")=="video"), {})
    aud  = next((s for s in info.get("streams",[]) if s.get("codec_type")=="audio"), {})
    fmt  = info.get("format", {})
    probe = {
        "width":    int(vid.get("width",0)),
        "height":   int(vid.get("height",0)),
        "fps":      vid.get("r_frame_rate","0/1"),
        "duration": float(fmt.get("duration",0)),
        "size_mb":  round(dest.stat().st_size/1_048_576,2),
        "codec":    vid.get("codec_name","?"),
        "audio":    aud.get("codec_name","none"),
        "bitrate":  fmt.get("bit_rate","?"),
        "color_space": vid.get("color_space","?"),
        "pix_fmt":  vid.get("pix_fmt","?"),
    }
    jid2 = _new_job(file.filename, dest, probe)
    # Generate thumbnail
    thumb_path = THUMBS / f"{jid2}.jpg"
    await P.run_ff("-ss","1","-i",str(dest),"-frames:v","1","-q:v","3",str(thumb_path))
    if thumb_path.exists():
        with open(thumb_path,"rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        JOBS[jid2]["thumb_b64"] = f"data:image/jpeg;base64,{b64}"
    return {"job_id": jid2, **probe, "filename": file.filename}

@app.get("/jobs")
def list_jobs():
    return list(JOBS.values())

@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    return _job(job_id)

@app.delete("/jobs/{job_id}")
def delete_job(job_id: str):
    j = _job(job_id)
    for k in ["path","output"]:
        p = j.get(k)
        if p and Path(p).exists():
            Path(p).unlink(missing_ok=True)
    del JOBS[job_id]
    return {"ok": True}

# ── Frame extraction ───────────────────────────────────────────────────────────

@app.post("/frames/extract")
async def extract_frames(req: FrameExtractRequest):
    j = _job(req.job_id)
    src = Path(j["path"])
    out_dir = FRAMES / req.job_id
    out_dir.mkdir(exist_ok=True)
    frames = []
    step = req.count
    for i in range(step):
        t = req.time + i
        out = out_dir / f"f_{t:.3f}.jpg"
        await P.run_ff("-ss", str(t), "-i", P.fp(src), "-frames:v","1","-q:v","2", P.fp(out))
        if out.exists():
            with open(out,"rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            frames.append({"time": t, "data": f"data:image/jpeg;base64,{b64}"})
    return {"frames": frames}

@app.get("/frame/{job_id}")
async def get_frame(job_id: str, t: float = Query(0.0)):
    j = _job(job_id)
    src = Path(j["path"])
    out_dir = FRAMES / job_id
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"f_{t:.2f}.jpg"
    if not out.exists():
        await P.run_ff("-ss", str(t), "-i", P.fp(src), "-frames:v","1","-q:v","2", P.fp(out))
    if out.exists():
        return FileResponse(str(out), media_type="image/jpeg")
    raise HTTPException(404, "Frame not found")

@app.get("/thumbnail/{job_id}")
def get_thumb(job_id: str):
    p = THUMBS / f"{job_id}.jpg"
    if p.exists():
        return FileResponse(str(p), media_type="image/jpeg")
    raise HTTPException(404, "Thumbnail not found")

# ── Enhancement ────────────────────────────────────────────────────────────────

@app.post("/enhance")
async def enhance(req: EnhanceRequest, bg: BackgroundTasks):
    j = _job(req.job_id)
    ext = ".mp4" if "mp4" in req.format else f".{req.format}"
    out = OUTPUTS / f"{req.job_id}_enhanced{ext}"
    _update(req.job_id, status="queued", progress=0, output=str(out))
    bg.add_task(_run_job, req.job_id,
        P.enhance_video(Path(j["path"]), out, req.enhancements,
                        req.resolution, req.format, req.quality,
                        req.color_strength, req.denoise_strength,
                        lambda p: _update(req.job_id, progress=p)))
    return {"job_id": req.job_id, "status": "queued"}

@app.post("/color-grade/{job_id}")
async def color_grade(job_id: str, req: ColorGradeRequest, bg: BackgroundTasks):
    j = _job(job_id)
    out = OUTPUTS / f"{job_id}_graded.mp4"
    _update(job_id, status="queued", progress=0, output=str(out))
    bg.add_task(_run_job, job_id,
        P.color_grade_video(Path(j["path"]), out, req,
                            lambda p: _update(job_id, progress=p)))
    return {"job_id": job_id, "status": "queued"}

@app.post("/trim/{job_id}")
async def trim(job_id: str, req: TrimRequest, bg: BackgroundTasks):
    j = _job(job_id)
    out = OUTPUTS / f"{job_id}_trim.mp4"
    _update(job_id, status="queued", output=str(out))
    bg.add_task(_run_job, job_id, P.trim_video(Path(j["path"]), out, req.start, req.end))
    return {"job_id": job_id, "status": "queued"}

@app.post("/compress/{job_id}")
async def compress(job_id: str, req: CompressRequest, bg: BackgroundTasks):
    j = _job(job_id)
    out = OUTPUTS / f"{job_id}_compressed.mp4"
    _update(job_id, status="queued", output=str(out))
    bg.add_task(_run_job, job_id, P.compress_video(Path(j["path"]), out, req.target_mb, req.crf))
    return {"job_id": job_id, "status": "queued"}

@app.post("/convert/{job_id}")
async def convert(job_id: str, req: ConvertRequest, bg: BackgroundTasks):
    j = _job(job_id)
    out = OUTPUTS / f"{job_id}_converted.{req.format.split('-')[0]}"
    _update(job_id, status="queued", output=str(out))
    bg.add_task(_run_job, job_id, P.convert_video(Path(j["path"]), out, req.format))
    return {"job_id": job_id, "status": "queued"}

@app.post("/speed/{job_id}")
async def speed(job_id: str, req: SpeedRequest, bg: BackgroundTasks):
    j = _job(job_id)
    out = OUTPUTS / f"{job_id}_speed.mp4"
    _update(job_id, status="queued", output=str(out))
    bg.add_task(_run_job, job_id, P.speed_video(Path(j["path"]), out, req.factor))
    return {"job_id": job_id, "status": "queued"}

@app.post("/chromakey/{job_id}")
async def chromakey(job_id: str, req: ChromaRequest, bg: BackgroundTasks):
    j = _job(job_id)
    out = OUTPUTS / f"{job_id}_chroma.mp4"
    _update(job_id, status="queued", output=str(out))
    bg.add_task(_run_job, job_id, P.chroma_key(Path(j["path"]), out, req.color, req.similarity, req.blend))
    return {"job_id": job_id, "status": "queued"}

@app.post("/region/{job_id}")
async def region_blur(job_id: str, req: RegionRequest, bg: BackgroundTasks):
    j = _job(job_id)
    out = OUTPUTS / f"{job_id}_region.mp4"
    _update(job_id, status="queued", output=str(out))
    bg.add_task(_run_job, job_id,
        P.region_effect(Path(j["path"]), out, req.x, req.y, req.width, req.height, req.effect))
    return {"job_id": job_id, "status": "queued"}

@app.post("/watermark/{job_id}")
async def watermark(job_id: str, req: WatermarkRequest, bg: BackgroundTasks):
    j = _job(job_id)
    out = OUTPUTS / f"{job_id}_watermarked.mp4"
    _update(job_id, status="queued", output=str(out))
    bg.add_task(_run_job, job_id,
        P.add_watermark(Path(j["path"]), out, req.text, req.position, req.opacity))
    return {"job_id": job_id, "status": "queued"}

@app.get("/download/{job_id}")
def download(job_id: str):
    j = _job(job_id)
    if j["status"] != "done":
        raise HTTPException(400, f"Job not done: {j['status']}")
    out = j.get("output") or j.get("path")
    p = Path(out) if out else None
    if not p or not p.exists():
        raise HTTPException(404, "Output file not found")
    return FileResponse(str(p), filename=p.name,
                        media_type="application/octet-stream")

# ── AI / Groq ──────────────────────────────────────────────────────────────────

@app.post("/ai/key")
def set_api_key(req: SetApiKeyRequest):
    AI_CONFIG["groq_api_key"] = req.groq_api_key
    return {"ok": True, "message": "Groq API key saved"}

@app.post("/ai/analyze")
async def ai_analyze(req: GroqAnalyzeRequest):
    if not _HTTPX_OK:
        raise HTTPException(503, "httpx not installed")
    key = req.groq_api_key or AI_CONFIG.get("groq_api_key","")
    if not key:
        raise HTTPException(401, "Groq API key required")
    j = _job(req.job_id)
    info = j.get("info", {})

    prompts = {
        "full": f"""You are a professional cinematographer and colorist analyzing footage for post-production.
Video metadata: {_json.dumps(info, indent=2)}
Analyze this footage and provide:
1. Technical quality assessment (exposure, focus, noise level, stability)
2. Color profile analysis (color temperature, dominant colors, white balance)
3. Recommended post-production enhancements with priority order
4. Suggested color grade look (e.g., warm cinematic, cold thriller, vibrant commercial)
5. Specific FFmpeg-compatible parameter suggestions
Be precise, technical, and actionable. Format as JSON with keys: technical_score, color_analysis, enhancements, color_grade_suggestion, parameters.""",
        
        "color": f"""You are a professional colorist. Analyze this video's metadata and suggest a comprehensive color grade.
Video info: {_json.dumps(info, indent=2)}
Provide specific values for: exposure, contrast, saturation, highlights, shadows, temperature, color wheel adjustments.
Suggest a named look (e.g., 'Teal & Orange Hollywood', 'Warm Sunset', 'Cold Nordic Thriller').
Return JSON with: look_name, exposure, contrast, saturation, highlights, shadows, temperature, tint, color_wheel_shadows, color_wheel_midtones, color_wheel_highlights, reasoning""",
        
        "technical": f"""You are a video engineer. Analyze this footage technically.
Metadata: {_json.dumps(info, indent=2)}
Identify: noise level, compression artifacts, dynamic range issues, frame rate problems, codec efficiency.
Suggest technical corrections. Return JSON with: noise_level, dynamic_range_score, codec_efficiency, recommendations""",
        
        "narrative": f"""You are a film director analyzing footage. Based on these technical specs: {_json.dumps(info, indent=2)}
Suggest: scene type (action, dialogue, landscape, etc.), pacing suggestions, visual storytelling enhancements,
mood/tone adjustments. Return JSON with: scene_type, mood, pacing_score, storytelling_suggestions, visual_style"""
    }
    
    prompt = prompts.get(req.analysis_type, prompts["full"])
    if req.query:
        prompt += f"\n\nAdditional context from filmmaker: {req.query}"

    try:
        async with _httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1500,
                    "temperature": 0.3,
                    "response_format": {"type": "json_object"}
                }
            )
        data = r.json()
        if r.status_code != 200:
            raise HTTPException(r.status_code, data.get("error", {}).get("message", "Groq error"))
        content = data["choices"][0]["message"]["content"]
        try:
            parsed = _json.loads(content)
        except:
            parsed = {"raw": content}
        return {"analysis": parsed, "model": "llama-3.3-70b-versatile", "type": req.analysis_type}
    except _httpx.TimeoutException:
        raise HTTPException(504, "Groq API timeout")
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/ai/chat")
async def ai_chat(req: GroqChatRequest):
    if not _HTTPX_OK:
        raise HTTPException(503, "httpx not installed")
    key = req.groq_api_key or AI_CONFIG.get("groq_api_key","")
    if not key:
        raise HTTPException(401, "Groq API key required")

    system = """You are PixelForge AI Director — an expert cinematographer, colorist, and post-production supervisor with 20+ years of experience.
You help filmmakers enhance their footage, suggest color grades, identify technical issues, and provide creative direction.
You know FFmpeg deeply and can suggest exact filter parameters. Be concise, technical, and creative.
When suggesting color grades or effects, provide specific numerical values whenever possible."""

    if req.video_context:
        system += f"\n\nCurrent video context: {_json.dumps(req.video_context)}"

    messages = [{"role":"system","content":system}] + req.messages

    try:
        async with _httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": messages,
                    "max_tokens": 1000,
                    "temperature": 0.7
                }
            )
        data = r.json()
        if r.status_code != 200:
            raise HTTPException(r.status_code, data.get("error",{}).get("message","Groq error"))
        return {
            "reply": data["choices"][0]["message"]["content"],
            "model": "llama-3.3-70b-versatile",
            "usage": data.get("usage", {})
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/health")
def health():
    return {
        "status": "ok",
        "uptime": round(time.time()-T0, 1),
        "jobs": len(JOBS),
        "ai_configured": bool(AI_CONFIG.get("groq_api_key")),
        "ffmpeg": bool(P.FFMPEG),
        "version": "6.0.0"
    }


# ── Static files ────────────────────────────────────────────────────────────────
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="static")
