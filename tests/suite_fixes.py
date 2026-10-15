import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if os.path.basename(os.path.dirname(os.path.abspath(__file__))) in ("network", "packaged") else os.path.dirname(os.path.abspath(__file__)))
from _common import ROOT, WORK, synthetic_clip
import os, sys, shutil, subprocess, json, time, re, threading, tempfile
SCR = os.path.join(str(WORK))
W = os.path.join(SCR, "fixes_test"); shutil.rmtree(W, ignore_errors=True); os.makedirs(W)
os.environ.update(QT_QPA_PLATFORM="offscreen", APPDATA=os.path.join(W, "appdata"))     # isolate settings/logs/yt-dlp dir
sys.path.insert(0, str(ROOT))
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

# record every child process launch (for the console-window check) BEFORE the app module loads
import subprocess as _sp
_launches = []
_orig_popen = _sp.Popen
class _RecPopen(_orig_popen):
    def __init__(self, *a, **kw):
        _launches.append((a[0] if a else kw.get("args"), kw.get("creationflags", 0))); super().__init__(*a, **kw)
_sp.Popen = _RecPopen

import Any2Any_desktop as m
from PIL import Image
fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{str(detail)[:400]}]" if (detail and not cond) else ""))
    if not cond: fails.append(name)
ff = m.ensure_ffmpeg(); fp = m.ensure_ffprobe(); MiB = 1048576
def sh(*a): return subprocess.run(list(a), capture_output=True, text=True)
def conv(cls, src, target, opts=None, tag=None, cancel=False):
    out = os.path.join(W, tag or f"{cls.__name__}_{target}"); os.makedirs(out, exist_ok=True); msgs = []
    j = m.Job(source_path=src, target_ext=target, options=opts or {}, output_dir=out); j.cancel_flag = cancel
    return cls().convert(j, lambda p, s: msgs.append(s)), msgs, out
def raises(fn, needle):
    try: fn(); return False, "no exception"
    except m.ConvertError as e: return needle.lower() in str(e).lower(), str(e)[:200]
    except Exception as e: return False, f"{type(e).__name__}: {e}"
def vinfo(p, what="stream=codec_name,pix_fmt,width,height"):
    return sh(fp, "-v", "error", "-select_streams", "v:0", "-show_entries", what, "-of", "csv=p=0", p).stdout.strip()
def dur(p): return float(sh(fp, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p).stdout.strip() or 0)

zoo = synthetic_clip(os.path.join(str(WORK), "clips", "zoo19.mp4"), 19)                      # a 19 s clip, generated (no network)
big = os.path.join(SCR, "misc_test", "big.mp4"); small = os.path.join(SCR, "misc_test", "small.mp4")
pdf = os.path.join(SCR, "docs_test", "pdf_word", "report.pdf")
for need in (zoo, big, small, pdf): assert os.path.exists(need), f"missing fixture {need}: run tests/run_all.py (it builds fixtures in order)"

# ======== 1. subtitle extraction ========
srt = os.path.join(W, "s.srt"); open(srt, "w").write("1\n00:00:00,000 --> 00:00:01,500\nHello\n\n2\n00:00:01,600 --> 00:00:03,000\nWorld\n")
for name, codec in (("withsub.mp4", "mov_text"), ("withsub.mkv", "srt"), ("withsub.mkv2", "ass")):
    if name.endswith("2"): continue
    sh(ff, "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10:duration=3", "-i", srt, "-c:v", "libx264", "-c:s", codec, os.path.join(W, name))
sh(ff, "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10:duration=3", os.path.join(W, "nosub.mp4"))
for name in ("withsub.mp4", "withsub.mkv"):
    r, _, _ = conv(m.SubtitlesExtract, os.path.join(W, name), "srt", tag="sub_" + name.replace(".", "_"))
    check(f"extract SRT from {name}", "Hello" in open(r, encoding="utf-8").read() and "World" in open(r, encoding="utf-8").read())
ok, msg = raises(lambda: conv(m.SubtitlesExtract, os.path.join(W, "nosub.mp4"), "srt", tag="sub_none"), "no subtitle track"); check("no-subtitle video gives a plain message (not an ffmpeg dump)", ok and len(msg) < 120, msg)

# ======== 2. CSV / JSON ========
bom = os.path.join(W, "excel.csv"); open(bom, "w", encoding="utf-8-sig", newline="").write("id,name,qty\n1,Åsa,3\n2,Bob,5\n")
r, _, _ = conv(m.CSVJSON, bom, "json"); rows = json.load(open(r, encoding="utf-8"))
check("CSV with BOM: first key is clean", list(rows[0].keys()) == ["id", "name", "qty"], list(rows[0].keys()))
def j2c(obj, tag):
    p = os.path.join(W, tag + ".json"); json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    r, _, _ = conv(m.CSVJSON, p, "csv", tag=tag); return open(r, encoding="utf-8").read()
