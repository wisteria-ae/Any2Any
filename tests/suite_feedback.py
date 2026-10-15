"""Checks for: decimal-MB size caps, hardware-encoder fallback, per-output Options rows, remembered choices,
merging images into one PDF, the privacy statement, the updater's address allowlist, and drag-and-drop."""
import os, sys, shutil, json, re, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import ROOT, WORK, FFMPEG, synthetic_clip
W = os.path.join(str(WORK), "feedback_test"); shutil.rmtree(W, ignore_errors=True); os.makedirs(W)
os.environ.update(QT_QPA_PLATFORM="offscreen", APPDATA=os.path.join(W, "appdata"))   # isolate settings / logs / yt-dlp folder
sys.path.insert(0, str(ROOT))
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
import Any2Any_desktop as m
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import fitz
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import Qt, QMimeData, QUrl, QPoint, QPointF
from PySide6.QtGui import QDropEvent, QDragEnterEvent, QAction

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{str(detail)[:300]}]" if (detail and not cond) else ""))
    if not cond: fails.append(name)
def conv(cls, src, target, opts=None, tag=None, cancel=False):
    out = os.path.join(W, tag or f"{cls.__name__}_{target}"); os.makedirs(out, exist_ok=True); msgs = []
    j = m.Job(source_path=src, target_ext=target, options=opts or {}, output_dir=out); j.cancel_flag = cancel
    return cls().convert(j, lambda p, s: msgs.append(s)), msgs, out
