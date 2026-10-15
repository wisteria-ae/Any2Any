import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if os.path.basename(os.path.dirname(os.path.abspath(__file__))) in ("network", "packaged") else os.path.dirname(os.path.abspath(__file__)))
from _common import ROOT, WORK, synthetic_clip
import os, sys, shutil, difflib, json, re, time
SCR = os.path.join(str(WORK))
W = os.path.join(SCR, "ocr_test"); shutil.rmtree(W, ignore_errors=True); os.makedirs(W)
os.environ.update(QT_QPA_PLATFORM="offscreen", APPDATA=os.path.join(W, "appdata"))
sys.path.insert(0, str(ROOT))
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
import Any2Any_desktop as m
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import fitz

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{str(detail)[:300]}]" if (detail and not cond) else ""))
    if not cond: fails.append(name)
def sim(a, b):
    n = lambda s: re.sub(r"\s+", " ", s).strip().lower()
    return difflib.SequenceMatcher(None, n(a), n(b)).ratio()
def conv(cls, src, target, opts=None, tag=None, cancel=False):
    out = os.path.join(W, tag or f"{cls.__name__}_{target}"); os.makedirs(out, exist_ok=True); msgs = []
    j = m.Job(source_path=src, target_ext=target, options=opts or {}, output_dir=out); j.cancel_flag = cancel
    return cls().convert(j, lambda p, s: msgs.append(s)), msgs, out
def raises(fn, needle):
    try: fn(); return False, "no exception"
    except m.ConvertError as e: return needle.lower() in str(e).lower(), str(e)[:220]
    except Exception as e: return False, f"{type(e).__name__}: {e}"
ARIAL = r"C:\Windows\Fonts\arial.ttf"
def text_image(lines, size=52, bg="white", fg="black", w=None, pad=30, gap=1.9, mode="RGB"):
    f = ImageFont.truetype(ARIAL, size); w = w or int(max(f.getlength(l) for l in lines) + 2*pad)
    img = Image.new(mode, (w, int(pad*2 + len(lines)*size*gap)), bg); d = ImageDraw.Draw(img)
    for i, l in enumerate(lines): d.text((pad, pad + i*size*gap), l, fill=fg, font=f)
    return img

T1 = ["The quick brown fox jumps over", "the lazy dog. Invoice #48213", "Total due: $1,234.56 by 2026-10-31"]
T2 = ["Meeting notes: Tuesday 14:30", "Action items: send report to Anna", "Budget approved for Q4 2026"]
T3 = ["Chapter Three begins here", "with a completely different page", "of scanned content, number 777"]

# ---- registry & language list ----
check("registry: image->txt, pdf->txt, pdf->pdf (searchable), gif->txt", all(m.find_conv(a, b) for a, b in (("png", "txt"), ("jpg", "txt"), ("heic", "txt"), ("pdf", "txt"), ("pdf", "pdf"), ("gif", "txt"))))
check("txt is an offered output", "txt" in m.SUPPORTED_OUTPUTS and "txt" in m.OUTPUT_CATEGORIES["Documents"] and "txt" in m.FORMAT_HELP)
langs = m.ocr_languages(); print("   installed OCR languages:", langs)
check("OCR available + at least one language", m.HAVE_OCR and len(langs) >= 1)

# ---- image -> text: accuracy across conditions ----
clean = text_image(T1); clean.save(os.path.join(W, "clean.png"))
r, msgs, _ = conv(m.ImageToText, os.path.join(W, "clean.png"), "txt"); got = open(r, encoding="utf-8").read()
print("   clean ->", repr(got.strip()))
check("clean printed text: >=97% match", sim(got, "\n".join(T1)) >= 0.97, sim(got, "\n".join(T1)))
check("output is a UTF-8 .txt with the right name", r.endswith("clean.txt"))

