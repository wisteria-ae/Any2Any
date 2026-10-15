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

import os, sys, re, json, shutil, subprocess, traceback, logging, time, threading
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Callable, Dict, List, Tuple

APP_NAME = "Any2Any (Local)"
APP_VERSION = "1.5.0"  # keep in sync with installer/Any2Any.iss MyAppVersion
DEFAULT_OUTPUT_DIR = str(Path.home() / "Downloads")

# The installed app has no console of its own, so every console program it launches (ffmpeg, ffprobe, ocrmypdf,
# PowerShell) would otherwise pop up a black window. All child processes go through these two helpers.
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW
def _aa_popen(*args, **kwargs):
    kwargs["creationflags"] = kwargs.get("creationflags", 0) | _NO_WINDOW
    return subprocess.Popen(*args, **kwargs)
def _sp_run(*args, **kwargs):
    kwargs["creationflags"] = kwargs.get("creationflags", 0) | _NO_WINDOW
    return subprocess.run(*args, **kwargs)

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

# ---------- yt-dlp: the bundled copy, or a newer one the user installed from inside the app ----------
# Sites change constantly and yt-dlp ships fixes within days, so Tools > "Update link downloader" can fetch the
# latest yt-dlp (+ its YouTube challenge solver) into the user's profile. It is only used when newer than the
# bundled copy, and anything that fails to import falls back to the bundled one.
_YTDLP_DIR = Path(os.getenv("APPDATA", str(Path.home() / "AppData/Roaming"))) / "Any2Any" / "ytdlp"
_YTDLP_PKGS = ("yt_dlp", "yt_dlp_ejs")

def _vkey(v) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:4])      # '2026.08.19' -> (2026, 8, 19)

def _pkg_version_on_disk(root: Path, pkg: str) -> Optional[str]:
    try:
        txt = (root / pkg / ("version.py" if pkg == "yt_dlp" else "_version.py")).read_text(encoding="utf-8")
        # yt_dlp writes  __version__ = '2026.08.19'   while setuptools-scm writes  __version__ = version = '0.8.0'
        m = re.search(r"__version__[ \t]*(?::[^=\n]+)?=[ \t]*(?:\w+[ \t]*=[ \t]*)?['\"]([^'\"]+)", txt)
        return m.group(1) if m else None
    except Exception:
        return None

def _purge_ytdlp_modules():
    for k in [k for k in list(sys.modules) if k.split(".")[0] in _YTDLP_PKGS]:
        del sys.modules[k]

try:
    import yt_dlp as ytdlp
except Exception:
    ytdlp = None
YTDLP_BUNDLED_VERSION = getattr(getattr(ytdlp, "version", None), "__version__", None)
YTDLP_SOURCE = "bundled"
_ov = _pkg_version_on_disk(_YTDLP_DIR, "yt_dlp")
if _ov and (YTDLP_BUNDLED_VERSION is None or _vkey(_ov) > _vkey(YTDLP_BUNDLED_VERSION)):
    try:
        _purge_ytdlp_modules(); sys.path.insert(0, str(_YTDLP_DIR))
        import yt_dlp as ytdlp
        YTDLP_SOURCE = "updated"
    except Exception:
        sys.path[:] = [p for p in sys.path if p != str(_YTDLP_DIR)]    # a bad update must never break the app
        _purge_ytdlp_modules()
        try:
            import yt_dlp as ytdlp
        except Exception:
            ytdlp = None
YTDLP_VERSION = getattr(getattr(ytdlp, "version", None), "__version__", "not installed")

_IMPORT_ERR: Dict[str, str] = {}      # why an optional library didn't load (shown in the Dependency Check and errors)
try:
    import camelot, pandas as pd
except Exception as _e:
    camelot = None; pd = None; _IMPORT_ERR["camelot"] = f"{type(_e).__name__}: {_e}"

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

try:
    import pymupdf  # same engine as fitz; used directly for HTML->PDF rendering
except Exception:
    pymupdf = None

try:
    import pdf2docx as _pdf2docx
except Exception:
    _pdf2docx = None

try:
    import mammoth
except Exception:
    mammoth = None

try:
    import markdownify as _markdownify
except Exception:
    _markdownify = None

try:
    import pymupdf4llm
except Exception:
    pymupdf4llm = None

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
from PySide6.QtCore import Qt, QThread, Signal, QSize, QTimer, QSettings
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFileDialog, QComboBox, QLineEdit,
    QProgressBar, QListWidget, QListWidgetItem, QMessageBox,
    QGroupBox, QFormLayout, QSpinBox, QCheckBox, QMenuBar, QDialog,
    QDialogButtonBox, QSizePolicy
)

# Text recognition (OCR) uses the engine built into Windows 10/11: offline, nothing extra to ship.
# !! This import MUST stay below the PySide6 imports: importing the Windows Runtime bindings (winrt) before Qt
# !! makes the process crash with an access violation while Qt loads. Qt first, then winrt, is fine.
try:
    from winrt.windows.media.ocr import OcrEngine as _OcrEngine
    from winrt.windows.globalization import Language as _WinLanguage
    from winrt.windows.graphics.imaging import BitmapDecoder as _BitmapDecoder
    from winrt.windows.storage.streams import InMemoryRandomAccessStream as _MemStream, DataWriter as _DataWriter
    HAVE_OCR = True
except Exception as _e:
    HAVE_OCR = False; _IMPORT_ERR["ocr"] = f"{type(_e).__name__}: {_e}"

SUPPORTED_OUTPUTS = [
    "mp3","m4a","opus","mp4","gif","webp-anim",
    "png","jpg","webp","avif",
    "pdf","docx","md","txt","xlsx","csv","json",
    "srt","mp4+burnsubs",
    "thumbnail"
]

# ---- UI helper labels / categories for format picker ----
SELECT_FORMAT_LABEL = "Select a file type"

# Map formats into human-friendly groups for a nicer combobox.
OUTPUT_CATEGORIES = {
    "Videos": ["mp4", "mp4+burnsubs", "gif", "webp-anim", "thumbnail"],
    "Audio": ["mp3", "m4a", "opus"],
    "Images": ["png", "jpg", "webp", "avif"],
    "Documents": ["pdf", "docx", "md", "txt"],
    "Data": ["xlsx", "csv", "json"],
    "Subtitles": ["srt"],
}


PRIVACY_NOTE = (
    "Privacy: everything runs on this PC. There is no telemetry, no analytics and no account.\n"
    "The only network use is what you ask for: downloading links you paste (requests go to those sites), "
    "Tools > 'Update link downloader' (pypi.org), and following Reddit short links. "
    "Cookies you provide (browser or cookies.txt) are read on this PC and sent only to the site you are downloading from."
)

# Hover text for each entry of the output-type dropdown.
FORMAT_HELP = {
    "mp4": "MP4 video (H.264/AAC): plays everywhere. From a video, or a GIF, or a link. Supports trim, scale, FPS and a size cap.",
    "mp4+burnsubs": "MP4 with the subtitles from a .srt file burned into the picture (set the file under Options > Subtitles file).",
    "gif": "Animated GIF. Honors trim and the size cap (it shrinks until it fits). Big for long clips; animated WebP or MP4 is smaller.",
    "webp-anim": "Animated WebP: much smaller than GIF at similar quality. Honors trim and the size cap.",
    "thumbnail": "The video's cover image, from a link.",
    "mp3": "MP3 audio, from a video, audio file or link. Optional loudness normalize, silence trim, size cap.",
    "m4a": "AAC audio in an M4A file.",
    "opus": "Opus audio: the best quality per megabyte, great for fitting under a size cap.",
    "png": "PNG image (lossless). From an image, or each page of a PDF.",
    "jpg": "JPEG image. Transparent areas become white. From an image, or each page of a PDF.",
    "webp": "WebP image (smaller than JPG/PNG).",
    "avif": "AVIF image (smallest, newer format).",
    "pdf": "PDF. From images (one PDF per image; tick 'searchable' to add recognized text), from a Word (.docx) file, or from a scanned PDF to make its text selectable and searchable (OCR).",
    "docx": "Word document, rebuilt from a PDF (text, tables and pictures). A scanned PDF is read with OCR into editable text.",
    "md": "Markdown text, from a PDF or a Word (.docx) file. Scanned PDF pages are read with OCR.",
    "txt": "Plain text read from a picture (screenshot, photo of a page, scan) or from a PDF, using Windows' built-in OCR. Works offline.",
    "xlsx": "Excel workbook with the tables found in a PDF (or from a CSV).",
    "csv": "CSV: tables from a PDF, or from an Excel/JSON file.",
    "json": "JSON, from a CSV file.",
    "srt": "Subtitle text (.srt) pulled out of a video's subtitle track.",
}

# Discord per-file upload caps, in MB. Last checked 2026-10. Free was raised from 10 to 20 MB on 2026-08-13
# (announced by @discord). Nitro Basic 50, Nitro 500 and server boost L2 50 / L3 100 agree across the third-party
# sources I could reach (Discord's own help page blocks automated reads); nothing supports a 1 GB Nitro figure.
# Discord applies the LARGER of the user's own limit and the server's boost limit (they don't stack).
# Discord changes these often: the "Custom size cap" option overrides any preset.
DISCORD_PRESETS = {
    "Off": 0,
    "Free (20 MB)": 20,
    "Nitro Basic (50 MB)": 50,
    "Nitro (500 MB)": 500,
    "Server boost L2 (50 MB)": 50,
    "Server boost L3 (100 MB)": 100,
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
LOG.info("yt-dlp %s (%s)", YTDLP_VERSION, YTDLP_SOURCE)
for _k, _v in _IMPORT_ERR.items(): LOG.warning("optional library %s failed to load: %s", _k, _v)

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

def _bundled_quickjs() -> Optional[str]:
    """Path to the QuickJS-ng engine shipped in vendor/quickjs (lets yt-dlp solve YouTube's JS challenges on PCs
    that have neither Deno nor Node.js installed)."""
    bases = [Path(__file__).parent]
    if getattr(sys, "_MEIPASS", None): bases.append(Path(sys._MEIPASS))
    if getattr(sys, "frozen", False): bases.append(Path(sys.executable).parent)
    for b in bases:
        p = b / "vendor" / "quickjs" / "qjs.exe"
        if p.exists(): return str(p)
    return None

def _js_runtimes() -> Dict[str, dict]:
    """Runtimes handed to yt-dlp: whatever the PC has (fastest), else the bundled QuickJS."""
    found = {n: {} for n in ("deno", "node", "bun", "quickjs") if shutil.which(n)}
    if not found:
        q = _bundled_quickjs()
        if q: found = {"quickjs": {"path": q}}
    return found

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
        proc = _aa_popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        out, _ = proc.communicate(timeout=10)
        return proc.returncode == 0, (out or "").strip()
    except subprocess.TimeoutExpired:
        try: proc.kill()
        except Exception: pass
        return False, "timeout"
    except Exception as e:
        return False, str(e)

def _map_encoder_choice(name: str) -> str:
    return {"x264":"libx264","nvenc":"h264_nvenc","qsv":"h264_qsv","amf":"h264_amf"}.get(name,"libx264")


def probe_duration_seconds(path: Path) -> float:
    try:
        out = _sp_run([ensure_ffprobe(), "-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1", str(path)],
                      capture_output=True, text=True, check=True, timeout=30).stdout
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

# Discord doesn't say whether its "MB" is 1,000,000 or 1,048,576 bytes. Treating a cap as the SMALLER (decimal) number
# means a file that fits it fits under either reading.
CAP_MB_BYTES = 1_000_000

def get_target_mb(opts: Dict[str,str]) -> int:
    """The raw size cap in MB (custom cap wins over the Discord preset). Headroom is NOT applied here: every
    converter subtracts options['headroom_percent'] itself, so it is applied exactly once."""
    if not opts or opts.get("size_targeting","0")!="1": return 0
    custom = int(opts.get("custom_cap_mb", 0))
    if custom > 0: return custom
    return int(opts.get("discord_size_mb", 0))

def compute_segment_seconds(target_mb: int, total_bps: int) -> int:
    if target_mb<=0 or total_bps<=0: return 0
    bits = int(target_mb * CAP_MB_BYTES * 8 * 0.97)
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

STALL_AFTER_SECS = 30          # silent this long -> check whether the process is still doing work
STALL_HARD_CAP_SECS = 1800     # ...but never wait longer than this with no output at all

def _proc_cpu_seconds(pid: int) -> Optional[float]:
    """Total CPU time (user+kernel) a process has used, or None if it can't be read."""
    if os.name != "nt": return None
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.windll.kernel32
        k.OpenProcess.restype = wintypes.HANDLE; k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        h = k.OpenProcess(0x1000, False, pid)                  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h: return None
        try:
            ft = [wintypes.FILETIME() for _ in range(4)]
            if not k.GetProcessTimes(h, *[ctypes.byref(f) for f in ft]): return None
            secs = lambda f: ((f.dwHighDateTime << 32) | f.dwLowDateTime) / 1e7
            return secs(ft[2]) + secs(ft[3])                   # kernel + user
        finally:
            k.CloseHandle(h)
    except Exception:
        return None

def run_proc(cmd: List[str], progress: Callable[[int,str],None], job: "Job"):
    LOG.info("cmd: %s", " ".join(cmd))
    start = time.time()
    try:
        # BELOW_NORMAL priority keeps the desktop responsive during long encodes (no-window flag is added by _aa_popen)
        proc = _aa_popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         creationflags=0x00004000 if os.name == "nt" else 0)
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

        last = time.time(); next_check = last + STALL_AFTER_SECS; cpu_mark = None
        while True:
            if job.cancel_flag:
                proc.terminate(); raise ConvertError("Cancelled")
            try:
                line = q.get(timeout=1)
            except queue.Empty:
                now = time.time()
                if now >= next_check:
                    # Silence alone isn't a stall: ffmpeg legitimately prints nothing for a long time in some passes
                    # (e.g. building a GIF palette from a long clip). Stalled = silent AND not using any CPU.
                    cpu = _proc_cpu_seconds(proc.pid)
                    busy = cpu is not None and cpu_mark is not None and (cpu - cpu_mark) > 0.2
                    if (busy or (cpu is not None and cpu_mark is None)) and now - last < STALL_HARD_CAP_SECS:
                        cpu_mark = cpu; next_check = now + STALL_AFTER_SECS      # still working (or one more window to measure)
                    else:
                        try: proc.terminate()
                        except Exception: pass
                        raise ConvertError(f"The converter stopped responding (no output and no activity for {int(now - last)}s). Aborted for safety.")
                continue
            if line is None:
                break
            last = time.time(); next_check = last + STALL_AFTER_SECS; cpu_mark = None
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
# NVENC tuning flags are built directly by encoder_preset_flags() below, applied at the call site in
# VideoToMP4.convert. No global subprocess patching.
def _prep_image(im, flatten: bool):
    """Apply camera rotation (EXIF) before metadata is dropped; optionally composite transparency onto white
    (formats without alpha, like JPG/PDF, otherwise turn transparent areas black)."""
    from PIL import ImageOps
    try: im = ImageOps.exif_transpose(im)
    except Exception: pass
    if not flatten: return im
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA"); bg = Image.new("RGB", rgba.size, (255, 255, 255)); bg.paste(rgba, mask=rgba.getchannel("A")); return bg
    return im.convert("RGB")

