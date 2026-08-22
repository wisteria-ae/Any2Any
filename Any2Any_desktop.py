# === Any2Any Final (windowed+safe+mix) build: 2025-08-12 09:43:50Z UTC ===
#!/usr/bin/env python3
from __future__ import annotations

# Any2Any (Local) — single-file desktop app
# - Default dark mode + DPI-aware
# - Options panel inc. "Enable size targeting (Discord/custom cap)"
# - GPU encoders (x264/NVENC/QSV/AMF)
# - yt-dlp tuned + Chrome-impersonation fallback
# - URL→GIF/WebP supported (download then convert)
# - Help → Conversion guide

import os, sys, re, json, shutil, subprocess, traceback, logging, time
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Callable, Dict, List, Tuple

APP_NAME = "Any2Any (Local)"
APP_VERSION = "1.1.1"  # keep in sync with installer/Any2Any.iss MyAppVersion
DEFAULT_OUTPUT_DIR = str(Path.home() / "Downloads")

# ---------- optional deps ----------
try:
    from PIL import Image
except Exception:
    Image = None

try:
    import pillow_heif; pillow_heif.register_heif_opener()
    HAVE_HEIF = True
except Exception:
    HAVE_HEIF = False

try:
    import yt_dlp as ytdlp
except Exception:
    ytdlp = None

try:
    import camelot, pandas as pd
except Exception:
    camelot = None; pd = None

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    HAVE_WATCHDOG = True
except Exception:
    HAVE_WATCHDOG = False

try:
    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import ID3
    from mutagen.mp3 import MP3
    HAVE_MUTAGEN = True
except Exception:
    HAVE_MUTAGEN = False

try:
    import psutil  # optional, used only for diagnostics if present
except Exception:
    psutil = None

# ---------- Qt ----------
from PySide6.QtCore import Qt, QThread, Signal, QSize, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFileDialog, QComboBox, QLineEdit,
    QProgressBar, QListWidget, QListWidgetItem, QMessageBox,
    QGroupBox, QFormLayout, QSpinBox, QCheckBox, QMenuBar, QDialog,
    QDialogButtonBox, QSizePolicy
)

SUPPORTED_OUTPUTS = [
    "mp3","m4a","opus","mp4","gif","webp-anim",
    "png","jpg","webp","avif",
    "pdf","xlsx","csv","json",
    "srt","mp4+subs","mp4+burnsubs",
    "frames","video-from-frames",
    "thumbnail","chapters-csv"
]

# ---- UI helper labels / categories for format picker ----
SELECT_FORMAT_LABEL = "Select a file type"

# Map formats into human-friendly groups for a nicer combobox.
OUTPUT_CATEGORIES = {
    "Videos": ["mp4", "mp4+subs", "mp4+burnsubs", "gif", "webp-anim", "frames", "video-from-frames", "thumbnail"],
    "Audio": ["mp3", "m4a", "opus"],
    "Images": ["png", "jpg", "webp", "avif"],
    "Docs / Data": ["pdf", "xlsx", "csv", "json"],
    "Subtitles & Chapters": ["srt", "chapters-csv"],
}


DISCORD_PRESETS = {
    "Off": 0,
    "Free (10 MB)": 10,
    "Nitro Basic (50 MB)": 50,
    "Nitro (500 MB)": 500,
    "Boosted 50 MB": 50,
    "Boosted 100 MB": 100,
}

# ---------- logging ----------
def _init_logger() -> logging.Logger:
    log_dir = Path(os.getenv("APPDATA", str(Path.home() / "AppData/Roaming"))) / "Any2Any" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"
    logger = logging.getLogger("any2any")
    logger.setLevel(logging.INFO)
    fh = logging.FileHandler(log_file, encoding="utf-8", mode="a")
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    if not logger.handlers:
        logger.addHandler(fh)
    logger.info("=== Any2Any started ===")
    return logger

LOG = _init_logger()

# ---------- helpers ----------
class ConvertError(RuntimeError): pass
def have_binary(name: str) -> bool: return shutil.which(name) is not None

def ff_candidates(names: List[str]) -> List[Path]:
    bases: List[Path] = []
    if getattr(sys, 'frozen', False):
        bases.append(Path(sys.executable).parent)
    bases += [Path(__file__).parent]
    if getattr(sys, "_MEIPASS", None):
        bases.append(Path(sys._MEIPASS))
    bases.append(Path(__file__).parent / "vendor" / "ffmpeg")
    out: List[Path] = []
    for b in bases:
        for n in names:
            p = b / n
            if p.exists(): out.append(p)
    for n in names:
        w = shutil.which(n)
        if w: out.append(Path(w))
    return out

def ensure_ffmpeg() -> str:
    for p in ff_candidates(["ffmpeg.exe","ffmpeg"]):
        if p.exists(): return str(p)
    raise ConvertError("ffmpeg not found. Put ffmpeg.exe in vendor\\ffmpeg or install system-wide.")

def ensure_ffprobe() -> str:
    for p in ff_candidates(["ffprobe.exe","ffprobe"]):
        if p.exists(): return str(p)
    w = shutil.which("ffprobe") or shutil.which("ffmpeg")
    if w: return w
    raise ConvertError("ffprobe not found. Put ffprobe.exe in vendor\\ffmpeg or install system-wide.")
# ---- safety helpers ----
def _test_hw_encoder(enc_name: str) -> tuple[bool, str]:
    """Quick preflight to catch broken GPU encoders (NVENC/QSV/AMF)."""
    try:
        ff = ensure_ffmpeg()
        cmd = [ff, "-hide_banner", "-loglevel", "error",
               "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=1",
               "-c:v", enc_name, "-f", "null", "-"]
        creationflags = 0
        try:
            import platform
            if platform.system() == 'Windows':
                creationflags = 0x00004000
        except Exception:
            pass
        proc = _aa_popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=creationflags)
        out, _ = proc.communicate(timeout=10)
        return proc.returncode == 0, (out or "").strip()
    except subprocess.TimeoutExpired:
        try: proc.kill()
        except: pass
        return False, "timeout"
    except Exception as e:
        return False, str(e)

def _map_encoder_choice(name: str) -> str:
    return {"x264":"libx264","nvenc":"h264_nvenc","qsv":"h264_qsv","amf":"h264_amf"}.get(name,"libx264")


def probe_duration_seconds(path: Path) -> float:
    try:
        out = subprocess.check_output([ensure_ffprobe(), "-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1", str(path)], text=True)
        return float(out.strip())
    except Exception:
        return 0.0

def parse_hhmmss(s: str) -> float:
    s = (s or "").strip()
    if not s: return 0.0
    if re.fullmatch(r"\d+(\.\d+)?", s): return float(s)
    parts = [float(x) for x in s.split(":")]
    if len(parts)==3: h,m,sec=parts; return h*3600+m*60+sec
    if len(parts)==2: m,sec=parts; return m*60+sec
    return 0.0

def safe_basename_from_url(url: str) -> str:
    base = url.rstrip("/").split("/")[-1]
    return re.sub(r"[^A-Za-z0-9._-]", "_", base) or "download"

def get_target_mb(opts: Dict[str,str]) -> int:
    if not opts or opts.get("size_targeting","0")!="1": return 0
    custom = int(opts.get("custom_cap_mb", 0))
    if custom > 0:
        head = int(opts.get("headroom_percent", 0))
        return max(0, custom - int(custom*head/100))
    return int(opts.get("discord_size_mb", 0))

def compute_segment_seconds(target_mb: int, total_bps: int) -> int:
    if target_mb<=0 or total_bps<=0: return 0
    bits = int(target_mb * 1024 * 1024 * 8 * 0.97)
    return max(5, bits // max(total_bps,1))

# ---------- converters ----------
@dataclass
class Job:
    source_path: Optional[str] = None
    source_url: Optional[str] = None
    target_ext: str = ""
    options: Dict[str, str] = None
    output_dir: str = DEFAULT_OUTPUT_DIR
    cancel_flag: bool = False

class Converter:
    accepts: Tuple[str,...]
    produces: Tuple[str,...]
    def can(self, in_ext: str, out_ext: str) -> bool:
        return (in_ext.lower() in self.accepts) and (out_ext.lower() in self.produces)
    def convert(self, job: "Job", progress: Callable[[int,str], None]) -> str: raise NotImplementedError

REGISTRY: list["Converter"] = []
def register(c: "Converter"): REGISTRY.append(c)
def find_conv(in_ext: str, out_ext: str) -> Optional["Converter"]:
    return next((c for c in REGISTRY if c.can(in_ext, out_ext)), None)

def run_proc(cmd: List[str], progress: Callable[[int,str],None], job: "Job"):
    LOG.info("cmd: %s", " ".join(cmd))
    start = time.time()
    try:
        creationflags = 0
        try:
            import platform
            if platform.system() == 'Windows':
                creationflags = 0x00004000
        except Exception:
            pass
        proc = _aa_popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=creationflags)
        pct = 5
        tail = []

        import queue, threading
        q: "queue.Queue" = queue.Queue()
        def _reader():
            try:
                for line in proc.stdout:
                    q.put(line)
            finally:
                q.put(None)  # EOF sentinel
        threading.Thread(target=_reader, daemon=True).start()

        last = time.time()
        while True:
            if job.cancel_flag:
                proc.terminate(); raise ConvertError("Cancelled")
            try:
                line = q.get(timeout=1)
            except queue.Empty:
                if time.time() - last > 30:
                    try: proc.terminate()
                    except: pass
                    raise ConvertError("Encoder stalled (no output >30s). Aborted for safety.")
                continue
            if line is None:
                break
            last = time.time()
            s = line.rstrip()
            tail.append(s)
            if len(tail) > 250: tail.pop(0)
            if "time=" in s or "frame=" in s: pct = min(95, pct+1)
            progress(pct, s[:100])
        ret = proc.wait()
        if ret != 0:
            err = "\n".join(tail[-40:])
            LOG.error("cmd exit=%s after %.6fs; tail:\n%s", ret, time.time()-start, err)
            raise ConvertError(f"Command failed (exit {ret})\n\nLast output:\n{err}")
    except FileNotFoundError as e:
        LOG.error("missing tool: %s", e)
        raise ConvertError(f"Missing tool: {e}")

