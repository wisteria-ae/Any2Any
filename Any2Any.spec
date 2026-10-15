# -*- mode: python ; coding: utf-8 -*-
import os


def _tree(src, dest_root, skip_top=()):
    """(file, dest_dir) pairs for every file under `src`, skipping the given top-level sub-folders."""
    out = []
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        if rel != '.' and rel.split(os.sep)[0] in skip_top:
            continue
        dest = dest_root if rel == '.' else os.path.join(dest_root, rel)
        out += [(os.path.join(root, f), dest) for f in files]
    return out


# ffmpeg: ship what's needed to RUN it, not the developer SDK (headers, import libs, docs, presets)
FFMPEG_DATAS = _tree('vendor\\ffmpeg', 'vendor\\ffmpeg', skip_top=('include', 'lib', 'doc', 'presets'))
# QuickJS-ng: tiny JS engine so YouTube works on PCs without Deno/Node.js
QUICKJS_DATAS = _tree('vendor\\quickjs', 'vendor\\quickjs')

# Camelot 2's PDF engine (playa) is compiled with mypyc; its modules import a hash-named runtime
# (e.g. 49480697f7d29e053205__mypyc.pyd) from inside the compiled code, which PyInstaller cannot see.
# Without it `import playa` fails in the packaged app and PDF -> Excel reports "Camelot not installed".
import glob, sysconfig
_SITE = sysconfig.get_paths()['purelib']
MYPYC_RUNTIMES = sorted({os.path.basename(p).split('.')[0] for p in glob.glob(os.path.join(_SITE, '*__mypyc*.pyd'))})

a = Analysis(
    ['Any2Any_desktop.py'],
    pathex=[],
    binaries=[],
    datas=FFMPEG_DATAS + QUICKJS_DATAS,
    hiddenimports=['PySide6', 'watchdog'] + MYPYC_RUNTIMES,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# PyInstaller also copies ffmpeg.exe's DLLs to the top level (a second ~200 MB copy). They already sit next to
# ffmpeg.exe in vendor\ffmpeg, which is where Windows looks first, so drop the top-level duplicates.
_FF_DLLS = {n.lower() for n in os.listdir('vendor\\ffmpeg') if n.lower().endswith('.dll')}
a.binaries = [b for b in a.binaries
              if not (b[0].lower() in _FF_DLLS and '\\' not in b[0] and '/' not in b[0])]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Any2Any',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['any2any.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Any2Any',
)