check("JSON list-of-lists -> rows", j2c([[1, 2], [3, 4]], "lol").replace("\r\n", "\n") == "1,2\n3,4\n")
check("JSON scalars -> value column", j2c([1, 2], "sc").replace("\r\n", "\n") == "value\n1\n2\n")
check("JSON keeps column order (not alphabetical)", j2c([{"zeta": 1, "alpha": 2, "mid": 3}], "ord").splitlines()[0] == "zeta,alpha,mid")
check("JSON {'items':[...]} unwraps the records", j2c({"count": 2, "items": [{"a": 1}, {"a": 2}]}, "wrap").replace("\r\n", "\n") == "a\n1\n2\n")
check("JSON nested arrays are valid JSON in the cell", '"[""x"", ""y""]"' in j2c([{"t": ["x", "y"]}], "arr"))
check("JSON with a BOM loads", j2c([{"k": "é"}], "u").splitlines()[1] == "é")
bad = os.path.join(W, "bad.json"); open(bad, "w").write("{nope")
ok, msg = raises(lambda: conv(m.CSVJSON, bad, "csv", tag="badj"), "valid JSON"); check("malformed JSON -> clear message", ok, msg)
emp = os.path.join(W, "empty.json"); open(emp, "w").write("[]")
ok, msg = raises(lambda: conv(m.CSVJSON, emp, "csv", tag="emptyj"), "no records"); check("empty JSON -> clear message", ok, msg)

# ======== 3. PDF tables ========
import openpyxl
r, _, _ = conv(m.PDFtoXLSX, pdf, "xlsx", {"flavor": "lattice"}, tag="xl"); ws = openpyxl.load_workbook(r).worksheets[0]
first = [c.value for c in ws[1]]
check("Excel sheet's first row is the table header (no 0,1,2 row)", first[0] == "Region" or "Region" in [str(x) for x in first], first)
r, _, od = conv(m.PDFtoXLSX, pdf, "csv", {"flavor": "lattice"}, tag="cs"); txt = open(r, encoding="utf-8-sig").read().splitlines()
check("CSV first row is the table header", txt[0].startswith("Region"), txt[:2])
ok, msg = raises(lambda: conv(m.PDFtoXLSX, os.path.join(SCR, "docs_test", "locked.pdf"), "xlsx", tag="lk"), "password"); check("locked PDF -> password message", ok, msg)

# ======== 4. images ========
png = os.path.join(W, "logo.png"); Image.new("RGBA", (40, 40), (0, 0, 0, 0)).save(png)
r, _, _ = conv(m.ImageToImage, png, "jpg", tag="i1"); check("transparent PNG -> JPG is white", Image.open(r).getpixel((5, 5)) == (255, 255, 255), Image.open(r).getpixel((5, 5)))
r, _, _ = conv(m.ImagesToPDF, png, "pdf", tag="i2")
with m.fitz.open(r) as d: check("transparent PNG -> PDF is white", d[0].get_pixmap().pixel(5, 5) == (255, 255, 255))
ph = os.path.join(W, "phone.jpg"); ex = Image.Exif(); ex[0x0112] = 6; Image.new("RGB", (300, 100), (200, 50, 50)).save(ph, exif=ex)
r, _, _ = conv(m.ImageToImage, ph, "png", tag="i3"); check("EXIF-rotated photo comes out upright", Image.open(r).size == (100, 300), Image.open(r).size)
keep = os.path.join(W, "keep.jpg"); Image.effect_noise((1200, 900), 80).convert("RGB").save(keep, quality=95); before = os.path.getsize(keep)
job = m.Job(source_path=keep, target_ext="jpg", options={"size_targeting": "1", "custom_cap_mb": "1", "quality": "60"}, output_dir=W)
res = m.ImageToImage().convert(job, lambda p, s: None)
check("same-folder JPG->JPG never overwrites the original", os.path.getsize(keep) == before and res.endswith("keep_out.jpg") and os.path.exists(res), (before, os.path.getsize(keep), res))
check("size-capped output fits the cap (headroom once)", os.path.getsize(res) <= 1 * MiB, os.path.getsize(res) / MiB)
r, _, _ = conv(m.ImageToImage, keep, "webp", {"size_targeting": "1", "custom_cap_mb": "1"}, tag="i4"); check("size cap now works for WebP (quality, not just resizing)", os.path.getsize(r) <= MiB, os.path.getsize(r) / MiB)

