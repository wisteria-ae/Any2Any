import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if os.path.basename(os.path.dirname(os.path.abspath(__file__))) in ("network", "packaged") else os.path.dirname(os.path.abspath(__file__)))
from _common import ROOT, WORK, synthetic_clip
import os, sys, shutil, subprocess
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(ROOT))
import Any2Any_desktop as m
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

OUT = os.path.join(str(WORK), "url_out3")
shutil.rmtree(OUT, ignore_errors=True); os.makedirs(OUT)

CASES = [
 ("X/Twitter",        "https://twitter.com/captainamerica/status/719944021058060289", ["mp4", "mp3"]),
 ("X via x.com",      "https://x.com/LisPower1/status/1001551623938805763?s=20",     ["mp4"]),
 ("X via fxtwitter",  "https://fxtwitter.com/LisPower1/status/1001551623938805763",  ["mp4"]),
 ("TikTok",           "https://www.tiktok.com/@pokemonlife22/video/7059698374567611694", ["mp4", "mp3"]),
 ("TikTok emoji title","https://www.tiktok.com/@barudakhb_/video/6984138651336838402", ["mp4"]),
 ("Streamable",       "https://streamable.com/moo", ["mp4"]),
 ("Twitch clip",      "https://clips.twitch.tv/FaintLightGullWholeWheat", ["mp4"]),
 ("Bluesky",          "https://bsky.app/profile/bsky.app/post/3l3vgf77uco2g", ["mp4"]),
]
bad = 0
for label, url, targets in CASES:
    for t in targets:
        sub = os.path.join(OUT, f"{label.split()[0]}_{t}_{abs(hash(url))%1000}"); os.makedirs(sub, exist_ok=True)
        job = m.Job(source_url=url, target_ext=t, options={}, output_dir=sub)
        try:
            res = m.URLToMedia().convert(job, lambda p, msg: None)
            ok = os.path.exists(res) and os.path.getsize(res) > 0
            extra = ""
            if ok and t == "mp4":
                pr = subprocess.run([m.ensure_ffprobe(), "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", res], capture_output=True, text=True).stdout.split()
                extra = f" codecs={pr}"
            print(f"{'PASS' if ok else 'FAIL'} {label:18} -> {t:4} {os.path.basename(res)} ({os.path.getsize(res)//1024} KB){extra}")
            if not ok: bad += 1
        except Exception as e:
            bad += 1
            print(f"FAIL {label:18} -> {t:4} {type(e).__name__}: {str(e)[:220]}")

# what a user sees for a login-gated site
sub = os.path.join(OUT, "reddit"); os.makedirs(sub, exist_ok=True)
try:
    m.URLToMedia().convert(m.Job(source_url="https://www.reddit.com/r/aww/comments/90bu6w/heat_index_was_110_degrees_so_we_offered_him_/", target_ext="mp4", options={}, output_dir=sub), lambda p, msg: None)
    print("UNEXPECTED: reddit succeeded anonymously")
except m.ConvertError as e:
    print("\nReddit (anonymous) error shown to user:\n----\n" + str(e) + "\n----")
    print("PASS reddit error is actionable" if "URL cookies" in str(e) else "FAIL reddit error lacks cookie hint")
print("\nfailures:", bad)