def sh(*a): return subprocess.run([str(x) for x in a], capture_output=True, text=True)
def codec(p): return sh(m.ensure_ffprobe(), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name", "-of", "csv=p=0", p).stdout.strip()
MB = 1_000_000

# ---------------------------------------------------------------- 1. caps are decimal MB, and hold
big = os.path.join(W, "big.mp4")
sh(FFMPEG, "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=5", "-vf", "noise=alls=35:allf=t+u", "-c:v", "libx264", "-crf", "14", "-preset", "ultrafast", big)
check("fixture video is far over a 20 MB cap", os.path.getsize(big) > 40 * MB, os.path.getsize(big))
check("get_target_mb returns the raw cap; CAP_MB_BYTES is decimal", m.get_target_mb({"size_targeting": "1", "discord_size_mb": "20"}) == 20 and m.CAP_MB_BYTES == 1_000_000)
for head in ("0", "3", "10"):
    r, _, _ = conv(m.VideoToMP4, big, "mp4", {"size_targeting": "1", "discord_size_mb": "20", "headroom_percent": head}, tag=f"cap_h{head}")
    check(f"video with a 20 MB cap and {head}% headroom is <= 20,000,000 bytes (safe under either MB definition)", os.path.getsize(r) <= 20 * MB, os.path.getsize(r))
r, _, _ = conv(m.VideoToMP4, big, "mp4", {"size_targeting": "1", "custom_cap_mb": "20", "headroom_percent": "0"}, tag="cap_custom")
check("custom 20 MB cap with 0% headroom is <= 20,000,000 bytes", os.path.getsize(r) <= 20 * MB, os.path.getsize(r))
noise = os.path.join(W, "noise.jpg"); Image.effect_noise((1600, 1200), 90).convert("RGB").save(noise, quality=95)
r, _, _ = conv(m.ImageToImage, noise, "jpg", {"size_targeting": "1", "custom_cap_mb": "1", "headroom_percent": "0"}, tag="img")
check("image capped at 1 MB is <= 1,000,000 bytes", os.path.getsize(r) <= MB, os.path.getsize(r))
clip = synthetic_clip(os.path.join(str(WORK), "clips", "zoo19.mp4"), 19)
r, _, _ = conv(m.VideoToGIF, clip, "gif", {"size_targeting": "1", "custom_cap_mb": "2", "headroom_percent": "0"}, tag="gif")
check("GIF capped at 2 MB is <= 2,000,000 bytes", os.path.getsize(r) <= 2 * MB, os.path.getsize(r))
wav = os.path.join(W, "long.wav"); sh(FFMPEG, "-y", "-f", "lavfi", "-i", "anoisesrc=d=60:c=pink:r=44100", wav)
r, _, _ = conv(m.VideoToMP3, wav, "mp3", {"size_targeting": "1", "custom_cap_mb": "1", "headroom_percent": "0"}, tag="mp3")
check("60 s audio capped at 1 MB is <= 1,000,000 bytes", os.path.getsize(r) <= MB, os.path.getsize(r))

# ---------------------------------------------------------------- 2. hardware encoders fall back to software
small = os.path.join(W, "small.mp4")
sh(FFMPEG, "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=4", "-f", "lavfi", "-i", "sine=duration=4", "-c:v", "libx264", "-c:a", "aac", "-shortest", small)
orig_test = m._test_hw_encoder
for enc in ("nvenc", "qsv"):
    r, msgs, _ = conv(m.VideoToMP4, small, "mp4", {"encoder": enc, "scale": "480p"}, tag=f"pre_{enc}")
    check(f"{enc}: whether or not the GPU encoder can start, the job produces an H.264 file", codec(r) == "h264", msgs[-3:])
m._test_hw_encoder = lambda name: (True, "")          # pretend the check passed, so the real encode is what fails
for enc in ("nvenc", "qsv"):
    r, msgs, _ = conv(m.VideoToMP4, small, "mp4", {"encoder": enc, "scale": "480p"}, tag=f"mid_{enc}")
    check(f"{enc}: encode that fails MID-JOB retries in software and succeeds", codec(r) == "h264" and any("retrying" in s for s in msgs), msgs[-3:])
r, msgs, _ = conv(m.VideoToMP4, small, "mp4", {"encoder": "qsv", "size_targeting": "1", "discord_size_mb": "20", "headroom_percent": "3"}, tag="mid_capped")
check("...also with a size cap", codec(r) == "h264" and os.path.getsize(r) <= 20 * MB)
try:
    conv(m.VideoToMP4, small, "mp4", {"encoder": "qsv", "scale": "480p"}, tag="mid_cancel", cancel=True); check("cancelling a GPU job is not retried", False, "no exception")
except m.ConvertError as e:
    check("cancelling a GPU job stays cancelled (no software retry)", str(e) == "Cancelled", str(e))
m._test_hw_encoder = orig_test

# ---------------------------------------------------------------- GUI-level checks (offscreen)
app = QApplication.instance() or QApplication([])
rec = {"info": [], "warn": []}
QMessageBox.information = staticmethod(lambda p, t, tx, *a, **k: rec["info"].append(tx))
QMessageBox.warning = staticmethod(lambda p, t, tx, *a, **k: rec["warn"].append(tx))
win = m.MainWindow(); win.start_next = lambda: None
check("default headroom is 3%", win.opt_headroom.value() == 3)
presets = [win.opt_discord.itemText(i) for i in range(win.opt_discord.count())]
check("presets include Free 20 MB, Nitro Basic 50, Nitro 500, boost 50 / 100", all(p in presets for p in ("Free (20 MB)", "Nitro Basic (50 MB)", "Nitro (500 MB)", "Server boost L2 (50 MB)", "Server boost L3 (100 MB)")), presets)
win._attach_info_tooltips()
check("preset tooltip explains that Discord uses the larger of personal and server limits", "LARGER" in win.opt_discord.toolTip(), win.opt_discord.toolTip()[:80])

# ---------------------------------------------------------------- 3. Options rows follow the output type
rows = {"start": win.opt_start, "scale": win.opt_scale, "encoder": win.opt_encoder, "cap": win.opt_cap_mb, "split": win.opt_split,
        "cookies": win.opt_cookies, "pages": win.opt_pages, "flavor": win.opt_flavor, "ocr": win.opt_ocr_lang_win,
        "searchable": win.opt_pdf_searchable, "merge": win.opt_pdf_merge, "quality": win.opt_quality, "strip": win.opt_stripmeta,
        "gif": win.opt_gif_fps, "subs": win.opt_subs_path.parentWidget(), "bitrate": win.opt_bitrate, "mdimg": win.opt_md_images,
        "loud": win.opt_loudnorm, "playlist": win.opt_playlist}
AUDIO = {"start", "cap", "split", "cookies", "bitrate", "loud", "playlist"}
EXPECT = {
    "mp4": {"start", "scale", "encoder", "cap", "split", "cookies", "playlist"},
    "gif": {"start", "cap", "cookies", "gif", "playlist"}, "webp-anim": {"start", "scale", "cap", "cookies", "gif", "playlist"},
    "mp3": AUDIO, "m4a": AUDIO, "opus": AUDIO,
    "txt": {"pages", "ocr"}, "pdf": {"pages", "ocr", "searchable", "merge"}, "md": {"pages", "ocr", "mdimg"}, "docx": {"pages", "ocr"},
    "jpg": {"cap", "quality", "strip", "pages"}, "png": {"cap", "strip", "pages"}, "webp": {"cap", "strip"}, "avif": {"cap", "strip"},
    "xlsx": {"pages", "flavor"}, "csv": {"pages", "flavor"}, "json": set(), "srt": set(),
    "mp4+burnsubs": {"subs"}, "thumbnail": {"cookies", "playlist"},
}
for tgt, want in EXPECT.items():
    win.format_box.setCurrentText(tgt); win._sync_visibility()
    got = {k for k, w in rows.items() if w.isVisibleTo(win)}
    check(f"Options for '{tgt}' show exactly the settings that apply", got == want, f"extra={sorted(got - want)} missing={sorted(want - got)}")
check("every supported output is covered by the table above", set(EXPECT) == set(m.SUPPORTED_OUTPUTS), sorted(set(m.SUPPORTED_OUTPUTS) ^ set(EXPECT)))
win.format_box.setCurrentIndex(0); win._sync_visibility()
check("before an output type is chosen, every row is visible", all(w.isVisibleTo(win) for w in rows.values()), [k for k, w in rows.items() if not w.isVisibleTo(win)])
win.format_box.setCurrentText("mp3"); win.opt_size_target.setChecked(True); win._sync_visibility()
check("audio bitrate row hides while a size cap is on (the cap decides the bitrate)", not win.opt_bitrate.isVisibleTo(win))
win.opt_size_target.setChecked(False)
win.format_box.setCurrentText("m4a"); o = win.collect_opts("m4a"); check("the bitrate setting reaches m4a/opus jobs too (was mp3 only)", "bitrate" in o, o)

# ---------------------------------------------------------------- 4. remembers the last choices
w1 = m.MainWindow(); w1.start_next = lambda: None
w1.format_box.setCurrentText("mp3"); w1.opt_size_target.setChecked(True); w1.opt_discord.setCurrentText("Nitro Basic (50 MB)")
w1.opt_cap_mb.setValue(12); w1.opt_headroom.setValue(7); w1.opt_encoder.setCurrentText("amf"); w1.opt_cookies.setCurrentText("firefox")
w1.format_box.setCurrentIndex(0)                         # the placeholder must not overwrite the remembered type
w2 = m.MainWindow()
check("output type is remembered", w2.format_box.currentText() == "mp3", w2.format_box.currentText())
check("size-cap on/off + preset + custom cap + headroom are remembered", w2.opt_size_target.isChecked() and w2.opt_discord.currentText() == "Nitro Basic (50 MB)" and w2.opt_cap_mb.value() == 12 and w2.opt_headroom.value() == 7)
check("encoder + cookies browser are remembered", w2.opt_encoder.currentText() == "amf" and w2.opt_cookies.currentText() == "firefox")

# ---------------------------------------------------------------- 5. merge several images into one PDF
imgs = []
for i, (w_, h_) in enumerate(((300, 100), (400, 120), (500, 140))):
    p = os.path.join(W, f"page{i+1}.png"); im = Image.new("RGB", (w_, h_), "white"); d = ImageDraw.Draw(im)
    d.text((10, 10), f"PAGE NUMBER {i+1} HELLO", fill="black", font=ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 22)); im.save(p); imgs.append(p)