# -------- images --------
# NVENC/VP9 tuning flags are built directly by encoder_preset_flags() below,
# applied at the call site in VideoToMP4.convert. No global subprocess patching.
_aa_popen = subprocess.Popen
def _img_save_fit(im: object, dst: Path, out_ext: str, limit_mb: int, q0: int, strip: bool):
    if not limit_mb:
        im.save(dst, **({"quality":q0,"optimize":True} if out_ext in ("jpg","jpeg") else {}), **({"exif": b""} if strip else {})); return
    limit = limit_mb*1024*1024; q = q0 if out_ext in ("jpg","jpeg") else None; scale = 1.0
    for _ in range(24):
        tmp = dst.with_suffix(".tmp"+dst.suffix)
        test = im if scale>=0.999 else im.resize((max(1,int(im.width*scale)), max(1,int(im.height*scale))))
        skw = ({"quality":max(25,int(q)), "optimize":True} if q else {})
        if strip: skw["exif"] = b""
        test.save(tmp, **skw); sz = tmp.stat().st_size; tmp.replace(dst)
        if sz <= limit: break
        if q and q > 35: q = int(q*0.85)
        else: scale *= 0.9

class ImageToImage(Converter):
    accepts = ("png","jpg","jpeg","webp","bmp","tiff","heic","heif","avif")
    produces = ("png","jpg","webp","avif")
    def convert(self, job: "Job", progress):
        if Image is None: raise ConvertError("Pillow not installed. pip install pillow")
        src = Path(job.source_path); out = job.target_ext.lower(); dst = Path(job.output_dir) / (src.stem + "." + out)
        o = job.options or {}; target = get_target_mb(o)
        strip = o.get("strip_meta","0")=="1"; max_dim = int(o.get("max_dim",0)); q = int(o.get("quality","90"))
        progress(5,"Opening image")
        try:
            with Image.open(src) as im:
                if max_dim and (im.width>max_dim or im.height>max_dim):
                    r = min(max_dim/im.width, max_dim/im.height); im = im.resize((max(1,int(im.width*r)), max(1,int(im.height*r))))
                if out in ("jpg","jpeg"): im = im.convert("RGB")
                if out=="avif" and not HAVE_HEIF: raise ConvertError("AVIF: pip install pillow-heif")
                progress(60,"Saving")
                _img_save_fit(im, dst, out, target, q, strip)
        except Exception as e:
            raise ConvertError(f"Image conversion failed: {e}")
        progress(100,f"Saved {dst.name}"); return str(dst)
register(ImageToImage())

class ImagesToPDF(Converter):
    accepts = ("png","jpg","jpeg","webp","bmp","tiff")
    produces = ("pdf",)
    def convert(self, job: "Job", progress):
        if Image is None: raise ConvertError("Pillow not installed")
        src = Path(job.source_path); dst = Path(job.output_dir) / (src.stem + ".pdf")
        with Image.open(src) as im: im.convert("RGB").save(dst, "PDF")
        progress(100,f"Saved {dst.name}"); return str(dst)
register(ImagesToPDF())

def _page_in_range(n:int, spec:str)->bool:
    s=(spec or "all").strip().lower()
    if s=="all": return True
    for part in [p.strip() for p in s.split(",") if p.strip()]:
        if "-" in part:
            a,b = part.split("-",1)
            try:
                if int(a)<=n<=int(b): return True
            except: pass
        else:
            try:
                if int(part)==n: return True
            except: pass
    return False