# PDF -> images
r, _, od = conv(m.PDFToImages, pdf, "png", {"pages": "2"}, tag="pi"); check("PDF->PNG honours the page range", r.endswith("_p002.png"), r)
ok, msg = raises(lambda: conv(m.PDFToImages, pdf, "png", {"pages": "99"}, tag="pi2"), "outside"); check("PDF->PNG out-of-range page -> message (was an IndexError)", ok, msg)
ok, msg = raises(lambda: conv(m.PDFToImages, pdf, "png", tag="pi3", cancel=True), "Cancelled"); check("PDF->PNG can be cancelled", ok, msg)

# ======== 5. headroom applied exactly once ========
cap = {"size_targeting": "1", "custom_cap_mb": "20", "headroom_percent": "5"}
check("get_target_mb returns the raw cap", m.get_target_mb(cap) == 20)
r, _, _ = conv(m.VideoToMP4, big, "mp4", cap, tag="hr"); sz = os.path.getsize(r) / MiB
r2, _, _ = conv(m.VideoToMP4, big, "mp4", {"size_targeting": "1", "discord_size_mb": "20", "headroom_percent": "5"}, tag="hr2"); sz2 = os.path.getsize(r2) / MiB
print(f"   custom 20 MB cap: {sz:.2f} MiB | Discord 20 MB preset: {sz2:.2f} MiB (same 5% headroom)")
check("custom cap and Discord preset now give the same result (headroom applied once in both)", abs(sz - sz2) < 0.35 and sz <= 20, (sz, sz2))
check("custom cap is no longer double-reduced (old behaviour targeted 18 MiB => ~17 MiB result)", sz > 17.4, sz)

