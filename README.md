# hls-converter-gui

PySide6 desktop GUI for [`hls-converter-cli`](https://pypi.org/project/hls-converter-cli/), by [@devdasher](https://github.com/devdasher).

## Install

```bash
pip install hls-converter-gui
```

This automatically installs `hls-converter-cli` (the GUI depends on it)
and `PySide6>=6.7`.

You also need `ffmpeg` and `ffprobe` on your `PATH` (or set their full
paths in Advanced).

## Run

```bash
hls-converter-gui
```

Or from a source checkout:

```bash
pip install -e .
python hls_converter_gui.py
```

## Features

- Convert any MKV/MP4/MOV/TS file to HLS (copy or encode modes)
- Per-quality video profiles, per-track audio overrides
- Subtitle extraction with offset and time-scale correction
- Add audio/subtitle tracks to an existing stream folder
- Built-in HLS preview player with external player fallback (VLC/MPV)
- Drag & drop, live progress, persistent settings

## License

MIT