def _img_save_fit(im: object, dst: Path, out_ext: str, limit_mb: float, q0: int, strip: bool):
    if not limit_mb:
        im.save(dst, **({"quality":q0,"optimize":True} if out_ext in ("jpg","jpeg") else {}), **({"exif": b""} if strip else {})); return
    limit = limit_mb*CAP_MB_BYTES; q = q0 if out_ext in ("jpg","jpeg","webp","avif") else None; scale = 1.0
    for _ in range(24):
        tmp = dst.with_suffix(".tmp"+dst.suffix)
        test = im if scale>=0.999 else im.resize((max(1,int(im.width*scale)), max(1,int(im.height*scale))))
        skw = ({"quality":max(25,int(q))} if q else {})
        if q and out_ext in ("jpg","jpeg"): skw["optimize"] = True
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
        head = int(o.get("headroom_percent","0")); limit = target*(1-head/100.0) if target else 0
        strip = o.get("strip_meta","0")=="1"; max_dim = int(o.get("max_dim",0)); q = int(o.get("quality","90"))
        if dst.resolve()==src.resolve(): dst = dst.with_name(src.stem+"_out."+out)   # never overwrite the original in place
        progress(5,"Opening image")
        try:
            with Image.open(src) as im0:
                im = _prep_image(im0, flatten=out in ("jpg","jpeg"))
                if max_dim and (im.width>max_dim or im.height>max_dim):
                    r = min(max_dim/im.width, max_dim/im.height); im = im.resize((max(1,int(im.width*r)), max(1,int(im.height*r))))
                if out=="avif" and not HAVE_HEIF: raise ConvertError("AVIF: pip install pillow-heif")
                progress(60,"Saving")
                _img_save_fit(im, dst, out, limit, q, strip)
        except ConvertError: raise
        except Exception as e:
            raise ConvertError(f"Image conversion failed: {e}")
        progress(100,f"Saved {dst.name}"); return str(dst)
register(ImageToImage())

