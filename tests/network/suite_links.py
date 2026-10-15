import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if os.path.basename(os.path.dirname(os.path.abspath(__file__))) in ("network", "packaged") else os.path.dirname(os.path.abspath(__file__)))
from _common import ROOT, WORK, synthetic_clip
import os, sys, shutil, json, subprocess
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(ROOT))
import Any2Any_desktop as m

OUT = os.path.join(str(WORK), "url_out2")
shutil.rmtree(OUT, ignore_errors=True); os.makedirs(OUT)
fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond: fails.append(name)

# ---------- offline: URL extraction / normalization ----------
check("extract: plain url", m.extract_urls("https://youtu.be/abc123") == ["https://youtu.be/abc123"])
check("extract: text around + trailing punct", m.extract_urls("look at this (https://x.com/a/status/1?s=20).") == ["https://x.com/a/status/1?s=20"])
check("extract: markdown link", m.extract_urls("[clip](https://v.redd.it/abc)") == ["https://v.redd.it/abc"])
check("extract: scheme-less", m.extract_urls("x.com/NASA/status/123") == ["https://x.com/NASA/status/123"])
check("extract: multiple + dedupe", m.extract_urls("https://a.com/1 https://b.com/2\nhttps://a.com/1") == ["https://a.com/1", "https://b.com/2"])
check("extract: junk text -> none", m.extract_urls("hello world, no link") == [])
check("extract: filename isn't a url", m.extract_urls("report.pdf") == [])
n = lambda u: m.normalize_media_url(u)[0]
check("rewrite fxtwitter", n("https://fxtwitter.com/NASA/status/1") == "https://x.com/NASA/status/1", n("https://fxtwitter.com/NASA/status/1"))
check("rewrite vxtwitter", n("https://www.vxtwitter.com/NASA/status/1") == "https://x.com/NASA/status/1")
check("rewrite ddinstagram", n("https://ddinstagram.com/reel/abc/") == "https://instagram.com/reel/abc/")
check("rewrite vxtiktok", n("https://vxtiktok.com/@u/video/1") == "https://tiktok.com/@u/video/1")
check("v.redd.it -> HLS", n("https://v.redd.it/abc123") == "https://v.redd.it/abc123/HLSPlaylist.m3u8")
check("yt mix stripped", n("https://www.youtube.com/watch?v=abc&list=RDabc") == "https://www.youtube.com/watch?v=abc")
check("yt real playlist kept", "list=PL123" in n("https://www.youtube.com/watch?v=abc&list=PL123"))
try:
    m.normalize_media_url("https://www.youtube.com/watch?v=abc&list=RDabc", allow_playlist=True); check("yt mix + playlist raises", False)
except m.ConvertError:
    check("yt mix + playlist raises (no longer swallowed)", True)
check("friendly: unsupported", "No downloadable video" in m._friendly_ytdlp_error(Exception("ERROR: Unsupported URL: https://x")))
check("friendly: login", "URL cookies" in m._friendly_ytdlp_error(Exception("ERROR: [twitter] 1: NSFW tweet requires authentication")))
check("height cap: short clip -> 1080", m._height_cap_for_target(20, 19) == 1080)
check("height cap: 60s @20MB -> 720", m._height_cap_for_target(20, 60) == 720, m._height_cap_for_target(20, 60))
check("height cap: 10min @20MB -> 240/360", m._height_cap_for_target(20, 600) <= 360, m._height_cap_for_target(20, 600))
check("height cap: unknown duration -> 720", m._height_cap_for_target(20, 0) == 720)

# ---------- live: every URL output type ----------
URL = "https://www.youtube.com/shorts/jNQXAC9IVRw"
def run(target, opts=None, url=URL):
    sub = os.path.join(OUT, target + ("_cap" if opts else "")); os.makedirs(sub, exist_ok=True)
    job = m.Job(source_url=url, target_ext=target, options=opts or {}, output_dir=sub)
    try:
        res = m.URLToMedia().convert(job, lambda p, msg: None)
        files = sorted(os.listdir(sub))
        return res, files, sub
    except Exception as e:
        return e, sorted(os.listdir(sub)), sub

for target, ext in [("mp3", ".mp3"), ("m4a", ".m4a"), ("opus", ".opus"), ("mp4", ".mp4"), ("gif", ".gif"), ("webp-anim", ".webp"), ("thumbnail", None)]:
    res, files, sub = run(target)
    if isinstance(res, Exception):
        check(f"live url->{target}", False, f"{type(res).__name__}: {str(res)[:200]} files={files}"); continue
    ok = os.path.exists(res) and os.path.getsize(res) > 0 and (ext is None or res.lower().endswith(ext))
    check(f"live url->{target}", ok, f"{res} files={files}")
    print(f"       -> {os.path.basename(res)}  ({os.path.getsize(res)} bytes)  folder={files}")
    if target == "mp4":
        check("mp4 name has no stray _out suffix", "_out" not in os.path.basename(res), os.path.basename(res))
        check("mp4: only one file left in folder (intermediates cleaned)", len(files) == 1, str(files))
        pr = subprocess.run([m.ensure_ffprobe(), "-v", "error", "-show_entries", "stream=codec_name,codec_type", "-of", "csv=p=0", res], capture_output=True, text=True).stdout
        check("mp4 is H.264 + AAC (Discord-friendly)", "h264" in pr and "aac" in pr, pr.replace("\n", " | "))

# size-targeted (new Discord free tier) download
res, files, sub = run("mp4", {"size_targeting": "1", "discord_size_mb": "20", "headroom_percent": "5"})
if isinstance(res, Exception): check("live url->mp4 @20MB cap", False, str(res)[:200])
else:
    sz = os.path.getsize(res) / (1024 * 1024)
    check("live url->mp4 @20MB cap fits", sz <= 20, f"{sz:.2f} MiB"); print(f"       -> {sz:.2f} MiB")

print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
