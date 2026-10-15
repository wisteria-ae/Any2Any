# Any2Any tests

These suites exercise the real converters and the real Qt window (offscreen), not mocks. They exist so that a change
to one converter can't quietly break another. **Run them before and after any change to `Any2Any_desktop.py`.**

```
python tests\run_all.py              # offline suites, a few minutes
python tests\run_all.py --network    # + real downloads (YouTube, X, TikTok, Bluesky, ...)
python tests\run_all.py --packaged   # + the built exe in dist\ (build it first: pyinstaller --noconfirm --clean Any2Any.spec)
python tests\run_all.py --only ocr   # only suites whose name contains "ocr"
```

Use the project's packaging venv (`.venv-pkg`) or any Python that has `requirements.txt` installed plus `pyinstaller`.
Fixtures and outputs go to `%TEMP%\any2any_tests` (set `ANY2ANY_TEST_WORK` to move them). Nothing here touches your real
settings or Downloads folder: each suite points `%APPDATA%` at its own scratch folder.

| Suite | What it covers |
|---|---|
| `suite_media_and_ui.py` | audio/video outputs, Discord size caps (pass-through vs re-encode), window logic (pre-checks, multi-link paste, option rows) |
| `suite_documents.py` | PDF ↔ Word, Word/PDF → Markdown, page ranges, error cases, cancel (uses real Microsoft Word when installed) |
| `suite_ocr.py` | picture/scanned PDF → text/Word/Markdown, searchable PDFs, sideways pages, languages, running inside the app's worker thread |
| `suite_feedback.py` | decimal-MB caps, GPU-encoder fallback, per-output Options rows, remembered choices, merge-images-to-PDF, privacy statement, updater address allowlist, drag-and-drop |
| `suite_fixes.py` | regressions: subtitles, CSV/JSON/Excel, images (rotation, transparency, never overwrite the original), GIF/WebP caps, watch folder, console-window flags |
| `suite_regression.py` | older fixes (dependency dialog, NVENC flags, subtitle burn-in path escaping, cancel) |
| `suite_updater.py` | the yt-dlp self-updater (real PyPI download, "newer wins", broken update falls back) |
| `network/suite_links.py`, `network/suite_sites.py` | real downloads from YouTube, X, TikTok, Streamable, Twitch, Bluesky; the Reddit login message |
| `packaged/` | `run_packaged_exe.ps1` runs the built exe's CLI on a clean-PC PATH; `check_packaged_outputs.py` verifies the files |

**Order matters.** `suite_media_and_ui` builds the video fixtures and `suite_documents` builds the Word/PDF fixtures that later
suites reuse, so use `run_all.py` rather than launching a single later suite on a fresh machine.

**Not covered by automation:** actually dragging a file from File Explorer onto the window (the suites feed Qt the same
drop events), NVENC/QSV on real hardware, and logged-in downloads (Reddit/Instagram cookies).

A suite prints one `PASS`/`FAIL` line per check and exits non-zero on any failure.
