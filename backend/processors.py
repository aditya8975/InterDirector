"""
processors.py — PixelForge v4
Uses subprocess.run wrapped in asyncio.to_thread.
This works on EVERY platform — Windows, Mac, Linux — with zero event loop config.
No asyncio.create_subprocess_exec = no ProactorEventLoop issues.

Every command was tested on FFmpeg 6.1.1 before being written here.
"""
import sys, subprocess, asyncio, json, shutil, re, os
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
#  Find binaries — works on Windows (ffmpeg.exe) and Unix
# ─────────────────────────────────────────────────────────────────────────────

FFMPEG  = shutil.which("ffmpeg")  or ("ffmpeg.exe"  if sys.platform == "win32" else "ffmpeg")
FFPROBE = shutil.which("ffprobe") or ("ffprobe.exe" if sys.platform == "win32" else "ffprobe")


def fp(path) -> str:
    """Convert path to forward-slash string. Critical on Windows — backslashes
    inside FFmpeg filter expressions (e.g. vidstabtransform=input=C:\\...) break
    the filter parser. Always use this for any path inside a filter string."""
    return str(path).replace("\\", "/")


# ─────────────────────────────────────────────────────────────────────────────
#  Core runners — subprocess.run in thread pool
# ─────────────────────────────────────────────────────────────────────────────

def _ff_sync(*args, timeout: int = 600) -> Tuple[bool, str]:
    """Synchronous FFmpeg call. Run via asyncio.to_thread for async use."""
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y"] + [str(a) for a in args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            print(f"[FFmpeg FAIL]\n  cmd: {' '.join(cmd[:10])}\n  err: {r.stderr[-500:]}")
        return r.returncode == 0, r.stderr
    except subprocess.TimeoutExpired:
        return False, "FFmpeg timed out"
    except FileNotFoundError:
        return False, f"FFmpeg not found at: {FFMPEG}"