docx_path = os.path.join(W, "note.docx")
import docx as _docx; dd = _docx.Document(); dd.add_paragraph("a word file"); dd.save(docx_path)
mw = m.MainWindow(); mw.start_next = lambda: None; mw.format_box.setCurrentText("pdf")
mw.add_files(imgs); check("without the option, 3 images -> 3 separate jobs", len(mw.queue) == 3, len(mw.queue))
mw.queue.clear(); mw.queue_list.clear(); mw.opt_pdf_merge.setChecked(True); mw.add_files(imgs)
check("with the option, 3 images -> ONE job", len(mw.queue) == 1 and json.loads(mw.queue[0].options["merge_paths"]) == imgs, [j.options for j in mw.queue])
check("...labelled clearly in the queue", "3 images" in mw.queue_list.item(0).text(), mw.queue_list.item(0).text())
job = mw.queue[0]; job.output_dir = os.path.join(W, "merge"); os.makedirs(job.output_dir)
r = m.ImagesToPDF().convert(job, lambda p, s: None)
with fitz.open(r) as d:
    check("the merged PDF has one page per image, in the order added", d.page_count == 3 and [round(p.rect.width) for p in d] == [300, 400, 500], [round(p.rect.width) for p in d])
check("...and is named <first>_merged.pdf", r.endswith("page1_merged.pdf"), r)
mw.queue.clear(); mw.queue_list.clear(); mw.add_files(imgs[:1])
check("a single image is never 'merged' (normal one-page PDF)", len(mw.queue) == 1 and "merge_paths" not in mw.queue[0].options)
mw.queue.clear(); mw.queue_list.clear(); mw.add_files(imgs + [docx_path])
check("a mixed drop (3 images + a Word file) -> merged-images job + the Word file as its own job", len(mw.queue) == 2 and sum("merge_paths" in j.options for j in mw.queue) == 1, [j.source_path for j in mw.queue])
mw.opt_pdf_searchable.setChecked(True); mw.queue.clear(); mw.queue_list.clear(); mw.add_files(imgs)
job = mw.queue[0]; job.output_dir = os.path.join(W, "merge_ocr"); os.makedirs(job.output_dir)
r = m.ImagesToPDF().convert(job, lambda p, s: None)
with fitz.open(r) as d: check("merge + searchable: every page gets its own text layer", all(f"{i+1}" in d[i].get_text() and "HELLO" in d[i].get_text().upper() for i in range(3)), [p.get_text()[:30] for p in d])
bad = [os.path.join(W, "ghost.png")]; mw.queue.clear(); mw.queue_list.clear(); mw.opt_pdf_searchable.setChecked(False)
job = m.Job(source_path=imgs[0], target_ext="pdf", options={"merge_paths": json.dumps(imgs + bad)}, output_dir=os.path.join(W, "merge_bad")); os.makedirs(job.output_dir)
try: m.ImagesToPDF().convert(job, lambda p, s: None); check("an unreadable image in a merge gives a clear error", False, "no error")
except m.ConvertError as e: check("an unreadable image in a merge gives a clear error naming the file", "ghost.png" in str(e), str(e))

