import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if os.path.basename(os.path.dirname(os.path.abspath(__file__))) in ("network", "packaged") else os.path.dirname(os.path.abspath(__file__)))
from _common import ROOT, WORK, synthetic_clip
import os, sys, time, shutil, subprocess, threading
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(ROOT))
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
import Any2Any_desktop as m
from PySide6.QtWidgets import QApplication, QMessageBox

W = os.path.join(str(WORK), "regress")
shutil.rmtree(W, ignore_errors=True); os.makedirs(W)
fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{str(detail)[:300]}]" if (detail and not cond) else ""))
    if not cond: fails.append(name)

# deps dialog: real newlines, one component per line
app = QApplication.instance() or QApplication([]); cap = {}
QMessageBox.information = staticmethod(lambda p, t, text, *a, **k: cap.update(text=text))
win = m.MainWindow(); win.check_deps()
check("deps dialog: real newlines", "\\n" not in cap["text"] and cap["text"].count("\n") >= 12, cap["text"].count("\n"))

# NVENC: one valid flag set, no leftover monkey-patching
fl = m.encoder_preset_flags("nvenc")
check("nvenc flags valid and single", fl.count("-multipass") == 1 and fl[fl.index("-multipass") + 1] == "fullres")
src = open(str(ROOT / "Any2Any_desktop.py"), encoding="utf-8").read()
check("no FIXPACK / subprocess monkeypatch left", all(t not in src for t in ("FIXPACK", "_aa_normalize_ffmpeg_args", "_sanitize_ffmpeg_cmd", "subprocess.Popen = ")))

# subtitle burn-in with a drive-letter path
ff = m.ensure_ffmpeg()
clip = os.path.join(W, "clip.mp4"); srt = os.path.join(W, "clip.srt")
subprocess.run([ff, "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10:duration=2", "-c:v", "libx264", clip], capture_output=True)
open(srt, "w", encoding="utf-8").write("1\n00:00:00,000 --> 00:00:02,000\nHELLO\n")
r = m.SubtitlesBurnIn().convert(m.Job(source_path=clip, target_ext="mp4+burnsubs", options={"subs_path": srt}, output_dir=W), lambda p, s: None)
check("subtitle burn-in with C:\\ path succeeds", os.path.exists(r) and os.path.getsize(r) > 0, r)

# run_proc: normal completion + prompt cancel while the child is silent
class J: cancel_flag = False
t0 = time.time(); m.run_proc(["cmd", "/c", "echo hi"], lambda p, s: None, J()); check("run_proc completes", time.time() - t0 < 5)
j = J(); threading.Timer(0.5, lambda: setattr(j, "cancel_flag", True)).start(); t0 = time.time()
try: m.run_proc(["cmd", "/c", "ping -n 20 127.0.0.1 >NUL"], lambda p, s: None, j); check("run_proc cancel", False, "no exception")
except m.ConvertError as e: check("run_proc cancels promptly", str(e) == "Cancelled" and time.time() - t0 < 3, f"{e} {time.time()-t0:.1f}s")

# cleanup logging instead of silent failure
check("finalize cleanup logs failures", "failed to remove intermediate" in src)

print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
