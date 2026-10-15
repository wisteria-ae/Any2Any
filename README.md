# Any2Any (Local)

A Windows desktop app that converts files and downloads links, built around fitting results under Discord's upload
limits. **Everything runs on your PC: no telemetry, no analytics, no account.**

## What it does

| From | To |
|---|---|
| **Video** (mp4, mkv, mov, avi, webm, m4v, mpg, flv, **gif**) | MP4 · MP3 / M4A / Opus · GIF · animated WebP · subtitles out (.srt) or burned in |
| **Audio** | MP3 / M4A / Opus |
| **Images** (png, jpg, webp, bmp, tiff, heic, avif) | PNG / JPG / WebP / AVIF · PDF (one per image, or **all combined into one PDF**) · text (OCR) |
| **PDF** | Word · Markdown · text · images (per page) · Excel/CSV tables · **searchable PDF** (for scans) |
| **Word** (.docx) | PDF · Markdown |
| **CSV / JSON / XLSX** | CSV ↔ JSON · CSV ↔ XLSX |
| **Links** (YouTube/Shorts, X, TikTok, Twitch, Streamable, Bluesky; Reddit/Instagram with login) | MP4 · MP3 / M4A / Opus · GIF · animated WebP · thumbnail |

* **Discord size cap:** presets for free (20 MB), Nitro Basic (50), Nitro (500) and server boosts (50 / 100), or any custom cap.
  Discord applies the *larger* of your own limit and the server's boost limit, so pick the bigger. Files already under the
  cap are left alone; over-cap videos are re-encoded, **re-checked, and retried** until they fit. A cap is treated as
  1,000,000 bytes per MB (the smaller reading), so a result that fits fits under either definition.
* **OCR** uses the text recognition built into Windows 10/11: offline, nothing extra to install. Scanned PDFs are read
  automatically for text / Word / Markdown output.
* **Also:** queue with cancel, drag and drop, watch folder, several links pasted at once, MP3 tag editor, dark mode,
  Tools → Dependency check, remembers your last choices, and a command-line mode (`--in --to --outdir --opts`).
* Options shown in the window follow the output type you choose: only the settings that apply are visible.

## Privacy

No telemetry, analytics, crash reporting or account. The only network use is what you ask for: downloading links you paste
(requests go to those sites), **Tools → Update link downloader** (contacts pypi.org, checks the SHA-256, and only accepts files
from `files.pythonhosted.org`), and following Reddit short links. Cookies you supply (browser or `cookies.txt`) are read on
this PC and sent only to the site you are downloading from. Help → About repeats this.

## Limitations

**Platform / install:** Windows 10/11 only (OCR and Word → PDF use Windows features). The installer is unsigned, so Windows
SmartScreen will warn. ~175 MB to download, ~630 MB installed.

**Links:** Reddit, Instagram and Vimeo need a logged-in session (Options → URL cookies or a `cookies.txt`); this wasn't tested
with a real account. Sites change often: use Tools → Update link downloader when one breaks. Video only (no image galleries or
text posts). TikTok short links and Reddit share links are untested with real examples.

**Discord:** Only the free 20 MB figure is from Discord's own announcement; Nitro Basic 50 / Nitro 500 / boosts 50 and 100
agree across third-party sources. Caps are whole megabytes. A very long video can't fit a small cap: you get a clear error, or
several parts if "Split" is on. NVENC and QSV encoders are untested (no hardware); if one fails the job retries in software.

**Documents:** Word → PDF is exact only if Microsoft Word is installed (otherwise a simpler built-in renderer); `.doc`, `.rtf`,
`.odt` need Word. PDF → Word / Markdown quality depends on the PDF. PDF → Excel/CSV needs a PDF with real text (or `ocrmypdf`).
Password-protected PDFs are rejected.

**OCR:** good at printed text, poor at handwriting and multi-column layouts. Reads only the languages installed in Windows
(Settings → Time & language → Language & region → add a language → install "Optical character recognition"). Scanned Word
output is plain paragraphs without the original layout. Sideways pages are auto-rotated for text output only.

**Images / data:** animated images lose their animation (except GIF → MP4). Nested JSON is flattened into columns.

## Building

```
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean Any2Any.spec          # -> dist\Any2Any\
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\Any2Any.iss   # -> installer\output\Any2AnySetup.exe
```

Keep `APP_VERSION` in `Any2Any_desktop.py` and `MyAppVersion` in `installer\Any2Any.iss` in sync. Tests: see `tests\README.md`.
Code-signing the installer (so SmartScreen stops warning) needs a certificate; Microsoft's Trusted Signing is the cheapest legitimate route.