# ---------------------------------------------------------------- 6. privacy statement + no telemetry
about = next(a for a in win.findChildren(QAction) if a.text() == "About"); about.trigger()
t = rec["info"][-1]
check("About shows the version", m.APP_VERSION in t)
check("About says there is no telemetry, no analytics, no account", all(s in t.lower() for s in ("no telemetry", "no analytics", "no account")), t[:200])
check("About names the only network uses (links, pypi.org update, Reddit short links) and the cookie rule", all(s in t for s in ("pypi.org", "Reddit", "Cookies")) and "only to the site you are downloading from" in t, t)
win._attach_info_tooltips()
check("cookie options say cookies stay on this PC", "read on this PC" in win.opt_cookies.toolTip() and "stays on this PC" in win.opt_cookies_file.toolTip(), (win.opt_cookies.toolTip()[-80:],))
src = (ROOT / "Any2Any_desktop.py").read_text(encoding="utf-8")
check("no telemetry / analytics / crash-reporting libraries anywhere in the app", not re.search(r"sentry|mixpanel|posthog|amplitude|segment\.io|google-analytics|googletagmanager|appinsights|datadog|bugsnag", src, re.I))
hosts = set(re.findall(r"https?://([A-Za-z0-9.-]+)", src))
print("   hostnames written in the source:", sorted(hosts))
check("the only hostnames in the source are the update server, its file host, Reddit's video host and a YouTube example", hosts <= {"pypi.org", "files.pythonhosted.org", "v.redd.it", "www.youtube.com", "www.reddit.com"}, sorted(hosts))
check("only two places make direct HTTP requests (the updater and the Reddit short-link resolver)", len(re.findall(r"urllib\.request\.urlopen|urlretrieve", src)) == 3, len(re.findall(r"urllib\.request\.urlopen|urlretrieve", src)))

