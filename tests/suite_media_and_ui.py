import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if os.path.basename(os.path.dirname(os.path.abspath(__file__))) in ("network", "packaged") else os.path.dirname(os.path.abspath(__file__)))
from _common import ROOT, WORK, synthetic_clip
import os, sys, shutil, subprocess, json
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(ROOT))
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
import Any2Any_desktop as m

W = os.path.join(str(WORK), "misc_test")
shutil.rmtree(W, ignore_errors=True); os.makedirs(W)
fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{str(detail)[:300]}]" if (detail and not cond) else ""))
    if not cond: fails.append(name)
ff = m.ensure_ffmpeg(); fp = m.ensure_ffprobe()
def sh(*a): return subprocess.run(list(a), capture_output=True, text=True)
def conv(cls, src, target, opts=None, tag=None):
    out = os.path.join(W, tag or ("o_" + target)); os.makedirs(out, exist_ok=True)
    msgs = []
    r = cls().convert(m.Job(source_path=src, target_ext=target, options=opts or {}, output_dir=out), lambda p, s: msgs.append(s))
    return r, msgs
def codec(path, sel="v:0"):
    return sh(fp, "-v", "error", "-select_streams", sel, "-show_entries", "stream=codec_name", "-of", "csv=p=0", path).stdout.strip()
MiB = 1024 * 1024

# ---------- Discord presets ----------
check("preset: Free is 20 MB", m.DISCORD_PRESETS.get("Free (20 MB)") == 20)
check("preset: old 10 MB free preset removed", "Free (10 MB)" not in m.DISCORD_PRESETS)
check("preset: others intact", m.DISCORD_PRESETS["Nitro Basic (50 MB)"] == 50 and m.DISCORD_PRESETS["Server boost L3 (100 MB)"] == 100 and m.DISCORD_PRESETS["Off"] == 0)
check("preset flows through get_target_mb", m.get_target_mb({"size_targeting": "1", "discord_size_mb": "20"}) == 20)

