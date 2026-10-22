<#
Downloads the ffmpeg build Any2Any is packaged with into vendor\ffmpeg (ffmpeg.exe, ffprobe.exe and their DLLs).
The binaries are not kept in git (one DLL alone is over GitHub's 100 MB file limit).

    powershell -ExecutionPolicy Bypass -File tools\fetch_ffmpeg.ps1          # skip if already present
    powershell -ExecutionPolicy Bypass -File tools\fetch_ffmpeg.ps1 -Force   # download again

Sources, in order:
  1. This repository's own "ffmpeg-n8.1.3" release: a pinned copy, verified against the fixed SHA-256 below.
  2. Fallback: BtbN/FFmpeg-Builds, FFmpeg 8.1 stable branch, win64 GPL shared build, verified against the SHA-256
     that GitHub reports for that release asset (the build there is rolling, so its hash changes over time).
Both are the GPL *shared* build; LICENSE.txt is copied next to the binaries. FFmpeg source: https://ffmpeg.org/download.html
#>
param([switch]$Force)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$repo = (Resolve-Path "$PSScriptRoot\..").Path
$dest = Join-Path $repo "vendor\ffmpeg"

$pinnedUrl  = "https://github.com/wisteria-ae/Any2Any/releases/download/ffmpeg-n8.1.3/ffmpeg-win64-gpl-shared.zip"
$pinnedHash = "644c112296ac892e4c81ba6f0daa57ce83951268d72197c73ada421af904ecf5"
$btbnAsset  = "ffmpeg-n8.1-latest-win64-gpl-shared-8.1.zip"
$btbnApi    = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/tags/latest"

if ((Test-Path "$dest\ffmpeg.exe") -and (Test-Path "$dest\ffprobe.exe") -and -not $Force) {
    "ffmpeg already present in $dest (use -Force to re-download)"; return
}

function Get-Verified($url, $expected, $out) {
    Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing
    $got = (Get-FileHash $out -Algorithm SHA256).Hash.ToLower()
    if ($got -ne $expected.ToLower()) { throw "SHA-256 mismatch (expected $expected, got $got). Download discarded." }
}

$tmp = Join-Path ([IO.Path]::GetTempPath()) ("any2any_ffmpeg_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory $tmp | Out-Null
try {
    $zip = Join-Path $tmp "ffmpeg.zip"
    $src = $null
    try {
        "Downloading the pinned ffmpeg build ..."
        Get-Verified $pinnedUrl $pinnedHash $zip
        $src = "pinned"
    } catch {
        "Pinned copy unavailable ($($_.Exception.Message)); trying BtbN/FFmpeg-Builds ..."
        $rel = Invoke-RestMethod -Uri $btbnApi -Headers @{ "User-Agent" = "Any2Any-build" }
        $a = $rel.assets | Where-Object { $_.name -eq $btbnAsset }
        if (-not $a) { throw "$btbnAsset is no longer published by BtbN/FFmpeg-Builds. Edit `$btbnAsset in tools\fetch_ffmpeg.ps1 to a current win64-gpl-shared build." }
        $h = ($a.digest -replace "^sha256:", "")
        if (-not $h) { throw "GitHub did not report a SHA-256 for $btbnAsset; refusing to use an unverified download." }
        Get-Verified $a.browser_download_url $h $zip
        $src = "btbn"
    }
    "SHA-256 verified."
    $x = Join-Path $tmp "x"
    Expand-Archive $zip -DestinationPath $x -Force
    if ($src -eq "btbn") {
        # BtbN layout: <folder>\bin\*.exe|dll + <folder>\LICENSE.txt. Keep runtime files only (no ffplay, headers, libs, docs).
        $root = Get-ChildItem $x -Directory | Where-Object { Test-Path "$($_.FullName)\bin\ffmpeg.exe" } | Select-Object -First 1
        if (-not $root) { throw "Unexpected archive layout: bin\ffmpeg.exe not found." }
        $flat = Join-Path $tmp "flat"; New-Item -ItemType Directory $flat | Out-Null
        Get-ChildItem "$($root.FullName)\bin" -File | Where-Object { $_.Name -notlike "ffplay*" } | Copy-Item -Destination $flat
        Copy-Item "$($root.FullName)\LICENSE.txt" $flat
        $x = $flat
    }
    if (-not (Test-Path "$x\ffmpeg.exe") -or -not (Test-Path "$x\ffprobe.exe")) { throw "Archive does not contain ffmpeg.exe and ffprobe.exe." }
    if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
    New-Item -ItemType Directory $dest | Out-Null
    Copy-Item "$x\*" $dest -Recurse
    & "$dest\ffmpeg.exe" -version | Select-Object -First 1
    "Installed to $dest"
} finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
