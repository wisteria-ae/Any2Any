<#
One command from a fresh clone to the installer:

    powershell -ExecutionPolicy Bypass -File build.ps1

Steps: fetch ffmpeg into vendor\ffmpeg (skipped if present) -> PyInstaller -> Inno Setup -> installer\output\Any2AnySetup.exe
Needs Python 3.11+ and Inno Setup 6 (winget install JRSoftware.InnoSetup). Creates .venv-pkg for the build.
#>
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

& powershell -NoProfile -ExecutionPolicy Bypass -File tools\fetch_ffmpeg.ps1
if ($LASTEXITCODE) { throw "fetching ffmpeg failed" }
if (-not (Test-Path vendor\quickjs\qjs.exe)) { throw "vendor\quickjs\qjs.exe is missing (it is committed to the repo)" }

if (-not (Test-Path .venv-pkg\Scripts\python.exe)) { python -m venv .venv-pkg; if ($LASTEXITCODE) { throw "could not create .venv-pkg" } }
$py = ".venv-pkg\Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements.txt pyinstaller
if ($LASTEXITCODE) { throw "pip install failed" }

& $py -m PyInstaller --noconfirm --clean Any2Any.spec
if ($LASTEXITCODE) { throw "PyInstaller failed" }

$iscc = @("$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found. Install it:  winget install JRSoftware.InnoSetup" }
& $iscc installer\Any2Any.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }
"Done: " + (Resolve-Path installer\output\Any2AnySetup.exe)