class ImagesToPDF(Converter):
    accepts = ("png","jpg","jpeg","webp","bmp","tiff")
    produces = ("pdf",)
    def convert(self, job: "Job", progress):
        if Image is None: raise ConvertError("Pillow not installed")
        o = job.options or {}; first = Path(job.source_path)
        paths = [Path(p) for p in json.loads(o["merge_paths"])] if o.get("merge_paths") else [first]
        merged = len(paths) > 1
        dst = Path(job.output_dir) / (first.stem + ("_merged" if merged else "") + ".pdf")
        pages = []
        for n, p in enumerate(paths):
            _check_cancel(job); progress(5 + int(45*n/len(paths)), f"Reading image {n+1}/{len(paths)}")
            try:
                with Image.open(p) as im: pages.append(_prep_image(im, flatten=True).copy())
            except Exception as e: raise ConvertError(f"Couldn't open {p.name}: {e}")
        pages[0].save(dst, "PDF", save_all=True, append_images=pages[1:])            # one page per image
        if o.get("ocr_pdf") == "1":                                                    # "make searchable": invisible text layer
            layers = {}
            for n, pg in enumerate(pages):
                _check_cancel(job); progress(50 + int(45*n/len(pages)), f"Recognizing text on page {n+1}/{len(pages)}")
                layers[n] = ocr_image(pg, o.get("ocr_lang", "auto"))
            _save_with_ocr(dst, layers, dst)
        progress(100, f"Saved {dst.name}" + (f" ({len(pages)} pages)" if merged else "") + (" (searchable)" if o.get("ocr_pdf") == "1" else "")); return str(dst)
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
        if fitz is None: raise ConvertError("PDF→image needs PyMuPDF. pip install PyMuPDF")
        pages=_pdf_page_list(job, src)          # validates the range / password, same rules as the other PDF outputs
        outputs=[]
        with fitz.open(str(src)) as doc:
            for n, i in enumerate(pages):
                _check_cancel(job)
                pix = doc[i].get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72))
                p = Path(job.output_dir)/f"{src.stem}_p{i+1:03d}.{out}"; pix.save(str(p)); outputs.append(str(p))
                progress(min(95, 5+n*90//max(1,len(pages))), f"Page {i+1}")
        if not outputs: raise ConvertError("No pages selected.")
        progress(100, f"Saved {len(outputs)} images")
        return "[MULTI]"+json.dumps(outputs) if len(outputs)>1 else outputs[0]
register(PDFToImages())

# -------- audio/video --------
def video_encoder(name: str) -> str:
    return _map_encoder_choice(name)

def encoder_preset_flags(enc: str, sized: bool = False) -> List[str]:
    """Encoder tuning. `sized` = a -b:v bitrate target follows (size-capped mode), otherwise quality-based."""
    if enc == "x264":
        return ["-preset","veryfast"]
    if enc == "amf":
        # AMF's defaults pick a very high bitrate (5x x264's size); ask for constant quality instead, or VBR when sized
        return ["-usage","transcoding","-quality","quality","-rc","vbr_peak"] if sized else \
               ["-usage","transcoding","-quality","quality","-rc","cqp","-qp_i","23","-qp_p","25","-qp_b","27"]
    if enc == "nvenc":
        # safer cross-version NVENC preset (p5 ~= medium/quality), plus quality tuning.
        # -multipass must be one of disabled/qres/fullres (not the old "2pass-full").
        return ["-preset","p5","-rc","vbr_hq","-tune","hq","-multipass","fullres","-profile:v","high"]
    # QSV/AMF often don't have a portable "preset" flag across builds
    return []

class VideoToMP3(Converter):
    accepts=("mp4","mkv","mov","avi","webm","m4a","mp3","wav","flac","opus")
    produces=("mp3","m4a","opus")   # ffmpeg picks AAC / Opus from the output extension
    def convert(self, job: "Job", progress):
        ff=ensure_ffmpeg(); src=Path(job.source_path); o=job.options or {}
        aext=job.target_ext.lower() if job.target_ext.lower() in ("mp3","m4a","opus") else "mp3"
        target=get_target_mb(o); start=parse_hhmmss(o.get("start","")); end=parse_hhmmss(o.get("end",""))
        loud=o.get("loudnorm","0")=="1"; silence=o.get("silence","0")=="1"; threads=int(o.get("threads","0")); split=o.get("split","0")=="1"; head=int(o.get("headroom_percent","0"))
        if target:
            dur=probe_duration_seconds(src);
            if dur<=0: raise ConvertError("Couldn't read duration")
            max_bits=int((target*CAP_MB_BYTES)*8*(1-head/100.0)); bps=max(64000, min(320000, max_bits//max(int(dur),1))); br=f"{bps//1000}k"
        else:
            br=o.get("bitrate","192k")
        dst=Path(job.output_dir)/(src.stem+"."+aext)
        if dst.resolve()==src.resolve(): dst=dst.with_name(src.stem+"_out."+aext)   # e.g. shrinking x.mp3 -> mp3 in place
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
        progress(5,f"Encoding {aext.upper()} @ {br}"); run_proc(cmd,progress,job)
        outputs=[str(dst)]
        if target and split:
            bps_est=int(br.replace("k",""))*1000; seg=compute_segment_seconds(target,bps_est)
            if seg>=5:
                base=dst.with_name(dst.stem+"_part_%03d."+aext)
                cmd2=[ff,"-y","-i",str(dst),"-c","copy","-f","segment","-segment_time",str(seg),"-reset_timestamps","1",str(base)]
                progress(80,f"Splitting ≤{target}MB"); run_proc(cmd2,progress,job)
                outputs=sorted([str(p) for p in dst.parent.glob(dst.stem+"_part_*."+aext)]) or [str(dst)]
        progress(100,f"Saved {Path(outputs[0]).name}")
        return "[MULTI]"+json.dumps(outputs) if len(outputs)>1 else outputs[0]
register(VideoToMP3())

def probe_video_codec(path) -> str:
    """codec_name of the first video stream ('' if it can't be read)."""
    try:
        out = _sp_run([ensure_ffprobe(), "-v", "error", "-select_streams", "v:0", "-show_entries",
                       "stream=codec_name", "-of", "csv=p=0", str(path)],
                      capture_output=True, text=True, timeout=20).stdout
        return out.strip().splitlines()[0].strip().lower() if out.strip() else ""
    except Exception:
        return ""

class VideoToMP4(Converter):
    accepts=("mkv","mov","avi","webm","m4v","mpg","mpeg","flv","mp4","gif")   # gif -> mp4 is the usual way to shrink one
    produces=("mp4",)
    def convert(self, job: "Job", progress):
        """GPU encoders (NVENC/QSV/AMF) are checked up front and fall back to x264 if they can't start. This second net
        covers one that starts fine and then fails mid-job (driver hiccup, unsupported input...): retry once in software."""
        enc=(job.options or {}).get("encoder","x264")
        try:
            return self._convert(job, progress)
        except ConvertError as e:
            if enc=="x264" or job.cancel_flag or str(e)=="Cancelled": raise
            LOG.warning("%s encode failed (%s); retrying with x264", enc, str(e).splitlines()[0][:160])
            progress(1, f"{enc.upper()} failed; retrying with software (x264) encoding")
            job.options=dict(job.options or {}, encoder="x264")      # same Job object, so Cancel keeps working
            return self._convert(job, progress)

    def _convert(self, job: "Job", progress):
        ff=ensure_ffmpeg(); src=Path(job.source_path); o=job.options or {}
        dst=Path(job.output_dir)/(src.stem+".mp4"); target=get_target_mb(o)
        start=parse_hhmmss(o.get("start","")); end=parse_hhmmss(o.get("end",""))
        scale=o.get("scale","original"); fps=o.get("fps","original"); enc=o.get("encoder","x264")
        force_reencode = o.get("reencode","0")=="1"   # e.g. downloaded HEVC/VP9 that Discord and browsers can't preview
        is_gif = src.suffix.lower()==".gif"
        if is_gif: force_reencode = True               # a GIF stream can't be copied into an MP4

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
        if is_gif: vf.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")   # H.264 needs even dimensions (GIFs often aren't)

        same_path = (dst.resolve() == src.resolve())
        if same_path: dst = src.with_name(src.stem+"_out.mp4")

        # A size cap only matters if the file is actually over it. Re-encoding something that already fits just
        # costs quality (and the bitrate math below would even inflate it up toward the cap), so pass it through.
        fits = bool(target) and not vf and not start and not end and not force_reencode \
               and src.stat().st_size <= target*CAP_MB_BYTES*(1 - head/100.0)

        if not target or fits:
            if fits: progress(3, f"Already under {target} MB; skipping re-encode")
            # try fast remux (seekable, mapped, faststart)
            if not vf and not force_reencode:
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
            cmd+=["-pix_fmt","yuv420p","-c:a","aac","-b:a","128k","-movflags","+faststart"]   # yuv420p: the format every phone/browser plays
            if vf: cmd+=["-vf",",".join(vf)]
            if threads>0: cmd+=["-threads",str(threads)]
            cmd+=[str(dst)]
            progress(40,f"Transcoding ({vcodec})"); run_proc(cmd,progress,job)
            progress(100,f"Saved {dst.name}"); return str(dst)

        # size targeted
        dur=probe_duration_seconds(src);
        if dur<=0: raise ConvertError("Couldn't read duration")
        # target bits for the whole file, apply headroom, then convert to per-second rates
        total_bits = int((target*CAP_MB_BYTES)*8*(1 - head/100.0))
        total_bps  = max(64000, total_bits // max(int(dur),1))  # bits per second budget
        # choose audio bitrate and leave the rest for video
        a_bps = 128000 if total_bps > 224000 else 96000
        v_bps = max(64000, total_bps - a_bps)
        v_k=max(100,v_bps//1000); a_k=max(64,a_bps//1000)
        vcodec=enc_lib
        hard=int(target*CAP_MB_BYTES); budget=hard*(1 - head/100.0)
        def _encode(vk: int):
            cmd=[ff,"-y"]
            if start: cmd+=["-ss",str(start)]
            if end and end>start: cmd+=["-to",str(end)]
            cmd+=["-i",str(src),"-map","0:v:0","-map","0:a:0?",
                  "-c:v",vcodec] + encoder_preset_flags(enc, sized=True) + \
                 ["-pix_fmt","yuv420p","-b:v",f"{vk}k","-maxrate",f"{int(vk*1.2)}k","-bufsize",f"{int(vk*2)}k",
                  "-c:a","aac","-b:a",f"{a_k}k","-movflags","+faststart"]
            if vf: cmd+=["-vf",",".join(vf)]
            if threads>0: cmd+=["-threads",str(threads)]
            cmd+=[str(dst)]
            run_proc(cmd,progress,job)
        # One-pass rate control can overshoot, and Discord rejects anything over the cap, so check the result and
        # retry at a lower bitrate. (With "split" on, an over-cap file is handled by splitting below instead.)
        for attempt in range(4):
            progress(10+attempt*15, f"Target ≤{target}MB" if attempt==0 else f"Came out over {target} MB; retrying at {v_k}k")
            _encode(v_k)
            size=dst.stat().st_size
            if size<=hard or split: break
            if v_k<=60 or attempt==3:
                try: dst.unlink()
                except Exception: pass
                raise ConvertError(f"Couldn't fit this video under {target} MB (best attempt {size/CAP_MB_BYTES:.1f} MB). "
                                   "Turn on 'Split into parts', trim it, or lower Scale/FPS.")
            v_k=max(60,int(v_k*budget/size*0.97))
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

def _trim_args(o: Dict[str,str]) -> List[str]:
    start=parse_hhmmss(o.get("start","")); end=parse_hhmmss(o.get("end","")); args: List[str]=[]
    if start: args+=["-ss",str(start)]
    if end and end>start: args+=["-to",str(end)]
    return args

def _int_opt(v, default: int) -> int:
    """'720p' / '480' / 10.0 -> int (the generic Scale dropdown stores values like '720p')."""
    digits=re.sub(r"\D","",str(v)) if v is not None else ""
    return int(digits) if digits else default

def _limit_bytes(o: Dict[str,str]) -> int:
    target=get_target_mb(o)
    return int(target*CAP_MB_BYTES*(1-int(o.get("headroom_percent","0"))/100.0)) if target else 0

class VideoToGIF(Converter):
    accepts=("mp4","mkv","mov","avi","webm")
    produces=("gif",)
    def convert(self, job:"Job", progress):
        import tempfile
        ff=ensure_ffmpeg(); src=Path(job.source_path); o=job.options or {}; dst=Path(job.output_dir)/(src.stem+".gif")
        trim=_trim_args(o); limit=_limit_bytes(o)
        fps=_int_opt(o.get("gif_fps"),10); h=_int_opt(o.get("gif_scale"),480); colors=256; dither="sierra2_4a"
        pal=Path(tempfile.gettempdir())/f"a2a_palette_{os.getpid()}_{time.time_ns()}.png"   # not next to the output: could clobber <name>.png
        try:
            for attempt in range(8):
                _check_cancel(job)
                chain=f"fps={fps},scale=-2:{h}:flags=lanczos"
                progress(10+attempt*5, f"Building palette ({h}p, {fps} fps)" if attempt==0 else f"Shrinking to fit {limit/CAP_MB_BYTES:.0f} MB ({h}p, {fps} fps)")
                run_proc([ff,"-y",*trim,"-i",str(src),"-vf",f"{chain},palettegen=max_colors={colors}",str(pal)],progress,job)
                run_proc([ff,"-y",*trim,"-i",str(src),"-i",str(pal),"-lavfi",f"{chain} [x]; [x][1:v] paletteuse=dither={dither}",str(dst)],progress,job)
                size=dst.stat().st_size
                if not limit or size<=limit: break
                if h<=96 and fps<=6 and colors<=64:
                    try: dst.unlink()
                    except Exception: pass
                    raise ConvertError(f"Couldn't get this GIF under {limit/CAP_MB_BYTES:.0f} MB (smallest try was {size/CAP_MB_BYTES:.1f} MB). Trim the clip, or use MP4 / animated WebP, which are far smaller.")
                # GIF size grows with width*height*frames, so scale the height by ~sqrt(needed ratio), then cut fps/colors
                h=max(96,int(h*max(0.5,min(0.9,(limit/size)**0.5*0.95))))
                if h<260: fps=max(6,fps-2)
                if attempt>=1: colors=max(64,colors//2); dither="bayer:bayer_scale=5"
        finally:
            try: pal.unlink()
            except Exception: pass
        progress(100,f"Saved {dst.name}"); return str(dst)
register(VideoToGIF())

class VideoToWebPAnim(Converter):
    accepts=("mp4","mkv","mov","avi","webm")
    produces=("webp-anim",)
    def convert(self, job:"Job", progress):
        ff=ensure_ffmpeg(); src=Path(job.source_path); o=job.options or {}; dst=Path(job.output_dir)/(src.stem+".webp")
        trim=_trim_args(o); limit=_limit_bytes(o)
        fps=_int_opt(o.get("fps"),15); h=_int_opt(o.get("scale"),480); q=70
        for attempt in range(8):
            _check_cancel(job)
            progress(10+attempt*5, "Encoding WebP" if attempt==0 else f"Shrinking to fit {limit/CAP_MB_BYTES:.0f} MB")
            run_proc([ff,"-y",*trim,"-i",str(src),"-vf",f"fps={fps},scale=-2:{h}","-loop","0","-c:v","libwebp","-lossless","0","-q:v",str(q),"-preset","picture",str(dst)],progress,job)
            size=dst.stat().st_size
            if not limit or size<=limit: break
            if q<=30 and h<=96:
                try: dst.unlink()
                except Exception: pass
                raise ConvertError(f"Couldn't get this animated WebP under {limit/CAP_MB_BYTES:.0f} MB (smallest try was {size/CAP_MB_BYTES:.1f} MB). Trim the clip or use MP4.")
            q=max(30,q-15)
            if q<=40 or size>limit*2: h=max(96,int(h*0.8))
            if h<260: fps=max(8,fps-2)
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
        import csv, json as J
        src=Path(job.source_path); out=job.target_ext.lower(); dst=Path(job.output_dir)/(src.stem+"."+out)
        # utf-8-sig: Excel writes a BOM that would otherwise end up glued to the first column name
        if src.suffix.lower()==".csv" and out=="json":
            with open(src, newline='', encoding='utf-8-sig') as f: rows=list(csv.DictReader(f))
            with open(dst,"w",encoding="utf-8") as g: J.dump(rows,g,ensure_ascii=False,indent=2)
        elif src.suffix.lower()==".json" and out=="csv":
            try:
                with open(src,encoding="utf-8-sig") as f: data=J.load(f)
            except ValueError as e:
                raise ConvertError(f"This isn't valid JSON: {e}")
            # {"items":[{...},{...}], ...}: use the one list of records instead of making a single row
            if isinstance(data, dict):
                lists=[v for v in data.values() if isinstance(v,list) and v and all(isinstance(x,dict) for x in v)]
                data = lists[0] if len(lists)==1 else [data]
            if not isinstance(data, list): data=[data]
            if not data: raise ConvertError("The JSON has no records to convert.")
            cell=lambda v: J.dumps(v, ensure_ascii=False) if isinstance(v,(list,dict)) else v
            if all(isinstance(x,(list,tuple)) for x in data):            # [[1,2],[3,4]] -> plain rows
                with open(dst,"w",newline="",encoding="utf-8") as g: csv.writer(g).writerows([[cell(c) for c in r] for r in data])
            else:                                                          # records (scalars become a "value" column)
                rows=[flatten(x) if isinstance(x,dict) else {"value": x} for x in data]
                keys=list(dict.fromkeys(k for r in rows for k in r))     # first-seen order, not alphabetical
                with open(dst,"w",newline="",encoding="utf-8") as g:
                    w=csv.DictWriter(g, fieldnames=keys); w.writeheader(); w.writerows([{k:cell(v) for k,v in r.items()} for r in rows])
        else: raise ConvertError("Unsupported direction")
        progress(100,f"Saved {dst.name}"); return str(dst)
register(CSVJSON())

class PDFtoXLSX(Converter):
    accepts=("pdf",); produces=("xlsx","csv")
    def convert(self, job:"Job", progress):
        if camelot is None or pd is None:
            raise ConvertError("PDF→Excel isn't available: the table-reading library failed to load"
                               + (f" ({_IMPORT_ERR['camelot']})" if "camelot" in _IMPORT_ERR else "")
                               + ". Running from source? Install the requirements: pip install -r requirements.txt")
        src=Path(job.source_path); out=job.target_ext.lower(); base=Path(job.output_dir)/src.stem
        pages=(job.options or {}).get("pages","all"); flavor=(job.options or {}).get("flavor","stream"); langs=(job.options or {}).get("ocr_langs","")
        _pdf_page_list(job, src)   # fail early and clearly on locked / unreadable PDFs
        try:
            tables=camelot.read_pdf(str(src), pages=pages, flavor=flavor)
            if tables.n==0: raise RuntimeError("no tables found")
        except Exception as first_err:
            if not have_binary("ocrmypdf"):
                raise ConvertError(f"No tables could be extracted ({first_err}). If this PDF is a scan, install ocrmypdf to OCR it first; otherwise try the other table flavor (stream/lattice).")
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
                # camelot's frame has integer column labels (0,1,2..) and the table's own header is its first row,
                # so writing labels would put a junk "0 1 2" row above the real header
                for i,t in enumerate(tables): t.df.to_excel(w, sheet_name=f"Table_{i+1}", index=False, header=False)
            progress(100,f"Saved {Path(dst).name}"); return dst
        outputs=[]
        for i,t in enumerate(tables):
            p=base.with_name(base.name+f"_table_{i+1}.csv"); t.df.to_csv(p,index=False,header=False,encoding="utf-8-sig"); outputs.append(str(p))
        progress(100,f"Saved {len(outputs)} CSVs")
        return "[MULTI]"+json.dumps(outputs) if len(outputs)>1 else outputs[0]
register(PDFtoXLSX())

class CSVXLSX(Converter):
    accepts=("csv","xlsx"); produces=("xlsx","csv")
    def convert(self, job:"Job", progress):
        if pd is None: raise ConvertError("pandas not installed")
        src=Path(job.source_path); out=job.target_ext.lower(); dst=Path(job.output_dir)/(src.stem+"."+out)
        if src.suffix.lower()==".csv" and out=="xlsx": pd.read_csv(src).to_excel(dst,index=False)
        elif src.suffix.lower()==".xlsx" and out=="csv": pd.read_excel(src).to_csv(dst,index=False,encoding="utf-8-sig")   # BOM so Excel shows accents
        else: raise ConvertError("Unsupported direction")
        progress(100,f"Saved {dst.name}"); return str(dst)
register(CSVXLSX())

# -------- documents: PDF <-> Word, and either -> Markdown --------
def _check_cancel(job: "Job"):
    if job.cancel_flag: raise ConvertError("Cancelled")

def _pdf_page_list(job: "Job", src: Path) -> List[int]:
    """0-based page indexes from the 'PDF pages' option ('all' or '1-3,5'). Also rejects locked PDFs early."""
    if fitz is None: raise ConvertError("PyMuPDF not installed. pip install pymupdf")
    try:
        with fitz.open(str(src)) as d:
            if d.needs_pass: raise ConvertError("This PDF is password-protected. Remove the password first.")
            total = d.page_count
    except ConvertError: raise
    except Exception as e: raise ConvertError(f"Couldn't open this PDF: {e}")
    spec = ((job.options or {}).get("pages") or "all").strip().lower().replace(" ", "")
    if spec in ("", "all"): return list(range(total))
    out: List[int] = []
    for part in spec.split(","):
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m: raise ConvertError(f"Bad page range '{part}'. Use 'all' or something like 1-3,5.")
        a = int(m.group(1)); b = int(m.group(2) or a)
        if a < 1 or b < a or b > total: raise ConvertError(f"Page range '{part}' is outside this PDF (it has {total} pages).")
        out.extend(range(a-1, b))
    return sorted(set(out))

# -------- OCR: images and scanned PDFs (Windows' built-in text recognition) --------
@dataclass
class OcrWord:
    text: str; x: float; y: float; w: float; h: float

@dataclass
class OcrLine:
    text: str; words: List[OcrWord]; x: float; y: float; w: float; h: float

@dataclass
class OcrPage:
    lines: List[OcrLine]; width: int; height: int
    def paragraphs(self) -> List[List[str]]:
        """Lines grouped into paragraphs by the vertical gap between them."""
        if not self.lines: return []
        hs = sorted(l.h for l in self.lines); med = hs[len(hs)//2]
        out = [[self.lines[0].text]]
        for prev, l in zip(self.lines, self.lines[1:]):
            if l.y - (prev.y + prev.h) > 0.6*med: out.append([])
            out[-1].append(l.text)
        return out
    def as_text(self) -> str:             # keeps the line breaks, blank line between paragraphs
        return "\n\n".join("\n".join(p) for p in self.paragraphs())
    def paragraph_texts(self) -> List[str]:   # one string per paragraph (for Word / Markdown)
        return [" ".join(p) for p in self.paragraphs()]

_OCR_HELP = ("Add one in Windows: Settings > Time & language > Language & region > Add a language, then open that "
             "language's options and install 'Optical character recognition'.")

def ocr_languages() -> List[Tuple[str, str]]:
    """[(tag, display name)] of the OCR languages installed in Windows."""
    if not HAVE_OCR: return []
    try: return [(l.language_tag, l.display_name) for l in _OcrEngine.available_recognizer_languages]
    except Exception: return []

def _ocr_engine(lang: str):
    if lang and lang != "auto":
        eng = _OcrEngine.try_create_from_language(_WinLanguage(lang))
        if eng is None:
            have = ", ".join(t for t, _ in ocr_languages()) or "none"
            raise ConvertError(f"The OCR language '{lang}' isn't installed (installed: {have}). {_OCR_HELP}")
        return eng
    eng = _OcrEngine.try_create_from_user_profile_languages()       # your Windows display/input languages
    if eng is None:
        langs = ocr_languages()
        if langs: eng = _OcrEngine.try_create_from_language(_WinLanguage(langs[0][0]))
    if eng is None: raise ConvertError(f"Windows has no OCR language installed. {_OCR_HELP}")
    return eng

def _ocr_score(page: "OcrPage") -> int:
    """How much real-looking text was found: letters in whole words of 3+ letters. Sideways text scores ~0."""
    return sum(len(w.text) for l in page.lines for w in l.words if len(w.text) >= 3 and w.text.isalpha())

def ocr_image(pil, lang: str = "auto", auto_rotate: bool = False) -> OcrPage:
    """Recognize the text in a PIL image. Coordinates in the result are in that image's own pixels.
    auto_rotate: if little is found, also try the picture turned 90/180/270 degrees and keep the best reading
    (for text output only: the coordinates then refer to the turned picture)."""
    page = _ocr_once(pil, lang)
    if auto_rotate and _ocr_score(page) < 12:
        best, best_s = page, _ocr_score(page)
        for ang in (90, 270, 180):
            cand = _ocr_once(pil.rotate(ang, expand=True), lang); sc = _ocr_score(cand)
            if sc > best_s: best, best_s = cand, sc
        if best_s >= 12: return best
    return page

def _ocr_once(pil, lang: str = "auto") -> OcrPage:
    """Recognize the text in a PIL image. Coordinates in the result are in that image's own pixels."""
    if not HAVE_OCR:
        raise ConvertError("Text recognition (OCR) needs Windows 10/11" + (f" ({_IMPORT_ERR['ocr']})" if "ocr" in _IMPORT_ERR else "") + ".")
    import asyncio, io
    im = pil if pil.mode == "RGB" else _prep_image(pil, flatten=True)
    W, H = im.size
    scale = 1.0
    maxd = int(_OcrEngine.max_image_dimension)
    if max(W, H) > maxd:
        scale = maxd / max(W, H)                         # the engine rejects very large images
    elif max(W, H) < OCR_MIN_SIDE:
        scale = min(OCR_MAX_UPSCALE, OCR_MIN_SIDE / max(W, H))   # small text reads far better when enlarged
    if scale != 1.0:
        im = im.resize((max(1, int(W*scale)), max(1, int(H*scale))), Image.LANCZOS)
    engine = _ocr_engine(lang)
    async def _run():
        buf = io.BytesIO(); im.save(buf, "PNG")
        stream = _MemStream(); w = _DataWriter(stream)
        w.write_bytes(buf.getvalue()); await w.store_async(); await w.flush_async(); w.detach_stream(); stream.seek(0)
        bmp = await (await _BitmapDecoder.create_async(stream)).get_software_bitmap_async()
        return await engine.recognize_async(bmp)
    try:
        res = asyncio.run(_run())
    except ConvertError: raise
    except Exception as e:
        raise ConvertError(f"Text recognition failed: {e}")
    lines: List[OcrLine] = []
    for ln in res.lines:
        words = [OcrWord(w.text, w.bounding_rect.x/scale, w.bounding_rect.y/scale, w.bounding_rect.width/scale, w.bounding_rect.height/scale) for w in ln.words]
        if not words: continue
        x0 = min(w.x for w in words); y0 = min(w.y for w in words)
        x1 = max(w.x + w.w for w in words); y1 = max(w.y + w.h for w in words)
        lines.append(OcrLine(ln.text, words, x0, y0, x1 - x0, y1 - y0))
    return OcrPage(lines, W, H)

OCR_MIN_SIDE = 1000      # images smaller than this (px, longest side) are enlarged before recognition
OCR_MAX_UPSCALE = 3.0

def _render_pdf_page(doc, pno: int, dpi: int = 200):
    pix = doc[pno].get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72), alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

def _page_has_text(page) -> bool:
    return len(page.get_text().strip()) >= 5

_OCR_FONT = next((p for p in (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\segoeui.ttf") if os.path.exists(p)), None)

def _add_ocr_layer(page, ocr: OcrPage):
    """Lay the recognized words over the page as invisible text, so the scan becomes searchable/selectable.
    Each word is sized to the width it has in the picture, so search highlights land on the printed word, and the
    page's rotation flag is honoured (the text is turned so it still reads horizontally as displayed)."""
    if not ocr.lines: return
    sx = page.rect.width / ocr.width; sy = page.rect.height / ocr.height
    measure = fitz.Font(fontfile=_OCR_FONT) if _OCR_FONT else fitz.Font("helv")
    fname = "helv"
    if _OCR_FONT:
        page.insert_font(fontname="a2aocr", fontfile=_OCR_FONT); fname = "a2aocr"
    inv = page.derotation_matrix                          # page.rect is the rotated view; text goes in unrotated space
    rot = page.rotation
    for ln in ocr.lines:
        for w in ln.words:
            unit = measure.text_length(w.text, fontsize=1)
            if not w.text.strip() or unit <= 0: continue
            pt = fitz.Point(w.x*sx, (w.y + w.h*0.82)*sy) * inv
            page.insert_text(pt, w.text, fontsize=min(80.0, max(3.0, (w.w*sx) / unit)), fontname=fname, rotate=rot, render_mode=3)

def _save_with_ocr(pdf_path: Path, layers: Dict[int, OcrPage], out_path: Path):
    """Add OCR text layers to the given pages of pdf_path and write the result to out_path (may be the same file)."""
    tmp = out_path.with_name(out_path.stem + ".tmp.pdf")
    with fitz.open(str(pdf_path)) as doc:
        for pno, ocr in layers.items(): _add_ocr_layer(doc[pno], ocr)
        doc.save(str(tmp), garbage=3, deflate=True)
    os.replace(str(tmp), str(out_path))

class ImageToText(Converter):
    accepts=("png","jpg","jpeg","webp","bmp","tiff","heic","heif","avif","gif"); produces=("txt",)
    def convert(self, job:"Job", progress):
        if Image is None: raise ConvertError("Pillow not installed. pip install pillow")
        src=Path(job.source_path); dst=Path(job.output_dir)/(src.stem+".txt"); lang=(job.options or {}).get("ocr_lang","auto")
        progress(10, "Opening image")
        try:
            with Image.open(src) as im0: im=_prep_image(im0, flatten=True)       # camera rotation + white background
        except Exception as e: raise ConvertError(f"Couldn't open this image: {e}")
        _check_cancel(job); progress(40, "Recognizing text")
        page=ocr_image(im, lang, auto_rotate=True)
        text=page.as_text()
        if not text.strip():
            raise ConvertError("No text was found in this image. Try a larger or sharper picture, or another OCR language (Options > OCR language).")
        dst.write_text(text+"\n", encoding="utf-8")
        progress(100, f"Saved {dst.name} ({len(page.lines)} lines)"); return str(dst)
register(ImageToText())

class PDFToText(Converter):
    accepts=("pdf",); produces=("txt",)
    def convert(self, job:"Job", progress):
        if fitz is None: raise ConvertError("PDF→text needs PyMuPDF. pip install PyMuPDF")
        src=Path(job.source_path); dst=Path(job.output_dir)/(src.stem+".txt"); lang=(job.options or {}).get("ocr_lang","auto")
        pages=_pdf_page_list(job, src); parts=[]; scanned=0
        with fitz.open(str(src)) as doc:
            for n, pno in enumerate(pages):
                _check_cancel(job)
                text=doc[pno].get_text("text", sort=True).strip()
                if len(text) < 5:                       # no text layer: a scan, so read the picture of the page
                    progress(5+int(90*n/max(len(pages),1)), f"Page {n+1}/{len(pages)}: recognizing text")
                    text=ocr_image(_render_pdf_page(doc, pno), lang, auto_rotate=True).as_text().strip(); scanned+=1
                else:
                    progress(5+int(90*(n+1)/max(len(pages),1)), f"Page {n+1}/{len(pages)}")
                if text: parts.append(text)
        out="\n\n".join(parts).strip()
        if not out: raise ConvertError("No text was found in this PDF, even with OCR.")
        dst.write_text(out+"\n", encoding="utf-8")
        progress(100, f"Saved {dst.name}" + (f" ({scanned} scanned page(s) read with OCR)" if scanned else "")); return str(dst)
register(PDFToText())

class PDFMakeSearchable(Converter):
    """Scanned PDF -> same PDF with an invisible text layer (select, search and copy text). Pages that already have text are left alone."""
    accepts=("pdf",); produces=("pdf",)
    def convert(self, job:"Job", progress):
        if fitz is None: raise ConvertError("This needs PyMuPDF. pip install PyMuPDF")
        src=Path(job.source_path); dst=Path(job.output_dir)/(src.stem+"_searchable.pdf"); lang=(job.options or {}).get("ocr_lang","auto")
        pages=_pdf_page_list(job, src); layers: Dict[int, OcrPage]={}
        with fitz.open(str(src)) as doc:
            for n, pno in enumerate(pages):
                _check_cancel(job); progress(5+int(85*n/max(len(pages),1)), f"Page {n+1}/{len(pages)}")
                if _page_has_text(doc[pno]): continue
                layers[pno]=ocr_image(_render_pdf_page(doc, pno), lang)
        if not layers:
            raise ConvertError("Every page of this PDF already has selectable text, so there was nothing to recognize.")
        progress(92, "Writing searchable PDF"); _save_with_ocr(src, layers, dst)
        progress(100, f"Saved {dst.name} ({len(layers)} page(s) made searchable)"); return str(dst)
register(PDFMakeSearchable())

class PDFToWord(Converter):
    accepts=("pdf",); produces=("docx",)
    def convert(self, job:"Job", progress):
        if _pdf2docx is None: raise ConvertError("PDF→Word needs pdf2docx. pip install pdf2docx")
        src=Path(job.source_path); dst=Path(job.output_dir)/(src.stem+".docx")
        pages=_pdf_page_list(job, src)
        if HAVE_OCR:
            with fitz.open(str(src)) as _d:
                scanned_only = not any(_page_has_text(_d[p]) for p in pages)
            if scanned_only: return self._from_scan(job, src, dst, pages, progress)     # pdf2docx can't rebuild text from pictures
        progress(5, "Reading PDF")
        cv=_pdf2docx.Converter(str(src))
        try:
            # the same steps cv.convert() runs (including its default settings), split up so Cancel can act between them
            st=cv.default_settings
            cv.load_pages(0, None, pages); _check_cancel(job)
            progress(15, "Analyzing layout"); cv.parse_document(**st); _check_cancel(job)
            progress(40, f"Rebuilding {len(pages)} page(s)"); cv.parse_pages(**st); _check_cancel(job)
            progress(85, "Writing Word file"); cv.make_docx(str(dst), **st)
        except ConvertError: raise
        except Exception as e: raise ConvertError(f"PDF→Word failed: {e}")
        finally: cv.close()
        progress(100, f"Saved {dst.name}"); return str(dst)

    def _from_scan(self, job, src, dst, pages, progress):
        """A scanned PDF has no text to rebuild, so read each page's picture with OCR and write the text as paragraphs."""
        import docx as _docx
        lang=(job.options or {}).get("ocr_lang","auto"); d=_docx.Document(); got=False
        with fitz.open(str(src)) as doc:
            for n, pno in enumerate(pages):
                _check_cancel(job); progress(5+int(85*n/max(len(pages),1)), f"Page {n+1}/{len(pages)}: recognizing text (scanned PDF)")
                for t in ocr_image(_render_pdf_page(doc, pno), lang, auto_rotate=True).paragraph_texts():
                    d.add_paragraph(t); got=True
                if n < len(pages)-1: d.add_page_break()
        if not got: raise ConvertError("No text was found in this PDF, even with OCR.")
        d.save(str(dst)); progress(100, f"Saved {dst.name} (scanned PDF: text recognized with OCR)"); return str(dst)
register(PDFToWord())

def _repair_merged_tables(md: str) -> str:
    """pymupdf4llm sometimes folds the text sitting just above a table (e.g. a numbered list) into the table's header
    row, inventing 'Col2', 'Col3' names for the other columns, and pushes the real header down to the first data row:

        |1. step one 2. step two|Col2|Col3|        ->     1. step one
        |---|---|---|                                     2. step two
        |Region|Q1|Q2|
        |North|10|12|                                     |Region|Q1|Q2|
                                                          |---|---|---|
                                                          |North|10|12|

    The output alone is enough to undo that, so this works on the Markdown text (no second extraction)."""
    lines = md.split("\n"); out: List[str] = []; i = 0
    sep = re.compile(r"\|(\s*:?-+:?\s*\|)+")
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("|") and i + 2 < len(lines) and sep.fullmatch(lines[i+1].strip()) and lines[i+2].startswith("|"):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(cells) > 1 and all(re.fullmatch(r"Col\d+", c) for c in cells[1:]):
                for part in re.sub(r"<br\s*/?>", "\n", cells[0]).split("\n"):
                    # the swallowed text may hold several list items run together: split before each "2." / "-" / bullet
                    for item in re.split(r"\s+(?=(?:\d+[.)]|[•*-])\s)", part.strip()):
                        if item.strip(): out.append(item.strip())
                out += ["", lines[i+2], lines[i+1]]          # real header (first data row), then the separator
                i += 3
                continue
        out.append(ln); i += 1
    return "\n".join(out)

class PDFToMarkdown(Converter):
    accepts=("pdf",); produces=("md",)
    def convert(self, job:"Job", progress):
        if pymupdf4llm is None: raise ConvertError("PDF→Markdown needs pymupdf4llm. pip install \"pymupdf4llm<1\"")
        src=Path(job.source_path); dst=Path(job.output_dir)/(src.stem+".md"); o=job.options or {}
        pages=_pdf_page_list(job, src)
        img_dir=Path(job.output_dir)/(src.stem+"_images"); want_images=o.get("md_images","0")=="1"
        extra={}
        if want_images:
            img_dir.mkdir(parents=True, exist_ok=True)
            extra=dict(write_images=True, image_path=str(img_dir), image_format="png")
        progress(5, "Reading PDF")
        parts=[]; scanned=0; lang=o.get("ocr_lang","auto")
        try:
            with fitz.open(str(src)) as doc:
                hdr=pymupdf4llm.IdentifyHeaders(doc, pages=pages)   # heading levels from font sizes, computed once for the whole doc
                for i, pno in enumerate(pages):
                    _check_cancel(job)
                    page_md=pymupdf4llm.to_markdown(doc, pages=[pno], hdr_info=hdr, show_progress=False, **extra)
                    if not page_md.strip() and HAVE_OCR:            # no text layer: a scanned page, so read its picture
                        progress(5+int(90*i/max(len(pages),1)), f"Page {i+1}/{len(pages)}: recognizing text")
                        page_md="\n\n".join(ocr_image(_render_pdf_page(doc, pno), lang, auto_rotate=True).paragraph_texts()); scanned+=1
                    parts.append(_repair_merged_tables(page_md))
                    progress(5+int(90*(i+1)/max(len(pages),1)), f"Page {i+1}/{len(pages)}")
        except ConvertError: raise
        except Exception as e: raise ConvertError(f"PDF→Markdown failed: {e}")
        md="\n\n".join(p.strip() for p in parts if p.strip())
        if not md.strip():
            raise ConvertError("No text was found in this PDF, even with OCR." if HAVE_OCR else
                               "No text found. This PDF looks scanned (images only) and OCR isn't available on this PC.")
        if want_images:
            # make image links relative so the .md can be moved together with its _images folder
            md=md.replace(str(img_dir)+os.sep, img_dir.name+"/").replace(img_dir.as_posix()+"/", img_dir.name+"/")
            try:
                if not any(img_dir.iterdir()): img_dir.rmdir()
            except Exception: pass
        dst.write_text(md.rstrip()+"\n", encoding="utf-8")
        progress(100, f"Saved {dst.name}" + (f" ({scanned} scanned page(s) read with OCR)" if scanned else "")); return str(dst)
register(PDFToMarkdown())

def _promote_first_row_to_header(html: str) -> str:
    """mammoth emits tables with plain <td> cells only, which Markdown converters render with an EMPTY header row.
    Word tables normally start with their header, so make the first row <th>."""
    def fix(tm):
        t = tm.group(0)
        if "<th" in t: return t
        return re.sub(r"<tr>(.*?)</tr>",
                      lambda r: "<tr>" + r.group(1).replace("<td", "<th").replace("</td>", "</th>") + "</tr>",
                      t, count=1, flags=re.S)
    return re.sub(r"<table.*?</table>", fix, html, flags=re.S)

def _docx_to_html(src: Path, img_dir: Optional[Path], rel_prefix: str, page_breaks: bool = False) -> str:
    """Word -> clean HTML via mammoth. Embedded images are written into img_dir and referenced as rel_prefix/imageN.ext.
    page_breaks=True keeps explicit Word page breaks as <hr class="pagebreak"> (mammoth drops them otherwise)."""
    if mammoth is None: raise ConvertError("Word support needs mammoth. pip install mammoth")
    exts={"image/png":"png","image/jpeg":"jpg","image/gif":"gif","image/bmp":"bmp","image/svg+xml":"svg","image/webp":"webp"}
    n=[0]
    def handle(image):
        ext=exts.get(image.content_type)
        if img_dir is None or ext is None: return {"src": ""}     # e.g. EMF/WMF vector clipart: skip
        n[0]+=1; name=f"image{n[0]}.{ext}"
        img_dir.mkdir(parents=True, exist_ok=True)
        with image.open() as f: (img_dir/name).write_bytes(f.read())
        return {"src": f"{rel_prefix}/{name}" if rel_prefix else name, "alt": image.alt_text or ""}
    try:
        with open(src, "rb") as f:
            res=mammoth.convert_to_html(f, convert_image=mammoth.images.img_element(handle),
                                        style_map=("br[type='page'] => hr.pagebreak" if page_breaks else None))
    except Exception as e:
        raise ConvertError(f"Couldn't read this Word file (is it a real .docx?): {e}")
    return _promote_first_row_to_header(re.sub(r'<img[^>]*src=""[^>]*/?>', "", res.value))

class WordToMarkdown(Converter):
    accepts=("docx",); produces=("md",)
    def convert(self, job:"Job", progress):
        if _markdownify is None: raise ConvertError("Word→Markdown needs markdownify. pip install markdownify")
        src=Path(job.source_path); dst=Path(job.output_dir)/(src.stem+".md"); img_dir=Path(job.output_dir)/(src.stem+"_images")
        progress(10, "Reading Word file"); html=_docx_to_html(src, img_dir, img_dir.name); _check_cancel(job)
        progress(60, "Converting to Markdown")
        md=_markdownify.markdownify(html, heading_style="ATX", bullets="-")
        md=re.sub(r"\n{3,}", "\n\n", md).strip()+"\n"
        dst.write_text(md, encoding="utf-8")
        progress(100, f"Saved {dst.name}"); return str(dst)
register(WordToMarkdown())

def _find_word() -> Optional[str]:
    """Path to WINWORD.EXE if Microsoft Word is installed (Windows only). Set ANY2ANY_NO_WORD=1 to ignore Word and
    always use the built-in renderer."""
    if os.name != "nt" or os.environ.get("ANY2ANY_NO_WORD") == "1": return None
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\WINWORD.EXE") as k:
                    return winreg.QueryValue(k, None) or "WINWORD.EXE"
            except OSError:
                continue
    except Exception:
        pass
    return None

# Drives the user's own Word so the PDF matches what Word would print. Only quits Word if we started it,
# so a Word window the user already has open isn't closed under them.
_WORD_TO_PDF_PS = r"""
$ErrorActionPreference = 'Stop'
$word = New-Object -ComObject Word.Application
$wasOpen = $word.Documents.Count
if ($wasOpen -eq 0) { $word.Visible = $false }
$word.DisplayAlerts = 0
try {
    $doc = $word.Documents.Open($env:A2A_IN, $false, $true)
    try { $doc.ExportAsFixedFormat($env:A2A_OUT, 17) } finally { $doc.Close($false) }
} finally {
    if ($wasOpen -eq 0) { $word.Quit() }
}
"""
def _docx_to_pdf_word(src: Path, dst: Path, job: "Job", progress):
    env=dict(os.environ, A2A_IN=str(src.resolve()), A2A_OUT=str(dst.resolve()))
    proc=_aa_popen(["powershell","-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass","-Command",_WORD_TO_PDF_PS],
                   env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    t0=time.time()
    while proc.poll() is None:
        if job.cancel_flag: proc.terminate(); raise ConvertError("Cancelled")
        if time.time()-t0 > 240: proc.terminate(); raise ConvertError("Word took too long (over 4 minutes).")
        time.sleep(0.4)
    err=(proc.stderr.read() or "").strip()
    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        raise ConvertError(f"Word could not convert this file: {err[:300] or 'no output produced'}")

_DOC_CSS = """
body { font-family: sans-serif; font-size: 11pt; }
h1 { font-size: 20pt; } h2 { font-size: 16pt; } h3 { font-size: 13pt; } h4, h5, h6 { font-size: 11pt; }
table { border-collapse: collapse; } td, th { border: 1px solid #888; padding: 3px 5px; }
img { max-width: 100%; }
hr.pagebreak { page-break-after: always; border: 0; height: 0; margin: 0; }
"""
def _docx_to_pdf_builtin(src: Path, dst: Path, job: "Job", progress):
    """No Word needed: Word -> HTML (mammoth) -> PDF (PyMuPDF Story). Good for text, headings, lists, tables, images;
    page layout/fonts won't match Word exactly."""
    if pymupdf is None: raise ConvertError("PyMuPDF not installed. pip install pymupdf")
    import tempfile
    with tempfile.TemporaryDirectory(prefix="a2a_doc_") as tmp:
        html=_docx_to_html(src, Path(tmp), "", page_breaks=True)
        story=pymupdf.Story(html=html, user_css=_DOC_CSS, archive=pymupdf.Archive(tmp))
        writer=pymupdf.DocumentWriter(str(dst))
        page=pymupdf.paper_rect("a4"); where=page+(54, 54, -54, -54)
        more, n = 1, 0
        while more and n < 3000:
            _check_cancel(job)
            dev=writer.begin_page(page); more,_=story.place(where); story.draw(dev); writer.end_page(); n+=1
            progress(min(90, 10+n*4), f"Rendering page {n}")
        writer.close()

class WordToPDF(Converter):
    # Word can also open the older/other formats; the built-in renderer only does .docx
    accepts=("docx",) + (("doc","rtf","odt") if _find_word() else ())
    produces=("pdf",)
    def convert(self, job:"Job", progress):
        src=Path(job.source_path); dst=Path(job.output_dir)/(src.stem+".pdf")
        engines=[]
        if _find_word(): engines.append(("Microsoft Word", _docx_to_pdf_word))
        if src.suffix.lower()==".docx": engines.append(("built-in renderer (basic layout)", _docx_to_pdf_builtin))
        if not engines: raise ConvertError(f"Converting .{src.suffix.lstrip('.')} files to PDF needs Microsoft Word installed.")
        last: Optional[Exception]=None
        for name, fn in engines:
            _check_cancel(job)
            try: dst.unlink()
            except FileNotFoundError: pass
            except Exception: pass
            progress(8, f"Converting with {name}")
            try:
                fn(src, dst, job, progress)
                if dst.exists() and dst.stat().st_size > 0:
                    progress(100, f"Saved {dst.name}"); return str(dst)
                last=ConvertError(f"{name} produced no output")
            except ConvertError as e:
                if str(e)=="Cancelled": raise
                LOG.warning("Word->PDF via %s failed: %s", name, e); last=e
            except Exception as e:
                LOG.warning("Word->PDF via %s failed: %s", name, e); last=ConvertError(f"{name}: {e}")
        raise ConvertError(f"Word→PDF failed: {last}")
register(WordToPDF())

class SubtitlesExtract(Converter):
    accepts=("mp4","mkv","mov","avi","webm"); produces=("srt",)
    def convert(self, job:"Job", progress):
        ff=ensure_ffmpeg(); src=Path(job.source_path); dst=Path(job.output_dir)/(src.stem+".srt")
        probe=_sp_run([ensure_ffprobe(),"-v","error","-select_streams","s","-show_entries","stream=codec_name","-of","csv=p=0",str(src)],
                      capture_output=True, text=True, timeout=30).stdout
        codecs=[c.strip().lower() for c in probe.splitlines() if c.strip()]
        if not codecs: raise ConvertError("This video has no subtitle track to extract.")
        image_based={"hdmv_pgs_subtitle","dvd_subtitle","dvb_subtitle","xsub","dvb_teletext"}
        text_idx=next((n for n,c in enumerate(codecs) if c not in image_based), None)
        if text_idx is None:
            raise ConvertError("The subtitles in this file are pictures (Blu-ray/DVD style), not text, so they can't be turned into an .srt without OCR.")
        # convert (not stream-copy): MP4's mov_text, ASS and WebVTT can't be copied into .srt as-is
        run_proc([ff,"-y","-i",str(src),"-map",f"0:s:{text_idx}","-c:s","srt",str(dst)],progress,job)
        progress(100,f"Saved {dst.name}"); return str(dst)
register(SubtitlesExtract())

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

# -------- yt-dlp self-update --------
def _ejs_version() -> str:
    try:
        from yt_dlp_ejs._version import __version__ as v
        return v
    except Exception:
        return "0"

def _pypi_latest_wheel(pkg: str) -> tuple:
    """(version, wheel_url, sha256) of the newest pure-Python wheel of `pkg` on PyPI."""
    import urllib.request
    req = urllib.request.Request(f"https://pypi.org/pypi/{pkg}/json", headers={"User-Agent": f"Any2Any/{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    whl = next((f for f in data["urls"] if f["filename"].endswith("-py3-none-any.whl")), None)
    if not whl: raise ConvertError(f"PyPI has no wheel for {pkg}.")
    return data["info"]["version"], whl["url"], whl["digests"]["sha256"]

def install_ytdlp_update(dest: Optional[Path] = None, force: bool = False) -> tuple:
    """Download the newest yt-dlp and yt-dlp-ejs from PyPI, verify their checksums, and unpack them into `dest`
    (default: the user's profile). Returns (old_version, new_version, changed). Nothing is touched unless every
    download verified; the swap is rolled back if it fails part-way."""
    import urllib.request, hashlib, zipfile, tempfile
    dest = dest or _YTDLP_DIR
    latest = {mod: _pypi_latest_wheel(pkg) for pkg, mod in (("yt-dlp", "yt_dlp"), ("yt-dlp-ejs", "yt_dlp_ejs"))}
    cur_ytdlp, cur_ejs = YTDLP_VERSION, _ejs_version()
    if not force and _vkey(latest["yt_dlp"][0]) <= _vkey(cur_ytdlp) and _vkey(latest["yt_dlp_ejs"][0]) <= _vkey(cur_ejs):
        return cur_ytdlp, cur_ytdlp, False
    work = Path(tempfile.mkdtemp(prefix="a2a_ytdlp_"))
    try:
        stage = work / "stage"; stage.mkdir()
        for mod, (ver, url, sha) in latest.items():
            from urllib.parse import urlparse as _up
            if _up(url).scheme != "https" or _up(url).hostname != "files.pythonhosted.org":
                raise ConvertError("The update pointed at an unexpected address, so it was refused. Nothing was changed.")
            whl = work / f"{mod}.whl"
            urllib.request.urlretrieve(url, str(whl))
            if hashlib.sha256(whl.read_bytes()).hexdigest().lower() != sha.lower():
                raise ConvertError("The download was corrupted (checksum mismatch). Nothing was changed.")
            with zipfile.ZipFile(whl) as z:
                for name in z.namelist():
                    if name.split("/")[0] != mod: continue          # only the package itself, not dist-info
                    target = (stage / name).resolve()
                    if stage.resolve() not in target.parents and target != stage.resolve():
                        raise ConvertError("The update contained an unsafe path. Nothing was changed.")
                    if name.endswith("/"): target.mkdir(parents=True, exist_ok=True)
                    else: target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(z.read(name))
            if _pkg_version_on_disk(stage, mod) is None:
                raise ConvertError(f"The downloaded {mod} looks wrong. Nothing was changed.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        old = dest.with_name(dest.name + ".old")
        shutil.rmtree(old, ignore_errors=True)
        had = dest.exists()
        if had: dest.rename(old)
        try:
            shutil.move(str(stage), str(dest))
        except Exception:
            if had and old.exists() and not dest.exists(): old.rename(dest)      # roll back
            raise
        shutil.rmtree(old, ignore_errors=True)
        return cur_ytdlp, latest["yt_dlp"][0], True
    finally:
        shutil.rmtree(work, ignore_errors=True)

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


# ---------- URL input handling (YouTube, Shorts, X/Twitter, Reddit, TikTok, Instagram, ...) ----------
# yt-dlp does the real work; this section only cleans what people paste so it reaches the right extractor.

# "Embed fixer" mirror hosts that get pasted around (especially from Discord) -> the real site.
_URL_HOST_REWRITES = {
    "fxtwitter.com": "x.com", "vxtwitter.com": "x.com", "fixupx.com": "x.com",
    "fixvx.com": "x.com", "twittpr.com": "x.com",
    "vxreddit.com": "reddit.com", "rxddit.com": "reddit.com",
    "ddinstagram.com": "instagram.com", "d.ddinstagram.com": "instagram.com",
    "instagramez.com": "instagram.com", "eeinstagram.com": "instagram.com",
    "vxtiktok.com": "tiktok.com", "tnktok.com": "tiktok.com",
}
_BARE_URL_RE = re.compile(r"^(?:[\w-]+\.)+[a-z]{2,}(?::\d+)?/\S+$", re.I)

def extract_urls(text: str) -> List[str]:
    """All http(s) links in pasted text (one or many). Also accepts scheme-less 'x.com/...' links."""
    text = text or ""
    urls = re.findall(r"https?://[^\s<>\"'\)\]]+", text, flags=re.I)
    if not urls:
        for tok in text.split():
            tok = tok.strip("()<>[]{}\"'“”‘’,;")
            if _BARE_URL_RE.match(tok):
                urls.append("https://" + tok)
    out, seen = [], set()
    for u in urls:
        u = u.rstrip(".,;:!?")
        if u and u not in seen:
            seen.add(u); out.append(u)
    return out

def _resolve_redirect(url: str, timeout: float = 10.0) -> str:
    """Follow redirects once to find where a short/share link really points. Returns url unchanged on failure."""
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0 Safari/537.36"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.geturl() or url
    except Exception as e:
        LOG.warning("redirect resolve failed for %s: %s", url, e)
        return url

def normalize_media_url(url: str, allow_playlist: bool = False) -> tuple[str, Optional[str]]:
    """Clean a pasted link. Returns (url, note) where note is a user-facing hint or None."""
    note = None
    u = urlparse(url.strip())
    host = (u.netloc or "").lower()
    bare = host[4:] if host.startswith("www.") else host

    if bare in _URL_HOST_REWRITES:
        u = u._replace(netloc=_URL_HOST_REWRITES[bare])
        host, bare, note = u.netloc.lower(), _URL_HOST_REWRITES[bare], f"Using the original {_URL_HOST_REWRITES[bare]} link."
        url = urlunparse(u)

    # Reddit short/share links (redd.it/<id>, reddit.com/r/<sub>/s/<id>) need one hop to the real post.
    if (bare == "redd.it" and re.fullmatch(r"/[A-Za-z0-9]+/?", u.path or "")) or \
       (bare.endswith("reddit.com") and re.search(r"/s/[A-Za-z0-9]+/?$", u.path or "")):
        resolved = _resolve_redirect(url)
        if "/comments/" in resolved:
            url, note = resolved, "Resolved Reddit share link."
    # v.redd.it/<id> is just the video host; hand yt-dlp its HLS manifest directly.
    elif bare == "v.redd.it" and re.fullmatch(r"/[A-Za-z0-9]+/?", u.path or ""):
        url, note = f"https://v.redd.it/{u.path.strip('/')}/HLSPlaylist.m3u8", "Using the v.redd.it video stream."

    # YouTube Mix/Radio (list=RD...) can't be enumerated: keep just the one video unless playlists were requested.
    if bare.endswith("youtube.com"):
        qs = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
        if (qs.get("list") or "").upper().startswith("RD"):
            if allow_playlist:
                raise ConvertError("YouTube Mix/Radio playlists are unviewable. Save the Mix to your own playlist or remove list=RD...")
            qs.pop("list", None)
            url = urlunparse((u.scheme, u.netloc, u.path, u.params, urlencode(qs), u.fragment))
            note = "Detected YouTube Mix/Radio; downloading just this video."
    return url, note

def _friendly_ytdlp_error(e: Exception) -> str:
    """Turn yt-dlp's raw failure into something actionable."""
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).strip()
    low = msg.lower()
    first = next((ln for ln in msg.splitlines() if ln.strip()), msg)
    if "unsupported url" in low:
        hint = "No downloadable video was found at this link (text/image-only post, or the site isn't supported)."
    elif any(k in low for k in ("sign in", "log in", "login", "cookies", "not a bot", "authenticat", "age-restricted", "age restricted", "nsfw", "private", "members-only")):
        hint = "This link needs a signed-in session. In Options set 'URL cookies' to the browser you're logged in with (Firefox is the most reliable on Windows), then retry."
    elif "429" in low or "too many requests" in low:
        hint = "The site is rate-limiting requests. Wait a few minutes and retry."
    elif any(k in low for k in ("unavailable", "removed", "deleted", "404", "not found", "geo", "country")):
        hint = "The video is unavailable (deleted, removed, or region-locked)."
    else:
        hint = ""
    return "Download failed: " + first + (("\n\n" + hint) if hint else "")

class _YDLLogger:
    """Route yt-dlp's own messages into app.log instead of the (nonexistent) console."""
    def debug(self, m): LOG.debug("yt-dlp: %s", m)
    def info(self, m): LOG.info("yt-dlp: %s", m)
    def warning(self, m): LOG.warning("yt-dlp: %s", m)
    def error(self, m): LOG.error("yt-dlp: %s", m)

def _height_cap_for_target(target_mb: int, duration: float) -> int:
    """Pick a download resolution that can plausibly fit the size cap, so a 10s clip isn't forced to 480p
    and a 20-minute video isn't downloaded at 1080p just to be crushed back down."""
    if not target_mb or duration <= 0:
        return 720
    kbps = (target_mb * 8000 * 0.93) / duration - 128   # video kbps left after ~128k audio
    for cap, need in ((1080, 3000), (720, 1500), (480, 700), (360, 350)):
        if kbps >= need:
            return cap
    return 240
# -------- centralized finalize --------
def finalize_outputs(outputs, out_ext: str, job: "Job", progress):
    """
    Choose a single canonical final file for a URL job and perform at most one
    post-process based on out_ext. Removes intermediates/duplicates.
    """
    from pathlib import Path as _P
    out_ext = (out_ext or "").lower()

    def _mp4_opts(path):
        # Anything that isn't H.264 (TikTok HEVC, VP9/AV1 from YouTube) won't preview inline on Discord/browsers: re-encode it.
        opts = dict(job.options or {})
        codec = probe_video_codec(path)
        if codec and codec != "h264":
            opts["reencode"] = "1"
            progress(10, f"Source is {codec}; converting to H.264 for compatibility")
        return opts
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
            sub = Job(source_path=str(stem_src), target_ext='mp4', options=_mp4_opts(stem_src), output_dir=job.output_dir)
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
            sub = Job(source_path=str(dst), target_ext='mp4', options=_mp4_opts(dst), output_dir=job.output_dir)
            final = Path(VideoToMP4().convert(sub, progress))
            # VideoToMP4 writes <name>_out.mp4 when input and output share a path; put the result back
            # under the clean name instead of leaving a stray "_out" suffix on every URL download.
            if final != dst:
                try:
                    final.replace(dst); final = dst
                except Exception as e:
                    LOG.warning("finalize_outputs: could not rename %s -> %s: %s", final, dst, e)

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
URL_OUTPUTS = ("mp3","mp4","m4a","opus","gif","webp-anim","thumbnail")

class URLToMedia(Converter):
    accepts=("url",); produces=URL_OUTPUTS
    def convert(self, job:"Job", progress):
        if ytdlp is None: raise ConvertError("yt-dlp not installed. pip install yt-dlp")
        if not job.source_url: raise ConvertError("No URL provided")
        out_ext=job.target_ext.lower(); outdir=Path(job.output_dir); outdir.mkdir(parents=True, exist_ok=True); o=job.options or {}
        allow_pl = (o.get('playlist','0')=='1')
        new_url, note = normalize_media_url(job.source_url, allow_pl)
        if note: progress(5, note)
        job.source_url = new_url
        target=get_target_mb(o)
        ydl_opts = {
            # unique per video (title + id): playlists, multi-video posts and re-downloads can't collide
            'outtmpl': str(outdir / '%(title|video).100B_%(id)s.%(ext)s'),
            'noplaylist': not allow_pl,
            'progress_hooks': [lambda d: _ydl_hook(d, progress, job)],
            'logger': _YDLLogger(), 'quiet': True, 'noprogress': True, 'no_color': True,
            'windowsfilenames': True, 'continuedl': True, 'overwrites': False,
            'concurrent_fragment_downloads': 16, 'retries': 5, 'fragment_retries': 20,
            'merge_output_format': 'mp4',
        }
        # use the bundled ffmpeg (yt-dlp otherwise only looks on PATH, which a clean install won't have)
        try: ydl_opts['ffmpeg_location'] = str(Path(ensure_ffmpeg()).parent)
        except ConvertError: pass
        # YouTube needs a JS runtime to solve its challenges; use one the PC has, else the bundled QuickJS
        js = _js_runtimes()
        if js: ydl_opts['js_runtimes'] = js
        browser = o.get('cookies_browser','')
        cfile = o.get('cookies_file','')
        if cfile:
            if not Path(cfile).is_file(): raise ConvertError(f"Cookies file not found: {cfile}")
            ydl_opts['cookiefile'] = cfile          # an exported cookies.txt wins over reading the browser
        elif browser and browser != 'none':
            ydl_opts['cookiesfrombrowser'] = (browser,)
        if shutil.which("aria2c"):
            ydl_opts.update({'external_downloader': 'aria2c', 'external_downloader_args': ['-x16','-s16','-k1M']})

        if out_ext=="thumbnail":
            ydl_opts2=dict(ydl_opts); ydl_opts2.update({'skip_download': True, 'writethumbnail': True})
            try:
                with ytdlp.YoutubeDL(ydl_opts2) as y:
                    info=y.extract_info(job.source_url, download=True)
                    if info and info.get('entries'): info=next((e for e in info['entries'] if e), info)
                    thumb=Path(y.prepare_filename(info)).with_suffix(".jpg")
                    if not thumb.exists():
                        for ext in (".webp",".png",".jpeg",".jpg"):
                            t=thumb.with_suffix(ext)
                            if t.exists(): thumb=t; break
            except Exception as e:
                if job.cancel_flag: raise ConvertError("Cancelled")
                raise ConvertError(_friendly_ytdlp_error(e))
            if not thumb.exists(): raise ConvertError("No thumbnail found for this link.")
            progress(100,"Saved thumbnail"); return str(thumb)

        def _probe_duration() -> float:
            try:
                with ytdlp.YoutubeDL({**ydl_opts, 'skip_download': True, 'noplaylist': True}) as y:
                    info=y.extract_info(job.source_url, download=False)
                return float((info or {}).get('duration') or 0)
            except Exception:
                return 0.0

        # build formats / postprocessors per output type
        dur = _probe_duration() if (target and out_ext in ("mp3","mp4")) else 0.0
        if out_ext=="mp3":
            ydl_opts['format'] = "bestaudio/best"
            pq=None
            if target and dur>0:
                head=int(o.get('headroom_percent','0'))
                max_bits=int((target*CAP_MB_BYTES)*8*(1-head/100.0)); bps=max(64000, min(320000, max_bits//max(int(dur),1))); pq=str(bps//1000)
            ydl_opts['postprocessors']=[{'key':'FFmpegExtractAudio','preferredcodec':'mp3','preferredquality': pq or o.get('bitrate','192k').replace('k','')}]
        elif out_ext in ("m4a","opus"):
            ydl_opts['format'] = "bestaudio/best"
            ydl_opts['postprocessors']=[{'key':'FFmpegExtractAudio','preferredcodec': out_ext}]
        elif out_ext=="mp4":
            # prefer H.264/AAC (plays everywhere, previews inline on Discord); fall back to anything available
            cap = f"[height<={_height_cap_for_target(target, dur)}]" if target else ""
            ydl_opts['format'] = (f"bv*{cap}[vcodec~='^(avc|h264)']+ba[acodec~='^(mp4a|aac)']"
                                  f"/bv*{cap}[ext=mp4]+ba[ext=m4a]/b{cap}[ext=mp4]/bv*{cap}+ba/b{cap}/b")
        elif out_ext in ("gif","webp-anim"):
            # these get shrunk to ~480p anyway; don't pull 4K and audio just to throw it away
            ydl_opts['format'] = "bv*[height<=720]/b[height<=720]/bv*/b"

        def _entry_files(y, e) -> List[str]:
            files=[rd.get('filepath') for rd in (e.get('requested_downloads') or []) if rd.get('filepath')]
            if not files:
                try: files=[y.prepare_filename(e)]
                except Exception: files=[]
            return files
        def _walk(y, res) -> List[List[str]]:
            if not res: return []
            if res.get('entries') is not None:
                out: List[List[str]] = []
                for e in res['entries']: out.extend(_walk(y, e))
                return out
            return [_entry_files(y, res)]
        def _download_with(opts_local) -> List[List[str]]:
            # one list of files per video, taken from what yt-dlp says it actually wrote
            # (prepare_filename() names the pre-conversion file, which is gone after mp3/m4a extraction)
            with ytdlp.YoutubeDL(opts_local) as y:
                return [g for g in _walk(y, y.extract_info(job.source_url, download=True)) if g]

        try:
            groups = _download_with(ydl_opts)
        except Exception as e:
            if job.cancel_flag: raise ConvertError("Cancelled")
            msg = str(e).lower()
            if any(k in msg for k in ("impersonate","cloudflare","403")):
                try:
                    from yt_dlp.networking.impersonate import ImpersonateTarget
                    ydl_opts['impersonate'] = ImpersonateTarget('chrome')
                    groups = _download_with(ydl_opts)
                except Exception as e2:
                    if job.cancel_flag: raise ConvertError("Cancelled")
                    raise ConvertError(_friendly_ytdlp_error(e2))
            else:
                raise ConvertError(_friendly_ytdlp_error(e))

        # finalize each video separately (a playlist / multi-video post must keep every item)
        results: List[str] = []
        for files in groups:
            if job.cancel_flag: raise ConvertError("Cancelled")
            r = finalize_outputs(files, out_ext, job, progress)
            if r.startswith("[MULTI]"): results.extend(json.loads(r[len("[MULTI]"):]))
            else: results.append(r)
        if not results: raise ConvertError("No outputs produced by downloader.")
        return "[MULTI]"+json.dumps(results) if len(results)>1 else results[0]
register(URLToMedia())

# ---------- watch ----------
def _wait_until_stable(p: Path, timeout: float = 600.0, quiet: float = 1.5) -> bool:
    """True once the file has stopped growing and can be opened (i.e. the copy/download that created it is done)."""
    deadline=time.time()+timeout; last=-1; stable_since=None
    while time.time()<deadline:
        try:
            size=p.stat().st_size
            if size>0 and size==last:
                stable_since = stable_since or time.time()
                if time.time()-stable_since>=quiet:
                    with open(p,"rb"): return True       # still locked by the writer -> PermissionError -> keep waiting
            else:
                stable_since=None
            last=size
        except FileNotFoundError:
            return False                                  # deleted / renamed away while we waited
        except OSError:
            stable_since=None
        time.sleep(0.5)
    return False

class WatchHandler(FileSystemEventHandler):
    """Runs on watchdog's thread. `sink` must be thread-safe (the app passes a Qt signal's emit), because Qt widgets
    may only be touched from the GUI thread. Files are only reported once they've finished being written."""
    def __init__(self, sink, patterns: Tuple[str,...]):
        self.sink, self.patterns = sink, patterns
        self._pending=set(); self._lock=threading.Lock()
    def _consider(self, path: str):
        p=Path(path)
        if p.suffix.lower().lstrip('.') not in self.patterns: return
        with self._lock:
            if str(p) in self._pending: return
            self._pending.add(str(p))
        def work():
            try:
                if _wait_until_stable(p): self.sink(str(p))
            finally:
                with self._lock: self._pending.discard(str(p))
        threading.Thread(target=work, daemon=True).start()
    def on_created(self, e):
        if not e.is_directory: self._consider(e.src_path)
    def on_moved(self, e):      # e.g. browsers write name.crdownload, then rename it into place
        if not e.is_directory: self._consider(e.dest_path)

# ---------- UI ----------
class MainWindow(QMainWindow):
    watch_found = Signal(str)   # emitted from the watcher thread; delivered on the GUI thread
    ytdlp_update_done = Signal(str, bool)   # (message, ok) from the update thread

    def __init__(self):
        super().__init__()
        self._own_outputs: set = set()      # files this app wrote; the watcher must not feed them back in
        self.watch_found.connect(self._on_watch_found)
        self.ytdlp_update_done.connect(self._on_ytdlp_update_done)
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        try:
            self.resize(1200, 820)
        except Exception:
            pass
        self.setMinimumSize(980, 700)

        self.output_dir = DEFAULT_OUTPUT_DIR
        # small portable settings file (not the registry): remembers the output folder and the open-when-done toggle
        _sp = Path(os.getenv("APPDATA", str(Path.home() / "AppData/Roaming"))) / "Any2Any"; _sp.mkdir(parents=True, exist_ok=True)
        self._settings = QSettings(str(_sp / "settings.ini"), QSettings.Format.IniFormat)
        _saved = str(self._settings.value("output_dir", "") or "")
        if _saved and Path(_saved).is_dir(): self.output_dir = _saved
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
        self.act_open_when_done = QAction("Open output folder when finished", self); self.act_open_when_done.setCheckable(True)
        self.act_open_when_done.setChecked(str(self._settings.value("open_when_done", "true")).lower() != "false")
        self.act_open_when_done.toggled.connect(lambda on: self._settings.setValue("open_when_done", "true" if on else "false"))
        m_tools.addAction(self.act_open_when_done)
        a_ytup = QAction("Update link downloader (yt-dlp)...", self); a_ytup.triggered.connect(self.update_ytdlp); m_tools.addAction(a_ytup)
        a_ytreset = QAction("Use built-in link downloader (remove update)...", self); a_ytreset.triggered.connect(self.reset_ytdlp); m_tools.addAction(a_ytreset)
        if HAVE_MUTAGEN:
            a_meta = QAction("Edit MP3 metadata (last result)...", self); a_meta.triggered.connect(self.edit_last_mp3_tags); m_tools.addAction(a_meta)

        m_help = menubar.addMenu("&Help")
        a_guide = QAction("Conversion guide...", self); a_guide.triggered.connect(self.show_help); m_help.addAction(a_guide)
        a_about = QAction("About", self); a_about.triggered.connect(lambda: QMessageBox.information(self, "About", f"{APP_NAME}\nVersion {APP_VERSION}\n\n{PRIVACY_NOTE}")); m_help.addAction(a_about)

        root.setMenuBar(menubar)

        # ---- top row ----
        top = QHBoxLayout()
        self.url_edit = QLineEdit(); self.url_edit.setPlaceholderText("Paste link(s): YouTube / Shorts, X (Twitter), Reddit, TikTok, Instagram, ...")
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
        self.opt_headroom = QSpinBox(); self.opt_headroom.setRange(0, 25); self.opt_headroom.setValue(3); form.addRow("Headroom %", self.opt_headroom)
        self.opt_split = QCheckBox("Split into ≤ cap parts (MP3/MP4)"); form.addRow(self.opt_split)
        self.opt_playlist = QCheckBox("URL: download playlist items"); form.addRow(self.opt_playlist)
        self.opt_cookies = QComboBox(); self.opt_cookies.addItems(["none","firefox","edge","chrome","brave"]); form.addRow("URL cookies (logged-in sites)", self.opt_cookies)
        self.opt_cookies_file = QLineEdit(); self.opt_cookies_file.setPlaceholderText("or a cookies.txt file (overrides the browser)")
        pick_ck = QPushButton("Choose..."); pick_ck.clicked.connect(lambda: self._pick_to_line(self.opt_cookies_file, "Choose cookies.txt", "Cookies (*.txt);;All files (*)"))
        ck_row = QHBoxLayout(); ck_row.addWidget(self.opt_cookies_file, 1); ck_row.addWidget(pick_ck)
        ck_wrap = QWidget(); ck_wrap.setLayout(ck_row); form.addRow("URL cookies file", ck_wrap)

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
        self.opt_md_images = QCheckBox("PDF → Markdown: also extract images"); form.addRow(self.opt_md_images)
        self.opt_ocr_lang_win = QComboBox(); self.opt_ocr_lang_win.addItem("auto (your Windows languages)", "auto")
        for _tag, _name in ocr_languages(): self.opt_ocr_lang_win.addItem(f"{_name} [{_tag}]", _tag)
        form.addRow("Text recognition language", self.opt_ocr_lang_win)
        self.opt_pdf_searchable = QCheckBox("Images → PDF: make the text searchable (OCR)"); form.addRow(self.opt_pdf_searchable)
        self.opt_pdf_merge = QCheckBox("Images → PDF: combine all the images you add into ONE PDF"); form.addRow(self.opt_pdf_merge)

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
        self._restore_settings()           # last output type, size-cap choices, ... (then start remembering changes)

    # ---- remembered choices ----
    def _restore_settings(self):
        st = self._settings
        def get(k, d=""): return str(st.value(k, d) if st.value(k, d) is not None else d)
        if get("format") in SUPPORTED_OUTPUTS: self.format_box.setCurrentText(get("format"))
        self.opt_size_target.setChecked(get("size_targeting") == "1")
        if get("discord_preset") in DISCORD_PRESETS: self.opt_discord.setCurrentText(get("discord_preset"))
        if get("custom_cap").isdigit(): self.opt_cap_mb.setValue(int(get("custom_cap")))
        if get("headroom").isdigit(): self.opt_headroom.setValue(min(25, int(get("headroom"))))
        for key, combo in (("encoder", self.opt_encoder), ("cookies_browser", self.opt_cookies)):
            i = combo.findText(get(key)); 
            if i >= 0: combo.setCurrentIndex(i)
        i = self.opt_ocr_lang_win.findData(get("ocr_lang"))
        if i >= 0: self.opt_ocr_lang_win.setCurrentIndex(i)
        self._sync_visibility()
        for sig in (self.format_box.currentTextChanged, self.opt_size_target.toggled, self.opt_discord.currentTextChanged,
                    self.opt_cap_mb.valueChanged, self.opt_headroom.valueChanged, self.opt_encoder.currentTextChanged,
                    self.opt_cookies.currentTextChanged, self.opt_ocr_lang_win.currentIndexChanged):
            sig.connect(self._save_settings)

    def _save_settings(self, *_):
        st = self._settings; fmt = self.format_box.currentText()
        if fmt in SUPPORTED_OUTPUTS: st.setValue("format", fmt)        # headers / the placeholder aren't remembered
        st.setValue("size_targeting", "1" if self.opt_size_target.isChecked() else "0")
        st.setValue("discord_preset", self.opt_discord.currentText()); st.setValue("custom_cap", self.opt_cap_mb.value())
        st.setValue("headroom", self.opt_headroom.value()); st.setValue("encoder", self.opt_encoder.currentText())
        st.setValue("cookies_browser", self.opt_cookies.currentText()); st.setValue("ocr_lang", self.opt_ocr_lang_win.currentData() or "auto")

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
                    if fmt in FORMAT_HELP:
                        self.format_box.setItemData(self.format_box.count() - 1, FORMAT_HELP[fmt], Qt.ToolTipRole)

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
            "• Video → MP4 (seekable), MP3/M4A/Opus, GIF, WebP-anim\n"
            "• Audio → MP3/M4A/Opus\n"
            "• Images → PNG/JPG/WebP/AVIF, Images → PDF\n"
            "• Pictures → text (OCR): screenshots, photos of pages, scans → .txt (Windows' built-in text recognition, offline)\n"
            "• PDF → Word (.docx), PDF → Markdown, PDF → text, PDF → images (PNG/JPG); scanned PDFs are read with OCR\n"
            "• Make a scanned PDF searchable: choose pdf with a PDF as input (Images → PDF can add searchable text too)\n"
            "• Word (.docx) → PDF, Word → Markdown\n"
            "• PDF → Excel/CSV (tables)\n"
            "• CSV ↔ JSON, CSV ↔ XLSX\n"
            "• Subtitles: extract SRT, burn-in\n"
            "• Links (YouTube/Shorts, X/Twitter, Reddit, TikTok, Instagram, Twitch, ...): MP4/MP3/M4A/Opus/GIF/WebP/thumbnail\n"
            "  Paste one or several links. Reddit, Instagram and some others require a signed-in browser: pick it under 'URL cookies'.\n\n"
            "Notes:\n"
            "• Discord size targeting is optional (free cap is 20 MB since Aug 2026); headroom helps hit the cap, and files already under it are left alone.\n"
            "• Word → PDF uses Microsoft Word when installed (exact layout); otherwise a built-in renderer (text, tables, images, basic layout).\n"
            "• OCR reads printed text well (screenshots, documents, receipts); it is poor at handwriting and multi-column layouts. For other languages add a Windows OCR language (Settings > Time & language).\n"
            "• PDF -> Excel/CSV tables still need a PDF with real text (or ocrmypdf installed).\n"
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
            f"yt-dlp: {YTDLP_VERSION} ({YTDLP_SOURCE})" if ytdlp is not None else "yt-dlp: MISSING",
            ok("Pillow", Image is not None),
            ok("pillow-heif", HAVE_HEIF),
            ok("Camelot", camelot is not None) + (f"  ({_IMPORT_ERR['camelot']})" if camelot is None and "camelot" in _IMPORT_ERR else ""),
            ok("pandas", pd is not None),
            ok("PyMuPDF", fitz is not None),
            ("OCR (Windows): OK, languages: " + (", ".join(t for t, _ in ocr_languages()) or "NONE installed")) if HAVE_OCR
                else "OCR (Windows): MISSING" + (f"  ({_IMPORT_ERR['ocr']})" if "ocr" in _IMPORT_ERR else ""),
            ok("pdf2docx (PDF→Word)", _pdf2docx is not None),
            ok("pymupdf4llm (PDF→Markdown)", pymupdf4llm is not None),
            ok("mammoth + markdownify (Word→Markdown)", mammoth is not None and _markdownify is not None),
            "Word→PDF engine: " + ("Microsoft Word" if _find_word() else "built-in renderer (install Word for exact layout)"),
            "JS runtime for YouTube: " + (", ".join(_js_runtimes()) + (" (bundled)" if "quickjs" in _js_runtimes() and "path" in _js_runtimes()["quickjs"] else "") or "NONE (YouTube may be limited; install Deno or Node.js)"),
            ok("watchdog", HAVE_WATCHDOG),
            ok("mutagen", HAVE_MUTAGEN),
            ok("aria2c (downloader)", have_binary("aria2c")),
        ]
        if psutil:
            msgs.append(f"Memory: {psutil.virtual_memory().percent}% used")
        QMessageBox.information(self, "Dependency Check", "\n".join(msgs))

    def update_ytdlp(self):
        ans = QMessageBox.question(self, "Update link downloader",
            f"Link downloads use yt-dlp {YTDLP_VERSION} ({YTDLP_SOURCE}).\n\n"
            "Sites change often and yt-dlp releases fixes within days. Download the latest yt-dlp and its YouTube helper "
            "from PyPI (about 5 MB)? It takes effect the next time you start Any2Any.",
            QMessageBox.Yes | QMessageBox.No)
        if ans != QMessageBox.Yes: return
        self.status_lbl.setText("Checking for a newer yt-dlp...")
        def work():
            try:
                old, new, changed = install_ytdlp_update()
                self.ytdlp_update_done.emit(f"Updated yt-dlp {old} → {new}.\n\nRestart Any2Any to use it." if changed
                                            else f"yt-dlp is already up to date ({old}).", True)
            except Exception as e:
                LOG.warning("yt-dlp update failed: %s", e)
                self.ytdlp_update_done.emit(f"Update failed: {e}", False)
        threading.Thread(target=work, daemon=True).start()

    def _on_ytdlp_update_done(self, msg: str, ok: bool):
        self.status_lbl.setText("Ready")
        (QMessageBox.information if ok else QMessageBox.warning)(self, "Update link downloader", msg)

    def reset_ytdlp(self):
        if not _YTDLP_DIR.exists():
            QMessageBox.information(self, "Link downloader", "No update is installed; the built-in yt-dlp is already in use."); return
        if QMessageBox.question(self, "Link downloader", "Remove the downloaded yt-dlp update and go back to the built-in version? (Takes effect after a restart.)",
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes: return
        shutil.rmtree(_YTDLP_DIR, ignore_errors=True)
        QMessageBox.information(self, "Link downloader", "Removed. Restart Any2Any to use the built-in yt-dlp.")

    def toggle_watch(self):
        if not HAVE_WATCHDOG:
            QMessageBox.information(self, "Watch folder", "Install watchdog: pip install watchdog")
            return
        if self.watch_observer:
            self.watch_observer.stop(); self.watch_observer.join(1.0); self.watch_observer=None
            QMessageBox.information(self,"Watch folder","Stopped watching"); return
        target = self.format_box.currentText().strip()
        if target not in SUPPORTED_OUTPUTS:
            QMessageBox.information(self, "Watch folder", "Choose an output type first; the watcher only picks up files that can become it."); return
        d = QFileDialog.getExistingDirectory(self, "Choose folder to watch")
        if not d: return
        self.start_watch(d, target)
        QMessageBox.information(self,"Watch folder",f"Watching: {d}\nNew files that can become {target} are converted once they finish copying.")

    def _on_watch_found(self, path: str):
        if os.path.normcase(os.path.abspath(path)) in self._own_outputs: return
        self.enqueue_file_path(path)

    def start_watch(self, folder: str, target: str):
        self.watch_path = folder
        # only react to files that can actually be converted to the chosen output
        pats = tuple({e for c in REGISTRY if target in c.produces for e in c.accepts})
        handler = WatchHandler(self.watch_found.emit, pats)
        self.watch_observer = Observer(); self.watch_observer.schedule(handler, folder, recursive=False); self.watch_observer.start()

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
        known = tgt in SUPPORTED_OUTPUTS
        VIDEO = {"mp4", "gif", "webp-anim"}; AUDIO = {"mp3", "m4a", "opus"}; IMAGES = {"png", "jpg", "webp", "avif"}
        def show(widget, targets):
            """Show a settings row only for the output types it applies to (all rows stay visible until a type is chosen)."""
            self._row_visible(widget, (not known) or tgt in targets)
        # size cap (Discord / custom)
        capped = VIDEO | AUDIO | IMAGES
        for w in (self.opt_size_target, self.opt_discord, self.opt_cap_mb, self.opt_headroom): show(w, capped)
        show(self.opt_split, {"mp4"} | AUDIO)
        # links
        links = {"mp3", "mp4", "m4a", "opus", "gif", "webp-anim", "thumbnail"}
        for w in (self.opt_playlist, self.opt_cookies, self.opt_cookies_file.parentWidget()): show(w, links)
        # video / audio
        for w in (self.opt_start, self.opt_end): show(w, VIDEO | AUDIO)
        for w in (self.opt_scale, self.opt_fps): show(w, {"mp4", "webp-anim"})
        self._row_visible(self.opt_bitrate, ((not known) or tgt in AUDIO) and not on)
        for w in (self.opt_loudnorm, self.opt_silence): show(w, AUDIO)
        show(self.opt_encoder, {"mp4"}); show(self.opt_threads, {"mp4"} | AUDIO)
        show(self.opt_gif_fps, {"gif", "webp-anim"}); show(self.opt_gif_scale, {"gif", "webp-anim"})
        self._row_visible(self.opt_subs_path.parentWidget(), (not known) or tgt == "mp4+burnsubs")
        # documents / OCR
        show(self.opt_pages, {"xlsx", "csv", "docx", "md", "txt", "pdf", "png", "jpg"})
        show(self.opt_flavor, {"xlsx", "csv"}); show(self.opt_ocr_langs, {"xlsx", "csv"})
        self.opt_md_images.setVisible((not known) or tgt == "md")
        show(self.opt_ocr_lang_win, {"txt", "md", "docx", "pdf"})
        self.opt_pdf_searchable.setVisible((not known) or tgt == "pdf")
        self.opt_pdf_merge.setVisible((not known) or tgt == "pdf")
        # images
        show(self.opt_quality, {"jpg"}); show(self.opt_stripmeta, IMAGES); show(self.opt_maxdim, IMAGES)

    # ---- file pickers & queue ----
    def pick_output_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Choose output folder", self.output_dir)
        if d: self.output_dir = d; self.lbl_outdir.setText(d); self._settings.setValue("output_dir", d)

    def pick_files(self):
        files,_ = QFileDialog.getOpenFileNames(self, "Choose files to convert")
        if files: self.add_files(files)

    def add_files(self, paths: List[str]):
        # GUI thread (drop / browse): reject files that can't become the chosen format up front, in one message
        target = self.format_box.currentText().strip()
        ok, bad = list(paths), []
        if target in SUPPORTED_OUTPUTS:
            ok = [p for p in paths if find_conv(Path(p).suffix.lower().lstrip("."), target)]
            bad = [p for p in paths if p not in ok]
        if bad:
            names = "\n".join("• " + Path(p).name for p in bad[:8]) + (f"\n…and {len(bad)-8} more" if len(bad) > 8 else "")
            QMessageBox.warning(self, "Can't convert these", f"These can't be converted to {target}:\n\n{names}\n\nPick an output type that applies to them (Help → Conversion guide lists what's supported).")
        jobs = 0
        images = [p for p in ok if Path(p).suffix.lower().lstrip(".") in ImagesToPDF.accepts]
        if target == "pdf" and self.opt_pdf_merge.isChecked() and len(images) >= 2:
            # combine the images (in the order added) into a single PDF; anything else in the drop is queued normally
            opts = self.collect_opts("pdf"); opts["merge_paths"] = json.dumps(images)
            self.queue.append(Job(source_path=images[0], target_ext="pdf", options=opts, output_dir=self.output_dir))
            self.queue_list.addItem(QListWidgetItem(f"{len(images)} images → one pdf"))
            ok = [p for p in ok if p not in images]; jobs += 1
            if not self.worker: self.start_next()
        for p in ok: self.enqueue_file_path(p); jobs += 1
        if jobs: self.status_lbl.setText(f"Queued {jobs} job(s)")

    def enqueue_file_path(self, p: str):
        target = self.format_box.currentText().strip()
        if target not in SUPPORTED_OUTPUTS: QMessageBox.warning(self, "Choose output type", "Please choose an output type first."); return
        opts = self.collect_opts(target)
        job = Job(source_path=p, target_ext=target, options=opts, output_dir=self.output_dir)
        self.queue.append(job); self.queue_list.addItem(QListWidgetItem(f"{Path(p).name} → {target}"))
        if not self.worker: self.start_next()

    def enqueue_from_ui(self):
        raw = self.url_edit.text().strip()
        urls = extract_urls(raw)   # one or many links; tolerates surrounding text and scheme-less x.com/... links
        target = self.format_box.currentText().strip()
        if target not in SUPPORTED_OUTPUTS: QMessageBox.warning(self, "Choose output type", "Please choose a real output type first."); return
        if urls and target not in URL_OUTPUTS:
            QMessageBox.warning(self, "Output type", f"Links can be saved as: {', '.join(URL_OUTPUTS)}.\n'{target}' only works on files."); return
        opts = self.collect_opts(target)
        for url in urls:
            job = Job(source_path=None, source_url=url, target_ext=target, options=opts, output_dir=self.output_dir)
            self.queue.append(job); self.queue_list.addItem(QListWidgetItem(f"{urlparse(url).netloc} → {target}"))
        if urls:
            self.url_edit.clear()
            if not self.worker: self.start_next()
        elif raw:
            QMessageBox.warning(self, "Invalid URL", "Couldn't find a link in that text. Paste a full link, e.g. https://www.youtube.com/watch?v=...")
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
        if self.opt_cookies.currentText()!="none": o["cookies_browser"]=self.opt_cookies.currentText()
        if self.opt_cookies_file.text().strip(): o["cookies_file"]=self.opt_cookies_file.text().strip()
        if self.opt_start.text().strip(): o["start"]=self.opt_start.text().strip()
        if self.opt_end.text().strip(): o["end"]=self.opt_end.text().strip()
        if self.opt_scale.currentText()!="original": o["scale"]=self.opt_scale.currentText()
        if self.opt_fps.currentText()!="original": o["fps"]=self.opt_fps.currentText()
        if target in ("mp3","m4a","opus") and not self.opt_size_target.isChecked(): o["bitrate"]=self.opt_bitrate.currentText()
        if self.opt_loudnorm.isChecked(): o["loudnorm"]="1"
        if self.opt_silence.isChecked(): o["silence"]="1"
        o["encoder"]=self.opt_encoder.currentText(); o["threads"]=str(self.opt_threads.value())
        if target in ("xlsx","csv"):
            if self.opt_pages.text().strip(): o["pages"]=self.opt_pages.text().strip()
            o["flavor"]=self.opt_flavor.currentText()
            if self.opt_ocr_langs.text().strip(): o["ocr_langs"]=self.opt_ocr_langs.text().strip()
        if target in ("docx","md"):
            if self.opt_pages.text().strip(): o["pages"]=self.opt_pages.text().strip()
        if target=="md" and self.opt_md_images.isChecked(): o["md_images"]="1"
        if target in ("txt","md","docx","pdf") and self.opt_ocr_lang_win.currentData() not in (None, "auto"): o["ocr_lang"]=self.opt_ocr_lang_win.currentData()
        if target=="pdf" and self.opt_pdf_searchable.isChecked(): o["ocr_pdf"]="1"
        if target in ("jpg","jpeg"): o["quality"]=str(self.opt_quality.value())
        if self.opt_stripmeta.isChecked(): o["strip_meta"]="1"
        if self.opt_maxdim.value()>0: o["max_dim"]=str(self.opt_maxdim.value())
        if target=="gif": o["gif_fps"]=str(self.opt_gif_fps.value()); o["gif_scale"]=str(self.opt_gif_scale.value())
        if target=="webp-anim": o["fps"]=o.get("fps","15"); o["scale"]=o.get("scale",str(self.opt_gif_scale.value()))
        if target=="mp4+burnsubs" and self.opt_subs_path.text().strip(): o["subs_path"]=self.opt_subs_path.text().strip()
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
        for _p in (json.loads(out[len("[MULTI]"):]) if out.startswith("[MULTI]") else [out]):
            self._own_outputs.add(os.path.normcase(os.path.abspath(_p)))
        if out.startswith("[MULTI]"):
            arr=json.loads(out[len("[MULTI]"):])
            for p in arr: self.recent.addItem(QListWidgetItem(p))
            self.last_result_path = arr[-1] if arr else None
        else:
            self.recent.addItem(QListWidgetItem(out)); self.last_result_path = out
        self.worker=None
        # once per batch (when the queue has drained), not after every single file
        if self.act_open_when_done.isChecked() and not self.queue:
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
                    "Enable target-size mode (e.g., Discord free = 20 MB). Files already under the cap are passed through without re-encoding.")
            add_tip(getattr(self, "opt_discord", None),
                    "Discord preset cap in MB. Applies when size targeting is enabled. Discord applies the LARGER of your own limit (free/Nitro) and the server's boost limit; they don't add up, so pick the bigger of the two.")
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
                    "Path to the .srt file whose text is burned into the video.")

            add_tip(getattr(self, "opt_stripmeta", None),
                    "Strip EXIF and other metadata from images.")
            add_tip(getattr(self, "opt_maxdim", None),
                    "Maximum dimension in pixels for image outputs (0=off).")
            add_tip(getattr(self, "opt_gif_fps", None),
                    "Target FPS for GIFs. Lower values reduce size.")
            add_tip(getattr(self, "opt_gif_scale", None),
                    "Target height in pixels for GIFs. Width scales to keep aspect.")

            add_tip(getattr(self, "opt_pages", None),
                    "Pages to process for PDFs, e.g., 'all' or '1-3,5'. Applies to Word, Markdown, Excel and CSV output.")
            add_tip(getattr(self, "opt_md_images", None),
                    "Save the PDF's pictures next to the .md in a <name>_images folder and link them. Off = text only. (Word → Markdown always extracts its images.)")
            add_tip(getattr(self, "opt_ocr_lang_win", None),
                    "Language of the text in the picture or scan. 'auto' uses your Windows languages. More languages: Windows Settings > Time & language > Language & region > add a language and install its 'Optical character recognition' feature.")
            add_tip(getattr(self, "opt_pdf_searchable", None),
                    "Adds an invisible layer of recognized text under the picture, so the PDF's text can be searched, selected and copied. The picture itself is unchanged.")
            add_tip(getattr(self, "opt_cookies", None),
                    "For links that need you to be logged in (Reddit, Instagram, age-restricted, private). Uses your browser's saved session. Firefox is the most reliable on Windows; Chrome/Edge often can't be read while open. Cookies are read on this PC and sent only to the site you're downloading from.")
            add_tip(getattr(self, "opt_cookies_file", None),
                    "A cookies.txt exported from your browser (e.g. with the 'Get cookies.txt LOCALLY' extension) while logged in. Works when reading Chrome/Edge directly fails. Overrides the browser choice. The file stays on this PC; its cookies go only to the site you're downloading from.")
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


def _ensure_output_dir(job: "Job"):
    """Create the output folder if it doesn't exist yet (e.g. a typed --outdir, or a folder that was deleted)."""
    try:
        Path(job.output_dir).mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise ConvertError(f"Can't use the output folder '{job.output_dir}': {e}")

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
            _ensure_output_dir(j)
            return conv.convert(j, prog)
        if not j.source_path:
            raise ConvertError("No input provided")
        _ensure_output_dir(j)
        if Path(j.source_path).is_dir():
            raise ConvertError("Folders can't be converted; pick the files inside it.")
        in_ext = Path(j.source_path).suffix.lower().lstrip(".")
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
        try:
            _ensure_output_dir(j)
            out = conv.convert(j, prog)
        except ConvertError as e:
            print(f"Error: {e}"); sys.exit(1)       # a message, not a Python traceback
        print("Output:", out); sys.exit(0)


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