small = text_image(T2, size=16, w=420, pad=10); small.save(os.path.join(W, "small.png"))
r, _, _ = conv(m.ImageToText, os.path.join(W, "small.png"), "txt"); got = open(r, encoding="utf-8").read(); s_up = sim(got, "\n".join(T2))
orig_min = m.OCR_MIN_SIDE; m.OCR_MIN_SIDE = 0
r2, _, _ = conv(m.ImageToText, os.path.join(W, "small.png"), "txt", tag="noup"); s_no = sim(open(r2, encoding="utf-8").read(), "\n".join(T2)); m.OCR_MIN_SIDE = orig_min
print(f"   small 16px text: {s_up:.2f} with auto-enlarge vs {s_no:.2f} without")
check("small text (16px) reads well thanks to auto-enlarge", s_up >= 0.9 and s_up >= s_no, (s_up, s_no))

blur = text_image(T1).filter(ImageFilter.GaussianBlur(1.3)); blur.save(os.path.join(W, "blur.png"))
r, _, _ = conv(m.ImageToText, os.path.join(W, "blur.png"), "txt"); sb = sim(open(r, encoding="utf-8").read(), "\n".join(T1)); print(f"   slightly blurred: {sb:.2f}")
check("slightly blurred text still reads (>=85%)", sb >= 0.85, sb)

inv = text_image(T1, bg="black", fg="white"); inv.save(os.path.join(W, "dark.png"))
r, _, _ = conv(m.ImageToText, os.path.join(W, "dark.png"), "txt"); sd = sim(open(r, encoding="utf-8").read(), "\n".join(T1)); print(f"   white-on-black: {sd:.2f}")
check("dark-mode screenshot (white on black) reads (>=85%)", sd >= 0.85, sd)

tr = Image.new("RGBA", clean.size, (0, 0, 0, 0)); d = ImageDraw.Draw(tr); f = ImageFont.truetype(ARIAL, 52)
for i, l in enumerate(T1): d.text((30, 30 + i*100), l, fill=(0, 0, 0, 255), font=f)
tr.save(os.path.join(W, "transparent.png"))
r, _, _ = conv(m.ImageToText, os.path.join(W, "transparent.png"), "txt"); st = sim(open(r, encoding="utf-8").read(), "\n".join(T1))
check("transparent PNG (black text, no background) reads (>=95%)", st >= 0.95, st)

ph = text_image(T1).rotate(90, expand=True); ex = Image.Exif(); ex[0x0112] = 6; ph.save(os.path.join(W, "phone.jpg"), exif=ex, quality=95)   # stored sideways + EXIF says rotate
r, _, _ = conv(m.ImageToText, os.path.join(W, "phone.jpg"), "txt"); sp = sim(open(r, encoding="utf-8").read(), "\n".join(T1))
check("sideways phone photo (EXIF rotation) reads (>=90%)", sp >= 0.9, sp)

Image.new("RGB", (400, 300), (200, 120, 40)).save(os.path.join(W, "blank.png"))
ok, msg = raises(lambda: conv(m.ImageToText, os.path.join(W, "blank.png"), "txt", tag="blank"), "No text was found"); check("picture with no text -> clear message", ok, msg)

huge = Image.new("RGB", (12500, 400), "white"); ImageDraw.Draw(huge).text((40, 60), "HUGE IMAGE TEST LINE 4242", fill="black", font=ImageFont.truetype(ARIAL, 120)); huge.save(os.path.join(W, "huge.png"))
r, _, _ = conv(m.ImageToText, os.path.join(W, "huge.png"), "txt", tag="huge"); gh = open(r, encoding="utf-8").read()
check("12,500 px wide image (over the engine limit) is shrunk and read", "4242" in gh, gh)

gif = text_image(T1); gif.save(os.path.join(W, "frame.gif"))
r, _, _ = conv(m.ImageToText, os.path.join(W, "frame.gif"), "txt", tag="gif"); check("GIF input reads", sim(open(r, encoding="utf-8").read(), "\n".join(T1)) >= 0.95)

# ---- language handling ----
r, _, _ = conv(m.ImageToText, os.path.join(W, "clean.png"), "txt", {"ocr_lang": "en-US"}, tag="en"); check("explicit language en-US works", sim(open(r, encoding="utf-8").read(), "\n".join(T1)) >= 0.97)
ok, msg = raises(lambda: conv(m.ImageToText, os.path.join(W, "clean.png"), "txt", {"ocr_lang": "ja-JP"}, tag="ja"), "isn't installed"); check("language that isn't installed -> says what IS installed + how to add", ok and "en-US" in msg and "Settings" in msg, msg)