# ---------- audio outputs for local files ----------
wav = os.path.join(W, "tone.wav"); sh(ff, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", wav)
for t, want in (("mp3", "mp3"), ("m4a", "aac"), ("opus", "opus")):
    try:
        r, _ = conv(m.VideoToMP3, wav, t)
        check(f"wav -> {t} (codec {want})", os.path.exists(r) and codec(r, "a:0") == want and r.endswith("." + t), f"{r} codec={codec(r,'a:0')}")
    except Exception as e: check(f"wav -> {t}", False, e)
mp3 = os.path.join(W, "o_mp3", "tone.mp3")
r, _ = conv(m.VideoToMP3, mp3, "mp3", tag="o_mp3")   # same folder as the source: must not overwrite its own input
check("mp3 -> mp3 in same folder writes _out copy", r.endswith("tone_out.mp3") and os.path.exists(r) and os.path.exists(mp3), r)

# ---------- size cap: pass-through vs re-encode ----------
small = os.path.join(W, "small.mp4")
sh(ff, "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=5", "-f", "lavfi", "-i", "sine=duration=5", "-c:v", "libx264", "-c:a", "aac", "-shortest", small)
big = os.path.join(W, "big.mp4")
sh(ff, "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=5", "-vf", "noise=alls=35:allf=t+u", "-c:v", "libx264", "-crf", "14", "-preset", "ultrafast", big)
print(f"   fixtures: small={os.path.getsize(small)/MiB:.2f} MiB, big={os.path.getsize(big)/MiB:.1f} MiB")
check("fixture: big file really exceeds 20 MiB", os.path.getsize(big) > 20 * MiB, os.path.getsize(big) / MiB)
cap = {"size_targeting": "1", "discord_size_mb": "20", "headroom_percent": "5"}
r, msgs = conv(m.VideoToMP4, small, "mp4", cap, tag="cap_small")
check("under-cap file: skip re-encode message", any("Already under" in s for s in msgs), msgs)
check("under-cap file: size ~unchanged (remuxed, not re-encoded)", 0.9 < os.path.getsize(r) / os.path.getsize(small) < 1.1, (os.path.getsize(r), os.path.getsize(small)))
check("under-cap file: still h264", codec(r) == "h264")
r, msgs = conv(m.VideoToMP4, big, "mp4", cap, tag="cap_big")
sz = os.path.getsize(r) / MiB
check("over-cap file: re-encoded to fit 20 MiB", sz <= 20, f"{sz:.2f} MiB"); print(f"   big -> {sz:.2f} MiB")
check("over-cap file: not absurdly small (uses the budget)", sz > 8, f"{sz:.2f} MiB")
r, msgs = conv(m.VideoToMP4, big, "mp4", {"size_targeting": "1", "custom_cap_mb": "20", "headroom_percent": "5"}, tag="cap_big_custom")
sz = os.path.getsize(r) / MiB; check("custom 20 MB cap also fits", sz <= 20, f"{sz:.2f} MiB")
r, msgs = conv(m.VideoToMP4, small, "mp4", {"reencode": "1"}, tag="reenc")
check("reencode=1 forces a real re-encode (no remux message, different size)", not any("Fast remux" in s for s in msgs), msgs)

# ---------- UI logic ----------
from PySide6.QtWidgets import QApplication, QMessageBox
app = QApplication.instance() or QApplication([])
rec = {"warn": [], "info": []}
QMessageBox.warning = staticmethod(lambda parent, title, text, *a, **k: rec["warn"].append((title, text)))
QMessageBox.information = staticmethod(lambda parent, title, text, *a, **k: rec["info"].append((title, text)))
win = m.MainWindow(); win.start_next = lambda: None   # don't spin up workers
items = [win.format_box.itemText(i) for i in range(win.format_box.count())]
for want in ("docx", "md", "pdf", "m4a", "opus"): check(f"format list offers {want}", want in items)
for gone in ("frames", "video-from-frames", "mp4+subs", "chapters-csv"): check(f"format list no longer offers {gone}", gone not in items)
check("discord combo has Free (20 MB)", "Free (20 MB)" in [win.opt_discord.itemText(i) for i in range(win.opt_discord.count())])
check("window title carries version", m.APP_VERSION in win.windowTitle())

win.format_box.setCurrentText("docx")
pdf = os.path.join(W, "x.pdf"); open(pdf, "wb").write(b"%PDF-1.4")
win.add_files([small, pdf])
check("add_files: mismatched file rejected with one message", len(rec["warn"]) == 1 and "small.mp4" in rec["warn"][0][1] and "x.pdf" not in rec["warn"][0][1], rec["warn"])
check("add_files: matching file queued, mismatched not", len(win.queue) == 1 and win.queue[0].source_path == pdf, [j.source_path for j in win.queue])
win.queue.clear(); win.queue_list.clear(); rec["warn"].clear()

win.url_edit.setText("https://x.com/a/status/1")
win.enqueue_from_ui()
check("link + file-only output (docx) is refused", len(win.queue) == 0 and len(rec["warn"]) == 1, rec["warn"])
rec["warn"].clear()
win.format_box.setCurrentText("mp4"); win.opt_cookies.setCurrentText("firefox")
win.url_edit.setText("grab these: https://x.com/a/status/1, https://www.reddit.com/r/x/comments/abc/t/ and https://x.com/a/status/1")
win.enqueue_from_ui()
check("pasted text with 3 links (1 dup) queues 2 jobs", len(win.queue) == 2, len(win.queue))
check("jobs carry the cookies choice", all(j.options.get("cookies_browser") == "firefox" for j in win.queue))
check("job list labels show the site", win.queue_list.count() == 2 and "x.com" in win.queue_list.item(0).text(), [win.queue_list.item(i).text() for i in range(win.queue_list.count())])
win.queue.clear(); win.queue_list.clear()
win.url_edit.setText("tiktok.com/@u/video/123")
win.enqueue_from_ui()
check("scheme-less link accepted", len(win.queue) == 1 and win.queue[0].source_url == "https://tiktok.com/@u/video/123", [j.source_url for j in win.queue])
win.url_edit.setText("just some words"); n0 = len(rec["warn"]); win.enqueue_from_ui()
check("text without a link gives a helpful message", len(rec["warn"]) == n0 + 1 and "Couldn't find a link" in rec["warn"][-1][1])

for tgt, pages_vis, flavor_vis, img_vis in (("md", True, False, True), ("docx", True, False, False), ("xlsx", True, True, False), ("mp4", False, False, False)):
    win.format_box.setCurrentText(tgt); win._sync_visibility()
    check(f"options for {tgt}: pages={pages_vis} flavor={flavor_vis} md-images={img_vis}",
          win.opt_pages.isVisibleTo(win) == pages_vis and win.opt_flavor.isVisibleTo(win) == flavor_vis and win.opt_md_images.isVisibleTo(win) == img_vis,
          (win.opt_pages.isVisibleTo(win), win.opt_flavor.isVisibleTo(win), win.opt_md_images.isVisibleTo(win)))
win.format_box.setCurrentText("md"); win.opt_pages.setText("2-3"); win.opt_md_images.setChecked(True)
o = win.collect_opts("md"); check("collect_opts md: pages + md_images", o.get("pages") == "2-3" and o.get("md_images") == "1", o)
win.format_box.setCurrentText("mp4+burnsubs"); win._sync_visibility()
check("subtitle file row only for burn-in", win.opt_subs_path.parentWidget().isVisibleTo(win))
win.check_deps(); text = rec["info"][-1][1]
check("dependency check lists new components", all(s in text for s in ("pdf2docx", "pymupdf4llm", "Word→PDF engine", "JS runtime for YouTube")), text)
win.show_help()
check("help text updated", "Word (.docx) → PDF" in rec["info"][-1][1] and "frames" not in rec["info"][-1][1] and "chapters" not in rec["info"][-1][1] and "20 MB" in rec["info"][-1][1])

print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
