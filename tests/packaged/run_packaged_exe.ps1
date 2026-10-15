# Runs real conversions through the BUILT exe (dist\Any2Any\Any2Any.exe) on a "clean PC" PATH (no ffmpeg / Deno / Node),
# using the CLI mode (--in / --to / --outdir / --opts). check_packaged_outputs.py then inspects what it produced.
#
# Run the offline suites first (python tests\run_all.py): they build the fixtures this script feeds the exe.
# -SkipNetwork leaves out the link downloads (YouTube / X / TikTok) and the yt-dlp update-override cases.
param([switch]$SkipNetwork)
$ErrorActionPreference = "Continue"
$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$exe  = "$repo\dist\Any2Any\Any2Any.exe"
$root = if ($env:ANY2ANY_TEST_WORK) { $env:ANY2ANY_TEST_WORK } else { Join-Path $env:TEMP "any2any_tests" }
$py   = if (Test-Path "$repo\.venv-pkg\Scripts\python.exe") { "$repo\.venv-pkg\Scripts\python.exe" } else { "python" }
$sp   = (& $py -c "import sysconfig; print(sysconfig.get_paths()['purelib'])").Trim()
if (-not (Test-Path $exe)) { Write-Error "Build the exe first:  pyinstaller --noconfirm --clean Any2Any.spec"; exit 2 }

$out  = Join-Path $root "frozen2_out"
if (Test-Path $out) { Remove-Item $out -Recurse -Force }
New-Item -ItemType Directory $out | Out-Null

# A clean PC: no ffmpeg, no Deno, no Node on PATH
$env:PATH = "C:\Windows\System32;C:\Windows;C:\Windows\System32\WindowsPowerShell\v1.0"
"on stripped PATH -> ffmpeg: {0}  deno: {1}  node: {2}" -f [bool](Get-Command ffmpeg -ea 0), [bool](Get-Command deno -ea 0), [bool](Get-Command node -ea 0)

$fx  = "$root\fixes_test"
$zoo = "$root\clips\zoo19.mp4"
$in = @{
  docx = "$root\docs_test\report.docx"; pdf = "$root\docs_test\pdf_word\report.pdf"; wav = "$root\misc_test\tone.wav"
  csv = "$fx\excel.csv"; lol = "$fx\lol.json"; png = "$fx\logo.png"; gif = "$fx\odd.gif"; sub = "$fx\withsub.mp4"; zoo = $zoo; big = "$root\misc_test\big.mp4"
}
function Run($name, $src, $to, $opts = $null, $extraEnv = @{}) {
  $d = Join-Path $out $name; New-Item -ItemType Directory $d | Out-Null
  $argstr = "--in `"$src`" --to $to --outdir `"$d`""
  if ($opts) { $argstr += " --opts `"" + ($opts -replace '"', '\"') + "`"" }
  $saved = @{}; foreach ($k in $extraEnv.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k); [Environment]::SetEnvironmentVariable($k, $extraEnv[$k]) }
  $p = Start-Process -FilePath $exe -ArgumentList $argstr -Wait -PassThru -WindowStyle Hidden
  foreach ($k in $extraEnv.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
  $files = @(Get-ChildItem $d -Recurse -File)
  $res = if ($p.ExitCode -eq 0 -and $files.Count -gt 0) { "ran " } else { "FAIL" }
  "{0} {1,-22} exit={2} files={3}" -f $res, $name, $p.ExitCode, $files.Count
}

"--- documents (the docx_pdf_builtin case forces the built-in renderer even where Word is installed) ---"
Run "docx_pdf_word"      $in.docx "pdf"
Run "docx_pdf_builtin"   $in.docx "pdf" $null @{ ANY2ANY_NO_WORD = "1" }
Run "docx_md"            $in.docx "md"
Run "pdf_docx"           $in.pdf  "docx"
Run "pdf_md"             $in.pdf  "md"
Run "pdf_xlsx"           $in.pdf  "xlsx" '{"flavor":"lattice"}'
Run "pdf_png_p2"         $in.pdf  "png"  '{"pages":"2"}'
"--- data / images / media ---"
Run "csv_json"           $in.csv  "json"
Run "csv_xlsx"           $in.csv  "xlsx"
Run "json_csv"           $in.lol  "csv"
Run "png_jpg"            $in.png  "jpg"
Run "gif_mp4"            $in.gif  "mp4"
Run "sub_srt"            $in.sub  "srt"
Run "wav_opus"           $in.wav  "opus"
Run "zoo_gif_cap2"       $in.zoo  "gif"  '{"size_targeting":"1","custom_cap_mb":"2","headroom_percent":"5"}'
Run "big_mp4_cap20"      $in.big  "mp4"  '{"size_targeting":"1","discord_size_mb":"20","headroom_percent":"5"}'
"--- OCR (Windows' built-in engine, from the packaged exe) ---"
$oc = "$root\ocr_test"
Run "ocr_png_txt"        "$oc\clean.png"   "txt"
Run "ocr_small_txt"      "$oc\small.png"   "txt"
Run "ocr_sideimg_txt"    "$oc\sideimg.png" "txt"
Run "ocr_scan_txt"       "$oc\scan.pdf"    "txt"
Run "ocr_mixed_txt"      "$oc\mixed.pdf"   "txt"
Run "ocr_scan_md"        "$oc\scan.pdf"    "md"
Run "ocr_scan_docx"      "$oc\scan.pdf"    "docx"
Run "ocr_scan_searchable" "$oc\scan.pdf"   "pdf"
Run "ocr_img_pdf_search" "$oc\clean.png"   "pdf" '{"ocr_pdf":"1"}'
Run "ocr_badlang"        "$oc\clean.png"   "txt" '{"ocr_lang":"ja-JP"}'

if ($SkipNetwork) { "--- (links and the yt-dlp update override skipped: -SkipNetwork) ---"; return }
"--- links (clean PATH: bundled ffmpeg + bundled QuickJS only) ---"
Run "yt_rick_mp3"        "https://www.youtube.com/watch?v=dQw4w9WgXcQ" "mp3"
Run "x_multi_mp4"        "https://twitter.com/DavidToons_/status/1578353380363501568" "mp4"
Run "fxtwitter_mp4"      "https://fxtwitter.com/captainamerica/status/719944021058060289" "mp4"

"--- yt-dlp update override inside the packaged exe ---"
foreach ($v in @(@{n="ovr_good"; bad=$false}, @{n="ovr_bad"; bad=$true})) {
  $ad = Join-Path $root $v.n; if (Test-Path $ad) { Remove-Item $ad -Recurse -Force }
  $dd = Join-Path $ad "Any2Any\ytdlp"; New-Item -ItemType Directory $dd | Out-Null
  foreach ($pkg in "yt_dlp","yt_dlp_ejs") { robocopy "$sp\$pkg" "$dd\$pkg" /E /XD __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null }
  $vp = "$dd\yt_dlp\version.py"; (Get-Content $vp -Raw) -replace "__version__ = '[^']+'", "__version__ = '2099.01.01'" | Set-Content $vp -NoNewline
  if ($v.bad) { Set-Content "$dd\yt_dlp\__init__.py" "this is not python (((("}
  Run $v.n "https://www.youtube.com/shorts/jNQXAC9IVRw" "mp3" $null @{ APPDATA = $ad }
  $log = Join-Path $ad "Any2Any\logs\app.log"
  "      app.log says: " + ((Get-Content $log -ErrorAction SilentlyContinue | Where-Object { $_ -match "yt-dlp 20" } | Select-Object -Last 1) -replace "^.*\] ", "")
}
