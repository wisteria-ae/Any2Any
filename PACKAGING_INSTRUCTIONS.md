# Task: Turn Any2Any into a real installer

This is a working PySide6 desktop app that already builds via PyInstaller
(`Any2Any.spec` → `dist/Any2Any/`). That's a folder of files, not something a
user can double-click to install. The job is to wrap that build in a proper
Windows installer (`Any2AnySetup.exe`), without touching how the app itself
works.

## Ground rules

1. **Commit first.** Run `git init` in this folder if it's not already a
   repo, then commit the current state as-is before making any changes. This
   is the undo button — use it if anything goes sideways.
2. **Do not edit `Any2Any_desktop.py`.** The application source is off
   limits. If you hit a problem that seems to require a source change (e.g.
   a hardcoded path that breaks once installed), stop and tell me what you
   found and why — don't just patch it.
3. **`Any2Any.spec` is allowed to change** if the PyInstaller build itself
   needs adjusting (e.g. a missing hidden import), but treat it as sensitive
   — explain any change before/after making it.
4. **Everything new goes in a new `installer/` folder** — the Inno Setup
   script, any build helper scripts, notes. Don't scatter new files into the
   project root.
5. **Don't touch `dist/` or `build/` by hand** — they're PyInstaller output.
   Regenerate them with a clean build (step below), don't edit their
   contents directly.

## Step 1 — Inspect before building

Before running anything, report back:
- Confirm `Any2Any_desktop.py` is the entry point and what GUI framework/version it targets (PySide6 per `requirements.txt`).
- Full dependency list from `requirements.txt`, cross-checked against actual
  `import` statements in `Any2Any_desktop.py` — flag anything imported but
  not declared, or declared but unused.
- **Check for external (non-pip) binary dependencies.** `camelot-py[cv]`
  requires Ghostscript to be installed on the system to parse PDFs — this is
  a known gap since PyInstaller can't auto-bundle it like it does the
  vendored `vendor/ffmpeg`. Confirm whether Ghostscript is required for the
  app's actual PDF-conversion features, and if so, whether it needs to be
  bundled in `installer/` and installed silently by the installer, or
  documented as a prerequisite.
- Note that `vendor/ffmpeg` and `any2any.ico` are already correctly
  referenced as `datas`/`icon` in `Any2Any.spec` — confirm they still are.

Do not proceed to building until this report is shared.

## Step 2 — Clean rebuild

1. Delete the existing `build/` and `dist/` folders (these are just
   PyInstaller cache/output, safe to regenerate — but confirm with me first
   since they're pre-existing, not something you created).
2. Create a **fresh virtual environment** (not the existing `.venv`, which
   may have packages installed that aren't in `requirements.txt`). Install
   only from `requirements.txt` into it.
3. Run `pyinstaller Any2Any.spec` from that clean environment.
4. If the build fails or the app doesn't launch from `dist/Any2Any/Any2Any.exe`,
   diagnose why (usually a missing hidden import) and fix it in the `.spec`
   file — report what was missing and why.

## Step 3 — Wrap it in an installer

Use **Inno Setup** (free, no install-time dependencies beyond the Inno Setup
compiler itself, produces a single `.exe` installer — simplest fit for a
PySide6 desktop app like this).

- Write the `.iss` script in `installer/Any2Any.iss`.
- Installer should: install `dist/Any2Any/*` to Program Files, create a Start
  Menu shortcut using `any2any.ico`, optionally offer a desktop shortcut, and
  include an uninstaller.
- If Ghostscript turns out to be a hard requirement (see Step 1), either
  bundle its installer as a silent dependency step, or have the installer
  check for it and show a clear message — don't fail silently.
- Compile it to `installer/output/Any2AnySetup.exe`.

## Step 4 — Verify, don't assume

- Run the compiled installer on this machine (or confirm with me before
  running if that feels risky — it does modify Program Files/Start Menu).
- Launch the installed app from its Start Menu shortcut, not from `dist/`.
- Actually perform one real file conversion for each major format the app
  supports (check `Any2Any_desktop.py` for what those are) and confirm
  output matches what running `Any2Any_desktop.py` directly from source
  produces.
- Uninstall it afterward and confirm the uninstaller removes what it
  installed.

## Step 5 — Summarize

List every file added or changed, and why. Flag anything you weren't sure
about rather than guessing silently.