class PDFToImages(Converter):
    accepts = ("pdf",)
    produces = ("png","jpg")
    def convert(self, job: "Job", progress):
        src=Path(job.source_path); out=job.target_ext.lower(); dpi=int((job.options or {}).get("dpi","150"))
        outputs=[]
        if fitz:
            doc = fitz.open(str(src))
            for i, page in enumerate(doc):
                if (job.options or {}).get("pages") and not _page_in_range(i+1, job.options.get("pages","all")): continue
                pix = page.get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72))
                p = Path(job.output_dir)/f"{src.stem}_p{i+1:03d}.{out}"; pix.save(str(p)); outputs.append(str(p))
                progress(min(95, 5+i*90//max(1,doc.page_count)), f"Page {i+1}")
        else:
            tool = shutil.which("pdftoppm")
            if not tool: raise ConvertError("Install PyMuPDF (pip install PyMuPDF) or pdftoppm.")
            base = Path(job.output_dir)/f"{src.stem}_p"
            subprocess.check_call([tool, "-r", str(dpi), str(src), str(base)])
        progress(100, f"Saved {len(outputs)} images")
        return "[MULTI]"+json.dumps(outputs) if len(outputs)>1 else outputs[0]
register(PDFToImages())

# -------- audio/video --------
def video_encoder(name: str) -> str:
    return _map_encoder_choice(name)

def encoder_preset_flags(enc: str) -> List[str]:
    if enc == "x264":
        return ["-preset","veryfast"]
    if enc == "nvenc":
        # safer cross-version NVENC preset (p5 ~= medium/quality), plus quality tuning.
        # -multipass must be one of disabled/qres/fullres (not the old "2pass-full").
        return ["-preset","p5","-rc","vbr_hq","-tune","hq","-multipass","fullres","-profile:v","high"]
    # QSV/AMF often don't have a portable "preset" flag across builds
    return []

class VideoToMP3(Converter):
    accepts=("mp4","mkv","mov","avi","webm","m4a","mp3","wav","flac","opus")
    produces=("mp3",)
    def convert(self, job: "Job", progress):
        ff=ensure_ffmpeg(); src=Path(job.source_path); o=job.options or {}
        target=get_target_mb(o); start=parse_hhmmss(o.get("start","")); end=parse_hhmmss(o.get("end",""))
        loud=o.get("loudnorm","0")=="1"; silence=o.get("silence","0")=="1"; threads=int(o.get("threads","0")); split=o.get("split","0")=="1"; head=int(o.get("headroom_percent","0"))
        if target:
            dur=probe_duration_seconds(src);
            if dur<=0: raise ConvertError("Couldn't read duration")
            max_bits=int((target*1024*1024)*8*(1-head/100.0)); bps=max(64000, min(320000, max_bits//max(int(dur),1))); br=f"{bps//1000}k"
        else:
            br=o.get("bitrate","192k")
        dst=Path(job.output_dir)/(src.stem+".mp3")
        cmd=[ff,"-y"];
        if start: cmd+=["-ss",str(start)]
        if end and end>start: cmd+=["-to",str(end)]
        cmd+=["-i",str(src),"-vn"]
        af=[];
        if loud: af.append("loudnorm")
        if silence: af.append("silenceremove=start_periods=1:start_threshold=-40dB:start_silence=0.5:stop_periods=1:stop_threshold=-40dB:stop_silence=0.5")
        if af: cmd+=["-af",",".join(af)]
        if threads>0: cmd+=["-threads",str(threads)]
        cmd+=["-b:a",br,str(dst)]
        progress(5,f"Encoding MP3 @ {br}"); run_proc(cmd,progress,job)
        outputs=[str(dst)]
        if target and split:
            bps_est=int(br.replace("k",""))*1000; seg=compute_segment_seconds(target,bps_est)
            if seg>=5:
                base=dst.with_name(dst.stem+"_part_%03d.mp3")
                cmd2=[ff,"-y","-i",str(dst),"-c","copy","-f","segment","-segment_time",str(seg),"-reset_timestamps","1",str(base)]
                progress(80,f"Splitting ≤{target}MB"); run_proc(cmd2,progress,job)
                outputs=sorted([str(p) for p in dst.parent.glob(dst.stem+"_part_*.mp3")]) or [str(dst)]
        progress(100,f"Saved {Path(outputs[0]).name}")
        return "[MULTI]"+json.dumps(outputs) if len(outputs)>1 else outputs[0]
register(VideoToMP3())

class VideoToMP4(Converter):
    accepts=("mkv","mov","avi","webm","m4v","mpg","mpeg","flv","mp4")
    produces=("mp4",)
    def convert(self, job: "Job", progress):
        ff=ensure_ffmpeg(); src=Path(job.source_path); o=job.options or {}
        dst=Path(job.output_dir)/(src.stem+".mp4"); target=get_target_mb(o)
        start=parse_hhmmss(o.get("start","")); end=parse_hhmmss(o.get("end",""))
        scale=o.get("scale","original"); fps=o.get("fps","original"); enc=o.get("encoder","x264")

        enc_lib = video_encoder(enc)
        if enc_lib != 'libx264':
            ok, tail = _test_hw_encoder(enc_lib)
            if not ok:
                LOG.warning("hardware encoder %s failed preflight, fallback to libx264. tail=%s", enc_lib, tail)
                progress(1, f"{enc.upper()} encoder unavailable, using x264")
                enc, enc_lib = 'x264', 'libx264'
        threads=int(o.get("threads","0")); split=o.get("split","0")=="1"; head=int(o.get("headroom_percent","0"))
        vf=[];
        if scale in ("1080p","720p","480p","360p"): h={"1080p":1080,"720p":720,"480p":480,"360p":360}[scale]; vf.append(f"scale=-2:{h}")
        if fps!="original": vf.append(f"fps={fps}")

        same_path = (dst.resolve() == src.resolve())
        if same_path: dst = src.with_name(src.stem+"_out.mp4")

        if not target:
            # try fast remux (seekable, mapped, faststart)
            if not vf:
                try:
                    cmd=[ff,"-y"]
                    if start: cmd+=["-ss",str(start)]
                    if end and end>start: cmd+=["-to",str(end)]
                    cmd+=["-i",str(src),"-map","0:v:0","-map","0:a:0?","-c","copy","-movflags","+faststart"]
                    if threads>0: cmd+=["-threads",str(threads)]
                    cmd+=[str(dst)]
                    progress(5,"Fast remux"); run_proc(cmd,progress,job)
                    progress(100,f"Saved {dst.name}"); return str(dst)
                except ConvertError:
                    pass
            # re-encode
            vcodec=enc_lib
            cmd=[ff,"-y"]
            if start: cmd+=["-ss",str(start)]
            if end and end>start: cmd+=["-to",str(end)]
            cmd+=["-i",str(src),"-map","0:v:0","-map","0:a:0?","-c:v",vcodec]
            cmd+=encoder_preset_flags(enc)
            cmd+=["-c:a","aac","-b:a","128k","-movflags","+faststart"]
            if vf: cmd+=["-vf",",".join(vf)]
            if threads>0: cmd+=["-threads",str(threads)]
            cmd+=[str(dst)]
            progress(40,f"Transcoding ({vcodec})"); run_proc(cmd,progress,job)
            progress(100,f"Saved {dst.name}"); return str(dst)

        # size targeted
        dur=probe_duration_seconds(src);
        if dur<=0: raise ConvertError("Couldn't read duration")
        # target bits for the whole file, apply headroom, then convert to per-second rates
        total_bits = int((target*1024*1024)*8*(1 - head/100.0))
        total_bps  = max(64000, total_bits // max(int(dur),1))  # bits per second budget
        # choose audio bitrate and leave the rest for video
        a_bps = 128000 if total_bps > 224000 else 96000
        v_bps = max(64000, total_bps - a_bps)
        v_k=max(100,v_bps//1000); a_k=max(64,a_bps//1000)
        vcodec=enc_lib
        cmd=[ff,"-y"]
        if start: cmd+=["-ss",str(start)]
        if end and end>start: cmd+=["-to",str(end)]
        cmd+=["-i",str(src),"-map","0:v:0","-map","0:a:0?",
              "-c:v",vcodec] + encoder_preset_flags(enc) + \
             ["-b:v",f"{v_k}k","-maxrate",f"{int(v_k*1.2)}k","-bufsize",f"{int(v_k*2)}k",
              "-c:a","aac","-b:a",f"{a_k}k","-movflags","+faststart"]
        if vf: cmd+=["-vf",",".join(vf)]
        if threads>0: cmd+=["-threads",str(threads)]
        cmd+=[str(dst)]
        progress(10,f"Target ≤{target}MB"); run_proc(cmd,progress,job)
        outputs=[str(dst)]
        if split:
            total_bps=int((v_k+a_k)*1000); seg=compute_segment_seconds(target,total_bps)
            if seg>=5:
                base=dst.with_name(dst.stem+"_part_%03d.mp4")
                cmd2=[ff,"-y","-i",str(dst),"-c","copy","-f","segment","-segment_time",str(seg),"-reset_timestamps","1",str(base)]
                progress(85,"Splitting"); run_proc(cmd2,progress,job)
                outputs=sorted([str(p) for p in dst.parent.glob(dst.stem+"_part_*.mp4")]) or [str(dst)]
        progress(100,f"Saved {Path(outputs[0]).name}")
        return "[MULTI]"+json.dumps(outputs) if len(outputs)>1 else outputs[0]
register(VideoToMP4())

class VideoToGIF(Converter):
    accepts=("mp4","mkv","mov","avi","webm")
    produces=("gif",)
    def convert(self, job:"Job", progress):
        ff=ensure_ffmpeg(); src=Path(job.source_path); dst=Path(job.output_dir)/(src.stem+".gif")
        fps=(job.options or {}).get("gif_fps","10"); h=(job.options or {}).get("gif_scale","480"); pal=dst.with_suffix(".png")
        run_proc([ff,"-y","-i",str(src),"-vf",f"fps={fps},scale=-2:{h}:flags=lanczos,palettegen",str(pal)],progress,job)
        run_proc([ff,"-y","-i",str(src),"-i",str(pal),"-lavfi",f"fps={fps},scale=-2:{h}:flags=lanczos [x]; [x][1:v] paletteuse",str(dst)],progress,job)
        try: pal.unlink()
        except: pass
        progress(100,f"Saved {dst.name}"); return str(dst)
register(VideoToGIF())

class VideoToWebPAnim(Converter):
    accepts=("mp4","mkv","mov","avi","webm")
    produces=("webp-anim",)
    def convert(self, job:"Job", progress):
        ff=ensure_ffmpeg(); src=Path(job.source_path); dst=Path(job.output_dir)/(src.stem+".webp")
        fps=(job.options or {}).get("fps","15"); h=(job.options or {}).get("scale","480")
        run_proc([ff,"-y","-i",str(src),"-vf",f"fps={fps},scale=-2:{h}","-loop","0","-c:v","libwebp","-lossless","0","-q:v","70","-preset","picture",str(dst)],progress,job)
        progress(100,f"Saved {dst.name}"); return str(dst)
register(VideoToWebPAnim())

# -------- data / tables / subs --------
def flatten(d, parent=''):
    out={}
    for k,v in d.items():
        nk = f"{parent}.{k}" if parent else k
        if isinstance(v, dict): out.update(flatten(v, nk))
        else: out[nk]=v
    return out

class CSVJSON(Converter):
    accepts=("csv","json"); produces=("json","csv")
    def convert(self, job:"Job", progress):
        src=Path(job.source_path); out=job.target_ext.lower(); dst=Path(job.output_dir)/(src.stem+"."+out)
        if src.suffix.lower()==".csv" and out=="json":
            import csv, json as J
            with open(src, newline='', encoding='utf-8') as f: rows=list(csv.DictReader(f))
            with open(dst,"w",encoding="utf-8") as g: J.dump(rows,g,ensure_ascii=False,indent=2)
        elif src.suffix.lower()==".json" and out=="csv":
            import json as J, csv
            with open(src,encoding="utf-8") as f: data=J.load(f)
            if isinstance(data, dict): data=[data]
            rows=[flatten(x) for x in data if isinstance(x,dict)]
            keys=sorted({k for r in rows for k in r.keys()})
            with open(dst,"w",newline="",encoding="utf-8") as g:
                w=csv.DictWriter(g, fieldnames=keys); w.writeheader(); w.writerows(rows)
        else: raise ConvertError("Unsupported direction")
        progress(100,f"Saved {dst.name}"); return str(dst)
register(CSVJSON())

class PDFtoXLSX(Converter):
    accepts=("pdf",); produces=("xlsx","csv")
    def convert(self, job:"Job", progress):
        if camelot is None or pd is None:
            raise ConvertError("PDF→Excel needs camelot+pandas. pip install 'camelot-py[cv]' pandas openpyxl")
        src=Path(job.source_path); out=job.target_ext.lower(); base=Path(job.output_dir)/src.stem
        pages=(job.options or {}).get("pages","all"); flavor=(job.options or {}).get("flavor","stream"); langs=(job.options or {}).get("ocr_langs","")
        try:
            tables=camelot.read_pdf(str(src), pages=pages, flavor=flavor)
            if tables.n==0: raise RuntimeError("no_tables")
        except Exception:
            if not have_binary("ocrmypdf"): raise ConvertError("No tables; OCR fallback not installed (ocrmypdf).")
            tmp = src.with_name(src.stem+".ocr.pdf")
            cmd=["ocrmypdf","--skip-text"]
            if langs: cmd+=["-l",langs]
            cmd += [str(src), str(tmp)]
            ocr_proc = _aa_popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            while ocr_proc.poll() is None:
                if job.cancel_flag:
                    ocr_proc.terminate(); raise ConvertError("Cancelled")
                time.sleep(0.5)
            if ocr_proc.returncode != 0:
                raise ConvertError(f"ocrmypdf failed (exit {ocr_proc.returncode})")
            tables=camelot.read_pdf(str(tmp), pages=pages, flavor=flavor)
            try: tmp.unlink()
            except: pass
            if tables.n==0: raise ConvertError("No tables even after OCR.")
        if out=="xlsx":
            dst=str(base.with_suffix(".xlsx"))
            with pd.ExcelWriter(dst) as w:
                for i,t in enumerate(tables): t.df.to_excel(w, sheet_name=f"Table_{i+1}", index=False)
            progress(100,f"Saved {Path(dst).name}"); return dst
        outputs=[]
        for i,t in enumerate(tables):
            p=base.with_name(base.name+f"_table_{i+1}.csv"); t.df.to_csv(p,index=False); outputs.append(str(p))
        progress(100,f"Saved {len(outputs)} CSVs")
        return "[MULTI]"+json.dumps(outputs) if len(outputs)>1 else outputs[0]
register(PDFtoXLSX())

class CSVXLSX(Converter):
    accepts=("csv","xlsx"); produces=("xlsx","csv")
    def convert(self, job:"Job", progress):
        if pd is None: raise ConvertError("pandas not installed")
        src=Path(job.source_path); out=job.target_ext.lower(); dst=Path(job.output_dir)/(src.stem+"."+out)
        if src.suffix.lower()==".csv" and out=="xlsx": pd.read_csv(src).to_excel(dst,index=False)
        elif src.suffix.lower()==".xlsx" and out=="csv": pd.read_excel(src).to_csv(dst,index=False)
        else: raise ConvertError("Unsupported direction")
        progress(100,f"Saved {dst.name}"); return str(dst)
register(CSVXLSX())

class SubtitlesExtract(Converter):
    accepts=("mp4","mkv","mov","avi","webm"); produces=("srt",)
    def convert(self, job:"Job", progress):
        ff=ensure_ffmpeg(); src=Path(job.source_path); dst=Path(job.output_dir)/(src.stem+".srt")
        run_proc([ff,"-y","-i",str(src),"-map","0:s:0","-c","copy",str(dst)],progress,job)
        progress(100,f"Saved {dst.name}"); return str(dst)
register(SubtitlesExtract())

class SubtitlesEmbed(Converter):
    accepts=("mp4",); produces=("mp4+subs",)
    def convert(self, job:"Job", progress):
        ff=ensure_ffmpeg(); src=Path(job.source_path); subs=(job.options or {}).get("subs_path","")
        if not subs or not Path(subs).exists(): raise ConvertError("Provide .srt path in Options → Subtitles file")
        dst=Path(job.output_dir)/(src.stem+"_subs.mp4")
        run_proc([ff,"-y","-i",str(src),"-i",subs,"-map","0:v:0","-map","1:0","-c","copy","-c:s","mov_text","-movflags","+faststart",str(dst)],progress,job)
        progress(100,f"Saved {dst.name}"); return str(dst)
register(SubtitlesEmbed())

def _ffmpeg_escape_filter_path(p: str) -> str:
    # ffmpeg's filtergraph parser treats ':' as an option separator and '\' as an
    # escape char even inside single quotes, so a raw Windows path like
    # C:\Users\name\clip.srt must be normalized before going into e.g. subtitles='...'
    p = str(p).replace("\\", "/")
    p = p.replace(":", "\\:")
    p = p.replace("'", "\\'")
    return p

class SubtitlesBurnIn(Converter):
    accepts=("mp4","mkv","mov","avi","webm"); produces=("mp4+burnsubs",)
    def convert(self, job:"Job", progress):
        ff=ensure_ffmpeg(); src=Path(job.source_path); subs=(job.options or {}).get("subs_path","")
        if not subs or not Path(subs).exists(): raise ConvertError("Provide .srt path in Options → Subtitles file")
        dst=Path(job.output_dir)/(src.stem+"_burn.mp4")
        subs_escaped=_ffmpeg_escape_filter_path(subs)
        run_proc([ff,"-y","-i",str(src),"-vf",f"subtitles='{subs_escaped}'","-c:v","libx264","-preset","veryfast","-c:a","aac","-movflags","+faststart",str(dst)],progress,job)
        progress(100,f"Saved {dst.name}"); return str(dst)
register(SubtitlesBurnIn())

class VideoToFrames(Converter):
    accepts=("mp4","mkv","mov","avi","webm"); produces=("frames",)
    def convert(self, job:"Job", progress):
        ff=ensure_ffmpeg(); src=Path(job.source_path); outdir=Path(job.output_dir)/(src.stem+"_frames"); outdir.mkdir(parents=True, exist_ok=True)
        fps=(job.options or {}).get("frames_fps","1"); pattern=str(outdir/"frame_%05d.png")
        run_proc([ff,"-y","-i",str(src),"-vf",f"fps={fps}",pattern],progress,job)
        progress(100,"Saved frames folder"); return str(outdir)
register(VideoToFrames())

class FramesToVideo(Converter):
    accepts=("frames",); produces=("video-from-frames",)
    def convert(self, job:"Job", progress):
        ff=ensure_ffmpeg(); src=Path(job.source_path); o=job.options or {}; fps=o.get("frames_fps","24"); pattern=o.get("frames_pattern","frame_%05d.png")
        if not Path(src).is_dir(): raise ConvertError("Pick a frames folder as input")
        dst=Path(job.output_dir)/(Path(src).name+".mp4")
        run_proc([ff,"-y","-framerate",str(fps),"-i",str(Path(src)/pattern),"-c:v","libx264","-pix_fmt","yuv420p","-movflags","+faststart",str(dst)],progress,job)
        progress(100,f"Saved {dst.name}"); return str(dst)
register(FramesToVideo())

# -------- URLs (yt-dlp) --------
def _ydl_hook(d: Dict, cb: Callable[[int,str],None], job: "Job"):
    if job.cancel_flag:
        raise ytdlp.utils.DownloadError("Cancelled") if ytdlp else Exception("Cancelled")
    if d.get('status')=='downloading':
        p=d.get('downloaded_bytes',0); t=d.get('total_bytes') or d.get('total_bytes_estimate') or 0
        pct = int(p*100/t) if t else 10
        cb(min(pct,95), "Downloading")
    elif d.get('status')=='finished':
        cb(97, "Post-processing")


def _sanitize_youtube_url(url: str, allow_playlist: bool) -> tuple[str, str|None]:
    """Handle YouTube Mix/Radio (RD...) URLs.
    - If list=RD... and playlists are not allowed, strip it and keep the single video.
    - If playlists are allowed, tell the user it's unviewable and how to fix.
    Returns (new_url, note) where note is a user-facing hint.
    """
    try:
        u = urlparse(url)
        if u.netloc.endswith('youtube.com') or u.netloc.endswith('music.youtube.com'):
            qs = dict((k, v[0] if isinstance(v, list) else v) for k,v in parse_qs(u.query, keep_blank_values=True).items())
            lst = (qs.get('list') or '').upper()
            if lst.startswith('RD'):
                # Mix/Radio playlist
                if not allow_playlist:
                    # Strip the list param and keep ?v=...
                    qs.pop('list', None)
                    new_qs = urlencode(qs, doseq=False)
                    url = urlunparse((u.scheme, u.netloc, u.path, u.params, new_qs, u.fragment))
                    return url, 'Detected YouTube Mix/Radio; downloading just this video.'
                else:
                    raise ConvertError('YouTube Mix/Radio playlists are unviewable. Save the Mix to your own playlist or remove list=RD...')
    except Exception:
        pass
    return url, None
# -------- centralized finalize --------
def finalize_outputs(outputs, out_ext: str, job: "Job", progress):
    """
    Choose a single canonical final file for a URL job and perform at most one
    post-process based on out_ext. Removes intermediates/duplicates.
    """
    from pathlib import Path as _P
    out_ext = (out_ext or "").lower()
    # Normalize and drop missing
    paths = []
    for o in outputs or []:
        try:
            p = _P(o)
            if p.exists():
                paths.append(p)
        except Exception:
            continue

    if not paths:
        raise ConvertError("No outputs produced by downloader.")

    # Deduplicate by absolute path and size
    uniq = {}
    for p in paths:
        try:
            key = (str(p.resolve()), p.stat().st_size)
        except Exception:
            key = (str(p), p.name)
        uniq[key] = p
    paths = list(uniq.values())

    # If ytdlp already produced the right container, prefer that one
    for p in paths:
        if p.suffix.lower().lstrip('.') == out_ext:
            final = p
            # We'll still rename to the single final filename to keep naming consistent
            break
    else:
        final = None

    # Decide the canonical destination name
    # Use the first candidate's stem; keep in the user-chosen output_dir
    stem_src = paths[0]
    dst = _P(job.output_dir) / f"{stem_src.stem}.{out_ext}" if out_ext else stem_src

    def _cleanup(intermediates, keep):
        for q in intermediates:
            try:
                if q != keep and q.exists():
                    # Avoid deleting the final destination
                    if q.resolve() != keep.resolve():
                        q.unlink()
            except Exception as e:
                LOG.warning("finalize_outputs: failed to remove intermediate %s: %s", q, e)

    # Route by target extension (single post-step)
    if out_ext == "mp4":
        if final is None:
            # Run through VideoToMP4 ONCE
            sub = Job(source_path=str(stem_src), target_ext='mp4', options=job.options, output_dir=job.output_dir)
            produced = Path(VideoToMP4().convert(sub, progress))
            # Rename/move to canonical name if needed
            if produced != dst:
                try:
                    if dst.exists(): dst.unlink()
                except Exception: pass
                try:
                    produced.replace(dst)
                except Exception:
                    dst = produced
            final = dst
        else:
            # Ensure faststart/seekable once if needed by passing through VideoToMP4 remux path
            if final != dst:
                try:
                    if dst.exists(): dst.unlink()
                except Exception: pass
                try:
                    final.replace(dst)
                except Exception:
                    dst = final
            # quick remux to guarantee faststart (VideoToMP4 handles it idempotently)
            sub = Job(source_path=str(dst), target_ext='mp4', options=job.options, output_dir=job.output_dir)
            final = Path(VideoToMP4().convert(sub, progress))

        _cleanup(paths, final)
        return str(final)

    elif out_ext in ("mp3","m4a","opus"):
        # If we already have correct audio file, move to canonical name and return
        if final is None:
            # Try to pick best audio/video source and rely on converter chain elsewhere, but here we assume ytdlp created audio
            # Choose the largest .webm/.m4a/.mp4 and accept if it already matches
            cand = None
            for p in paths:
                if p.suffix.lower().lstrip('.') in ("mp3","m4a","opus","webm","mp4"):
                    cand = p; break
            if cand is None:
                cand = paths[0]
            # If not target ext, let upstream audio converters handle when user re-queues;
            # but typically ytdlp produced the right container already per ydl_opts.
            # We'll just set final to cand and rename to dst if it already matches target.
            if cand.suffix.lower().lstrip('.') == out_ext:
                final = cand
        if final is None:
            # Nothing to do; pick first path and rename if matching ext
            # (yt-dlp should have produced exact audio ext already)
            for p in paths:
                if p.suffix.lower().lstrip('.') in ("mp3","m4a","opus"):
                    final = p; break
            if final is None:
                final = paths[0]

        # Move/rename to canonical name if extension already matches; else keep as-is
        if final.suffix.lower().lstrip('.') == out_ext:
            if final != dst:
                try:
                    if dst.exists(): dst.unlink()
                except Exception: pass
                try:
                    final.replace(dst); final = dst
                except Exception:
                    pass
        _cleanup(paths, final)
        return str(final)

    elif out_ext == "gif":
        # Convert once from a single video source
        src_vid = None
        for p in paths:
            if p.suffix.lower().lstrip('.') in ("mp4","mkv","mov","avi","webm"):
                src_vid = p; break
        if src_vid is None:
            src_vid = paths[0]
        sub = Job(source_path=str(src_vid), target_ext='gif', options=job.options, output_dir=job.output_dir)
        final = Path(VideoToGIF().convert(sub, progress))
        _cleanup(paths, final)
        return str(final)

    elif out_ext in ("webp-anim","webp"):
        src_vid = None
        for p in paths:
            if p.suffix.lower().lstrip('.') in ("mp4","mkv","mov","avi","webm"):
                src_vid = p; break
        if src_vid is None:
            src_vid = paths[0]
        sub = Job(source_path=str(src_vid), target_ext='webp-anim', options=job.options, output_dir=job.output_dir)
        final = Path(VideoToWebPAnim().convert(sub, progress))
        _cleanup(paths, final)
        return str(final)

    else:
        # Unknown/other: pick the first by preference and move to canonical name
        chosen = final or paths[0]
        if out_ext and chosen.suffix.lower().lstrip('.') == out_ext and chosen != dst:
            try:
                if dst.exists(): dst.unlink()
            except Exception: pass
            try:
                chosen.replace(dst); chosen = dst
            except Exception:
                pass
        _cleanup(paths, chosen)
        return str(chosen)
class URLToMedia(Converter):
    accepts=("url",); produces=("mp3","mp4","m4a","opus","gif","webp-anim","thumbnail","chapters-csv")
    def convert(self, job:"Job", progress):
        if ytdlp is None: raise ConvertError("yt-dlp not installed. pip install yt-dlp")
        if not job.source_url: raise ConvertError("No URL provided")
        out_ext=job.target_ext.lower(); outdir=Path(job.output_dir); outdir.mkdir(parents=True, exist_ok=True); o=job.options or {}
        allow_pl = (o.get('playlist','0')=='1')
        new_url, note = _sanitize_youtube_url(job.source_url, allow_pl)
        if note: progress(5, note)
        job.source_url = new_url
        target=get_target_mb(o)
        ydl_opts = {
            'outtmpl': str(outdir / (safe_basename_from_url(job.source_url) + '.%(ext)s')),
            'noplaylist': not (o.get('playlist','0')=='1'),
            'progress_hooks': [lambda d: _ydl_hook(d, progress, job)],
            'quiet': True, 'restrictfilenames': True, 'continuedl': True, 'overwrites': False,
            'concurrent_fragment_downloads': 16, 'retries': 5, 'fragment_retries': 20,
            'merge_output_format': 'mp4',
        }
        if shutil.which("aria2c"):
            ydl_opts.update({'external_downloader': 'aria2c', 'external_downloader_args': ['-x16','-s16','-k1M']})

        if out_ext=="thumbnail":
            ydl_opts2=dict(ydl_opts); ydl_opts2.update({'skip_download': True, 'writethumbnail': True})
            with ytdlp.YoutubeDL(ydl_opts2) as y:
                info=y.extract_info(job.source_url, download=True)
                thumb=Path(y.prepare_filename(info)).with_suffix(".jpg")
                if not thumb.exists():
                    for ext in (".webp",".png",".jpeg",".jpg"):
                        t=thumb.with_suffix(ext)
                        if t.exists(): thumb=t; break
            progress(100,"Saved thumbnail"); return str(thumb)

        if out_ext=="chapters-csv":
            with ytdlp.YoutubeDL({'quiet':True}) as y: info=y.extract_info(job.source_url, download=False)
            ch=info.get("chapters") or []; p=outdir/(safe_basename_from_url(job.source_url)+"_chapters.csv")
            import csv as _csv
            with open(p,"w",newline="",encoding="utf-8") as f:
                w=_csv.writer(f); w.writerow(["title","start","end","start_s","end_s"])
                for c in ch: w.writerow([c.get("title",""),c.get("start_time"),c.get("end_time"),c.get("start_time"),c.get("end_time")])
            progress(100,"Saved chapters CSV"); return str(p)

        # audio/video (and gif/webp via post-convert)
        outputs: List[str] = []

        def _download_with(opts_local):
            with ytdlp.YoutubeDL(opts_local) as y:
                res=y.extract_info(job.source_url, download=True); outs=[]
                def prepared(r):
                    p=Path(y.prepare_filename(r))
                    return p
                if 'entries' in res:
                    for e in res['entries']: outs.append(str(prepared(e)))
                else: outs.append(str(prepared(res)))
                return outs

        # build formats for direct av outputs
        if out_ext in ("mp3","m4a","opus","mp4"):
            if out_ext=="mp3":
                ydl_opts['format'] = "bestaudio/best"
                pq=None
                if target:
                    try:
                        with ytdlp.YoutubeDL({'quiet': True}) as y:
                            info=y.extract_info(job.source_url, download=False); dur=info.get('duration') or 0
                    except Exception: dur=0
                    if dur>0:
                        max_bits=int((target*1024*1024)*8*0.98); bps=max(64000, min(320000, max_bits//max(int(dur),1))); pq=str(bps//1000)
                ydl_opts['postprocessors']=[{'key':'FFmpegExtractAudio','preferredcodec':'mp3','preferredquality': pq or (o.get('bitrate','192k').replace('k','') if o else '192')}]
            elif out_ext=="m4a":
                ydl_opts['format']="bestaudio[ext=m4a]/bestaudio"
                ydl_opts['postprocessors']=[{'key':'FFmpegAudioConvertor','preferredcodec':'m4a'}]
            elif out_ext=="opus":
                ydl_opts['format']="bestaudio[ext=webm]/bestaudio"
                ydl_opts['postprocessors']=[{'key':'FFmpegAudioConvertor','preferredcodec':'opus'}]
            elif out_ext=="mp4":
                fmt='bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
                if target: fmt='bv*[height<=480][ext=mp4]+ba[ext=m4a]/bv*[height<=480]/best[ext=mp4]/best'
                ydl_opts.update({'format': fmt})

        try:
            outputs = _download_with(ydl_opts)
        except Exception as e:
            msg = str(e).lower()
            if any(k in msg for k in ("impersonate","cloudflare","403")):
                ydl_opts.setdefault('extractor_args', {}).setdefault('generic', {})['impersonate'] = ['chrome']
                outputs = _download_with(ydl_opts)
            else:
                raise ConvertError(f"yt-dlp error: {e}")
        # centralized finalize
        return finalize_outputs(outputs, out_ext, job, progress)
register(URLToMedia())

# ---------- watch ----------
class WatchHandler(FileSystemEventHandler):
    def __init__(self, enqueue_fn, patterns: Tuple[str,...]): self.enqueue_fn, self.patterns = enqueue_fn, patterns
    def on_created(self, e):
        if e.is_directory: return
        p=Path(e.src_path)
        if p.suffix.lower().lstrip('.') in self.patterns: self.enqueue_fn(str(p))

# ---------- UI ----------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        try:
            self.resize(1200, 820)
        except Exception:
            pass
        self.setMinimumSize(980, 700)

        self.output_dir = DEFAULT_OUTPUT_DIR
        self.queue: list["Job"] = []
        self.worker: Optional["Worker"] = None
        self.watch_observer = None
        self.watch_path: Optional[str] = None
        self.last_result_path: Optional[str] = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # ---- menu ----
        menubar = QMenuBar()
        m_file = menubar.addMenu("&File")
        a_open = QAction("Open files...", self); a_open.triggered.connect(self.pick_files); m_file.addAction(a_open)
        a_out  = QAction("Choose output folder...", self); a_out.triggered.connect(self.pick_output_dir); m_file.addAction(a_out)
        m_file.addSeparator(); a_exit = QAction("Exit", self); a_exit.triggered.connect(self.close); m_file.addAction(a_exit)

        m_tools = menubar.addMenu("&Tools")
        a_dep = QAction("Dependency check", self); a_dep.triggered.connect(self.check_deps); m_tools.addAction(a_dep)
        a_dark = QAction("Toggle dark mode", self); a_dark.triggered.connect(lambda: apply_dark(QApplication.instance())); m_tools.addAction(a_dark)
        a_watch = QAction("Enable watch folder...", self); a_watch.triggered.connect(self.toggle_watch); m_tools.addAction(a_watch)
        a_log = QAction("Open log folder", self); a_log.triggered.connect(self.open_log_folder); m_tools.addAction(a_log)
        if HAVE_MUTAGEN:
            a_meta = QAction("Edit MP3 metadata (last result)...", self); a_meta.triggered.connect(self.edit_last_mp3_tags); m_tools.addAction(a_meta)

        m_help = menubar.addMenu("&Help")
        a_guide = QAction("Conversion guide...", self); a_guide.triggered.connect(self.show_help); m_help.addAction(a_guide)
        a_about = QAction("About", self); a_about.triggered.connect(lambda: QMessageBox.information(self, "About", f"{APP_NAME}\nVersion {APP_VERSION}")); m_help.addAction(a_about)

        root.setMenuBar(menubar)

        # ---- top row ----
        top = QHBoxLayout()
        self.url_edit = QLineEdit(); self.url_edit.setPlaceholderText("Paste a link (YouTube, direct URL)...")
        self.format_box = QComboBox(); self._build_format_combo()
        # format default moved to _build_format_combo()
        btn_browse = QPushButton("Browse files...")
        btn_browse.clicked.connect(self.pick_files)
        top.addWidget(self.url_edit, 1); top.addWidget(QLabel("to")); top.addWidget(self.format_box); top.addWidget(btn_browse)
        root.addLayout(top)

        # ---- drop zone ----
        self.drop = DropZone(self.add_files); root.addWidget(self.drop)

        # ---- options ----
        self.group_opts = QGroupBox("Options"); form = QFormLayout()
        self.opt_size_target = QCheckBox("Enable size targeting (Discord/custom cap)"); self.opt_size_target.setChecked(False); form.addRow(self.opt_size_target)
        self.opt_discord = QComboBox(); self.opt_discord.addItems(list(DISCORD_PRESETS.keys())); self.opt_discord.setCurrentText("Off"); form.addRow("Discord size preset", self.opt_discord)
        self.opt_cap_mb = QSpinBox(); self.opt_cap_mb.setRange(0, 10000); self.opt_cap_mb.setValue(0); form.addRow("Custom size cap (MB)", self.opt_cap_mb)
        self.opt_headroom = QSpinBox(); self.opt_headroom.setRange(0, 25); self.opt_headroom.setValue(5); form.addRow("Headroom %", self.opt_headroom)
        self.opt_split = QCheckBox("Split into ≤ cap parts (MP3/MP4)"); form.addRow(self.opt_split)
        self.opt_playlist = QCheckBox("URL: download playlist items"); form.addRow(self.opt_playlist)

        self.opt_start = QLineEdit(); self.opt_start.setPlaceholderText("e.g., 0:10")
        self.opt_end   = QLineEdit(); self.opt_end.setPlaceholderText("e.g., 2:00")
        form.addRow("Trim start", self.opt_start); form.addRow("Trim end", self.opt_end)

        self.opt_scale = QComboBox(); self.opt_scale.addItems(["original","1080p","720p","480p","360p"]); form.addRow("Scale", self.opt_scale)
        self.opt_fps   = QComboBox(); self.opt_fps.addItems(["original","60","30","24","15","10"]); form.addRow("FPS", self.opt_fps)

        self.opt_bitrate = QComboBox(); self.opt_bitrate.addItems(["128k","192k","256k","320k"]); self.opt_bitrate.setCurrentText("192k"); form.addRow("Audio bitrate (if no cap)", self.opt_bitrate)
        self.opt_loudnorm = QCheckBox("Loudness normalize"); form.addRow(self.opt_loudnorm)
        self.opt_silence  = QCheckBox("Trim silence (start/end)"); form.addRow(self.opt_silence)

        self.opt_encoder = QComboBox(); self.opt_encoder.addItems(["x264","nvenc","qsv","amf"]); self.opt_encoder.setCurrentText("x264"); form.addRow("Video encoder", self.opt_encoder)
        self.opt_threads = QSpinBox(); self.opt_threads.setRange(0, 32); self.opt_threads.setValue(0); form.addRow("FFmpeg threads (0=auto)", self.opt_threads)

        self.opt_pages = QLineEdit(); self.opt_pages.setPlaceholderText("all or 1-3,5"); form.addRow("PDF pages", self.opt_pages)
        self.opt_flavor = QComboBox(); self.opt_flavor.addItems(["stream","lattice"]); form.addRow("Table flavor", self.opt_flavor)
        self.opt_ocr_langs = QLineEdit(); self.opt_ocr_langs.setPlaceholderText("e.g., eng+spa"); form.addRow("OCR languages", self.opt_ocr_langs)

        self.opt_quality = QSpinBox(); self.opt_quality.setRange(1,100); self.opt_quality.setValue(90); form.addRow("JPEG quality", self.opt_quality)
        self.opt_stripmeta = QCheckBox("Strip image metadata"); form.addRow(self.opt_stripmeta)
        self.opt_maxdim = QSpinBox(); self.opt_maxdim.setRange(0,10000); self.opt_maxdim.setValue(0); form.addRow("Max dimension (px, 0=off)", self.opt_maxdim)

        self.opt_gif_fps = QSpinBox(); self.opt_gif_fps.setRange(1,60); self.opt_gif_fps.setValue(10); form.addRow("GIF FPS", self.opt_gif_fps)
        self.opt_gif_scale = QSpinBox(); self.opt_gif_scale.setRange(64,2160); self.opt_gif_scale.setValue(480); form.addRow("GIF height (px)", self.opt_gif_scale)

        self.opt_subs_path = QLineEdit(); self.opt_subs_path.setPlaceholderText("Path to .srt for embed/burn")
        pick_srt = QPushButton("Choose..."); pick_srt.clicked.connect(lambda: self._pick_to_line(self.opt_subs_path,"Choose .srt","Subtitle files (*.srt)"))
        row = QHBoxLayout(); row.addWidget(self.opt_subs_path,1); row.addWidget(pick_srt)
        wrap = QWidget(); wrap.setLayout(row); form.addRow("Subtitles file", wrap)

        self.group_opts.setLayout(form); self._form = form
        
        self.group_opts.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        from PySide6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidget(self.group_opts)
        scroll.setWidgetResizable(True)
        root.addWidget(scroll)

        # ---- output/actions ----
        actions = QHBoxLayout()
        self.lbl_outdir = QLabel(self.output_dir); self.lbl_outdir.setStyleSheet("color:#777")
        btn_out = QPushButton("Choose output folder..."); btn_out.clicked.connect(self.pick_output_dir)
        self.btn_convert = QPushButton("Convert"); self.btn_convert.clicked.connect(self.enqueue_from_ui)
        self.btn_cancel = QPushButton("Cancel"); self.btn_cancel.setEnabled(False); self.btn_cancel.clicked.connect(self.cancel_current)
        actions.addWidget(btn_out); actions.addWidget(self.lbl_outdir,1); actions.addWidget(self.btn_cancel); actions.addWidget(self.btn_convert)
        root.addLayout(actions)
        self._wire_format_guard(root)

    def _build_format_combo(self):
        """Populate the format combobox with a disabled placeholder and category headers."""
        self.format_box.clear()
        self.format_box.addItem(SELECT_FORMAT_LABEL)
        model = self.format_box.model()
        # Disable placeholder row
        try:
            item0 = model.item(0)
            if item0:
                item0.setEnabled(False)
                item0.setSelectable(False)
        except Exception:
            pass

        for cat, items in OUTPUT_CATEGORIES.items():
            # Add a disabled header for the category
            self.format_box.addItem(f"— {cat} —")
            idx = self.format_box.count() - 1
            try:
                header_item = model.item(idx)
                if header_item:
                    header_item.setEnabled(False)
                    header_item.setSelectable(False)
            except Exception:
                pass

            for fmt in items:
                if fmt in SUPPORTED_OUTPUTS:
                    self.format_box.addItem(fmt)

        # Start on the placeholder
        self.format_box.setCurrentIndex(0)

    def _wire_format_guard(self,  root):
        """Disable Convert until a real output format is chosen; react to changes."""
        def is_real_format(txt: str) -> bool:
            return bool(txt) and (txt in SUPPORTED_OUTPUTS)

        # Initial
        self.btn_convert.setEnabled(is_real_format(self.format_box.currentText()))
        # Live updates
        self.format_box.currentTextChanged.connect(
            lambda txt: self.btn_convert.setEnabled(is_real_format(txt))
        )


        # ---- progress/status/lists ----
        self.progress = QProgressBar(); root.addWidget(self.progress)
        self.status_lbl = QLabel("Ready"); root.addWidget(self.status_lbl)
        lists = QHBoxLayout()
        self.queue_list = QListWidget(); self.queue_list.setMinimumHeight(120)
        left = QVBoxLayout(); left.addWidget(QLabel("Queue")); left.addWidget(self.queue_list,1)
        self.recent = QListWidget(); self.recent.itemDoubleClicked.connect(self.open_recent_item)
        right = QVBoxLayout(); right.addWidget(QLabel("Recent results (double-click to open)")); right.addWidget(self.recent,1)
        lists.addLayout(left,1); lists.addLayout(right,1); root.addLayout(lists)

        # visibility
        self.format_box.currentTextChanged.connect(self._sync_visibility)
        self.opt_discord.currentTextChanged.connect(self._sync_visibility)
        self.opt_size_target.toggled.connect(self._sync_visibility)
        self._sync_visibility()

    # ---- UI helpers ----
    def open_log_folder(self):
        try:
            log_dir = Path(os.getenv("APPDATA", str(Path.home() / "AppData/Roaming"))) / "Any2Any" / "logs"
            if os.name=="nt": os.startfile(str(log_dir))  # type: ignore
            elif sys.platform.startswith("darwin"): subprocess.call(["open", str(log_dir)])
            else: subprocess.call(["xdg-open", str(log_dir)])
        except Exception: pass

    def _pick_to_line(self, line: QLineEdit, title: str, filt: str):
        fp,_=QFileDialog.getOpenFileName(self, title, "", filt)
        if fp: line.setText(fp)

    def show_help(self):
        text = (
            "Conversions supported:\n"
            "• Video → MP4 (seekable), MP3, GIF, WebP-anim, frames\n"
            "• Audio → MP3\n"
            "• Images → PNG/JPG/WebP/AVIF, Images → PDF\n"
            "• PDF → images (PNG/JPG)\n"
            "• PDF → Excel/CSV (tables)\n"
            "• CSV ↔ JSON, CSV ↔ XLSX\n"
            "• Subtitles: extract SRT, embed (mov_text), burn-in\n"
            "• URL links: MP4/MP3/M4A/OPUS/GIF/WebP/thumbnail/chapters-csv\n\n"
            "Notes:\n"
            "• Discord size targeting is optional; headroom helps hit the cap.\n"
            "• NVENC/QSV/AMF appear only if your ffmpeg supports them.\n"
            "• GIF/WebP from URLs first download MP4 then convert.\n"
            "• If output == input name, tool writes *_out.mp4 to avoid overwrite.\n"
        )
        QMessageBox.information(self, "Conversion guide", text)

    def check_deps(self):
        def ok(name, cond): return "{}: {}".format(name, "OK" if cond else "MISSING")
        msgs = [
            ok("ffmpeg", any(p.exists() for p in ff_candidates(["ffmpeg.exe","ffmpeg"]))),
            ok("ffprobe", any(p.exists() for p in ff_candidates(["ffprobe.exe","ffprobe"]))),
            ok("yt-dlp", ytdlp is not None),
            ok("Pillow", Image is not None),
            ok("pillow-heif", HAVE_HEIF),
            ok("Camelot", camelot is not None),
            ok("pandas", pd is not None),
            ok("PyMuPDF", fitz is not None),
            ok("watchdog", HAVE_WATCHDOG),
            ok("mutagen", HAVE_MUTAGEN),
            ok("aria2c (downloader)", have_binary("aria2c")),
        ]
        if psutil:
            msgs.append(f"Memory: {psutil.virtual_memory().percent}% used")
        QMessageBox.information(self, "Dependency Check", "\n".join(msgs))

    def toggle_watch(self):
        if not HAVE_WATCHDOG:
            QMessageBox.information(self, "Watch folder", "Install watchdog: pip install watchdog")
            return
        if self.watch_observer:
            self.watch_observer.stop(); self.watch_observer.join(1.0); self.watch_observer=None
            QMessageBox.information(self,"Watch folder","Stopped watching"); return
        d = QFileDialog.getExistingDirectory(self, "Choose folder to watch")
        if not d: return
        self.watch_path = d
        pats = tuple(set(sum([c.accepts for c in REGISTRY], ())))
        handler = WatchHandler(self.enqueue_file_path, pats)
        self.watch_observer = Observer(); self.watch_observer.schedule(handler, d, recursive=False); self.watch_observer.start()
        QMessageBox.information(self,"Watch folder",f"Watching: {d}")

    def edit_last_mp3_tags(self):
        if not HAVE_MUTAGEN: QMessageBox.information(self,"Tags","Install mutagen: pip install mutagen"); return
        if not self.last_result_path or not self.last_result_path.lower().endswith(".mp3"):
            QMessageBox.information(self,"Tags","The last result is not an MP3."); return
        dlg = QDialog(self); dlg.setWindowTitle("Edit MP3 metadata")
        form = QFormLayout(dlg); t=QLineEdit(); a=QLineEdit(); al=QLineEdit()
        form.addRow("Title", t); form.addRow("Artist", a); form.addRow("Album", al)
        btns = QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); form.addWidget(btns)
        def apply():
            audio = MP3(self.last_result_path, ID3=ID3)
            try: audio.add_tags()
            except Exception: pass
            tags = EasyID3(self.last_result_path)
            if t.text(): tags["title"] = t.text()
            if a.text(): tags["artist"] = a.text()
            if al.text(): tags["album"] = al.text()
            tags.save(); dlg.accept()
        btns.accepted.connect(apply); btns.rejected.connect(dlg.reject); dlg.exec()

    # ---- visibility helpers ----
    def _row_visible(self, field_widget, show: bool):
        try:
            lbl = self._form.labelForField(field_widget)
            if lbl is not None:
                lbl.setVisible(show)
        except Exception:
            pass
        field_widget.setVisible(show)

    def _sync_visibility(self):
        tgt = self.format_box.currentText()
        on  = self.opt_size_target.isChecked()
        for w in (self.opt_discord, self.opt_cap_mb, self.opt_headroom, self.opt_split):
            w.setEnabled(on)
        self._row_visible(self.opt_bitrate, tgt == "mp3" and not on)
        show_pdf = tgt in ("xlsx", "csv")
        self._row_visible(self.opt_pages,     show_pdf)
        self._row_visible(self.opt_flavor,    show_pdf)
        self._row_visible(self.opt_ocr_langs, show_pdf)
        self._row_visible(self.opt_quality,   tgt in ("jpg", "jpeg"))
        self._row_visible(self.opt_gif_fps,   tgt in ("gif", "webp-anim"))
        self._row_visible(self.opt_gif_scale, tgt in ("gif", "webp-anim"))
        subs_field = self.opt_subs_path.parentWidget()
        self._row_visible(subs_field, tgt in ("mp4+subs", "mp4+burnsubs"))

    # ---- file pickers & queue ----
    def pick_output_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Choose output folder", self.output_dir)
        if d: self.output_dir = d; self.lbl_outdir.setText(d)

    def pick_files(self):
        files,_ = QFileDialog.getOpenFileNames(self, "Choose files to convert")
        if files: self.add_files(files)

    def add_files(self, paths: List[str]):
        for p in paths: self.enqueue_file_path(p)
        self.status_lbl.setText(f"Queued {len(paths)} file(s)")

    def enqueue_file_path(self, p: str):
        target = self.format_box.currentText().strip()
        if target not in SUPPORTED_OUTPUTS: QMessageBox.warning(self, "Choose output type", "Please choose an output type first."); return
        opts = self.collect_opts(target)
        job = Job(source_path=p, target_ext=target, options=opts, output_dir=self.output_dir)
        self.queue.append(job); self.queue_list.addItem(QListWidgetItem(f"{Path(p).name} → {target}"))
        if not self.worker: self.start_next()

    def enqueue_from_ui(self):
        raw = self.url_edit.text().strip(); url = ""
        if raw:
            if re.match(r"^https?://", raw, re.I): url = raw
            else:
                m = re.search(r"https?://\S+", raw, flags=re.I)
                if m: url = m.group(0)
        target = self.format_box.currentText().strip()
        if target not in SUPPORTED_OUTPUTS: QMessageBox.warning(self, "Choose output type", "Please choose a real output type first."); return
        opts = self.collect_opts(target)
        if url:
            job = Job(source_path=None, source_url=url, target_ext=target, options=opts, output_dir=self.output_dir)
            self.queue.append(job); self.queue_list.addItem(QListWidgetItem(f"URL → {target}"))
            self.url_edit.clear()
            if not self.worker: self.start_next()
        elif raw:
            QMessageBox.warning(self, "Invalid URL", "Please paste a valid http(s) link.")
    def collect_opts(self, target: str) -> Dict[str,str]:
        o: Dict[str,str] = {}
        if self.opt_size_target.isChecked():
            o["size_targeting"]="1"
            cap = DISCORD_PRESETS.get(self.opt_discord.currentText(), 0)
            if cap: o["discord_size_mb"]=str(cap)
            if self.opt_cap_mb.value()>0: o["custom_cap_mb"]=str(self.opt_cap_mb.value())
            o["headroom_percent"]=str(self.opt_headroom.value())
            if self.opt_split.isChecked(): o["split"]="1"
        if self.opt_playlist.isChecked(): o["playlist"]="1"
        if self.opt_start.text().strip(): o["start"]=self.opt_start.text().strip()
        if self.opt_end.text().strip(): o["end"]=self.opt_end.text().strip()
        if self.opt_scale.currentText()!="original": o["scale"]=self.opt_scale.currentText()
        if self.opt_fps.currentText()!="original": o["fps"]=self.opt_fps.currentText()
        if target=="mp3" and not self.opt_size_target.isChecked(): o["bitrate"]=self.opt_bitrate.currentText()
        if self.opt_loudnorm.isChecked(): o["loudnorm"]="1"
        if self.opt_silence.isChecked(): o["silence"]="1"
        o["encoder"]=self.opt_encoder.currentText(); o["threads"]=str(self.opt_threads.value())
        if target in ("xlsx","csv"):
            if self.opt_pages.text().strip(): o["pages"]=self.opt_pages.text().strip()
            o["flavor"]=self.opt_flavor.currentText()
            if self.opt_ocr_langs.text().strip(): o["ocr_langs"]=self.opt_ocr_langs.text().strip()
        if target in ("jpg","jpeg"): o["quality"]=str(self.opt_quality.value())
        if self.opt_stripmeta.isChecked(): o["strip_meta"]="1"
        if self.opt_maxdim.value()>0: o["max_dim"]=str(self.opt_maxdim.value())
        if target=="gif": o["gif_fps"]=str(self.opt_gif_fps.value()); o["gif_scale"]=str(self.opt_gif_scale.value())
        if target=="webp-anim": o["fps"]=o.get("fps","15"); o["scale"]=o.get("scale",str(self.opt_gif_scale.value()))
        if target in ("mp4+subs","mp4+burnsubs") and self.opt_subs_path.text().strip(): o["subs_path"]=self.opt_subs_path.text().strip()
        return o

    # ---- worker wiring ----
    def start_next(self):
        if self.worker or not self.queue: return
        job = self.queue.pop(0)
        if self.queue_list.count()>0: self.queue_list.takeItem(0)
        self.progress.setValue(0); self.status_lbl.setText("Starting...")
        self.btn_convert.setEnabled(False); self.btn_cancel.setEnabled(True)
        self.worker = Worker(job); self.worker.progressed.connect(self.on_progress)
        self.worker.finished_ok.connect(self.on_finished); self.worker.failed.connect(self.on_failed); self.worker.start()

    def cancel_current(self):
        if self.worker: self.worker.job.cancel_flag=True; self.btn_cancel.setEnabled(False); self.status_lbl.setText("Cancelling...")

    def on_progress(self, p:int, msg:str): self.progress.setValue(p); self.status_lbl.setText(msg)

    def on_finished(self, out:str):
        self.btn_convert.setEnabled(True); self.btn_cancel.setEnabled(False); self.progress.setValue(100); self.status_lbl.setText("Done ✓")
        if out.startswith("[MULTI]"):
            arr=json.loads(out[len("[MULTI]"):])
            for p in arr: self.recent.addItem(QListWidgetItem(p))
            self.last_result_path = arr[-1] if arr else None
        else:
            self.recent.addItem(QListWidgetItem(out)); self.last_result_path = out
        self.worker=None
        try:
            p = json.loads(out[len("[MULTI]"):])[0] if out.startswith("[MULTI]") else out
            self.open_in_os(Path(p))
        except Exception: pass
        QTimer.singleShot(100, self.start_next)

    def on_failed(self, err:str):
        self.btn_convert.setEnabled(True); self.btn_cancel.setEnabled(False); self.progress.setValue(0); self.status_lbl.setText("Failed ✗")
        QMessageBox.critical(self, "Conversion failed", err); self.worker=None; QTimer.singleShot(100, self.start_next)

    def open_recent_item(self, item: QListWidgetItem): self.open_in_os(Path(item.text()))
    def open_in_os(self, p: Path):
        try:
            folder = p if p.is_dir() else p.parent
            if sys.platform.startswith("darwin"): subprocess.call(["open", str(folder)])
            elif os.name=="nt": os.startfile(str(folder))  # type: ignore
            else: subprocess.call(["xdg-open", str(folder)])
        except Exception: pass

# drop zone

    # ---- info tooltip attachments ----
    def _attach_info_tooltips(self) -> None:
        """Attach hover tooltips to key controls and their labels. Safe no-op if anything is missing."""
        try:
            form = getattr(self, "_form", None)
            if form is None:
                return

            def add_tip(widget, tip: str):
                if not widget or not tip:
                    return
                try:
                    lbl = form.labelForField(widget)
                    if lbl is not None:
                        lbl.setToolTip(tip)
                    widget.setToolTip(tip)
                except Exception:
                    pass

            add_tip(getattr(self, "opt_size_target", None),
                    "Enable target-size mode (e.g., Discord 10/25 MB). Uses 1-pass VBV by default; x264 supports true 2-pass; NVENC uses VBR HQ fallback.")
            add_tip(getattr(self, "opt_discord", None),
                    "Discord preset cap in MB. Applies when size targeting is enabled.")
            add_tip(getattr(self, "opt_cap_mb", None),
                    "Custom size cap in MB. If set, overrides the preset.")
            add_tip(getattr(self, "opt_headroom", None),
                    "Headroom % kept under the cap so the final file reliably fits (container overhead, VBR swings).")
            add_tip(getattr(self, "opt_split", None),
                    "Split outputs that still exceed the cap into parts instead of failing.")

            add_tip(getattr(self, "opt_start", None),
                    "Trim start time (ss). Accepts seconds or HH:MM:SS.mmm")
            add_tip(getattr(self, "opt_end", None),
                    "Trim end time (to). Accepts seconds or HH:MM:SS.mmm")

            add_tip(getattr(self, "opt_scale", None),
                    "Resize the video. 'original' keeps source; presets like 1080p/720p downscale.")
            add_tip(getattr(self, "opt_fps", None),
                    "Force output frame rate. 'original' keeps source. Lower FPS reduces size.")
            add_tip(getattr(self, "opt_encoder", None),
                    "Video encoder to use. x264 supports CRF/2-pass; NVENC uses VBR HQ for size targets.")
            add_tip(getattr(self, "opt_threads", None),
                    "FFmpeg worker threads. 0 lets ffmpeg choose automatically.")

            add_tip(getattr(self, "opt_bitrate", None),
                    "Audio bitrate for audio-only outputs or when uncapped. Size targets balance A/V automatically.")
            add_tip(getattr(self, "opt_loudnorm", None),
                    "Loudness normalize audio using ffmpeg loudnorm.")
            add_tip(getattr(self, "opt_silence", None),
                    "Trim leading/trailing silence for audio-only conversions.")

            add_tip(getattr(self, "opt_subs_path", None),
                    "Path to .srt file. The app can mux (mov_text) or burn-in depending on target.")

            add_tip(getattr(self, "opt_stripmeta", None),
                    "Strip EXIF and other metadata from images.")
            add_tip(getattr(self, "opt_maxdim", None),
                    "Maximum dimension in pixels for image outputs (0=off).")
            add_tip(getattr(self, "opt_gif_fps", None),
                    "Target FPS for GIFs. Lower values reduce size.")
            add_tip(getattr(self, "opt_gif_scale", None),
                    "Target height in pixels for GIFs. Width scales to keep aspect.")

            add_tip(getattr(self, "opt_pages", None),
                    "Pages to process for PDFs, e.g., 'all' or '1-3,5'.")
            add_tip(getattr(self, "opt_flavor", None),
                    "Table extraction flavor for PDF→CSV/XLSX.")
            add_tip(getattr(self, "opt_ocr_langs", None),
                    "OCR language codes (e.g., eng+spa).")

        except Exception:
            pass

class DropZone(QLabel):
    def __init__(self, on_files): super().__init__("Drop files here or click ‘Browse files…’"); self.setAlignment(Qt.AlignCenter); self.setStyleSheet("border:2px dashed #888; padding:32px; border-radius:12px; color:#666;"); self.setAcceptDrops(True); self.on_files=on_files
    def minimumSizeHint(self): return QSize(200, 120)
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls(): e.acceptProposedAction()
    def dropEvent(self, e):
        paths=[u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths: self.on_files(paths)

# dark mode
DARK=False
def apply_dark(app: QApplication):
    global DARK; DARK=not DARK if DARK else True  # default to dark on first call
    if not DARK: app.setPalette(app.style().standardPalette()); return
    from PySide6.QtGui import QPalette, QColor
    app.setStyle("Fusion"); p=QPalette()
    p.setColor(QPalette.Window, QColor(53,53,53)); p.setColor(QPalette.WindowText, QColor(220,220,220))
    p.setColor(QPalette.Base, QColor(35,35,35)); p.setColor(QPalette.AlternateBase, QColor(53,53,53))
    p.setColor(QPalette.Text, QColor(220,220,220)); p.setColor(QPalette.Button, QColor(53,53,53))
    p.setColor(QPalette.ButtonText, QColor(220,220,220)); p.setColor(QPalette.Highlight, QColor(42,130,218))
    p.setColor(QPalette.HighlightedText, QColor(0,0,0)); app.setPalette(p)


# worker
class Worker(QThread):
    progressed = Signal(int, str)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, job: "Job"):
        super().__init__()
        self.job = job

    def run(self):
        try:
            out = self._run()
            self.finished_ok.emit(out)
        except ConvertError as e:
            self.failed.emit(str(e))
        except Exception:
            self.failed.emit(traceback.format_exc())

    def _run(self) -> str:
        j = self.job
        prog = lambda p, m: self.progressed.emit(p, m)
        if j.source_url and not j.source_path:
            conv = find_conv("url", j.target_ext)
            if not conv:
                raise ConvertError(f"No converter for URL → {j.target_ext}")
            return conv.convert(j, prog)
        if not j.source_path:
            raise ConvertError("No input provided")
        in_ext = "frames" if Path(j.source_path).is_dir() else Path(j.source_path).suffix.lower().lstrip(".")
        conv = find_conv(in_ext, j.target_ext)
        if not conv:
            raise ConvertError(f"No converter for {in_ext} → {j.target_ext}")
        return conv.convert(j, prog)

# CLI passthrough
def run_cli_if_requested():
    import argparse
    ap = argparse.ArgumentParser(description="Any2Any")
    ap.add_argument("--in", dest="src"); ap.add_argument("--to", dest="target")
    ap.add_argument("--outdir", default=DEFAULT_OUTPUT_DIR); ap.add_argument("--opts", default="{}")
    args, _ = ap.parse_known_args()
    if args.src and args.target:
        j = Job(
            source_path=args.src if re.match(r"^[a-zA-Z]:|[/\\]", args.src) else None,
            source_url=args.src if re.match(r"^https?://", args.src) else None,
            target_ext=args.target,
            options=json.loads(args.opts),
            output_dir=args.outdir
        )
        def prog(p, m): print(f"{p}% {m}")
        if j.source_url and not j.source_path:
            in_ext = "url"
        else:
            in_ext = Path(args.src).suffix.lower().lstrip('.') if args.src else ""
        conv = find_conv(in_ext, j.target_ext) if in_ext else None
        if not conv and j.source_url:
            conv = find_conv("url", j.target_ext)
        if not conv:
            print(f"No converter for input → {args.target}"); sys.exit(2)
        out = conv.convert(j, prog); print("Output:", out); sys.exit(0)


def main():
    run_cli_if_requested()
    app = QApplication(sys.argv)
    # High-DPI friendly and dark by default
    try:
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)  # type: ignore[attr-defined]
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)      # type: ignore[attr-defined]
    except Exception:
        pass
    apply_dark(app)  # default dark
    win = MainWindow(); win.show()
    QTimer.singleShot(0, win._attach_info_tooltips)
    sys.exit(app.exec())

if __name__=="__main__":
    main()