# ---- scanned PDFs ----
def scan_pdf(path, imgs, rotate=0):
    with fitz.open() as d:
        for im in imgs:
            tmp = os.path.join(W, "_p.png"); im.save(tmp); pg = d.new_page(width=612, height=792)
            r = fitz.Rect(36, 36, 576, 36 + 540*im.height/im.width); pg.insert_image(r, filename=tmp)
            if rotate: pg.set_rotation(rotate)
        d.save(path)
scan = os.path.join(W, "scan.pdf"); scan_pdf(scan, [text_image(T1), text_image(T2)])
with fitz.open(scan) as d: check("fixture is image-only (no text layer)", all(len(p.get_text().strip()) == 0 for p in d))
r, msgs, _ = conv(m.PDFToText, scan, "txt"); t = open(r, encoding="utf-8").read()
check("scanned PDF -> text: both pages recognized", sim(t, "\n".join(T1 + T2)) >= 0.9 and "Invoice #48213" in t and "Budget approved" in t, t[:200])
check("...and it says OCR was used", any("scanned page" in s for s in msgs), msgs[-1])
r, _, _ = conv(m.PDFToText, scan, "txt", {"pages": "2"}, tag="p2"); t2 = open(r, encoding="utf-8").read()
check("page range respected with OCR", "Budget approved" in t2 and "Invoice" not in t2, t2)

# mixed: page 1 real text, pages 2-3 scans
mixed = os.path.join(W, "mixed.pdf")
with fitz.open() as d:
    pg = d.new_page(width=612, height=792); pg.insert_text((72, 100), "This page has a real text layer: Alpha Bravo Charlie.", fontsize=14)
    for im in (text_image(T2), text_image(T3)):
        tmp = os.path.join(W, "_q.png"); im.save(tmp); p2 = d.new_page(width=612, height=792); p2.insert_image(fitz.Rect(36, 36, 576, 36 + 540*im.height/im.width), filename=tmp)
    d.save(mixed)
r, msgs, _ = conv(m.PDFToText, mixed, "txt", tag="mix"); t = open(r, encoding="utf-8").read()
check("mixed PDF -> text: real text AND scanned pages", "Alpha Bravo Charlie" in t and "Budget approved" in t and "number 777" in t, t)
check("...only the 2 scanned pages needed OCR", "(2 scanned page" in msgs[-1], msgs[-1])
r, msgs, _ = conv(m.PDFToMarkdown, mixed, "md", tag="mixmd"); t = open(r, encoding="utf-8").read()
check("PDF -> Markdown: scanned pages read with OCR, text page kept", "Alpha Bravo" in t and "Budget approved" in t and "number 777" in t, t)
r, msgs, _ = conv(m.PDFToMarkdown, scan, "md", tag="scanmd"); t = open(r, encoding="utf-8").read()
check("fully scanned PDF -> Markdown no longer errors", "Invoice" in t and "Meeting notes" in t, t)

import docx
r, msgs, _ = conv(m.PDFToWord, scan, "docx", tag="scandocx"); dd = docx.Document(r); txt = "\n".join(p.text for p in dd.paragraphs)
check("fully scanned PDF -> Word: editable text recognized", "Invoice #48213" in txt and "Budget approved" in txt, txt[:200])
check("...with a page break between the pages", "w:br" in dd.element.xml and 'w:type="page"' in dd.element.xml)

# ---- searchable PDF ----
before = os.path.join(W, "before.png")
with fitz.open(scan) as d: d[0].get_pixmap(dpi=100).save(before)
r, msgs, _ = conv(m.PDFMakeSearchable, scan, "pdf", tag="srch")
with fitz.open(r) as d:
    t0 = d[0].get_text(); t1 = d[1].get_text()
    check("searchable PDF: text is selectable, and copies as whole LINES (not one word per line)", "The quick brown fox jumps over" in t0 and "Meeting notes: Tuesday 14:30" in t1, (t0[:80], t1[:80]))
    hits = d[0].search_for("Invoice"); check("searchable PDF: search finds the word", len(hits) >= 1, hits)
    d[0].get_pixmap(dpi=100).save(os.path.join(W, "after.png"))
    check("searchable PDF: the page still contains the picture", len(d[0].get_images()) >= 1)
