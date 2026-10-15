import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if os.path.basename(os.path.dirname(os.path.abspath(__file__))) in ("network", "packaged") else os.path.dirname(os.path.abspath(__file__)))
from _common import ROOT, WORK, synthetic_clip
import os, sys, shutil, subprocess, json, tempfile, re
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
PY = sys.executable
APP = str(ROOT)
SP = __import__('sysconfig').get_paths()['purelib']
ROOT = os.path.join(str(WORK), "updater_test")
shutil.rmtree(ROOT, ignore_errors=True); os.makedirs(ROOT)
fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{str(detail)[:300]}]" if (detail and not cond) else ""))
    if not cond: fails.append(name)

def probe(appdata, code):
    env = dict(os.environ, APPDATA=appdata, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    r = subprocess.run([PY, "-c", "import sys; sys.path.insert(0, r'%s'); import Any2Any_desktop as m\n%s" % (APP, code)], capture_output=True, text=True, env=env, cwd=APP)
    out = [l for l in r.stdout.splitlines() if l.startswith("@@")]
    return (out[-1][2:] if out else ""), r.stderr[-400:]

# 1) no override installed -> bundled
a1 = os.path.join(ROOT, "a1"); os.makedirs(a1)
out, err = probe(a1, "print('@@' + m.YTDLP_SOURCE + '|' + m.YTDLP_VERSION)")
check("no update installed -> uses bundled yt-dlp", out.startswith("bundled|"), out + err)
bundled_ver = out.split("|")[1] if "|" in out else "?"

# 2) real download from PyPI into an isolated folder
a2 = os.path.join(ROOT, "a2"); dest = os.path.join(a2, "Any2Any", "ytdlp")
out, err = probe(a2, f"from pathlib import Path\nr = m.install_ytdlp_update(Path(r'{dest}'), force=True)\nprint('@@' + '|'.join(map(str, r)))")
print("   install result:", out, err if not out else "")
parts = out.split("|")
check("PyPI download + checksum + unpack succeeded", len(parts) == 3 and parts[2] == "True", out + err)
check("unpacked yt_dlp + yt_dlp_ejs with versions", os.path.exists(os.path.join(dest, "yt_dlp", "version.py")) and os.path.exists(os.path.join(dest, "yt_dlp_ejs", "_version.py")))
check("no leftovers (.old / temp stage)", not os.path.exists(dest + ".old"))
# the downloaded copy is a working yt-dlp on its own
r = subprocess.run([PY, "-c", f"import sys; sys.path.insert(0, r'{dest}'); import yt_dlp; y = yt_dlp.YoutubeDL({{'quiet': True}}); print('@@' + yt_dlp.version.__version__ + '|' + type(y.get_info_extractor('Youtube')).__name__)"], capture_output=True, text=True)
o2 = [l for l in r.stdout.splitlines() if l.startswith("@@")]
check("downloaded yt-dlp imports and builds the YouTube extractor", bool(o2) and o2[-1].endswith("|YoutubeIE"), r.stdout[-200:] + r.stderr[-300:])
print("   downloaded version:", o2[-1][2:].split("|")[0] if o2 else "?", "| bundled:", bundled_ver)
# up-to-date path (no force): returns changed=False when nothing newer exists
out, err = probe(a2, f"from pathlib import Path\nr = m.install_ytdlp_update(Path(r'{dest}'))\nprint('@@' + '|'.join(map(str, r)))")
print("   second (non-forced) check:", out)
check("non-forced check works", out.count("|") == 2, out + err)

# 3) a NEWER copy on disk wins over the bundled one
a3 = os.path.join(ROOT, "a3"); d3 = os.path.join(a3, "Any2Any", "ytdlp"); os.makedirs(d3)
for pkg in ("yt_dlp", "yt_dlp_ejs"): shutil.copytree(os.path.join(SP, pkg), os.path.join(d3, pkg), ignore=shutil.ignore_patterns("__pycache__"))
vp = os.path.join(d3, "yt_dlp", "version.py"); t = open(vp, encoding="utf-8").read(); open(vp, "w", encoding="utf-8").write(re.sub(r"__version__ = '[^']+'", "__version__ = '2099.01.01'", t))
out, err = probe(a3, "print('@@' + m.YTDLP_SOURCE + '|' + m.YTDLP_VERSION + '|' + m.ytdlp.__file__)")
check("newer installed copy is used (source=updated, version=2099.01.01)", out.startswith("updated|2099.01.01|") and a3 in out, out + err)
out, err = probe(a3, "y = m.ytdlp.YoutubeDL({'quiet': True}); print('@@' + type(y.get_info_extractor('Youtube')).__name__)")
check("the updated copy actually works", out == "YoutubeIE", out + err)

# 4) an OLDER copy on disk is ignored (e.g. after the app itself ships a newer yt-dlp)
a4 = os.path.join(ROOT, "a4"); d4 = os.path.join(a4, "Any2Any", "ytdlp"); shutil.copytree(d3, d4)
vp = os.path.join(d4, "yt_dlp", "version.py"); t = open(vp, encoding="utf-8").read(); open(vp, "w", encoding="utf-8").write(re.sub(r"__version__ = '[^']+'", "__version__ = '2000.01.01'", t))
out, err = probe(a4, "print('@@' + m.YTDLP_SOURCE + '|' + m.YTDLP_VERSION)")
check("older installed copy is ignored", out.startswith("bundled|") and "2000" not in out, out + err)

# 5) a BROKEN newer copy falls back to bundled instead of breaking the app
a5 = os.path.join(ROOT, "a5"); d5 = os.path.join(a5, "Any2Any", "ytdlp"); shutil.copytree(d3, d5)
open(os.path.join(d5, "yt_dlp", "__init__.py"), "w", encoding="utf-8").write("this is not python ((((\n")
out, err = probe(a5, "y = m.ytdlp.YoutubeDL({'quiet': True}); print('@@' + m.YTDLP_SOURCE + '|' + m.YTDLP_VERSION + '|' + type(y.get_info_extractor('Youtube')).__name__)")
check("broken update -> falls back to bundled, app still works", out.startswith("bundled|") and out.endswith("|YoutubeIE") and "2099" not in out, out + err)

print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
