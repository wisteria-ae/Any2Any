import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if os.path.basename(os.path.dirname(os.path.abspath(__file__))) in ("network", "packaged") else os.path.dirname(os.path.abspath(__file__)))
from _common import ROOT, WORK, synthetic_clip
import os, sys, re, glob, subprocess, unicodedata
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
import pymupdf, docx, openpyxl
from PIL import Image
SCR = os.path.join(str(WORK))
O = os.path.join(SCR, "frozen2_out"); DIST = str(ROOT / "dist" / "Any2Any")
FP = os.path.join(DIST, "_internal", "vendor", "ffmpeg", "ffprobe.exe")
MiB = 1048576; fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{str(detail)[:300]}]" if (detail and not cond) else ""))
    if not cond: fails.append(name)
def files(case, ext=None):
    fs = [f for f in glob.glob(os.path.join(O, case, "**", "*"), recursive=True) if os.path.isfile(f)]
    return [f for f in fs if ext is None or f.lower().endswith(ext)]
def one(case, ext):
    fs = files(case, ext); return fs[0] if fs else None
def probe(p, sel="v:0", what="stream=codec_name,width,height,pix_fmt"):
    return subprocess.run([FP, "-v", "error", "-select_streams", sel, "-show_entries", what, "-of", "csv=p=0", p], capture_output=True, text=True).stdout.strip()
def pdf_text(p):
    with pymupdf.open(p) as d: return unicodedata.normalize("NFKC", "\n".join(pg.get_text() for pg in d)), d.page_count

p = one("docx_pdf_word", ".pdf"); t, n = pdf_text(p)
check("docx->pdf via Word (frozen): 3 pages + heading", n == 3 and "Quarterly Report" in t, (n, t[:60]))
p = one("docx_pdf_builtin", ".pdf"); t, n = pdf_text(p)
check("docx->pdf built-in renderer (frozen): pages + all text + Cyrillic", n >= 3 and "PAGE-TWO-MARKER" in t and "PAGE-THREE-MARKER" in t and "Привет" in t, (n, t[:80]))
md = open(one("docx_md", ".md"), encoding="utf-8").read()
check("docx->md (frozen): table with header + image extracted", "| Region | Q1 | Q2 |" in md and os.path.exists(os.path.join(O, "docx_md", "report_images", "image1.png")))
d = docx.Document(one("pdf_docx", ".docx")); txt = "\n".join(x.text for x in d.paragraphs)
check("pdf->docx (frozen): text + table", "Quarterly Report" in txt and len(d.tables) >= 1)
md = open(one("pdf_md", ".md"), encoding="utf-8").read()
check("pdf->md (frozen): merged-table repair active", "Col2" not in md and re.search(r"\|\s*Region\s*\|\s*Q1\s*\|\s*Q2\s*\|", md) is not None and "1. step one" in md and "2. step two" in md, md[:300])
ws = openpyxl.load_workbook(one("pdf_xlsx", ".xlsx")).worksheets[0]
check("pdf->xlsx (frozen): header row first", "Region" in [str(c.value) for c in ws[1]], [c.value for c in ws[1]])
fs = files("pdf_png_p2", ".png"); check("pdf->png pages=2 (frozen): only page 2", len(fs) == 1 and fs[0].endswith("_p002.png"), fs)
wx = openpyxl.load_workbook(one("csv_xlsx", ".xlsx")).worksheets[0]
check("csv->xlsx via pandas (frozen)", [c.value for c in wx[1]] == ["id", "name", "qty"] and wx.cell(2, 2).value == "Åsa", [c.value for c in wx[1]])
import json
rows = json.load(open(one("csv_json", ".json"), encoding="utf-8")); check("csv(BOM)->json (frozen): clean first key", list(rows[0])[0] == "id", list(rows[0]))
check("json list-of-lists->csv (frozen)", open(one("json_csv", ".csv"), encoding="utf-8").read().replace("\r\n", "\n") == "1,2\n3,4\n")
check("transparent png->jpg (frozen): white", Image.open(one("png_jpg", ".jpg")).getpixel((5, 5)) == (255, 255, 255))
i = probe(one("gif_mp4", ".mp4")).split(","); check("odd gif->mp4 (frozen): h264, even size, yuv420p", i[0] == "h264" and i[3] == "yuv420p" and int(i[1]) % 2 == 0 and int(i[2]) % 2 == 0, i)
check("mp4 subtitle track->srt (frozen)", "Hello" in open(one("sub_srt", ".srt"), encoding="utf-8").read())
check("wav->opus (frozen)", probe(one("wav_opus", ".opus"), "a:0", "stream=codec_name") == "opus")
g = one("zoo_gif_cap2", ".gif"); check("19 s gif fits a 2 MB cap (frozen)", g and os.path.getsize(g) <= 2 * MiB, g and os.path.getsize(g) / MiB)
b = one("big_mp4_cap20", ".mp4"); check("380 MB 1080p -> under 20 MB cap, h264 (frozen)", b and os.path.getsize(b) <= 20 * MiB and probe(b).startswith("h264"), b and os.path.getsize(b) / MiB)
r = files("yt_rick_mp3", ".mp3"); check("YouTube (needs JS challenges) -> full mp3 on a PC with no Deno/Node/ffmpeg (bundled QuickJS)", len(r) == 1 and os.path.getsize(r[0]) > 3 * MiB, [(os.path.basename(x), os.path.getsize(x)) for x in r])
x = files("x_multi_mp4", ".mp4"); check("2-video X post -> both videos kept (frozen)", len(x) == 2 and len({os.path.basename(f) for f in x}) == 2, [os.path.basename(f) for f in x])
f = files("fxtwitter_mp4", ".mp4"); check("fxtwitter link -> mp4 H.264 (frozen)", len(f) == 1 and probe(f[0]).startswith("h264"), f)
for case, want in (("ovr_good", "2099.01.01 (updated)"), ("ovr_bad", "(bundled)")):
    log = os.path.join(SCR, case, "Any2Any", "logs", "app.log"); line = [l for l in open(log, encoding="utf-8", errors="replace") if "yt-dlp 20" in l]
    got = line[-1].strip() if line else "NO LOG LINE"
    check(f"{case}: log shows '{want}'", want in got, got)
    check(f"{case}: conversion still works", len(files(case, ".mp3")) == 1)