a, b = Image.open(before).convert("L"), Image.open(os.path.join(W, "after.png")).convert("L")
from PIL import ImageChops
diff = sum(ImageChops.difference(a, b).getdata()) / (a.width * a.height)
check("searchable PDF: looks identical (text layer is invisible)", diff < 0.5, f"mean pixel difference {diff:.3f}")
# the invisible words sit where the printed words are
with fitz.open(r) as d:
    hit = d[0].search_for("Invoice")[0]; ocr_inv = m.ocr_image(m._render_pdf_page(d, 0), "auto")
    w = next(w for l in ocr_inv.lines for w in l.words if w.text.lower().startswith("invoice"))
    s = 72/200; cx_hit, cy_hit = (hit.x0 + hit.x1)/2, (hit.y0 + hit.y1)/2; cx_w, cy_w = (w.x + w.w/2)*s, (w.y + w.h/2)*s
    check("searchable PDF: hidden words line up with the printed ones (within 8 pt)", abs(cx_hit - cx_w) < 8 and abs(cy_hit - cy_w) < 8, (cx_hit, cx_w, cy_hit, cy_w))
ok, msg = raises(lambda: conv(m.PDFMakeSearchable, r, "pdf", tag="again"), "already has selectable text"); check("PDF that's already searchable -> clear message", ok, msg)
# (a) content stored sideways + /Rotate flag so it DISPLAYS upright: layer must be derotated correctly
rotf = os.path.join(W, "rotflag.pdf")
with fitz.open() as d:
    side = text_image(T1).rotate(90, expand=True); side.save(os.path.join(W, "_side.png"))     # text reads bottom-to-top
    pg = d.new_page(width=612, height=792); sc = 700 / side.height                                # portrait page, picture scaled to fit
    pg.insert_image(fitz.Rect(36, 36, 36 + side.width*sc, 36 + side.height*sc), filename=os.path.join(W, "_side.png"))
    pg.set_rotation(90); d.save(rotf)                                                              # viewer turns it clockwise -> upright
with fitz.open(rotf) as d: viewed = m._render_pdf_page(d, 0)
upright_ok = sim(m.ocr_image(viewed, "auto").as_text(), "\n".join(T1))
print(f"   (fixture check: the rotate-flag page displays upright and reads at {upright_ok:.2f})")
r, _, _ = conv(m.PDFMakeSearchable, rotf, "pdf", tag="rotflag")
with fitz.open(r) as d:
    pg = d[0]; hits = pg.search_for("Invoice")
    check("page with a rotate flag: text layer is searchable", len(hits) >= 1, pg.get_text()[:80])
    if hits:
        view_hit = hits[0] * pg.rotation_matrix                                   # unrotated page space -> what you see
        viewed = m._render_pdf_page(d, 0); w = next(w for l in m.ocr_image(viewed, "auto").lines for w in l.words if w.text.lower().startswith("invoice"))
        k = 72/200; ccx, ccy = (view_hit.x0 + view_hit.x1)/2, (view_hit.y0 + view_hit.y1)/2
        check("...and lines up with the printed word as displayed (within 8 pt)", abs(ccx - (w.x + w.w/2)*k) < 8 and abs(ccy - (w.y + w.h/2)*k) < 8, (ccx, (w.x + w.w/2)*k, ccy, (w.y + w.h/2)*k))
