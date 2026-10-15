<#
Downloads the ffmpeg build Any2Any is packaged with into vendor\ffmpeg (ffmpeg.exe, ffprobe.exe and their DLLs).
The binaries are not kept in git (one DLL alone is over GitHub's 100 MB file limit).

    powershell -ExecutionPolicy Bypass -File tools\fetch_ffmpeg.ps1          # skip if already present
    powershell -ExecutionPolicy Bypass -File tools\fetch_ffmpeg.ps1 -Force   # download again

Source: BtbN/FFmpeg-Builds, FFmpeg 8.1 stable branch, win64 GPL *shared* build (the installer ships it together with its
LICENSE.txt; the FFmpeg source for that build is published by BtbN and ffmpeg.org). The download is checked against the
SHA-256 that GitHub reports for the release asset.
#>
param([switch]$Force)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$repo   = (Resolve-Path "$PSScriptRoot\..").Path
$dest   = Join-Path $repo "vendor\ffmpeg"
$asset  = "ffmpeg-n8.1-latest-win64-gpl-shared-8.1.zip"
$api    = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/tags/latest"

if ((Test-Path "$dest\ffmpeg.exe") -and (Test-Path "$dest\ffprobe.exe") -and -not $Force) {
    "ffmpeg already present in $dest (use -Force to re-download)"; return
}
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
"Looking up $asset ..."
$rel = Invoke-RestMethod -Uri $api -Headers @{ "User-Agent" = "Any2Any-build" }
$a = $rel.assets | Where-Object { $_.name -eq $asset }
if (-not $a) { throw "$asset is no longer published by BtbN/FFmpeg-Builds. Edit `$asset in tools\fetch_ffmpeg.ps1 to a current win64-gpl-shared build." }
$expected = ($a.digest -replace "^sha256:", "").ToLower()
if (-not $expected) { throw "GitHub did not report a SHA-256 for $asset; refusing to use an unverified download." }

$tmp = Join-Path ([IO.Path]::GetTempPath()) ("any2any_ffmpeg_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory $tmp | Out-Null
try {
    $zip = Join-Path $tmp $asset
    "Downloading {0:N0} MB ..." -f ($a.size / 1MB)
    Invoke-WebRequest -Uri $a.browser_download_url -OutFile $zip -UseBasicParsing
    $got = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
    if ($got -ne $expected) { throw "SHA-256 mismatch for $asset (expected $expected, got $got). Download discarded." }
    "SHA-256 verified."
    Expand-Archive $zip -DestinationPath $tmp -Force
    $bin = Get-ChildItem $tmp -Directory | Where-Object { Test-Path "$($_.FullName)\bin\ffmpeg.exe" } | Select-Object -First 1
    if (-not $bin) { throw "Unexpected archive layout: bin\ffmpeg.exe not found." }
    if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
    New-Item -ItemType Directory $dest | Out-Null
    # runtime files only: ffmpeg/ffprobe + DLLs (no ffplay, headers, import libs or docs) + the license
    Get-ChildItem "$($bin.FullName)\bin" -File | Where-Object { $_.Name -notlike "ffplay*" } | Copy-Item -Destination $dest
    Copy-Item "$($bin.FullName)\LICENSE.txt" $dest
    & "$dest\ffmpeg.exe" -version | Select-Object -First 1
    "Installed to $dest"
} finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