# OCR (packaged exe)
import difflib
def sim(a, b):
    n = lambda s: re.sub(r"[ " + chr(9) + chr(10) + chr(13) + "]+", " ", s).strip().lower()
    return difflib.SequenceMatcher(None, n(a), n(b)).ratio()
T1 = "The quick brown fox jumps over" + chr(10) + "the lazy dog. Invoice #48213" + chr(10) + "Total due: $1,234.56 by 2026-10-31"
T2 = "Meeting notes: Tuesday 14:30" + chr(10) + "Action items: send report to Anna" + chr(10) + "Budget approved for Q4 2026"
rd = lambda p: open(p, encoding="utf-8").read()
check("OCR png->txt (frozen)", sim(rd(one("ocr_png_txt", ".txt")), T1) >= 0.97, rd(one("ocr_png_txt", ".txt")))
check("OCR small 16px text (frozen)", sim(rd(one("ocr_small_txt", ".txt")), T2) >= 0.9)
check("OCR sideways picture auto-rotated (frozen)", sim(rd(one("ocr_sideimg_txt", ".txt")), T1) >= 0.9)
t = rd(one("ocr_scan_txt", ".txt")); check("OCR scanned pdf->txt (frozen)", "Invoice #48213" in t and "Budget approved" in t, t[:120])
t = rd(one("ocr_mixed_txt", ".txt")); check("OCR mixed pdf->txt: real text + scans (frozen)", "Alpha Bravo Charlie" in t and "Budget approved" in t and "number 777" in t, t[:160])
t = rd(one("ocr_scan_md", ".md")); check("OCR scanned pdf->md (frozen)", "Invoice" in t and "Meeting notes" in t, t[:120])
dd = docx.Document(one("ocr_scan_docx", ".docx")); t = chr(10).join(p.text for p in dd.paragraphs); check("OCR scanned pdf->docx (frozen)", "Invoice #48213" in t and "Budget approved" in t, t[:120])
with pymupdf.open(one("ocr_scan_searchable", ".pdf")) as d: check("scanned pdf -> searchable pdf (frozen): whole lines selectable + searchable", "The quick brown fox jumps over" in d[0].get_text() and len(d[0].search_for("Invoice")) >= 1 and len(d[0].get_images()) >= 1, d[0].get_text()[:80])
with pymupdf.open(one("ocr_img_pdf_search", ".pdf")) as d: check("image -> searchable pdf option (frozen)", "Invoice #48213" in d[0].get_text().replace(chr(10), " "), d[0].get_text()[:80])
check("OCR with a language that isn't installed fails cleanly (frozen): no output files", len(files("ocr_badlang")) == 0, files("ocr_badlang"))

# packaging
top = os.path.join(DIST, "_internal"); vf = os.path.join(top, "vendor", "ffmpeg")
check("no duplicate ffmpeg DLLs at top level", not any(os.path.exists(os.path.join(top, n)) for n in ("avcodec-62.dll", "avfilter-11.dll", "avformat-62.dll")))
check("ffmpeg DLLs still sit next to ffmpeg.exe", all(os.path.exists(os.path.join(vf, n)) for n in ("ffmpeg.exe", "avcodec-62.dll", "avformat-62.dll")))
check("ffmpeg SDK files left out (include/lib/doc/presets)", not any(os.path.isdir(os.path.join(vf, d)) for d in ("include", "lib", "doc", "presets")))
check("QuickJS bundled", os.path.exists(os.path.join(top, "vendor", "quickjs", "qjs.exe")))
tot = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(DIST) for f in fs)
print(f"   dist size: {tot / MiB:.0f} MiB (was 856 MiB)")
print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