# ---------------------------------------------------------------- 7. updater refuses unexpected download addresses
dest = Path(W) / "ytdlp_dest"
orig = m._pypi_latest_wheel
for label, url in (("another host", "https://evil.example.com/yt_dlp-9999.whl"), ("plain http", "http://files.pythonhosted.org/yt_dlp-9999.whl"), ("look-alike host", "https://files.pythonhosted.org.evil.com/x.whl")):
    m._pypi_latest_wheel = lambda pkg, _u=url: ("9999.01.01", _u, "0" * 64)
    try: m.install_ytdlp_update(dest, force=True); check(f"updater refuses {label}", False, "no error")
    except m.ConvertError as e: check(f"updater refuses {label}, and changes nothing", "unexpected address" in str(e) and not dest.exists(), str(e))
m._pypi_latest_wheel = orig

# ---------------------------------------------------------------- 8. drag and drop (Qt drop events into the real drop zone)
dw = m.MainWindow(); dw.start_next = lambda: None; dw.format_box.setCurrentText("pdf")
_keep = []     # QDragEnter/QDropEvent don't own their QMimeData: keep it alive for as long as the event is used, as a real drag does
def mime_for(paths):
    mi = QMimeData(); mi.setUrls([QUrl.fromLocalFile(p) for p in paths]); _keep.append(mi); return mi
ev = QDragEnterEvent(QPoint(5, 5), Qt.CopyAction, mime_for(imgs[:1]), Qt.LeftButton, Qt.NoModifier); dw.drop.dragEnterEvent(ev)
check("dragging a file over the drop zone is accepted", ev.isAccepted())
tm = QMimeData(); _keep.append(tm); tm.setText("just some text"); ev = QDragEnterEvent(QPoint(5, 5), Qt.CopyAction, tm, Qt.LeftButton, Qt.NoModifier); dw.drop.dragEnterEvent(ev)
check("dragging plain text is not accepted", not ev.isAccepted())
dw.queue.clear(); dw.queue_list.clear()
dw.drop.dropEvent(QDropEvent(QPointF(5, 5), Qt.CopyAction, mime_for(imgs), Qt.LeftButton, Qt.NoModifier))
norm = lambda ps: [os.path.normcase(os.path.normpath(x)) for x in ps]        # Qt hands over C:/... paths, Windows tools use C:\...
check("dropping 3 images queues those 3 files, in order", len(dw.queue) == 3 and norm([j.source_path for j in dw.queue]) == norm(imgs), [j.source_path for j in dw.queue])
dw.queue.clear(); dw.queue_list.clear(); n0 = len(rec["warn"])
dw.drop.dropEvent(QDropEvent(QPointF(5, 5), Qt.CopyAction, mime_for([small]), Qt.LeftButton, Qt.NoModifier))
check("dropping a file that can't become the chosen type queues nothing and explains why", len(dw.queue) == 0 and len(rec["warn"]) == n0 + 1 and "small.mp4" in rec["warn"][-1], rec["warn"][-1:])
dw.queue.clear(); dw.queue_list.clear(); dw.opt_pdf_merge.setChecked(True)
dw.drop.dropEvent(QDropEvent(QPointF(5, 5), Qt.CopyAction, mime_for(imgs), Qt.LeftButton, Qt.NoModifier))
check("dropping 3 images with 'combine' ticked queues ONE merged job", len(dw.queue) == 1 and "merge_paths" in dw.queue[0].options)
dw.queue.clear(); dw.queue_list.clear(); dw.format_box.setCurrentText("txt"); dw.opt_pdf_merge.setChecked(False)
dw.drop.dropEvent(QDropEvent(QPointF(5, 5), Qt.CopyAction, mime_for([imgs[0]]), Qt.LeftButton, Qt.NoModifier))
check("dropping a picture for text recognition (txt) queues it", len(dw.queue) == 1 and dw.queue[0].target_ext == "txt")

print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