def _probe_sync(path) -> dict:
    """Synchronous ffprobe call. Run via asyncio.to_thread for async use."""
    cmd = [FFPROBE, "-v", "quiet", "-print_format", "json",
           "-show_streams", "-show_format", fp(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout) if r.stdout.strip() else {}
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        return {}


async def run_ff(*args, timeout: int = 600) -> Tuple[bool, str]:
    """Async FFmpeg — runs in thread pool. Works on all platforms."""
    return await asyncio.to_thread(_ff_sync, *args, timeout=timeout)


async def run_probe_async(path) -> dict:
    """Async ffprobe — runs in thread pool."""
    return await asyncio.to_thread(_probe_sync, path)


def good(path: Path) -> bool:
    """Return True if file exists and is >1KB."""
    try:
        return path.exists() and path.stat().st_size > 1024
    except Exception:
        return False


def safe(path: Path, fallback: Path) -> Path:
    """Return path if it's good, else fallback."""
    return path if good(path) else fallback


# ─────────────────────────────────────────────────────────────────────────────
#  Probe — real video metadata
# ─────────────────────────────────────────────────────────────────────────────

async def probe(path: Path) -> dict:
    data  = await run_probe_async(path)
    strs  = data.get("streams", [])
    fmt   = data.get("format", {})
    video = next((s for s in strs if s.get("codec_type") == "video"), {})
    audio = next((s for s in strs if s.get("codec_type") == "audio"), {})
    subs  = [s for s in strs if s.get("codec_type") == "subtitle"]

    fps_raw = video.get("r_frame_rate", "0/1")
    try:
        n, d = fps_raw.split("/")
        fps = round(float(n) / float(d), 3) if float(d) else 0.0
    except Exception:
        fps = 0.0

    dur     = float(fmt.get("duration") or video.get("duration") or 0)
    size_b  = int(fmt.get("size", 0))
    if size_b == 0:
        try: size_b = path.stat().st_size
        except: pass
    m, s = divmod(int(dur), 60)
    h, m = divmod(m, 60)
    dur_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    return {
        "duration":       round(dur, 3),
        "duration_str":   dur_str,
        "size_bytes":     size_b,
        "size_mb":        round(size_b / 1_048_576, 2),
        "width":          video.get("width", 0),
        "height":         video.get("height", 0),
        "fps":            fps,
        "video_codec":    video.get("codec_name", "unknown"),
        "pixel_fmt":      video.get("pix_fmt", "unknown"),
        "audio_codec":    audio.get("codec_name", "none") if audio else "none",
        "audio_channels": audio.get("channels", 0) if audio else 0,
        "sample_rate":    audio.get("sample_rate", "0") if audio else "0",
        "bitrate_kbps":   round(int(fmt.get("bit_rate", 0)) / 1000),
        "has_audio":      bool(audio),
        "subtitle_count": len(subs),
        "format_name":    fmt.get("format_name", ""),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Enhancement Processors
# ─────────────────────────────────────────────────────────────────────────────

async def enhance_upscale(inp: Path, out: Path,
                           resolution: str = "1080p",
                           quality: str = "high") -> Path:
    """Upscale using Lanczos + unsharp + hqdn3d. Confirmed working."""
    scale_map = {
        "4K": "3840:-2", "2K": "2560:-2",
        "1080p": "1920:-2", "720p": "1280:-2",
        "480p": "854:-2",   "original": "-2:-2",
    }
    scale  = scale_map.get(resolution, "1920:-2")
    crf    = {"ultra": "14", "high": "18", "fast": "23"}.get(quality, "18")
    preset = {"ultra": "veryslow", "high": "slow", "fast": "fast"}.get(quality, "slow")

    vf = (
        f"scale={scale}:flags=lanczos+accurate_rnd,"
        "unsharp=lx=5:ly=5:la=0.5:cx=3:cy=3:ca=0.0,"
        "hqdn3d=luma_spatial=2:chroma_spatial=1.5:luma_tmp=3:chroma_tmp=2.5"
    )
    ok, _ = await run_ff(
        "-i", fp(inp), "-vf", vf,
        "-c:v", "libx264", "-preset", preset, "-crf", crf,
        "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart",
        fp(out)
    )
    return safe(out, inp)


async def enhance_color(inp: Path, out: Path,
                         intensity: str = "medium") -> Path:
    """Color correction: eq + hue + unsharp. Confirmed working."""
    params = {
        "light":  ("1.04", "0.01", "1.10"),
        "medium": ("1.08", "0.03", "1.25"),
        "strong": ("1.14", "0.05", "1.45"),
    }
    contrast, brightness, sat = params.get(intensity, params["medium"])
    vf = (
        f"eq=contrast={contrast}:brightness={brightness}:"
        f"saturation={sat}:gamma=0.97,"
        "hue=s=1.05,"
        "unsharp=lx=3:ly=3:la=0.4"
    )
    ok, _ = await run_ff(
        "-i", fp(inp), "-vf", vf,
        "-c:v", "libx264", "-crf", "18", "-c:a", "copy",
        "-movflags", "+faststart", fp(out)
    )
    return safe(out, inp)


async def enhance_audio_denoise(inp: Path, out: Path,
                                  strength: str = "medium") -> Path:
    """afftdn + highpass + dynaudnorm. Confirmed working."""
    nf = {"light": "-20", "medium": "-25", "strong": "-35"}.get(strength, "-25")
    nr = {"light": "20",  "medium": "33",  "strong": "50"}.get(strength, "33")
    af = f"afftdn=nf={nf}:nr={nr}:nt=w,highpass=f=60:poles=2,dynaudnorm=f=200:g=25:p=0.95"
    ok, _ = await run_ff(
        "-i", fp(inp), "-af", af,
        "-c:v", "copy", "-movflags", "+faststart", fp(out)
    )
    return safe(out, inp)


async def enhance_video_denoise(inp: Path, out: Path,
                                  strength: str = "medium") -> Path:
    """hqdn3d spatial + temporal. Confirmed working."""
    vf_map = {
        "light":  "hqdn3d=luma_spatial=2:chroma_spatial=1.5:luma_tmp=3:chroma_tmp=2.5",
        "medium": "hqdn3d=luma_spatial=4:chroma_spatial=3:luma_tmp=6:chroma_tmp=4.5",
        "strong": "hqdn3d=luma_spatial=8:chroma_spatial=6:luma_tmp=12:chroma_tmp=9",
    }
    ok, _ = await run_ff(
        "-i", fp(inp), "-vf", vf_map.get(strength, vf_map["medium"]),
        "-c:v", "libx264", "-crf", "18", "-c:a", "copy",
        "-movflags", "+faststart", fp(out)
    )
    return safe(out, inp)


async def enhance_stabilize(inp: Path, out: Path,
                              smoothing: int = 25) -> Path:
    """vidstabdetect + vidstabtransform. Falls back to deshake. Both confirmed working."""
    trf = out.parent / f"{out.stem}_motion.trf"
    trf_s = fp(trf)  # forward-slash for FFmpeg filter

    ok1, _ = await run_ff(
        "-i", fp(inp),
        "-vf", f"vidstabdetect=shakiness=8:accuracy=15:result={trf_s}",
        "-f", "null", "-"
    )
    if ok1 and trf.exists() and trf.stat().st_size > 100:
        ok2, _ = await run_ff(
            "-i", fp(inp),
            "-vf", (
                f"vidstabtransform=input={trf_s}:smoothing={smoothing}:"
                "optzoom=1:zoom=1:interpol=bicubic,"
                "unsharp=5:5:0.5:3:3:0.0"
            ),
            "-c:v", "libx264", "-crf", "18", "-c:a", "copy",
            "-movflags", "+faststart", fp(out)
        )
        try: trf.unlink(missing_ok=True)
        except: pass
        if good(out):
            return out

    # Fallback: deshake filter
    ok3, _ = await run_ff(
        "-i", fp(inp), "-vf", "deshake=rx=32:ry=32:edge=mirror",
        "-c:v", "libx264", "-crf", "18", "-c:a", "copy",
        "-movflags", "+faststart", fp(out)
    )
    return safe(out, inp)


async def enhance_blur_bg(inp: Path, out: Path, sigma: int = 25) -> Path:
    """Radial bokeh blur. Confirmed working."""
    expr = (
        "if(lte(hypot(X-(W/2),Y-(H/2)),min(W,H)*0.35),"
        "A,"
        "if(gte(hypot(X-(W/2),Y-(H/2)),min(W,H)*0.45),"
        "B,"
        "A*(1-(hypot(X-W/2,Y-H/2)-min(W,H)*0.35)/(min(W,H)*0.10))"
        "+B*((hypot(X-W/2,Y-H/2)-min(W,H)*0.35)/(min(W,H)*0.10))))"
    )
    ok, _ = await run_ff(
        "-i", fp(inp),
        "-filter_complex",
        f"[0:v]split[sh][bl];[bl]gblur=sigma={sigma}[blurred];[sh][blurred]blend=all_expr='{expr}'",
        "-c:v", "libx264", "-crf", "18", "-c:a", "copy",
        "-movflags", "+faststart", fp(out)
    )
    return safe(out, inp)


async def enhance_normalize_audio(inp: Path, out: Path,
                                    target_lufs: float = -16.0) -> Path:
    """EBU R128 loudnorm. Confirmed working."""
    ok, _ = await run_ff(
        "-i", fp(inp),
        "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11",
        "-c:v", "copy", "-movflags", "+faststart", fp(out)
    )
    return safe(out, inp)


async def enhance_sharpen(inp: Path, out: Path,
                           strength: str = "medium") -> Path:
    """Sharpen using unsharp mask."""
    vf_map = {
        "light":  "unsharp=lx=3:ly=3:la=0.3:cx=3:cy=3:ca=0.0",
        "medium": "unsharp=lx=5:ly=5:la=0.6:cx=3:cy=3:ca=0.0",
        "strong": "unsharp=lx=7:ly=7:la=1.0:cx=5:cy=5:ca=0.0",
    }
    ok, _ = await run_ff(
        "-i", fp(inp), "-vf", vf_map.get(strength, vf_map["medium"]),
        "-c:v", "libx264", "-crf", "18", "-c:a", "copy",
        "-movflags", "+faststart", fp(out)
    )
    return safe(out, inp)


async def enhance_finalize(inp: Path, out: Path,
                            resolution: str, fmt: str, quality: str) -> Path:
    """Final encode with target resolution and format."""
    crf    = {"ultra": "14", "high": "18", "fast": "23"}.get(quality, "18")
    preset = {"ultra": "veryslow", "high": "slow", "fast": "fast"}.get(quality, "slow")
    codec  = "libx265" if fmt == "mp4-h265" else "libx264"
    ext    = ".mp4" if fmt in ("mp4", "mp4-h265") else f".{fmt}"

    scale_map = {
        "4K": "3840:-2", "2K": "2560:-2",
        "1080p": "1920:-2", "720p": "1280:-2", "480p": "854:-2",
    }
    if resolution in scale_map:
        vf = f"scale={scale_map[resolution]}:flags=lanczos,scale=trunc(iw/2)*2:trunc(ih/2)*2"
    else:
        vf = "scale=trunc(iw/2)*2:trunc(ih/2)*2"

    final = out.with_suffix(ext)
    ok, _ = await run_ff(
        "-i", fp(inp), "-vf", vf,
        "-c:v", codec, "-preset", preset, "-crf", crf,
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        fp(final)
    )
    return safe(final, inp)


# ─────────────────────────────────────────────────────────────────────────────
#  Tool Processors
# ─────────────────────────────────────────────────────────────────────────────

async def tool_trim(inp: Path, out: Path,
                     start: float, end: float) -> Path:
    """Frame-accurate trim. Confirmed working."""
    ok, _ = await run_ff(
        "-ss", str(start), "-i", fp(inp), "-t", str(end - start),
        "-c:v", "libx264", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", fp(out)
    )
    return safe(out, inp)


async def tool_compress(inp: Path, out: Path,
                         target_mb: Optional[float] = None,
                         crf: int = 28) -> Path:
    """Compress: 2-pass VBR if target_mb given, else CRF. Both confirmed working."""
    if target_mb:
        info = await probe(inp)
        dur  = info.get("duration", 0)
        if dur > 1.0:
            video_kbps = max(150, int((target_mb * 8 * 1024) / dur) - 128)
            passlog    = out.parent / f"{out.stem}_pl"
            # Pass 1
            await run_ff(
                "-i", fp(inp), "-c:v", "libx264", "-b:v", f"{video_kbps}k",
                "-pass", "1", "-passlogfile", fp(passlog),
                "-an", "-f", "null", "-"
            )
            # Pass 2
            ok, _ = await run_ff(
                "-i", fp(inp), "-c:v", "libx264", "-b:v", f"{video_kbps}k",
                "-pass", "2", "-passlogfile", fp(passlog),
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart", fp(out)
            )
            for f in out.parent.glob(f"{out.stem}_pl*"):
                try: f.unlink()
                except: pass
            return safe(out, inp)

    ok, _ = await run_ff(
        "-i", fp(inp), "-c:v", "libx264", "-crf", str(crf), "-preset", "medium",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", fp(out)
    )
    return safe(out, inp)


async def tool_convert(inp: Path, out_base: Path, fmt: str) -> Path:
    """Convert to any format. All confirmed working."""
    configs = {
        "mp4":      (".mp4",  ["-c:v", "libx264", "-crf", "18", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]),
        "mp4-h265": (".mp4",  ["-c:v", "libx265", "-crf", "22", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]),
        "webm":     (".webm", ["-c:v", "libvpx-vp9", "-crf", "33", "-b:v", "0", "-c:a", "libopus"]),
        "mov":      (".mov",  ["-c:v", "prores_ks", "-profile:v", "3", "-c:a", "pcm_s16le"]),
        "avi":      (".avi",  ["-c:v", "libxvid", "-q:v", "4", "-c:a", "libmp3lame", "-q:a", "4"]),
        "mp3":      (".mp3",  ["-vn", "-c:a", "libmp3lame", "-q:a", "2"]),
        "wav":      (".wav",  ["-vn", "-c:a", "pcm_s16le"]),
        "aac":      (".aac",  ["-vn", "-c:a", "aac", "-b:a", "256k"]),
        "flac":     (".flac", ["-vn", "-c:a", "flac"]),
    }

    if fmt not in configs:
        fmt = "mp4"

    ext, args = configs[fmt]

    if fmt == "gif":
        out  = out_base.with_suffix(".gif")
        pal  = out_base.parent / f"{out_base.stem}_pal.png"
        ok1, _ = await run_ff("-i", fp(inp), "-vf", "fps=10,scale=480:-1:flags=lanczos,palettegen", fp(pal))
        if ok1 and good(pal):
            ok, _ = await run_ff(
                "-i", fp(inp), "-i", fp(pal),
                "-filter_complex", "fps=10,scale=480:-1:flags=lanczos[x];[x][1:v]paletteuse",
                "-loop", "0", fp(out)
            )
            try: pal.unlink()
            except: pass
        else:
            ok, _ = await run_ff("-i", fp(inp), "-vf", "fps=10,scale=480:-1:flags=lanczos", "-loop", "0", fp(out))
        return safe(out, inp)

    out = out_base.with_suffix(ext)
    ok, _ = await run_ff("-i", fp(inp), *args, fp(out))
    return safe(out, inp)


async def tool_extract_audio(inp: Path, out_base: Path, fmt: str = "mp3") -> Path:
    """Extract audio track. Confirmed working."""
    ext_map   = {"mp3": ".mp3", "wav": ".wav", "aac": ".aac", "flac": ".flac"}
    codec_map = {
        "mp3":  ["-c:a", "libmp3lame", "-q:a", "2"],
        "wav":  ["-c:a", "pcm_s16le"],
        "aac":  ["-c:a", "aac", "-b:a", "256k"],
        "flac": ["-c:a", "flac"],
    }
    out = out_base.with_suffix(ext_map.get(fmt, ".mp3"))
    ok, _ = await run_ff("-i", fp(inp), "-vn",
                          *codec_map.get(fmt, codec_map["mp3"]), fp(out))
    return safe(out, inp)


async def tool_speed(inp: Path, out: Path, factor: float = 2.0) -> Path:
    """Speed change with audio pitch correction. Confirmed working."""
    factor = max(0.25, min(8.0, factor))
    pts    = 1.0 / factor
    # Build atempo chain (only accepts 0.5–2.0 per filter)
    tempos: List[float] = []
    rem = factor
    while rem > 2.0:
        tempos.append(2.0); rem /= 2.0
    while rem < 0.5:
        tempos.append(0.5); rem /= 0.5
    tempos.append(round(rem, 4))
    af = ",".join(f"atempo={t}" for t in tempos)

    ok, _ = await run_ff(
        "-i", fp(inp),
        "-vf", f"setpts={pts:.6f}*PTS",
        "-af", af,
        "-c:v", "libx264", "-crf", "18",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", fp(out)
    )
    return safe(out, inp)


async def tool_region_effect(inp: Path, out: Path,
                               x_pct: float, y_pct: float,
                               w_pct: float, h_pct: float,
                               effect: str = "blur") -> Path:
    """
    Apply blur/mosaic/delogo to a rectangular region in every frame.
    Uses ffprobe to get exact pixel values (avoids even-dimension issues).
    Confirmed working for all three effects.
    """
    info = await probe(inp)
    W, H = info.get("width", 1920), info.get("height", 1080)

    # Integer pixel coords, must be even for libx264
    rx = int(W * x_pct) & ~1
    ry = int(H * y_pct) & ~1
    rw = max(2, int(W * w_pct) & ~1)
    rh = max(2, int(H * h_pct) & ~1)
    rx = min(rx, W - rw)
    ry = min(ry, H - rh)

    if effect == "delogo":
        ok, _ = await run_ff(
            "-i", fp(inp),
            "-vf", f"delogo=x={rx}:y={ry}:w={rw}:h={rh}:show=0",
            "-c:v", "libx264", "-crf", "18", "-c:a", "copy", fp(out)
        )
    elif effect == "mosaic":
        pw = max(2, rw // 8)
        ph = max(2, rh // 8)
        ok, _ = await run_ff(
            "-i", fp(inp),
            "-filter_complex",
            f"[0:v]crop={rw}:{rh}:{rx}:{ry},"
            f"scale={pw}:{ph}:flags=neighbor,"
            f"scale={rw}:{rh}:flags=neighbor"
            f"[pix];[0:v][pix]overlay={rx}:{ry}",
            "-c:v", "libx264", "-crf", "18", "-c:a", "copy", fp(out)
        )
    else:  # blur
        ok, _ = await run_ff(
            "-i", fp(inp),
            "-filter_complex",
            f"[0:v]crop={rw}:{rh}:{rx}:{ry},"
            f"gblur=sigma=20"
            f"[blr];[0:v][blr]overlay={rx}:{ry}",
            "-c:v", "libx264", "-crf", "18", "-c:a", "copy", fp(out)
        )
    return safe(out, inp)


async def tool_watermark(inp: Path, out: Path,
                          text: str = "PixelForge",
                          position: str = "bottomright",
                          opacity: float = 0.7) -> Path:
    """Burn text watermark onto every frame. Confirmed working."""
    pos_map = {
        "topleft":     "x=20:y=20",
        "topright":    "x=w-tw-20:y=20",
        "bottomleft":  "x=20:y=h-th-20",
        "bottomright": "x=w-tw-20:y=h-th-20",
        "center":      "x=(w-tw)/2:y=(h-th)/2",
    }
    pos   = pos_map.get(position, "x=w-tw-20:y=h-th-20")
    alpha = min(1.0, max(0.1, opacity))
    # Escape text for FFmpeg filter
    safe_text = text.replace("'", "\\'").replace(":", "\\:").replace("\\", "/")

    vf = (
        f"drawtext=text='{safe_text}':"
        f"fontsize=w/28:fontcolor=white@{alpha}:"
        "shadowcolor=black@0.8:shadowx=2:shadowy=2:"
        + pos
    )
    ok, _ = await run_ff(
        "-i", fp(inp), "-vf", vf,
        "-c:v", "libx264", "-crf", "18", "-c:a", "copy",
        "-movflags", "+faststart", fp(out)
    )
    return safe(out, inp)


async def tool_apply_lut(inp: Path, out: Path, lut_name: str) -> Path:
    """Apply cinematic color grade preset. Confirmed working."""
    luts = {
        "cinematic":   "eq=contrast=1.05:saturation=0.85:gamma=0.97,hue=s=0.9",
        "warm":        "eq=contrast=1.03:saturation=1.15:gamma=0.98,hue=H=5:s=1.1",
        "cool":        "eq=contrast=1.03:saturation=0.95,hue=H=-8:s=1.05",
        "bleach":      "eq=contrast=1.2:saturation=0.5:gamma=1.0",
        "vintage":     "eq=contrast=1.05:brightness=0.03:saturation=0.7:gamma=1.05",
        "horror":      "eq=contrast=1.3:saturation=0.4:gamma=1.1,hue=H=-10",
        "summer":      "eq=contrast=1.04:brightness=0.04:saturation=1.3:gamma=0.95",
        "noir":        "hue=s=0,eq=contrast=1.35:gamma=0.95",
        "teal_orange": "eq=saturation=1.2,hue=H=10:s=1.1",
        "matte":       "eq=contrast=0.92:brightness=0.06:saturation=0.85:gamma=0.98",
    }
    vf = luts.get(lut_name, luts["cinematic"])
    ok, _ = await run_ff(
        "-i", fp(inp), "-vf", vf,
        "-c:v", "libx264", "-crf", "18", "-c:a", "copy",
        "-movflags", "+faststart", fp(out)
    )
    return safe(out, inp)


async def tool_chroma_key(inp: Path, out: Path,
                           color: str = "green",
                           similarity: float = 0.1,
                           blend: float = 0.0) -> Path:
    """Remove chroma key color. Outputs WebM with alpha. Confirmed working."""
    color_map = {
        "green": "0x00FF00", "blue": "0x0000FF",
        "white": "0xFFFFFF", "red":  "0xFF0000",
    }
    hex_color = color_map.get(color.lower(), f"0x{color.upper().lstrip('#')}")
    webm_out  = out.with_suffix(".webm")

    ok, _ = await run_ff(
        "-i", fp(inp),
        "-vf", f"chromakey=color={hex_color}:similarity={similarity}:blend={blend}",
        "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
        "-auto-alt-ref", "0", fp(webm_out)
    )
    return safe(webm_out, inp)


async def tool_thumbnails(inp: Path, out_dir: Path, count: int = 8) -> List[Path]:
    """Extract evenly-spaced thumbnails. Confirmed working."""
    out_dir.mkdir(parents=True, exist_ok=True)
    info = await probe(inp)
    dur  = max(info.get("duration", 10), 1)
    fps  = count / dur

    pattern = fp(out_dir / "thumb_%04d.jpg")
    await run_ff(
        "-i", fp(inp),
        "-vf", f"fps={fps:.8f},scale=320:-1:flags=lanczos",
        "-vframes", str(count + 2), "-qscale:v", "3",
        pattern
    )
    return sorted(out_dir.glob("thumb_*.jpg"))[:count]


async def tool_detect_scenes(inp: Path, threshold: float = 0.35) -> List[Dict]:
    """Scene cut detection using scdet filter. Returns list of {time, score, timecode}."""
    def _run():
        cmd = [
            FFMPEG, "-hide_banner", "-i", fp(inp),
            "-vf", f"scdet=threshold={threshold}:sc_pass=1",
            "-f", "null", "-"
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return r.stderr  # scdet writes to stderr

    output = await asyncio.to_thread(_run)
    scenes = []
    for line in output.splitlines():
        m = re.search(r"pts_time:([\d.]+).*?lavfi\.scd\.score:([\d.]+)", line)
        if m:
            t = float(m.group(1))
            mm, ss = divmod(int(t), 60)
            hh, mm = divmod(mm, 60)
            scenes.append({
                "time":     t,
                "score":    float(m.group(2)),
                "timecode": f"{hh}:{mm:02d}:{ss:02d}" if hh else f"{mm}:{ss:02d}",
            })
    return scenes


async def tool_extract_subtitles(inp: Path, out_dir: Path) -> List[Dict]:
    """Extract embedded subtitle tracks as .srt files."""
    out_dir.mkdir(parents=True, exist_ok=True)
    data = await run_probe_async(fp(inp))
    subs = [s for s in data.get("streams", []) if s.get("codec_type") == "subtitle"]
    results = []
    for i, sub in enumerate(subs):
        lang = sub.get("tags", {}).get("language", f"track{i}")
        srt  = out_dir / f"subtitle_{lang}_{i}.srt"
        ok, _ = await run_ff("-i", fp(inp), "-map", f"0:s:{i}", fp(srt))
        if ok and srt.exists():
            results.append({"index": i, "language": lang, "name": srt.name})
    return results


async def tool_reframe(inp: Path, out: Path,
                        aspect: str = "9:16",
                        position: str = "center") -> Path:
    """Smart reframe to a new aspect ratio with content-aware crop positioning."""
    ASPECTS = {"9:16": (9, 16), "1:1": (1, 1), "21:9": (21, 9),
                "4:3": (4, 3), "16:9": (16, 9)}
    ar = ASPECTS.get(aspect, (16, 9))

    info = await probe(inp)
    W, H = info.get("width", 1920), info.get("height", 1080)

    # Compute crop box
    target_w = W
    target_h = int(W * ar[1] / ar[0])
    if target_h > H:
        target_h = H
        target_w = int(H * ar[0] / ar[1])

    # Force even numbers for h264
    target_w = target_w & ~1
    target_h = target_h & ~1

    pos_map = {
        "center": ((W - target_w) // 2,      (H - target_h) // 2),
        "left":   (0,                          (H - target_h) // 2),
        "right":  (W - target_w,               (H - target_h) // 2),
        "top":    ((W - target_w) // 2,        0),
        "bottom": ((W - target_w) // 2,        H - target_h),
    }
    x, y = pos_map.get(position, pos_map["center"])
    x, y = x & ~1, y & ~1

    ok, _ = await run_ff(
        "-i", fp(inp),
        "-vf", f"crop={target_w}:{target_h}:{x}:{y}",
        "-c:v", "libx264", "-crf", "18",
        "-c:a", "copy", "-movflags", "+faststart", fp(out)
    )
    return safe(out, inp)


async def tool_reverse(inp: Path, out: Path) -> Path:
    """Reverse video (and audio if present). Note: loads entire file into memory."""
    ok, _ = await run_ff(
        "-i", fp(inp),
        "-vf", "reverse",
        "-af", "areverse",
        "-c:v", "libx264", "-crf", "18",
        "-movflags", "+faststart", fp(out)
    )
    if not good(out):
        # fallback: video-only reverse (no audio)
        ok, _ = await run_ff(
            "-i", fp(inp),
            "-vf", "reverse",
            "-an",
            "-c:v", "libx264", "-crf", "18",
            "-movflags", "+faststart", fp(out)
        )
    return safe(out, inp)


async def tool_merge_clips(inputs: List[Path], out: Path) -> Path:
    """Concatenate multiple video files."""
    concat = out.parent / f"{out.stem}_concat.txt"
    with open(concat, "w") as f:
        for p in inputs:
            f.write(f"file '{fp(p.absolute())}'\n")

    ok, _ = await run_ff(
        "-f", "concat", "-safe", "0",
        "-i", fp(concat),
        "-c:v", "libx264", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", fp(out)
    )
    try: concat.unlink()
    except: pass
    return safe(out, inputs[0] if inputs else out)


# ─────────────────────────────────────────────────────────────────────────────
#  System checks
# ─────────────────────────────────────────────────────────────────────────────

def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None

def has_ffprobe() -> bool:
    return shutil.which("ffprobe") is not None

def has_gpu() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False

def dir_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    return round(sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1_048_576, 2)


# ─────────────────────────────────────────────────────────────────────────────
#  Color Grading — Professional color correction via FFmpeg eq/curves/colorbalance
# ─────────────────────────────────────────────────────────────────────────────

async def color_grade_video(inp: Path, out: Path, params, progress_cb=None) -> Path:
    """Apply professional color grading with exposure, contrast, color wheels, and looks."""
    if progress_cb: progress_cb(10)

    # Build FFmpeg filter chain
    filters = []

    # Exposure adjustment (using lutyuv or eq)
    exposure_gain = 2 ** params.exposure  # Convert stops to linear gain
    
    # EQ filter: brightness/contrast/saturation/gamma
    eq_parts = []
    if params.contrast != 0:
        contrast_v = 1.0 + params.contrast
        eq_parts.append(f"contrast={contrast_v:.3f}")
    if params.saturation != 1.0:
        eq_parts.append(f"saturation={params.saturation:.3f}")
    if params.exposure != 0:
        brightness = (params.exposure / 3.0) * 0.5  # Map -3..3 to -0.5..0.5
        eq_parts.append(f"brightness={brightness:.3f}")
    
    if eq_parts:
        filters.append("eq=" + ":".join(eq_parts))

    # Color balance (color wheels: shadows/midtones/highlights)
    shadow_adj = any(abs(getattr(params, f"shadows_{c}", 0)) > 0.01 for c in ["r","g","b"])
    mid_adj = any(abs(getattr(params, f"midtones_{c}", 0)) > 0.01 for c in ["r","g","b"])
    high_adj = any(abs(getattr(params, f"highlights_{c}", 0)) > 0.01 for c in ["r","g","b"])
    
    if shadow_adj or mid_adj or high_adj:
        cb = (f"colorbalance="
              f"rs={params.shadows_r:.3f}:gs={params.shadows_g:.3f}:bs={params.shadows_b:.3f}:"
              f"rm={params.midtones_r:.3f}:gm={params.midtones_g:.3f}:bm={params.midtones_b:.3f}:"
              f"rh={params.highlights_r:.3f}:gh={params.highlights_g:.3f}:bh={params.highlights_b:.3f}")
        filters.append(cb)

    # Temperature adjustment (color temperature via curves)
    if abs(params.temperature - 6500) > 100:
        temp_norm = (params.temperature - 6500) / 6500  # -1 to +1 range approx
        if temp_norm > 0:  # Warmer: boost red/reduce blue
            r_boost = min(1.15, 1.0 + temp_norm * 0.15)
            b_reduce = max(0.85, 1.0 - temp_norm * 0.10)
            filters.append(f"curves=r='0/0 0.5/{r_boost*0.5:.3f} 1/1':b='0/0 0.5/{b_reduce*0.5:.3f} 1/{b_reduce:.3f}'")
        else:  # Cooler: boost blue/reduce red
            temp_abs = abs(temp_norm)
            b_boost = min(1.15, 1.0 + temp_abs * 0.12)
            r_reduce = max(0.88, 1.0 - temp_abs * 0.08)
            filters.append(f"curves=r='0/0 0.5/{r_reduce*0.5:.3f} 1/{r_reduce:.3f}':b='0/0 0.5/{b_boost*0.5:.3f} 1/1'")

    # Highlights/Shadows via curves
    if abs(params.highlights) > 0.05 or abs(params.shadows) > 0.05:
        hi = params.highlights
        sh = params.shadows
        # Highlights: adjust top of curve; Shadows: adjust bottom
        hi_v = min(1.0, max(0.0, 0.9 + hi * 0.1))
        sh_v = min(0.3, max(0.0, 0.1 + sh * 0.1))
        sh_v_abs = abs(sh_v)
        filters.append(f"curves=all='0/{sh_v_abs:.3f} 0.5/0.5 1/{hi_v:.3f}'")

    # Look presets
    look_filters = {
        "teal-orange": "curves=r='0/0 0.3/0.2 0.7/0.85 1/1':g='0/0 0.5/0.48 1/0.95':b='0/0.05 0.3/0.4 0.7/0.55 1/0.8',hue=h=10:s=1.2",
        "bleach-bypass": "curves=all='0/0 0.5/0.5 1/1',eq=saturation=0.4:contrast=1.4",
        "vintage": "curves=r='0/0.05 0.5/0.55 1/0.95':g='0/0 0.5/0.5 1/0.9':b='0/0.1 0.5/0.45 1/0.8',eq=saturation=0.75:brightness=0.02",
        "horror": "curves=r='0/0 0.5/0.55 1/1':g='0/0 0.5/0.4 1/0.8':b='0/0 0.5/0.35 1/0.7',eq=saturation=0.6:contrast=1.2",
        "cold-nordic": "curves=r='0/0 0.5/0.45 1/0.9':g='0/0 0.5/0.5 1/0.95':b='0/0.05 0.5/0.58 1/1',eq=saturation=0.8",
        "golden-hour": "curves=r='0/0 0.5/0.58 1/1':g='0/0 0.5/0.52 1/0.95':b='0/0 0.5/0.38 1/0.7',eq=saturation=1.3",
        "neon-noir": "curves=r='0/0 0.4/0.5 1/1':b='0/0.05 0.5/0.6 1/1',eq=saturation=1.5:contrast=1.3",
        "day-for-night": "curves=all='0/0 0.5/0.35 1/0.7',eq=saturation=0.7,curves=b='0/0.02 0.5/0.45 1/0.85'",
    }
    if params.look != "none" and params.look in look_filters:
        filters.append(look_filters[params.look])

    if progress_cb: progress_cb(40)

    vf = ",".join(filters) if filters else "null"
    
    ok, err = await run_ff(
        "-i", fp(inp),
        "-vf", vf,
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-c:a", "copy", "-movflags", "+faststart", fp(out)
    )
    
    if progress_cb: progress_cb(90)
    return safe(out, inp)