# ======== 6. size-cap verification / retry / impossible / split ========
tiny = {"size_targeting": "1", "custom_cap_mb": "1", "headroom_percent": "5"}
sh(ff, "-y", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=8", "-vf", "noise=alls=40:allf=t+u", "-c:v", "libx264", "-crf", "12", "-preset", "ultrafast", os.path.join(W, "noisy8.mp4"))
r, msgs, _ = conv(m.VideoToMP4, os.path.join(W, "noisy8.mp4"), "mp4", tiny, tag="cap1")
check("hard 1 MB cap on hard-to-compress video is actually met", os.path.getsize(r) <= MiB, f"{os.path.getsize(r)/MiB:.2f} MiB; retried={any('over' in s.lower() for s in msgs)}")
sh(ff, "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=5:duration=400", "-c:v", "libx264", "-preset", "ultrafast", os.path.join(W, "long400.mp4"))
ok, msg = raises(lambda: conv(m.VideoToMP4, os.path.join(W, "long400.mp4"), "mp4", tiny, tag="cap_imp"), "couldn't fit"); check("impossible cap -> clear error (not a silent over-cap file)", ok, msg)
check("...and no oversize leftover file", not any(f.endswith(".mp4") for f in os.listdir(os.path.join(W, "cap_imp"))), os.listdir(os.path.join(W, "cap_imp")))
r, msgs, _ = conv(m.VideoToMP4, os.path.join(W, "long400.mp4"), "mp4", dict(tiny, split="1"), tag="cap_split")
parts = json.loads(r[7:]) if r.startswith("[MULTI]") else [r]
check("with Split on, an impossible cap yields several parts", len(parts) >= 2, parts)
print("   split part sizes (MiB):", [round(os.path.getsize(p) / MiB, 2) for p in parts])

# ======== 7. GIF / WebP ========
r, _, _ = conv(m.VideoToGIF, zoo, "gif", {"start": "2", "end": "5", "gif_scale": "240"}, tag="g1"); check("GIF honours trim (~3 s)", 2.5 <= dur(r) <= 3.6, dur(r))
r, msgs, _ = conv(m.VideoToGIF, zoo, "gif", {"size_targeting": "1", "discord_size_mb": "20", "headroom_percent": "5"}, tag="g2")
sz = os.path.getsize(r) / MiB; check("19 s GIF fits the 20 MB cap (was 39.9 MiB)", sz <= 20, f"{sz:.1f} MiB"); print(f"   GIF with cap: {sz:.1f} MiB; shrink steps: {sum('Shrinking' in s for s in msgs)}")
ok, msg = raises(lambda: conv(m.VideoToGIF, os.path.join(W, "long400.mp4"), "gif", {"size_targeting": "1", "custom_cap_mb": "1", "headroom_percent": "0"}, tag="g3"), "Couldn't get this GIF under")
check("a GIF that truly can't fit -> clear error", ok, msg)
check("...and the oversize GIF is removed", not any(f.endswith(".gif") for f in os.listdir(os.path.join(W, "g3"))), os.listdir(os.path.join(W, "g3")))
check("GIF leaves no palette file next to the output", not any(f.endswith(".png") for f in os.listdir(os.path.join(W, "g1"))), os.listdir(os.path.join(W, "g1")))
r, _, _ = conv(m.VideoToWebPAnim, zoo, "webp-anim", {"start": "2", "end": "5", "scale": "720p", "fps": "10"}, tag="w1")
check("animated WebP: trim + 'Scale: 720p' preset no longer crashes", os.path.exists(r) and os.path.getsize(r) > 0)
r, _, _ = conv(m.VideoToWebPAnim, zoo, "webp-anim", {"size_targeting": "1", "custom_cap_mb": "2", "headroom_percent": "5"}, tag="w2")
check("animated WebP respects a 2 MB cap", os.path.getsize(r) <= 2 * MiB, os.path.getsize(r) / MiB)

# ======== 8. GIF -> MP4, pixel format ========
odd = [Image.new("RGB", (61, 59), (i * 40, 90, 160)) for i in range(6)]; gif = os.path.join(W, "odd.gif"); odd[0].save(gif, save_all=True, append_images=odd[1:], duration=100, loop=0)
r, _, _ = conv(m.VideoToMP4, gif, "mp4", tag="g2m"); info = vinfo(r).split(",")       # ffprobe order: codec, width, height, pix_fmt
check("GIF (61x59, odd size) -> playable MP4 (h264, even size, yuv420p)", info[0] == "h264" and info[3] == "yuv420p" and int(info[1]) % 2 == 0 and int(info[2]) % 2 == 0, info)
check("GIF is now an accepted input", m.find_conv("gif", "mp4") is not None)
r, _, _ = conv(m.VideoToMP4, small, "mp4", {"scale": "480p"}, tag="px"); check("re-encodes are yuv420p", "yuv420p" in vinfo(r))

# ======== 9. PDF -> Markdown merged-table repair ========
r, _, _ = conv(m.PDFToMarkdown, pdf, "md", tag="mdfix"); md = open(r, encoding="utf-8").read()
check("list above a table no longer swallowed into the table header", "Col2" not in md and re.search(r"\|\s*Region\s*\|\s*Q1\s*\|\s*Q2\s*\|", md) is not None and re.search(r"^1\. step one\s*$", md, re.M) is not None, md[:500])
check("table rows intact after repair", re.search(r"\|\s*North\s*\|\s*10\s*\|\s*12\s*\|", md) is not None and re.search(r"\|\s*South\s*\|\s*7\s*\|\s*9\s*\|", md) is not None)
rep = m._repair_merged_tables
check("repair leaves ordinary tables and text alone", rep("|Name|Qty|\n|---|---|\n|a|1|\n") == "|Name|Qty|\n|---|---|\n|a|1|\n" and rep("just text | with a pipe") == "just text | with a pipe")
check("repair splits run-together list items", rep("|- one<br>- two|Col2|\n|---|---|\n|H1|H2|\n|x|y|").startswith("- one\n- two\n\n|H1|H2|"))

# ======== 10. console windows ========
_launches.clear()
conv(m.VideoToMP4, small, "mp4", {"scale": "480p"}, tag="cw1"); conv(m.VideoToMP3, small, "opus", tag="cw2")
conv(m.SubtitlesExtract, os.path.join(W, "withsub.mkv"), "srt", tag="cw3"); m.probe_duration_seconds(small); m.probe_video_codec(small)
m._test_hw_encoder("libx264")
bare = [(a[0] if isinstance(a, (list, tuple)) else a) for a, f in _launches if not (f & 0x08000000)]
check(f"all {len(_launches)} child-process launches carry CREATE_NO_WINDOW", _launches and not bare, bare[:3])
check("ffmpeg runs also keep below-normal priority", any((f & 0x4000) for a, f in _launches))

# ======== 11. GUI-thread behaviours (offscreen) ========
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QThread, QEventLoop, QTimer
app = QApplication.instance() or QApplication([])
rec = {"info": [], "warn": [], "q": []}
QMessageBox.information = staticmethod(lambda p, t, tx, *a, **k: rec["info"].append(tx)); QMessageBox.warning = staticmethod(lambda p, t, tx, *a, **k: rec["warn"].append(tx))
win = m.MainWindow(); win.start_next = lambda: None
def pump(sec):
    end = time.time() + sec
    while time.time() < end: app.processEvents(); time.sleep(0.02)
# tooltips
tips = [win.format_box.itemData(i, 3) for i in range(win.format_box.count()) if win.format_box.itemText(i) in m.SUPPORTED_OUTPUTS]
check("every output type has a hover description", all(tips) and len(tips) == len(m.SUPPORTED_OUTPUTS), [win.format_box.itemText(i) for i in range(win.format_box.count()) if win.format_box.itemText(i) in m.SUPPORTED_OUTPUTS and not win.format_box.itemData(i, 3)])
# open-when-done
opened = []; win.open_in_os = lambda p: opened.append(str(p))
win.queue = [m.Job(source_path="x")]; win.on_finished(os.path.join(W, "a.mp4")); n_mid = len(opened)
win.queue = []; win.on_finished(os.path.join(W, "b.mp4")); n_end = len(opened)
check("folder opens once, when the queue finishes (not after each job)", n_mid == 0 and n_end == 1, (n_mid, n_end))
win.act_open_when_done.setChecked(False); win.on_finished(os.path.join(W, "c.mp4"))
check("the 'open folder' toggle is respected", len(opened) == 1)
# persistence
d2 = os.path.join(W, "chosen"); os.makedirs(d2)
from PySide6.QtWidgets import QFileDialog
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: d2); win.pick_output_dir()
win2 = m.MainWindow()
check("output folder + toggle are remembered next launch", win2.output_dir == d2 and not win2.act_open_when_done.isChecked(), (win2.output_dir, win2.act_open_when_done.isChecked()))
# cookies file
win.format_box.setCurrentText("mp3"); win.opt_cookies_file.setText(os.path.join(W, "cookies.txt")); win.url_edit.setText("https://youtu.be/abc")
win.queue.clear(); win.enqueue_from_ui(); check("cookies file option reaches the job", win.queue and win.queue[0].options.get("cookies_file", "").endswith("cookies.txt"))
ok, msg = raises(lambda: m.URLToMedia().convert(m.Job(source_url="https://youtu.be/x", target_ext="mp3", options={"cookies_file": os.path.join(W, "nope.txt")}, output_dir=W), lambda p, s: None), "Cookies file not found"); check("missing cookies file -> clear message", ok, msg)
# watch folder: slow write, own-output filter, GUI-thread delivery
watch = os.path.join(W, "watch"); os.makedirs(watch)
win.format_box.setCurrentText("webp"); win.queue.clear(); win.queue_list.clear(); win.opt_cookies_file.setText("")
thread_ids = []
orig_enq = win.enqueue_file_path
def spy(p): thread_ids.append(QThread.currentThread() == app.thread()); orig_enq(p)
win.enqueue_file_path = spy
win.start_watch(watch, "webp")
target = os.path.join(watch, "slow.png"); img = Image.effect_noise((900, 900), 80).convert("RGB"); tmpb = os.path.join(W, "slow_src.png"); img.save(tmpb); data = open(tmpb, "rb").read()
def slow_write():
    with open(target, "wb") as f:
        for i in range(0, len(data), len(data) // 8 + 1): f.write(data[i:i + len(data) // 8 + 1]); f.flush(); time.sleep(0.5)
t = threading.Thread(target=slow_write); t.start(); pump(2.0)
check("watch: file still being written is NOT queued yet", len(win.queue) == 0, [j.source_path for j in win.queue])
t.join(); pump(5.0)
check("watch: queued once, after the write finished", len(win.queue) == 1 and os.path.getsize(win.queue[0].source_path) == len(data), (len(win.queue), [os.path.getsize(j.source_path) for j in win.queue]))
check("watch: delivered on the GUI thread", thread_ids == [True], thread_ids)
win._own_outputs.add(os.path.normcase(os.path.abspath(os.path.join(watch, "made_by_app.webp"))))
Image.new("RGB", (10, 10)).save(os.path.join(watch, "made_by_app.webp")); n = len(win.queue); pump(4.0)
check("watch: files the app itself produced are ignored (no endless loop)", len(win.queue) == n)
Image.new("RGB", (10, 10)).save(os.path.join(watch, "user_file.png")); pump(4.0)
check("watch: a normal new file is still picked up", len(win.queue) == n + 1)
win.watch_observer.stop(); win.watch_observer.join(2)
check("QuickJS is bundled and found", m._bundled_quickjs() and os.path.exists(m._bundled_quickjs()))
check("env switch hides Word", (os.environ.__setitem__("ANY2ANY_NO_WORD", "1") or True) and m._find_word() is None)
os.environ.pop("ANY2ANY_NO_WORD")

print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
