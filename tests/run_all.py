"""Run the Any2Any test suites in the right order and print a summary.

    python tests/run_all.py              # offline suites (a few minutes)
    python tests/run_all.py --network    # + real downloads (YouTube, X, TikTok, ...)
    python tests/run_all.py --packaged   # + the built exe in dist/ (needs a fresh PyInstaller build; add --network for links)
    python tests/run_all.py --only ocr   # just suites whose name contains "ocr"

Order matters: the media suite builds the video fixtures and the documents suite builds the Word/PDF fixtures that
later suites reuse. Fixtures and outputs go to %TEMP%\\any2any_tests (override with ANY2ANY_TEST_WORK).
"""
import os, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OFFLINE = ["suite_media_and_ui.py", "suite_documents.py", "suite_ocr.py", "suite_feedback.py",
           "suite_fixes.py", "suite_regression.py", "suite_updater.py"]
NETWORK = ["network/suite_links.py", "network/suite_sites.py"]


def run_py(rel):
    t = time.time()
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-u", str(HERE / rel)], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    lines = (r.stdout + "\n" + r.stderr).splitlines()
    return lines, time.time() - t, r.returncode


def summarize(name, lines, secs, code):
    ok = sum(l.startswith("PASS ") for l in lines)
    bad = [l for l in lines if l.startswith("FAIL ")]
    crashed = code not in (0, 1) or any("Traceback" in l for l in lines) or (ok == 0 and not bad)
    status = "ok " if not bad and not crashed else "BAD"
    print(f"[{status}] {name:34} pass={ok:3} fail={len(bad)}  {secs:5.0f}s" + ("   CRASHED (see output below)" if crashed else ""))
    for l in bad: print("        " + l[:300])
    if crashed:
        for l in lines[-12:]: print("        | " + l[:200])
    return ok, len(bad) + (1 if crashed else 0)


def main():
    args = sys.argv[1:]
    only = args[args.index("--only") + 1].lower() if "--only" in args else None
    suites = OFFLINE + (NETWORK if "--network" in args else [])
    if only: suites = [s for s in OFFLINE + NETWORK if only in s.lower()]
    total_ok = total_bad = 0
    for rel in suites:
        ok, bad = summarize(rel, *run_py(rel))
        total_ok += ok; total_bad += bad
    if "--packaged" in args:
        ps1 = HERE / "packaged" / "run_packaged_exe.ps1"
        print("packaged exe: running every conversion through dist/Any2Any/Any2Any.exe ...")
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)] + (["-SkipNetwork"] if "--network" not in args else []),
                       capture_output=True)
        ok, bad = summarize("packaged/check_packaged_outputs.py", *run_py("packaged/check_packaged_outputs.py"))
        total_ok += ok; total_bad += bad
    print(f"\nTOTAL: {total_ok} passed, {total_bad} failed")
    sys.exit(1 if total_bad else 0)


if __name__ == "__main__":
    main()