# (b) a page that is genuinely sideways on screen: text outputs auto-detect it
sidepdf = os.path.join(W, "sideways.pdf"); scan_pdf(sidepdf, [text_image(T1)], rotate=90)
r, msgs, _ = conv(m.PDFToText, sidepdf, "txt", tag="sidetxt"); t = open(r, encoding="utf-8").read()
check("a sideways scanned page is detected and read upright (text output)", sim(t, "\n".join(T1)) >= 0.9, t[:120])
sideimg = text_image(T1).rotate(90, expand=True); sideimg.save(os.path.join(W, "sideimg.png"))
r, _, _ = conv(m.ImageToText, os.path.join(W, "sideimg.png"), "txt", tag="sideimg"); t = open(r, encoding="utf-8").read()
check("a sideways picture (no EXIF flag) is detected and read", sim(t, "\n".join(T1)) >= 0.9, t[:120])
# images -> searchable pdf
r, _, _ = conv(m.ImagesToPDF, os.path.join(W, "clean.png"), "pdf", {"ocr_pdf": "1"}, tag="i2p")
with fitz.open(r) as d: check("Images -> PDF with 'searchable' option: text layer present", "Invoice #48213" in d[0].get_text().replace("\n", " ") and len(d[0].get_images()) == 1, d[0].get_text()[:80])
r, _, _ = conv(m.ImagesToPDF, os.path.join(W, "clean.png"), "pdf", tag="i2p0")
with fitz.open(r) as d: check("...and without the option nothing changes (no text layer)", len(d[0].get_text().strip()) == 0)

# ---- cancel / worker thread / UI ----
ok, msg = raises(lambda: conv(m.PDFToText, scan, "txt", tag="cx", cancel=True), "Cancelled"); check("OCR conversion can be cancelled", ok, msg)
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QEventLoop, QTimer
app = QApplication.instance() or QApplication([])
res = {}
job = m.Job(source_path=os.path.join(W, "clean.png"), target_ext="txt", options={}, output_dir=os.path.join(W, "worker")); os.makedirs(job.output_dir)
wk = m.Worker(job); loop = QEventLoop()
wk.finished_ok.connect(lambda o: (res.update(ok=o), loop.quit())); wk.failed.connect(lambda e: (res.update(err=e), loop.quit())); QTimer.singleShot(30000, loop.quit)
wk.start(); loop.exec()
check("OCR runs inside the app's real worker thread (QThread)", "ok" in res and sim(open(res["ok"], encoding="utf-8").read(), "\n".join(T1)) >= 0.97, res)

rec = {"info": []}; QMessageBox.information = staticmethod(lambda p, t, tx, *a, **k: rec["info"].append(tx))
win = m.MainWindow(); win.start_next = lambda: None
for tgt, lang_vis, srch_vis in (("txt", True, False), ("pdf", True, True), ("md", True, False), ("docx", True, False), ("mp4", False, False), ("png", False, False)):
    win.format_box.setCurrentText(tgt); win._sync_visibility()
    check(f"options for {tgt}: language row={lang_vis}, searchable box={srch_vis}", win.opt_ocr_lang_win.isVisibleTo(win) == lang_vis and win.opt_pdf_searchable.isVisibleTo(win) == srch_vis)
items = [win.opt_ocr_lang_win.itemData(i) for i in range(win.opt_ocr_lang_win.count())]
check("language dropdown: auto + installed languages", items[0] == "auto" and "en-US" in items, items)
win.format_box.setCurrentText("pdf"); win.opt_pdf_searchable.setChecked(True); win.opt_ocr_lang_win.setCurrentIndex(items.index("en-US"))
o = win.collect_opts("pdf"); check("collect_opts passes ocr_pdf + ocr_lang", o.get("ocr_pdf") == "1" and o.get("ocr_lang") == "en-US", o)
win.format_box.setCurrentText("txt"); win.opt_ocr_lang_win.setCurrentIndex(0); check("'auto' adds no option", "ocr_lang" not in win.collect_opts("txt"))
win.queue.clear(); win.add_files([os.path.join(W, "clean.png")]); check("GUI accepts a picture for txt", len(win.queue) == 1)
win.check_deps(); check("Dependency Check lists OCR + languages", any("OCR (Windows): OK, languages: en-US" in t for t in rec["info"]), rec["info"][-1][:400])
win._attach_info_tooltips()      # the app does this right after the window is shown
check("tooltips are set on the new options", "Optical character recognition" in win.opt_ocr_lang_win.toolTip() and "searched" in win.opt_pdf_searchable.toolTip().lower(), (win.opt_ocr_lang_win.toolTip()[:40], win.opt_pdf_searchable.toolTip()[:40]))
win.show_help(); check("help text mentions OCR", "OCR" in rec["info"][-1])

print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
