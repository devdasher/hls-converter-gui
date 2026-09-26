# hls-converter-gui

> **⚠️ BETA RELEASE — v0.7.0**
>
> This project is currently in **Beta**. The UI layout, settings storage
> format, and bundled player behavior may still change between 0.x releases.
> Please report bugs and feedback at:
>
> **https://github.com/devdasher/hls-converter-gui/issues**

A PySide6 **desktop GUI** for the `hls-converter-cli` package, by
[@devdasher](https://github.com/devdasher).

Converts any MKV / MP4 / MOV / TS / WEBM / AVI file into an HLS stream
folder — with per-quality video profiles, per-track audio overrides,
WebVTT subtitle extraction, and a built-in HLS preview player — through a
clean, dependency-aware interface.

The GUI is a frontend. The actual conversion is performed by the
`hls-converter-cli` Python package, which is installed automatically as a
dependency.

---

## Table of contents

- [hls-converter-gui](#hls-converter-gui)
  - [Table of contents](#table-of-contents)
  - [Features](#features)
  - [Screenshots](#screenshots)
  - [Installation](#installation)
    - [Option 1 — Download a prebuilt executable (recommended for non-developers)](#option-1--download-a-prebuilt-executable-recommended-for-non-developers)
    - [Option 2 — Install from PyPI](#option-2--install-from-pypi)
    - [Option 3 — From source](#option-3--from-source)
  - [Requirements](#requirements)
  - [Quick start](#quick-start)
  - [The Convert tab](#the-convert-tab)
    - [Files](#files)
    - [Video](#video)
    - [Audio](#audio)
    - [Subtitles](#subtitles)
    - [Advanced](#advanced)
  - [The Add Track tab](#the-add-track-tab)
    - [Files](#files-1)
    - [Track options](#track-options)
  - [The Player tab](#the-player-tab)
  - [The Log tab](#the-log-tab)
  - [External players](#external-players)
  - [Settings and persistence](#settings-and-persistence)
  - [Drag and drop](#drag-and-drop)
  - [Command-line equivalent](#command-line-equivalent)
  - [FAQ and troubleshooting](#faq-and-troubleshooting)
  - [Beta notes](#beta-notes)
  - [Related project — CLI](#related-project--cli)
  - [License](#license)

---

## Features

- **Convert** any media file to HLS with a single click
- **Copy or encode** video, with per-resolution target profiles
- **Per-track audio overrides** — bitrate, channel count, codec
- **Subtitle extraction** with per-track offset and time-scale correction
- **Add Track** mode: attach audio or subtitle files to an existing stream
  and regenerate the master playlist
- **Built-in HLS preview player** with audio and subtitle track selection
- **External player launcher** for VLC, MPV, MPC-HC, and KMPlayer
- **Local HTTP server** so you can open the stream in Safari, VLC, or any
  HLS-capable player, straight from the Player tab
- **Drag & drop** files and folders onto the appropriate tab
- **Live progress** with ETA and frame rate
- **Disk space precheck** before starting a long conversion
- **Smart folder naming** — strips quality/codec/release tokens from
  filenames so different renders of the same title end up in the same
  destination
- **Merge detection** — if a compatible stream folder already exists next to
  the source file, the GUI offers to merge into it instead of creating a
  new one
- **Persistent settings** — every field is remembered across runs
- **Cross-platform** — Windows, Linux, and macOS

---

## Screenshots

<table>
<tr>
<td align="center" width="50%">
<img src="https://github.com/user-attachments/assets/db7a4eaf-4f0a-4644-946a-081d03e5c784" width="350" alt="Convert tab"><br>
<b>Convert tab</b>
</td>
<td align="center" width="50%">
<img src="https://github.com/user-attachments/assets/e8098f4a-4ff9-423c-b79c-a60bec5b406e" width="350" alt="Add Track tab"><br>
<b>Add Track tab</b>
</td>
</tr>
<tr>
<td align="center" width="50%">
<img src="https://github.com/user-attachments/assets/26db48bd-806a-4f8b-811a-78c703dff6b8" width="350" alt="Player tab"><br>
<b>Player tab</b>
</td>
<td align="center" width="50%">
<img src="https://github.com/user-attachments/assets/bd8a6f29-0f3c-4a8d-a406-fc556aa6d8b4" width="350" alt="Log tab"><br>
<b>Log tab</b>
</td>
</tr>
</table>

---

## Installation

### Option 1 — Download a prebuilt executable (recommended for non-developers)

Head to the **Releases** page and download the archive for your operating
system:

**https://github.com/devdasher/hls-converter-gui/releases**

| OS      | Asset name pattern |
| ------- | ------------------ |
| Windows | `HLSConverter-v0.7.0-beta-Windows-x64-Portable.zip` |
| Linux   | `HLSConverter-v0.7.0-beta-Linux-x64-Portable.zip` |
| macOS   | `HLSConverter-v0.7.0-beta-macOS-*-Portable.zip` |

Each release also publishes a `.sha256` checksum file so you can verify the
download. Extract the archive, and run the executable.

The packaged executables still require **FFmpeg** and **ffprobe** to be
installed on your system (see [Requirements](#requirements)).

### Option 2 — Install from PyPI

```
pip install hls-converter-gui
```

This automatically installs:
- `hls-converter-cli` (the conversion engine)
- `PySide6 >= 6.7`
- `platformdirs >= 4`

Then run:

```
hls-converter-gui
```

### Option 3 — From source

```
git clone https://github.com/devdasher/hls-converter-gui.git
cd hls-converter-gui
pip install -e .
python hls_converter_gui.py
```

---

## Requirements

- **Python 3.10 or newer** (only for the pip install; the prebuilt
  executables bundle their own Python)
- **PySide6 6.7 or newer** (needed for the built-in HLS player; older
  versions have broken HLS support in the FFmpeg media backend)
- **FFmpeg and ffprobe** installed and available on your `PATH`, or set
  their full paths in **Advanced**

How to install FFmpeg:

| OS      | Source |
| ------- | ------ |
| Windows | https://www.gyan.dev/ffmpeg/builds/ — download the essentials build and add `bin` to PATH |
| macOS   | `brew install ffmpeg` |
| Linux   | `sudo apt install ffmpeg` or your distribution's equivalent |

---

## Quick start

1. Launch the app: `hls-converter-gui` (or double-click the executable).
2. In the **Convert** tab, click **Browse…** next to *Input* and choose a
   media file.
3. Leave the defaults or adjust the video and audio options.
4. Click **Start**.

The stream is written to `<source folder>/<source name>-stream/` by
default (or next to the source file if you enabled the checkbox), and the
path to the new `master.m3u8` is filled in on the **Player** tab
automatically.

Click **Play** in the Player tab to preview the result immediately.

---

## The Convert tab

### Files

- **Input** — the source media file (MKV, MP4, MOV, TS, M4V, WEBM, AVI).
- **Output** — destination folder. If "Save output next to the source
  file" is checked (the default), the field is auto-filled as
  `<source>-stream/`. Uncheck the box to type your own path or pick one
  with **Browse…**.
- **Merge with…** — pick an existing `*-stream` folder to merge this
  conversion into. Useful when you want to add new renditions to an
  already-built stream.
- **Remove existing output directory first** — wipes the destination
  folder before starting. Equivalent to `--clean` on the CLI.

### Video

- **Mode** — `copy`, `encode`, or `none`.
- **Copy label** — folder name for copy mode. Default: source height.
- **Encode profiles** — checkboxes for target heights (240p through 2160p).
- **Bitrate level** — `data-saver`, `small`, `balanced`, `high`, `source`.
- **Rate mode** — `auto`, `crf`, `abr`, `2pass`.
- **Encoder** — FFmpeg encoder name (`libx264`, `libx265`, `h264_nvenc`, …).
- **Preset** — speed/quality tradeoff for x264/x265.
- **Bitrate overrides** — per-quality bitrate in kbps, e.g.
  `480=850,720=1300,1080=2800`.
- **CRF overrides** — per-quality CRF, e.g. `480=24,720=20,1080=18`.

Controls are enabled or disabled automatically based on the video mode
you choose, so you can't accidentally set an override that won't apply.

### Audio

- **Mode** — `Copy all tracks`, `Encode all tracks`, `Skip audio`, or
  `Encode specific tracks`.
- **Specific tracks** — comma-separated track numbers when using
  "Encode specific tracks".
- **Encoder** — target codec for encode mode.
- **AAC profile** — only used when the encoder is `aac`.
- **Profiles** — encoded renditions, e.g. `128:2,224:6`. Each entry is
  `BITRATE` or `BITRATE:CHANNELS`.
- **Bitrate level** — `data-saver`, `small`, `balanced`, `high`, `source`.
- **Bitrate overrides** — per-track bitrate, e.g. `1=128,2=224`.
- **Channel overrides** — per-track channel count, e.g. `1=2,2=6`.

### Subtitles

- **Mode** — `auto` (extract everything) or `none`.
- **Offset (ms)** — global shift, or per-track syntax `1=500,2=-300`.
- **Time scale** — drift correction, e.g. `25/23.976`.

### Advanced

- **ffmpeg path** and **ffprobe path** — set these if FFmpeg is not on
  your PATH.
- **Threads** — encoder thread count.
- **Segment time** — HLS segment length in seconds.
- **Keyframe interval** — force a keyframe every N seconds.
- **Default audio track** and **Default subtitle track** — mark a specific
  track as `DEFAULT=YES` in the master playlist.
- **Do not (re)generate master.m3u8** — useful when merging multiple
  conversions into one folder.
- **Dry run** — print FFmpeg commands without executing them.
- **Check free disk space before starting** — estimate the required output
  size and warn if the destination drive looks too small.

---

## The Add Track tab

Add an external audio or subtitle file to an existing stream folder and
regenerate the master playlist.

### Files

- **Track file** — the audio (`.m4a`, `.aac`, `.mp3`, `.ac3`, `.eac3`,
  `.opus`, `.flac`, `.wav`, `.ogg`, `.mka`) or subtitle (`.srt`, `.ass`,
  `.ssa`, `.vtt`, `.sub`, `.sbv`) file to attach.
- **Stream folder** — the target stream folder. Must contain a
  `master.m3u8` or a `video/` subfolder.

### Track options

- **Audio mode** — `auto`, `copy`, or `encode`.
- **Audio codec** — target codec when re-encoding.
- **AAC profile** — only used when the codec is `aac`.
- **Audio level** — auto-bitrate level for encode mode.
- **Track number** — 0 for automatic (next available), or specify a number.
- **Language** — ISO 639-1 language tag, e.g. `fa`, `en`, `de`.
- **Title** — human-readable title shown in the player's track menu.
- **Subtitle offset** and **Subtitle scale** — same as the Convert tab.
- **Do not regenerate master.m3u8** — leave the existing master playlist
  untouched.

---

## The Player tab

Preview any `master.m3u8` stream without leaving the app.

- **Playlist** — path to the `.m3u8` file. Auto-filled after a successful
  conversion.
- **Server** — a local HTTP URL that you can copy and paste into VLC,
  MPV, KMPlayer, or Safari. The GUI runs a small HTTP server on a random
  loopback port whenever a stream is loaded.
- **External player** — pick an installed player and click **Launch**.
  Supports MPV, VLC, MPC-HC, and KMPlayer out of the box, and any
  executable you point to via **Browse…**.
- **Audio / Subtitles** — selects the active tracks in the built-in
  player when the master playlist declares multiple renditions.
- **Playback controls** — Play / Pause, Stop, seek, time display, volume.

**Subtitles in the built-in player:** Qt's FFmpeg backend has limited
support for rendering HLS subtitles. If you need to verify subtitle
alignment, use **Launch** to open the stream in VLC or MPV.

---

## The Log tab

Live, colour-coded output from the conversion engine:

- **INFO** — normal progress and status messages
- **OK** — completed steps
- **WARN** — recoverable issues
- **ERROR** — failures

Buttons at the bottom let you **Save…** the log to a text file or **Clear**
it. Log output is filtered to hide noisy FFmpeg internal messages that
don't affect the result.

---

## External players

The GUI auto-detects popular external players in the following locations:

| Player  | Where it looks |
| ------- | -------------- |
| MPV     | PATH, `C:\Program Files\mpv\`, `C:\Program Files (x86)\mpv\` |
| VLC     | PATH, `C:\Program Files\VideoLAN\VLC\`, `C:\Program Files (x86)\VideoLAN\VLC\` |
| MPC-HC  | PATH, `C:\Program Files\MPC-HC\`, `C:\Program Files (x86)\MPC-HC\` |
| KMPlayer| PATH, `C:\Program Files\The KMPlayer\`, `C:\Program Files (x86)\The KMPlayer\` |

If your player is not detected, use **Browse…** to point directly at the
executable. The path is remembered across sessions.

---

## Settings and persistence

Every field in the GUI is saved automatically to a SQLite database when
you:

- Click **Save Settings**
- Close the app

The database lives in a per-user, per-OS directory:

| OS      | Location |
| ------- | -------- |
| Windows | `%APPDATA%\devdasher\hls-converter-gui\settings.sql` |
| macOS   | `~/Library/Application Support/hls-converter-gui/settings.sql` |
| Linux   | `~/.local/share/hls-converter-gui/settings.sql` |

Window geometry is saved alongside the field values, so the app reopens
at the same size and position.

The About dialog (**Help → About**) shows the exact path to your settings
database, which is useful for troubleshooting.

---

## Drag and drop

Drop a file onto the window and the GUI routes it based on the active
tab:

| Active tab | What happens |
| ---------- | ------------ |
| **Convert** | The file becomes the input; output path is auto-filled |
| **Add Track** | The file becomes the track to attach |
| **Player** | The playlist (or folder containing `master.m3u8`) is loaded |
| **Log** | Auto-detected: audio/subtitle → Add Track; `.m3u8` → Player; everything else → Convert |

Dropping a folder onto the Player tab will look for `master.m3u8` inside
it automatically.

---

## Command-line equivalent

Every GUI action maps to a `hls-converter-cli` invocation. If you want to
script the same operations from a terminal, install the CLI package
directly:

```
pip install hls-converter-cli
```

See the CLI repository for full documentation:
**https://github.com/devdasher/hls-converter-cli**

---

## FAQ and troubleshooting

**The built-in player won't open HLS**

Upgrade to PySide6 6.7 or newer:

```
pip install --upgrade "PySide6>=6.7"
```

Older PySide6 versions have a broken HLS implementation in the FFmpeg
media backend (QTBUG-111378). The bundled executables already ship with a
compatible version.

**Subtitles don't appear in the built-in player**

This is a known limitation of Qt's FFmpeg backend. Use **Launch** to open
the stream in VLC or MPV. The subtitle files themselves are generated
correctly — verify by opening the stream in an external player or in a
browser.

**The conversion starts but produces no output**

Check the Log tab for the first `[ERROR]` line. Common causes:

- FFmpeg is not on your PATH and no path is set in Advanced.
- The destination drive is full.
- The input file is corrupt or has an unsupported codec.

**"Out of disk space"**

Encode mode multiplies the output size. For a 4 GB source with three
profiles expect roughly 10–12 GB. The disk space precheck warns you about
this before starting, and you can disable it in Advanced if you know your
drive can handle it.

**"Executable not found" on launch**

The prebuilt executable needs FFmpeg and ffprobe on the same system. See
Requirements above. You can also set their full paths in the Advanced
section of the Convert tab.

**The app opens but the window is tiny / off-screen**

Window geometry is restored from the settings database. If your monitor
configuration has changed (e.g. you unplugged an external display), the
restored geometry may be off-screen. Delete the settings database and
restart:

```
# Windows
del "%APPDATA%\devdasher\hls-converter-gui\settings.sql"

# macOS / Linux
rm ~/Library/Application\ Support/hls-converter-gui/settings.sql
rm ~/.local/share/hls-converter-gui/settings.sql
```

**How do I reset all settings?**

Delete the settings database (see location above) and restart the app.

**Where do I report a bug?**

Please include:

- Your OS and version
- The app version (**Help → About**)
- The **first** `[ERROR]` line from the Log tab
- Whether you're using the prebuilt executable or the pip install

File issues at: **https://github.com/devdasher/hls-converter-gui/issues**

---

## Beta notes

This is a **Beta** release. The following may still change before 1.0:

- The UI layout and label wording
- The settings database schema (fields may be renamed or added)
- The bundled player's behaviour and supported media formats
- The prebuilt executable packaging (PyInstaller options, bundled modules)

**Known limitations in this Beta:**

- The built-in HLS player cannot render HLS subtitles reliably. Use an
  external player (VLC or MPV) for verification.
- macOS prebuilt executables are only produced for the architecture of the
  current GitHub runner (currently arm64). Intel Mac users should install
  via `pip`.
- No auto-update. Re-download from the Releases page when a new version
  is published.
- The GUI is a single-user, single-conversion tool. Running two
  conversions concurrently in the same instance is not supported.

Feedback is welcome at
**https://github.com/devdasher/hls-converter-gui/issues**.

---

## Related project — CLI

The GUI is a frontend for the **hls-converter-cli** package. All the
conversion logic, the FFmpeg command construction, and the HLS playlist
generator live there. The GUI only provides the interface, progress
display, preview player, and settings persistence.

If you want to use the converter from a script or a Python program,
install the CLI directly:

```
pip install hls-converter-cli
```

Full documentation:
**https://github.com/devdasher/hls-converter-cli**

---

## License

MIT — see the `LICENSE` file for details.

Copyright © 2026 devdasher
