#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HLS Converter GUI — desktop frontend for the hls-converter-cli package.

Part of the hls-converter-gui project:
    https://github.com/devdasher/hls-converter-gui

Install (recommended):
    pip install hls-converter-gui

This pulls in `hls-converter-cli` (which provides the
`hls_converter_cli` module) and `PySide6>=6.7` automatically. Then run:

    hls-converter-gui

From a source checkout:
    pip install -e .          # installs the declared dependencies
    python hls_converter_gui.py

Requires: Python 3.10+, PySide6 6.7+ (for HLS playback via the FFmpeg
backend), platformdirs 4+, and `hls-converter-cli` on the same
interpreter.
"""

# ------------------------------------------------------------------
# Force FFmpeg media backend before any PySide6 import.
# ------------------------------------------------------------------
import os
import sys

os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")


import base64
import http.server
import json
import re
import shutil
import socketserver
import sqlite3
import subprocess
import threading
import traceback
from functools import partial
from html import escape
from pathlib import Path
from typing import Final, Optional

try:
    from platformdirs import user_data_dir as _platformdirs_user_data_dir
except ImportError:  # pragma: no cover
    # Only happens if the user copied hls_gui.py without installing the
    # package. We provide a minimal fallback so the app still runs and
    # the settings DB lands in the right per-OS location.
    _platformdirs_user_data_dir = None

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import (
    QAction, QColor, QDesktopServices, QFont, QPalette, QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QFormLayout, QLabel, QLineEdit, QPushButton,
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QFileDialog,
    QTabWidget, QGroupBox, QTextEdit, QProgressBar, QMessageBox,
    QScrollArea, QFrame, QSlider, QSizePolicy,
)

try:
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
    from PySide6.QtMultimediaWidgets import QVideoWidget
    PLAYER_AVAILABLE = True
except ImportError:
    QMediaPlayer = None   # type: ignore
    QAudioOutput = None   # type: ignore
    QVideoWidget = None   # type: ignore
    PLAYER_AVAILABLE = False

# ------------------------------------------------------------------
# Windows: suppress the console window that ffmpeg / ffprobe would
# otherwise spawn. CREATE_NO_WINDOW is only defined on Windows.
#
# We patch subprocess.run / subprocess.Popen at module level, which
# affects every subsequent call in this process — including the ones
# made by convert_hls.py, since Python looks up `run`/`Popen` on the
# subprocess module object at call time (not at import time).
# ------------------------------------------------------------------
if sys.platform.startswith("win"):
    _CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW

    _original_run = subprocess.run

    def _patched_run(*args, **kwargs):
        kwargs.setdefault("creationflags", _CREATE_NO_WINDOW)
        return _original_run(*args, **kwargs)

    _original_popen = subprocess.Popen

    def _patched_popen(*args, **kwargs):
        # Respect an explicit creationflags if the caller already set one.
        existing = kwargs.get("creationflags", 0)
        kwargs["creationflags"] = existing | _CREATE_NO_WINDOW
        return _original_popen(*args, **kwargs)

    subprocess.run = _patched_run
    subprocess.Popen = _patched_popen

try:
    import hls_converter_cli as hls
    from hls_converter_cli import (
        HLSConverter, AppConfig, VideoRequest, VideoProfile,
        AudioConfig, AudioRequest, AudioProfile, SubtitleConfig,
        AddToStreamOptions, ConversionResult, ProgressEvent,
        CancellationToken, ConverterError,
        DEFAULT_VIDEO_CODEC, DEFAULT_VIDEO_PRESET,
        DEFAULT_AUDIO_CODEC, DEFAULT_AUDIO_PROFILE,
        DEFAULT_KEYFRAME_INTERVAL, DEFAULT_SEGMENT_TIME,
        DEFAULT_VIDEO_QUALITIES, DEFAULT_VIDEO_LEVEL,
        VIDEO_LEVEL_MULTIPLIERS, AUDIO_LEVEL_MULTIPLIERS,
        SUPPORTED_AUDIO_ENCODE_CODECS, DEFAULT_AUDIO_LEVEL,
    )
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        f"ERROR: could not import the `hls_converter_cli` module ({exc}).\n"
        f"\n"
        f"The GUI depends on the hls-converter-cli package. Install it with:\n"
        f"\n"
        f"    pip install hls-converter-cli\n"
        f"\n"
        f"or, to install both at once:\n"
        f"\n"
        f"    pip install hls-converter-gui\n"
    ) from exc


# ==================================================================
# App metadata
# ==================================================================

APP_NAME: Final[str] = "HLS Converter"
APP_SUBTITLE: Final[str] = "Powered by FFMPEG"
APP_VERSION: Final[str] = "0.7.0"
APP_STATUS: Final[str] = "Beta"
APP_AUTHOR: Final[str] = "devdasher"
APP_DESCRIPTION: Final[str] = (
    "A desktop GUI for HLS conversion with per-quality video settings, "
    "per-track audio overrides, live progress, a built-in HLS preview "
    "player with audio/subtitle selection, drag & drop, and "
    "dependency-aware controls."
)
APP_COPYRIGHT: Final[str] = f"© 2026 {APP_AUTHOR}"
GITHUB_LINK: Final[str] = "https://github.com/devdasher/hls-converter-gui"
CLI_PYPI_LINK: Final[str] = "https://pypi.org/project/hls-converter-cli/"

WINDOW_TITLE = f"{APP_NAME} — v{APP_VERSION} ({APP_STATUS})"

TAB_CONVERT = 0
TAB_ADD     = 1
TAB_PLAYER  = 2
TAB_LOG     = 3

DEFAULT_STREAM_DIR_NAME = "stream"
MIN_FREE_SPACE_BYTES = 500 * 1024 * 1024   # 500 MB
GUI_DEFAULT_THREADS = 1
SETTINGS_DB_NAME = "settings.sql"


# ==================================================================
# Smart folder naming
# ==================================================================

# Tokens that are almost always quality/codec/source markers in release
# filenames, not part of the actual content title. Stripped from the
# END of the filename (never from the middle), so a title like
# "HD.Story.2020" is preserved as "HD.Story".
_QUALITY_TOKENS: set[str] = {
    # Resolutions
    "2160p", "1440p", "1080p", "1080i", "720p", "576p", "480p",
    "360p", "240p", "4k", "8k", "uhd", "fhd", "qhd", "hd", "sd",
    # HDR variants
    "hdr", "hdr10", "dolby", "vision", "dv", "sdr", "hlg",
    # Video codecs
    "x264", "x265", "h264", "h265", "hevc", "avc", "av1", "vp9",
    "xvid", "divx", "mpeg2", "mpeg4", "10bit", "8bit", "yuv420p",
    # Sources
    "bluray", "blu", "bdrip", "brrip", "bdremux", "remux", "webrip",
    "web", "webdl", "hdtv", "hdtc", "dvdrip", "dvd", "hdrip",
    # Audio codecs / channels
    "aac", "ac3", "eac3", "dd", "ddp", "dts", "dtshd", "truehd",
    "atmos", "flac", "mp3", "opus",
    "1ch", "2ch", "5ch", "6ch", "7ch", "8ch",
    # Language markers
    "multi", "dual", "subs", "softsub", "softsubs", "hardsub",
    "hardsubs", "sub", "dub",
    # Release flags
    "proper", "repack", "extended", "uncut", "unrated", "remastered",
    "imax", "criterion", "directors", "director", "cut", "final",
    "theatrical", "anniversary", "edition",
}


def _strip_quality_suffixes(stem: str) -> str:
    """Return a folder-friendly base name derived from a media filename.

    Strips common quality / codec / source / release tokens from the
    filename so that different renders of the same title end up in the
    same folder. The first token is preserved even if it looks like a
    quality marker, so a title like "HD.Story" or "4K.Movie" isn't
    mangled.

        A.Girls.Perspective.2021.E01.1080p.Nevarky.JizzNak
            -> A.Girls.Perspective.2021.E01.Nevarky.JizzNak
        Movie.Name.2020.1080p.BluRay.x264
            -> Movie.Name.2020
        HD.Story.2020.720p.WEB-DL
            -> HD.Story.2020
    """
    if not stem:
        return stem

    # Remove brackets/parens, normalise separators.
    stripped = re.sub(r"[\[\]\(\)\{\}]", "", stem)
    normalized = re.sub(r"[._\-\s]+", ".", stripped).strip(".")
    if not normalized:
        return stem

    tokens = normalized.split(".")

    # Keep the first token unconditionally; strip quality markers
    # everywhere else.
    kept: list[str] = []
    removed_any = False
    for i, tok in enumerate(tokens):
        if i == 0:
            kept.append(tok)
            continue
        if tok.lower() in _QUALITY_TOKENS:
            removed_any = True
            continue
        kept.append(tok)

    if not kept or not removed_any:
        return stem
    return ".".join(kept)

# ==================================================================
# Path helpers
# ==================================================================

def _user_data_dir() -> Path:
    """Per-user, per-OS writable directory for settings.

    When the GUI is pip-installed, its own package directory lives
    inside site-packages (often read-only, and always wiped on
    upgrade), so we must NOT store the settings database next to the
    module. The correct locations are:

        Windows : %APPDATA%\\devdasher\\hls-converter-gui
        macOS   : ~/Library/Application Support/hls-converter-gui
        Linux   : ~/.local/share/hls-converter-gui  (or $XDG_DATA_HOME)

    `platformdirs` handles all three. If it's missing (e.g. the user
    copied hls_gui.py without installing the package), we fall back to
    the same paths by hand.
    """
    if _platformdirs_user_data_dir is not None:
        d = Path(_platformdirs_user_data_dir("hls-converter-gui", "devdasher"))
    else:  # pragma: no cover — fallback for a source checkout
        if sys.platform.startswith("win"):
            base = Path(
                os.environ.get("APPDATA")
                or (Path.home() / "AppData" / "Roaming")
            )
        elif sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support"
        else:
            base = Path(
                os.environ.get("XDG_DATA_HOME")
                or (Path.home() / ".local" / "share")
            )
        d = base / "hls-converter-gui"

    d.mkdir(parents=True, exist_ok=True)
    return d


def _migrate_old_settings(old_path: Path, new_path: Path) -> None:
    """One-shot migration from the old 'settings.sql next to hls_gui.py'
    layout to the new per-user data directory.

    The old layout only ever worked when the script was run from a
    writable checkout. If a legacy DB is found and the new one does not
    exist yet, we copy it over. Best-effort — failures are ignored.
    """
    if new_path.exists():
        return
    if not old_path.is_file():
        return
    try:
        shutil.copy2(old_path, new_path)
    except Exception:
        pass


# ==================================================================
# SQLite settings store
# ==================================================================

class SettingsDB:
    """Tiny key/value store backed by a SQLite file in the user's
    per-application data directory (see `_user_data_dir`).

    The SQLite connection is kept open for the entire lifetime of the
    application. On Windows, SQLite opens the file without the
    FILE_SHARE_DELETE flag, so the file cannot be deleted by another
    process while the app is running. When the app closes, close()
    releases the lock and the file can then be removed by the user.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        self._open()

    # ------------------------------------------------------------------

    def _open(self) -> None:
        try:
            # check_same_thread=False lets us use the connection from the
            # GUI thread and any worker threads; we serialise access with
            # the internal lock.
            self._conn = sqlite3.connect(
                str(self.path), timeout=5.0, check_same_thread=False,
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS settings ("
                "  key   TEXT PRIMARY KEY,"
                "  value TEXT NOT NULL"
                ")"
            )
            self._conn.commit()
        except Exception as exc:
            print(f"WARNING: could not open settings DB: {exc}", file=sys.stderr)
            self._conn = None

    # ------------------------------------------------------------------

    def get(self, key: str, default: str = "") -> str:
        if self._conn is None:
            return default
        try:
            with self._lock:
                cur = self._conn.execute(
                    "SELECT value FROM settings WHERE key = ?", (key,)
                )
                row = cur.fetchone()
                return str(row[0]) if row else default
        except Exception:
            return default

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self.get(key, str(default)))
        except (TypeError, ValueError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        v = self.get(key, "1" if default else "0").strip().lower()
        return v in ("1", "true", "yes", "on")

    def set(self, key: str, value: str) -> None:
        if self._conn is None:
            return
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, str(value)),
                )
                self._conn.commit()
        except Exception as exc:
            print(
                f"WARNING: could not save setting {key}: {exc}",
                file=sys.stderr,
            )

    def set_many(self, pairs: dict[str, str]) -> None:
        if self._conn is None:
            return
        try:
            with self._lock:
                self._conn.executemany(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    [(k, str(v)) for k, v in pairs.items()],
                )
                self._conn.commit()
        except Exception as exc:
            print(f"WARNING: could not save settings batch: {exc}", file=sys.stderr)

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None


# ==================================================================
# Log filtering
# ==================================================================

_LOG_NOISE_PATTERNS = [
    re.compile(r"^\[hls @ [^\]]+\] Opening '[^']*' for writing\s*$"),
    re.compile(r"^\[hls @ [^\]]+\] Failed to open file '[^']*'\s*$"),
    re.compile(r"^Press \[q\] to stop.*$"),
    re.compile(r"^\s*$"),
]


def _is_log_noise(level: str, message: str) -> bool:
    if level != "ERROR":
        return False
    msg = message.strip()
    for pat in _LOG_NOISE_PATTERNS:
        if pat.match(msg):
            return True
    return False


def _friendly_error_summary(raw: str) -> str:
    if not raw:
        return "An unknown error occurred."
    low = raw.lower()

    if "no space left on device" in low or "errno 28" in low or "-28 " in low:
        return (
            "Out of disk space on the output drive.\n\n"
            "Free up space on the destination drive, or choose a "
            "different output folder, then try again."
        )
    if "permission denied" in low or "errno 13" in low:
        return (
            "Permission denied.\n\n"
            "Make sure the output folder is writable and that no other "
            "program is currently using files in it."
        )
    if "executable not found" in low:
        return (
            "FFmpeg or ffprobe could not be found.\n\n"
            "Install FFmpeg and make sure it is on your PATH, or set the "
            "full path in the Advanced section."
        )
    if "does not exist" in low or "no such file or directory" in low:
        return (
            "A required file or folder was not found.\n\n"
            "Double-check the input file and output folder paths."
        )
    if "cancelled by user" in low:
        return "Conversion was cancelled."
    if "could not parse ffprobe json" in low:
        return (
            "Could not read media information.\n\n"
            "The input file may be corrupt, or ffprobe may not be "
            "installed correctly."
        )
    if "unsupported audio codec" in low:
        return raw
    if "video playlist was not created" in low:
        return (
            "FFmpeg finished but did not produce a playlist.\n\n"
            "This usually means the codec or container is not supported "
            "by your FFmpeg build."
        )
    return raw


def _disk_usage_for(path: Path) -> tuple[int, int]:
    p = path
    while True:
        if p.exists():
            try:
                u = shutil.disk_usage(p)
                return u.free, u.total
            except Exception:
                return -1, -1
        parent = p.parent
        if parent == p:
            return -1, -1
        p = parent


# ==================================================================
# Small helpers
# ==================================================================

def _fmt_ms(ms: int) -> str:
    if not ms or ms <= 0:
        return "--:--"
    s = ms // 1000
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def _fmt_bytes(n: int) -> str:
    if n < 0:
        return "?"
    units = ["B", "KB", "MB", "GB", "TB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{f:.1f} {u}"
        f /= 1024
    return f"{f:.1f} TB"


def _default_output_dir(input_path: Path) -> Path:
    base = _strip_quality_suffixes(input_path.stem) or input_path.stem
    return Path.cwd() / DEFAULT_STREAM_DIR_NAME / base


def _next_to_source_dir(input_path: Path) -> Path:
    base = _strip_quality_suffixes(input_path.stem) or input_path.stem
    return input_path.parent / f"{base}-stream"

def _make_hint(text: str) -> QLabel:
    """Small grey description label under a field."""
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet("color: #777;")
    return lbl


def _find_master_playlist(playlist: Path) -> Optional[Path]:
    if playlist.is_dir():
        m = playlist / "master.m3u8"
        return m if m.is_file() else None
    if playlist.name == "master.m3u8":
        return playlist
    sibling = playlist.parent / "master.m3u8"
    if sibling.is_file():
        return sibling
    p = playlist.parent
    for _ in range(3):
        parent = p.parent
        if parent == p:
            break
        m = parent / "master.m3u8"
        if m.is_file():
            return m
        p = parent
    return None


def _parse_ext_x_media_attrs(payload: str) -> dict:
    attrs: dict[str, str] = {}
    i, n = 0, len(payload)
    while i < n:
        while i < n and payload[i] in " \t,":
            i += 1
        if i >= n:
            break
        j = i
        while j < n and payload[j] not in "=,":
            j += 1
        key = payload[i:j].strip()
        if j >= n or payload[j] != "=":
            i = j + 1
            continue
        j += 1
        if j < n and payload[j] == '"':
            j += 1
            k = j
            while k < n and payload[k] != '"':
                k += 1
            value = payload[j:k]
            i = k + 1
        else:
            k = j
            while k < n and payload[k] != ",":
                k += 1
            value = payload[j:k].strip()
            i = k + 1
        attrs[key] = value
    return attrs


def _parse_master_playlist(master: Path) -> dict:
    empty = {"audio": [], "subtitles": []}
    try:
        text = master.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return empty

    audio: list[dict] = []
    subs: list[dict] = []

    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("#EXT-X-MEDIA:"):
            continue
        attrs = _parse_ext_x_media_attrs(line[len("#EXT-X-MEDIA:"):])
        t = attrs.get("TYPE", "").upper()
        entry = {
            "name":       attrs.get("NAME", ""),
            "language":   attrs.get("LANGUAGE", ""),
            "group_id":   attrs.get("GROUP-ID", ""),
            "uri":        attrs.get("URI", ""),
            "default":    attrs.get("DEFAULT", "NO").upper() == "YES",
            "autoselect": attrs.get("AUTOSELECT", "NO").upper() == "YES",
        }
        if t == "AUDIO":
            audio.append(entry)
        elif t == "SUBTITLES":
            subs.append(entry)

    return {"audio": audio, "subtitles": subs}


def _track_label(entry: dict, fallback_index: int) -> str:
    parts: list[str] = []
    lang = (entry.get("language") or "").strip()
    if lang and lang.lower() != "und":
        parts.append(lang.upper())
    name = (entry.get("name") or "").strip()
    if name:
        parts.append(name)
    if not parts:
        parts.append(f"Track {fallback_index}")
    if entry.get("default"):
        parts.append("★")
    return " — ".join(parts)


# ==================================================================
# Local HLS HTTP server
# ==================================================================

class _QuietHLSHandler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".m3u8": "application/vnd.apple.mpegurl",
        ".m3u":  "application/vnd.apple.mpegurl",
        ".ts":   "video/mp2t",
        ".m4s":  "video/iso.segment",
        ".mp4":  "video/mp4",
        ".vtt":  "text/vtt",
        ".aac":  "audio/aac",
        ".m4a":  "audio/mp4",
    }

    def __init__(self, *args, directory=None, **kwargs):
        super().__init__(*args, directory=directory, **kwargs)

    def log_message(self, fmt, *args):
        pass

    def log_error(self, fmt, *args):
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionAbortedError,
                ConnectionResetError,
                BrokenPipeError,
                OSError):
            self.close_connection = True

    def copyfile(self, source, outputfile):
        try:
            super().copyfile(source, outputfile)
        except (ConnectionAbortedError,
                ConnectionResetError,
                BrokenPipeError,
                OSError):
            pass


class _ThreadingHTTPServer(socketserver.ThreadingMixIn,
                           http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32
    block_on_close = False


class LocalHLSServer:
    def __init__(self) -> None:
        self._httpd: Optional[_ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.root: Optional[Path] = None
        self.port: int = 0

    def start(self, root: Path) -> str:
        self.stop()
        self.root = root
        handler = partial(_QuietHLSHandler, directory=str(root))
        self._httpd = _ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            kwargs={"poll_interval": 0.2},
            daemon=True,
        )
        self._thread.start()
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        httpd = self._httpd
        self._httpd = None
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        self._thread = None
        self.root = None
        self.port = 0


# ==================================================================
# Background worker thread
# ==================================================================

class ConversionWorker(QThread):
    log_signal = Signal(str, str)
    progress_signal = Signal(object)
    finished_signal = Signal(object)

    def __init__(
        self,
        config: AppConfig,
        *,
        mode: str,
        input_file: Path,
        output_dir: Optional[Path] = None,
        stream_dir: Optional[Path] = None,
        options: Optional[AddToStreamOptions] = None,
        dry_run: bool = False,
    ) -> None:
        super().__init__()
        self.config = config
        self.mode = mode
        self.input_file = input_file
        self.output_dir = output_dir
        self.stream_dir = stream_dir
        self.options = options
        self.dry_run = dry_run
        self.cancel_token = CancellationToken()

    def run(self) -> None:
        # `convert_hls._DRY_RUN` is a module-level global. The GUI only
        # ever runs one worker at a time (the Start button is disabled
        # while a conversion is in flight), so this assignment is safe.
        hls._DRY_RUN = self.dry_run
        converter = HLSConverter(self.config)

        def on_log(level: str, message: str) -> None:
            self.log_signal.emit(level, message)

        def on_progress(ev: ProgressEvent) -> None:
            self.progress_signal.emit(ev)

        try:
            if self.mode == "convert":
                result = converter.convert(
                    input_file=self.input_file,
                    output_dir=self.output_dir,
                    on_log=on_log,
                    on_progress=on_progress,
                    cancel_token=self.cancel_token,
                )
            else:
                result = converter.add_to_stream(
                    input_file=self.input_file,
                    stream_dir=self.stream_dir,
                    options=self.options or AddToStreamOptions(),
                    on_log=on_log,
                    on_progress=on_progress,
                    cancel_token=self.cancel_token,
                )
        except Exception as exc:
            self.log_signal.emit("ERROR", f"Unhandled exception: {exc}")
            self.log_signal.emit("ERROR", traceback.format_exc())
            result = ConversionResult(success=False, error=str(exc))

        self.finished_signal.emit(result)

    def cancel(self) -> None:
        self.cancel_token.cancel()


# ==================================================================
# Main window
# ==================================================================

class MainWindow(QMainWindow):

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumSize(550, 650)   # was 640, 520
        self.resize(600, 700)           # was 900, 720

        self.setAcceptDrops(True)

        self.worker: Optional[ConversionWorker] = None

        # Settings database — kept open for the app's lifetime so the
        # file can't be deleted while running (SQLite holds an OS lock).
        # Stored in the per-user application data directory so it works
        # when the GUI is pip-installed (site-packages is read-only and
        # is wiped on upgrade).
        settings_dir = _user_data_dir()
        new_db_path = settings_dir / SETTINGS_DB_NAME
        # Best-effort: migrate a legacy DB from a source checkout.
        _migrate_old_settings(
            Path(__file__).resolve().parent / SETTINGS_DB_NAME,
            new_db_path,
        )
        try:
            self._settings_db: Optional[SettingsDB] = SettingsDB(new_db_path)
        except Exception:
            self._settings_db = None

        self._player: Optional[QMediaPlayer] = None
        self._audio_output: Optional[QAudioOutput] = None
        self._hls_server = LocalHLSServer()
        self._master_path: Optional[Path] = None
        self._audio_renditions: list[dict] = []
        self._subtitle_renditions: list[dict] = []
        self._source_loaded = False
        self._pending_audio_track: Optional[int] = None
        self._pending_subtitle_track: Optional[int] = None

        self._url_candidates: list[QUrl] = []
        self._url_index: int = 0

        self._external_players: dict[str, Optional[str]] = {}
        self._manual_player_path: str = ""

        self._last_error_was_disk_full = False

        self._build_menu()
        self._build_ui()
        self._init_player()

        # Load saved settings (blocks signals while applying).
        self._load_settings_from_db()

        self._log_backend_info()
        self._wire_dependencies()
        self._refresh_all_dependencies()

        # Restore window geometry, if any.
        self._restore_geometry()

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        menu = self.menuBar()

        file_menu = menu.addMenu("&File")
        act_open = QAction("&Open input…", self)
        act_open.setShortcut("Ctrl+O")
        act_open.triggered.connect(self._pick_input)
        file_menu.addAction(act_open)

        act_out = QAction("Choose &output…", self)
        act_out.setShortcut("Ctrl+Shift+O")
        act_out.triggered.connect(self._pick_output)
        file_menu.addAction(act_out)

        file_menu.addSeparator()
        act_play = QAction("&Play HLS playlist…", self)
        act_play.setShortcut("Ctrl+P")
        act_play.triggered.connect(self._pick_playlist)
        file_menu.addAction(act_play)

        act_save_settings = QAction("&Save settings", self)
        act_save_settings.setShortcut("Ctrl+Shift+S")
        act_save_settings.triggered.connect(self._on_save_settings)
        file_menu.addAction(act_save_settings)

        act_save_log = QAction("Save &log…", self)
        act_save_log.setShortcut("Ctrl+S")
        act_save_log.triggered.connect(self._save_log)
        file_menu.addAction(act_save_log)

        file_menu.addSeparator()
        act_quit = QAction("&Quit", self)
        act_quit.setShortcut("Ctrl+Q")
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        help_menu = menu.addMenu("&Help")
        act_about = QAction(f"&About {APP_NAME}", self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

    def _show_about(self) -> None:
        db_path = ""
        if self._settings_db is not None:
            db_path = str(self._settings_db.path)
        player_line = (
            "Includes a built-in HLS preview player with audio/subtitle "
            "selection."
            if PLAYER_AVAILABLE
            else "QtMultimedia is not available; the player tab is disabled."
        )
        box = QMessageBox(self)
        box.setWindowTitle(f"About {APP_NAME}")
        box.setIcon(QMessageBox.Icon.Information)
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        box.setText(
            f"<h3>{APP_NAME} <small>v{APP_VERSION} ({APP_STATUS})</small></h3>"
            f"<p>{APP_DESCRIPTION}</p>"
            f"<p><b>{APP_SUBTITLE}</b><br>{player_line}</p>"
            f"<hr>"
            f"<p><b>Quick start</b></p>"
            f"<ul>"
            f"<li><b>Convert</b> — pick a media file; the HLS stream is "
            f"written to <code>./{DEFAULT_STREAM_DIR_NAME}/&lt;input-stem&gt;/</code> "
            f"or next to the source.</li>"
            f"<li><b>Add track</b> — attach an audio or subtitle file to an "
            f"existing stream folder.</li>"
            f"<li><b>Player</b> — preview any <code>master.m3u8</code>.</li>"
            f"<li><b>Log</b> — live progress and messages.</li>"
            f"</ul>"
            f"<p><b>Settings</b></p>"
            f"<p>Click <i>Save Settings</i> (or just close the app) to "
            f"persist your preferences to:<br>"
            f"<code>{escape(db_path) if db_path else '(unavailable)'}</code></p>"
            f"<p>Troubleshooting</p>"
            f"<ul>"
            f"<li><i>Out of disk space</i> — free up room on the output "
            f"drive; the app estimates the required space before starting.</li>"
            f"<li><i>Built-in player won't open HLS</i> — upgrade to "
            f"PySide6 6.7+.</li>"
            f"<li><i>Subtitles don't show</i> — Qt's FFmpeg backend has "
            f"limited HLS subtitle rendering. Use an external player.</li>"
            f"</ul>"
            f"<p><b>Links</b></p>"
            f"<ul>"
            f"<li>GUI source: <a href='{GITHUB_LINK}'>{GITHUB_LINK}</a></li>"
            f"<li>CLI package: <a href='{CLI_PYPI_LINK}'>{CLI_PYPI_LINK}</a></li>"
            f"</ul>"
            f"<p><small>{APP_COPYRIGHT}</small></p>"
        )
        box.exec()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_convert_tab(), "Convert")
        self.tabs.addTab(self._build_add_tab(), "Add track")
        self.tabs.addTab(self._build_player_tab(), "Player")
        self.tabs.addTab(self._build_log_tab(), "Log")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self.tabs, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(6)

        self.stage_bar = QProgressBar()
        self.stage_bar.setRange(0, 100)
        self.stage_bar.setValue(0)
        self.stage_bar.setTextVisible(True)

        self.start_btn = QPushButton("Start")
        self.start_btn.setDefault(True)
        self.start_btn.clicked.connect(self._on_start)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel)

        self.save_settings_btn = QPushButton("Save Settings")
        self.save_settings_btn.setToolTip(
            "Persist the current preferences to the settings database."
        )
        self.save_settings_btn.clicked.connect(self._on_save_settings)

        bottom.addWidget(self.stage_bar, 1)
        bottom.addWidget(self.start_btn)
        bottom.addWidget(self.cancel_btn)
        bottom.addWidget(self.save_settings_btn)
        root.addLayout(bottom)

        self.status_label = QLabel("Ready.")
        root.addWidget(self.status_label)

    @staticmethod
    def _scroll(content: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(content)
        return scroll

    # ------------------------------------------------------------------
    # Convert tab
    # ------------------------------------------------------------------

    def _build_convert_tab(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        layout.addWidget(self._build_files_group())
        layout.addWidget(self._build_video_group())
        layout.addWidget(self._build_audio_group())
        layout.addWidget(self._build_subtitle_group())
        layout.addWidget(self._build_advanced_group())
        layout.addStretch(1)

        return self._scroll(content)

    def _build_files_group(self) -> QGroupBox:
        box = QGroupBox("Files")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        grid.setContentsMargins(12, 16, 12, 12)
        grid.setColumnStretch(1, 1)

        row = 0
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText(
            r"e.g. D:\Videos\Blade.Runner.1982.mkv  or  /home/user/movie.mp4"
        )
        in_btn = QPushButton("Browse…")
        in_btn.clicked.connect(self._pick_input)
        grid.addWidget(QLabel("Input:"), row, 0)
        grid.addWidget(self.input_edit, row, 1)
        grid.addWidget(in_btn, row, 2)
        row += 1
        grid.addWidget(
            _make_hint("Source media file to convert (MKV, MP4, MOV, TS, …)."),
            row, 1, 1, 2,
        )
        row += 1

        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText(
            r"e.g. D:\Videos\Blade.Runner.1982-stream   (leave empty for default)"
        )
        out_btn = QPushButton("Browse…")
        out_btn.clicked.connect(self._pick_output)

        merge_btn = QPushButton("Merge with…")
        merge_btn.setToolTip(
            "Pick an existing stream folder to add this file's "
            "qualities into."
        )
        merge_btn.clicked.connect(self._pick_merge_target)

        out_row = QHBoxLayout()
        out_row.setContentsMargins(0, 0, 0, 0)
        out_row.setSpacing(4)
        out_row.addWidget(self.output_edit, 1)
        out_row.addWidget(out_btn)
        out_row.addWidget(merge_btn)

        out_widget = QWidget()
        out_widget.setLayout(out_row)
        grid.addWidget(QLabel("Output:"), row, 0)
        grid.addWidget(out_widget, row, 1, 1, 2)
        row += 1
        self.output_hint = _make_hint(
            f"Folder that will contain the HLS stream. Default is "
            f"./{DEFAULT_STREAM_DIR_NAME}/<input-stem>/ in the current directory."
        )
        grid.addWidget(self.output_hint, row, 1, 1, 2)
        row += 1

        self.output_next_to_source_cb = QCheckBox(
            "Save output next to the source file"
        )
        self.output_next_to_source_cb.setChecked(True)
        self.output_next_to_source_cb.setToolTip(
            "When checked, the stream is written to "
            "<source folder>/<source name>-stream/ instead of ./stream/."
        )
        self.output_next_to_source_cb.toggled.connect(
            self._on_output_next_to_source_toggled
        )
        # Apply the initial state right away so the Output field starts
        self._on_output_next_to_source_toggled(True)
        grid.addWidget(self.output_next_to_source_cb, row, 1, 1, 2)
        row += 1
        grid.addWidget(
            _make_hint(
                "Convenient when your media library lives on a different "
                "drive than the app."
            ),
            row, 1, 1, 2,
        )
        row += 1

        self.clean_cb = QCheckBox(
            "Remove existing output directory first (--clean)"
        )
        self.clean_cb.setToolTip(
            "Deletes the output folder before starting."
        )
        grid.addWidget(self.clean_cb, row, 1, 1, 2)

        return box

    def _build_video_group(self) -> QGroupBox:
        box = QGroupBox("Video")
        form = QFormLayout(box)
        form.setSpacing(4)
        form.setContentsMargins(12, 16, 12, 12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFormAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )

        self.video_mode = QComboBox()
        self.video_mode.addItems(["copy", "encode", "none"])
        self.video_mode.setToolTip(
            "copy: stream-copy the source video track\n"
            "encode: re-encode to one or more profiles\n"
            "none: skip video entirely"
        )
        form.addRow("Mode:", self.video_mode)
        form.addRow(_make_hint(
            "copy = no re-encoding (fastest); "
            "encode = re-encode to chosen resolutions; "
            "none = skip video."
        ))

        self.video_label = QLineEdit()
        self.video_label.setPlaceholderText("e.g. 1080  or  hd  or  source")
        form.addRow("Copy label:", self.video_label)
        form.addRow(_make_hint(
            "Optional folder name for copy mode. "
            "If empty, the source height is used (e.g. \"1080p\")."
        ))

        prof_widget = QWidget()
        prof_grid = QGridLayout(prof_widget)
        prof_grid.setContentsMargins(0, 0, 0, 0)
        prof_grid.setHorizontalSpacing(16)
        prof_grid.setVerticalSpacing(2)
        self.profile_checks: dict[int, QCheckBox] = {}
        heights = [240, 360, 480, 540, 720, 1080, 1440, 2160]
        for i, h in enumerate(heights):
            cb = QCheckBox(f"{h}p")
            cb.setChecked(h in DEFAULT_VIDEO_QUALITIES)
            self.profile_checks[h] = cb
            prof_grid.addWidget(cb, i // 4, i % 4)
        prof_grid.setColumnStretch(4, 1)
        form.addRow("Encode profiles:", prof_widget)
        form.addRow(_make_hint(
            "Target resolutions to encode. Only used in encode mode."
        ))

        self.video_level = QComboBox()
        self.video_level.addItems(list(VIDEO_LEVEL_MULTIPLIERS.keys()))
        self.video_level.setCurrentText(DEFAULT_VIDEO_LEVEL)
        form.addRow("Bitrate level:", self.video_level)
        form.addRow(_make_hint(
            "data-saver / small / balanced / high / source. "
            "Higher = bigger files and better quality."
        ))

        self.video_rate_mode = QComboBox()
        self.video_rate_mode.addItems(["auto", "crf", "abr", "2pass"])
        form.addRow("Rate mode:", self.video_rate_mode)
        form.addRow(_make_hint(
            "auto picks CRF if you set a CRF override below, else ABR."
        ))

        self.video_codec = QLineEdit(DEFAULT_VIDEO_CODEC)
        self.video_codec.setPlaceholderText(
            "libx264 (H.264, default) | libx265 (H.265) | h264_nvenc (NVIDIA)"
        )
        form.addRow("Encoder:", self.video_codec)
        form.addRow(_make_hint(
            "FFmpeg encoder name. libx264 for widest compatibility, "
            "libx265 for smaller files, h264_nvenc for NVIDIA GPUs."
        ))

        self.video_preset = QLineEdit(DEFAULT_VIDEO_PRESET)
        self.video_preset.setPlaceholderText(
            "ultrafast | fast | medium | slow | veryslow"
        )
        form.addRow("Preset:", self.video_preset)
        form.addRow(_make_hint(
            "Speed/quality tradeoff for libx264 / libx265. "
            "Slower presets give smaller files at the same quality."
        ))

        self.video_bitrate_edit = QLineEdit()
        self.video_bitrate_edit.setPlaceholderText(
            "e.g. 480=850,720=1300,1080=2800"
        )
        form.addRow("Bitrate overrides:", self.video_bitrate_edit)
        form.addRow(_make_hint(
            "Per-quality target bitrate in kbps, comma-separated, "
            "KEY=VALUE where KEY is the height in pixels. Used with "
            "ABR / 2pass rate modes."
        ))

        self.video_crf_edit = QLineEdit()
        self.video_crf_edit.setPlaceholderText(
            "e.g. 480=24,720=20,1080=18"
        )
        form.addRow("CRF overrides:", self.video_crf_edit)
        form.addRow(_make_hint(
            "Per-quality CRF value, comma-separated. Lower = better "
            "quality & bigger files. Typical range: 18–28."
        ))

        return box

    def _build_audio_group(self) -> QGroupBox:
        box = QGroupBox("Audio")
        form = QFormLayout(box)
        form.setSpacing(4)
        form.setContentsMargins(12, 16, 12, 12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFormAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )

        self.audio_mode = QComboBox()
        self.audio_mode.addItems([
            "Copy all tracks",
            "Encode all tracks",
            "Skip audio",
            "Encode specific tracks",
        ])
        form.addRow("Mode:", self.audio_mode)
        form.addRow(_make_hint(
            "Copy = keep source audio; Encode = re-encode to profiles."
        ))

        self.audio_tracks = QLineEdit()
        self.audio_tracks.setPlaceholderText("e.g. 1,3   (comma-separated)")
        form.addRow("Specific tracks:", self.audio_tracks)
        form.addRow(_make_hint(
            "Comma-separated 1-based track numbers, used when 'Encode "
            "specific tracks' is selected. Example: 1,3"
        ))

        self.audio_codec = QComboBox()
        self.audio_codec.addItems(sorted(SUPPORTED_AUDIO_ENCODE_CODECS))
        if DEFAULT_AUDIO_CODEC in SUPPORTED_AUDIO_ENCODE_CODECS:
            self.audio_codec.setCurrentText(DEFAULT_AUDIO_CODEC)
        form.addRow("Encoder:", self.audio_codec)
        form.addRow(_make_hint(
            "aac = best HLS compatibility; libopus / ac3 / eac3 = "
            "higher quality at the same bitrate."
        ))

        self.audio_profile = QLineEdit(DEFAULT_AUDIO_PROFILE)
        self.audio_profile.setPlaceholderText(
            "aac_low (AAC-LC, default) | aac_he | aac_he_v2"
        )
        form.addRow("AAC profile:", self.audio_profile)
        form.addRow(_make_hint(
            "Only used when the encoder is aac. aac_he / aac_he_v2 are "
            "for very low bitrates."
        ))

        self.audio_profiles_edit = QLineEdit()
        self.audio_profiles_edit.setPlaceholderText(
            "e.g. 128:2,224:6   (bitrate:channels, comma-separated)"
        )
        form.addRow("Profiles:", self.audio_profiles_edit)
        form.addRow(_make_hint(
            "Encoded audio renditions, comma-separated. Each entry is "
            "BITRATE or BITRATE:CHANNELS. Empty = defaults (48/64/96/128/"
            "256/320 kbps)."
        ))

        self.audio_level = QComboBox()
        self.audio_level.addItems(list(AUDIO_LEVEL_MULTIPLIERS.keys()))
        self.audio_level.setCurrentText(DEFAULT_AUDIO_LEVEL)
        form.addRow("Bitrate level:", self.audio_level)
        form.addRow(_make_hint(
            "Auto-bitrate multiplier for each profile. 'source' keeps the "
            "original bitrate."
        ))

        self.audio_bitrate_edit = QLineEdit()
        self.audio_bitrate_edit.setPlaceholderText(
            "e.g. 1=128,2=224   (track=bitrate kbps)"
        )
        form.addRow("Bitrate overrides:", self.audio_bitrate_edit)
        form.addRow(_make_hint(
            "Per-track bitrate in kbps (keyed by track number). "
            "Example above forces track 1 to 128 kbps, track 2 to 224 kbps."
        ))

        self.audio_channels_edit = QLineEdit()
        self.audio_channels_edit.setPlaceholderText(
            "e.g. 1=2,2=6   (track=channels)"
        )
        form.addRow("Channel overrides:", self.audio_channels_edit)
        form.addRow(_make_hint(
            "Per-track channel count. Example above downmixes track 1 to "
            "stereo and keeps 5.1 on track 2."
        ))

        return box

    def _build_subtitle_group(self) -> QGroupBox:
        box = QGroupBox("Subtitles")
        form = QFormLayout(box)
        form.setSpacing(4)
        form.setContentsMargins(12, 16, 12, 12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFormAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )

        self.subs_mode = QComboBox()
        self.subs_mode.addItems(["auto", "none"])
        form.addRow("Mode:", self.subs_mode)
        form.addRow(_make_hint(
            "auto = extract every text subtitle as WebVTT; "
            "none = skip subtitles entirely."
        ))

        self.subs_offset = QLineEdit()
        self.subs_offset.setPlaceholderText(
            "e.g. 500 (delay)   or   1=500,2=-300   (per track)"
        )
        form.addRow("Offset (ms):", self.subs_offset)
        form.addRow(_make_hint(
            "Shift subtitles forward (positive) or backward (negative) in ms. "
            "Use TRACK=MS for per-track offsets."
        ))

        self.subs_scale = QLineEdit()
        self.subs_scale.setPlaceholderText(
            "e.g. 25/23.976   (source FPS / target FPS)"
        )
        form.addRow("Time scale:", self.subs_scale)
        form.addRow(_make_hint(
            "Fix subtitle drift by scaling timestamps. Common: subtitles "
            "timed for 25 fps but video is 23.976 fps."
        ))

        return box

    def _build_advanced_group(self) -> QGroupBox:
        box = QGroupBox("Advanced")
        form = QFormLayout(box)
        form.setSpacing(4)
        form.setContentsMargins(12, 16, 12, 12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFormAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )

        self.ffmpeg_edit = QLineEdit("ffmpeg")
        self.ffmpeg_edit.setPlaceholderText(
            r"e.g. C:\ffmpeg\bin\ffmpeg.exe   or   ffmpeg (if on PATH)"
        )
        form.addRow("ffmpeg path:", self.ffmpeg_edit)
        form.addRow(_make_hint(
            "Full path to ffmpeg.exe if it isn't on your PATH."
        ))

        self.ffprobe_edit = QLineEdit("ffprobe")
        self.ffprobe_edit.setPlaceholderText(
            r"e.g. C:\ffmpeg\bin\ffprobe.exe  or   ffprobe (if on PATH)"
        )
        form.addRow("ffprobe path:", self.ffprobe_edit)
        form.addRow(_make_hint(
            "Full path to ffprobe.exe if it isn't on your PATH."
        ))

        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 128)
        self.threads_spin.setValue(GUI_DEFAULT_THREADS)
        self.threads_spin.setSuffix(" threads")
        form.addRow("Threads:", self.threads_spin)
        form.addRow(_make_hint(
            "CPU threads used by the encoder. Default is 1; set to your "
            "physical core count for faster encodes."
        ))

        self.segment_spin = QSpinBox()
        self.segment_spin.setRange(1, 3600)
        self.segment_spin.setValue(DEFAULT_SEGMENT_TIME)
        self.segment_spin.setSuffix(" s")
        form.addRow("Segment time:", self.segment_spin)
        form.addRow(_make_hint(
            "HLS segment duration in seconds. 6 s is a common default; "
            "shorter = faster seeking, more files."
        ))

        self.keyframe_spin = QDoubleSpinBox()
        self.keyframe_spin.setRange(0.1, 60.0)
        self.keyframe_spin.setSingleStep(0.5)
        self.keyframe_spin.setValue(DEFAULT_KEYFRAME_INTERVAL)
        self.keyframe_spin.setSuffix(" s")
        form.addRow("Keyframe interval:", self.keyframe_spin)
        form.addRow(_make_hint(
            "Force a keyframe every N seconds. Must be <= segment time "
            "for clean segment boundaries."
        ))

        self.default_audio_spin = QSpinBox()
        self.default_audio_spin.setRange(0, 999)
        self.default_audio_spin.setSpecialValueText("(auto)")
        form.addRow("Default audio track:", self.default_audio_spin)
        form.addRow(_make_hint(
            "Track number marked DEFAULT=YES in the master playlist. "
            "0 = automatic selection."
        ))

        self.default_subs_spin = QSpinBox()
        self.default_subs_spin.setRange(0, 999)
        self.default_subs_spin.setSpecialValueText("(auto)")
        form.addRow("Default subtitle track:", self.default_subs_spin)
        form.addRow(_make_hint(
            "Subtitle track marked DEFAULT=YES. 0 = none."
        ))

        self.skip_master_cb = QCheckBox("Do not (re)generate master.m3u8")
        form.addRow("", self.skip_master_cb)
        form.addRow(_make_hint(
            "Skip master playlist generation. Useful when running multiple "
            "conversions into the same folder."
        ))

        self.dry_run_cb = QCheckBox("Dry run — print ffmpeg commands only")
        form.addRow("", self.dry_run_cb)
        form.addRow(_make_hint(
            "Preview every ffmpeg command without running it."
        ))

        self.check_space_cb = QCheckBox(
            "Check free disk space before starting (recommended)"
        )
        self.check_space_cb.setChecked(True)
        form.addRow("", self.check_space_cb)
        form.addRow(_make_hint(
            "Warns you when the destination drive looks too small for "
            "the estimated output size."
        ))

        return box

    # ------------------------------------------------------------------
    # Add-track tab
    # ------------------------------------------------------------------

    def _build_add_tab(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        files_box = QGroupBox("Files")
        grid = QGridLayout(files_box)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        grid.setContentsMargins(12, 16, 12, 12)
        grid.setColumnStretch(1, 1)

        row = 0
        self.add_input_edit = QLineEdit()
        self.add_input_edit.setPlaceholderText(
            r"e.g. D:\Audio\commentary.m4a  or  D:\Subs\english.srt"
        )
        add_in_btn = QPushButton("Browse…")
        add_in_btn.clicked.connect(self._pick_add_input)
        grid.addWidget(QLabel("Track file:"), row, 0)
        grid.addWidget(self.add_input_edit, row, 1)
        grid.addWidget(add_in_btn, row, 2)
        row += 1
        grid.addWidget(
            _make_hint(
                "Audio (.m4a/.aac/.mp3/.ac3/.eac3/…) or subtitle "
                "(.srt/.ass/.ssa/.vtt) file to attach."
            ),
            row, 1, 1, 2,
        )
        row += 1

        self.add_stream_edit = QLineEdit()
        self.add_stream_edit.setPlaceholderText(
            r"e.g. D:\Videos\Blade.Runner.1982-stream"
        )
        add_stream_btn = QPushButton("Browse…")
        add_stream_btn.clicked.connect(self._pick_add_stream)
        grid.addWidget(QLabel("Stream folder:"), row, 0)
        grid.addWidget(self.add_stream_edit, row, 1)
        grid.addWidget(add_stream_btn, row, 2)
        row += 1
        grid.addWidget(
            _make_hint(
                "Existing HLS stream folder. Must contain a master.m3u8 "
                "or video/ subfolder."
            ),
            row, 1, 1, 2,
        )

        layout.addWidget(files_box)

        opts_box = QGroupBox("Track options")
        form = QFormLayout(opts_box)
        form.setSpacing(4)
        form.setContentsMargins(12, 16, 12, 12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFormAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )

        self.add_audio_mode = QComboBox()
        self.add_audio_mode.addItems(["auto", "copy", "encode"])
        form.addRow("Audio mode:", self.add_audio_mode)
        form.addRow(_make_hint(
            "auto = copy when the source codec is HLS-compatible, "
            "otherwise re-encode to the codec below."
        ))

        self.add_audio_codec = QComboBox()
        self.add_audio_codec.addItems(sorted(SUPPORTED_AUDIO_ENCODE_CODECS))
        if DEFAULT_AUDIO_CODEC in SUPPORTED_AUDIO_ENCODE_CODECS:
            self.add_audio_codec.setCurrentText(DEFAULT_AUDIO_CODEC)
        form.addRow("Audio codec:", self.add_audio_codec)
        form.addRow(_make_hint(
            "Target codec when the audio is re-encoded."
        ))

        self.add_audio_profile = QLineEdit(DEFAULT_AUDIO_PROFILE)
        self.add_audio_profile.setPlaceholderText(
            "aac_low (default) | aac_he | aac_he_v2"
        )
        form.addRow("AAC profile:", self.add_audio_profile)
        form.addRow(_make_hint("Only used when the codec is aac."))

        self.add_audio_level = QComboBox()
        self.add_audio_level.addItems(list(AUDIO_LEVEL_MULTIPLIERS.keys()))
        self.add_audio_level.setCurrentText(DEFAULT_AUDIO_LEVEL)
        form.addRow("Audio level:", self.add_audio_level)
        form.addRow(_make_hint(
            "Auto-bitrate level when re-encoding. 'source' keeps the "
            "original bitrate."
        ))

        self.add_track_spin = QSpinBox()
        self.add_track_spin.setRange(0, 999)
        self.add_track_spin.setSpecialValueText("(auto)")
        form.addRow("Track number:", self.add_track_spin)
        form.addRow(_make_hint(
            "Output track number. 0 = next available (auto)."
        ))

        self.add_lang_edit = QLineEdit()
        self.add_lang_edit.setPlaceholderText(
            "e.g. en, fa, de, ar, ja   (ISO 639-1)"
        )
        form.addRow("Language:", self.add_lang_edit)
        form.addRow(_make_hint(
            "ISO 639-1 language tag used in the master playlist."
        ))

        self.add_title_edit = QLineEdit()
        self.add_title_edit.setPlaceholderText(
            "e.g. Director's Commentary  or  Persian"
        )
        form.addRow("Title:", self.add_title_edit)
        form.addRow(_make_hint(
            "Human-readable title shown in the player's track menu."
        ))

        self.add_subs_offset = QLineEdit()
        self.add_subs_offset.setPlaceholderText(
            "e.g. 500 (delay)   or   1=500   (per track)"
        )
        form.addRow("Subtitle offset:", self.add_subs_offset)
        form.addRow(_make_hint(
            "Shift subtitles forward (positive) or backward (negative) in ms."
        ))

        self.add_subs_scale = QLineEdit()
        self.add_subs_scale.setPlaceholderText("e.g. 25/23.976")
        form.addRow("Subtitle scale:", self.add_subs_scale)
        form.addRow(_make_hint(
            "Fix subtitle drift by scaling timestamps."
        ))

        self.add_skip_master_cb = QCheckBox("Do not regenerate master.m3u8")
        form.addRow("", self.add_skip_master_cb)
        form.addRow(_make_hint(
            "Skip regenerating the master playlist after adding the track."
        ))

        layout.addWidget(opts_box)
        layout.addStretch(1)

        return self._scroll(content)

    # ------------------------------------------------------------------
    # Player tab
    # ------------------------------------------------------------------

    def _build_player_tab(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        if not PLAYER_AVAILABLE:
            msg = QLabel(
                "QtMultimedia is not available in this environment.\n\n"
                "Install / upgrade with:\n"
                "    pip install --upgrade \"PySide6>=6.7\"\n\n"
                "The rest of the application works normally."
            )
            msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            msg.setWordWrap(True)
            layout.addWidget(msg, 1)
            return content

        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(QLabel("Playlist:"))
        self.player_path_edit = QLineEdit()
        self.player_path_edit.setPlaceholderText(
            r"e.g. D:\Videos\Movie-stream\master.m3u8  (auto-filled after conversion)"
        )
        self.player_path_edit.textChanged.connect(self._on_player_path_changed)
        top.addWidget(self.player_path_edit, 1)

        open_file_btn = QPushButton("Open file…")
        open_file_btn.clicked.connect(self._pick_playlist)
        top.addWidget(open_file_btn)

        open_dir_btn = QPushButton("Open folder…")
        open_dir_btn.clicked.connect(self._pick_stream_folder)
        top.addWidget(open_dir_btn)

        reload_btn = QPushButton("Reload")
        reload_btn.clicked.connect(self._reload_current)
        top.addWidget(reload_btn)

        layout.addLayout(top)
        layout.addWidget(_make_hint(
            "After a successful conversion this field is filled in "
            "automatically. Just click Play."
        ))

        srv = QHBoxLayout()
        srv.setSpacing(6)
        srv.addWidget(QLabel("Server:"))
        self.server_url_edit = QLineEdit()
        self.server_url_edit.setReadOnly(True)
        self.server_url_edit.setPlaceholderText("(no local HTTP server running)")
        srv.addWidget(self.server_url_edit, 1)

        self.copy_url_btn = QPushButton("Copy URL")
        self.copy_url_btn.setEnabled(False)
        self.copy_url_btn.clicked.connect(self._copy_server_url)
        srv.addWidget(self.copy_url_btn)

        self.open_url_btn = QPushButton("Open in browser")
        self.open_url_btn.setEnabled(False)
        self.open_url_btn.clicked.connect(self._open_server_url)
        srv.addWidget(self.open_url_btn)

        layout.addLayout(srv)
        layout.addWidget(_make_hint(
            "Paste this URL into VLC, MPV, KMPlayer, or Safari if the "
            "built-in player has issues."
        ))

        ext = QHBoxLayout()
        ext.setSpacing(6)
        ext.addWidget(QLabel("External player:"))
        self.external_player_combo = QComboBox()
        self.external_player_combo.addItem("None (use built-in)", "")
        self.external_player_combo.setMinimumWidth(180)
        self.external_player_combo.currentIndexChanged.connect(
            self._on_external_player_changed
        )
        ext.addWidget(self.external_player_combo, 1)

        self.browse_player_btn = QPushButton("Browse…")
        self.browse_player_btn.setToolTip(
            "Pick an external player executable (vlc.exe, mpv.exe, …)."
        )
        self.browse_player_btn.clicked.connect(self._pick_external_player)
        ext.addWidget(self.browse_player_btn)

        self.launch_player_btn = QPushButton("Launch")
        self.launch_player_btn.setEnabled(False)
        self.launch_player_btn.clicked.connect(self._launch_external_player)
        ext.addWidget(self.launch_player_btn)

        layout.addLayout(ext)
        layout.addWidget(_make_hint(
            "External players handle HLS subtitles and seeking better than "
            "the built-in player. VLC / MPV are recommended."
        ))

        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(200)
        self.video_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        pal = self.video_widget.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(0, 0, 0))
        self.video_widget.setPalette(pal)
        self.video_widget.setAutoFillBackground(True)
        layout.addWidget(self.video_widget, 1)

        tracks_row = QHBoxLayout()
        tracks_row.setSpacing(8)

        tracks_row.addWidget(QLabel("Audio:"))
        self.audio_track_combo = QComboBox()
        self.audio_track_combo.setMinimumWidth(160)
        self.audio_track_combo.setEnabled(False)
        self.audio_track_combo.currentIndexChanged.connect(
            self._on_audio_track_changed
        )
        tracks_row.addWidget(self.audio_track_combo, 1)

        tracks_row.addWidget(QLabel("Subtitles:"))
        self.subtitle_track_combo = QComboBox()
        self.subtitle_track_combo.setMinimumWidth(160)
        self.subtitle_track_combo.setEnabled(False)
        self.subtitle_track_combo.currentIndexChanged.connect(
            self._on_subtitle_track_changed
        )
        tracks_row.addWidget(self.subtitle_track_combo, 1)

        layout.addLayout(tracks_row)
        layout.addWidget(_make_hint(
            "Note: the built-in player often can't render HLS subtitles. "
            "If subtitles don't appear, use the external player."
        ))

        controls = QHBoxLayout()
        controls.setSpacing(6)

        self.play_btn = QPushButton("Play")
        self.play_btn.setFixedWidth(80)
        self.play_btn.clicked.connect(self._toggle_play)
        controls.addWidget(self.play_btn)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setFixedWidth(80)
        self.stop_btn.clicked.connect(self._stop_playback)
        controls.addWidget(self.stop_btn)

        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.sliderMoved.connect(self._on_seek_moved)
        self.seek_slider.sliderReleased.connect(self._on_seek_released)
        controls.addWidget(self.seek_slider, 1)

        self.time_label = QLabel("--:-- / --:--")
        self.time_label.setMinimumWidth(80)
        self.time_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        controls.addWidget(self.time_label)

        controls.addWidget(QLabel("Vol:"))
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setFixedWidth(90)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        controls.addWidget(self.volume_slider)

        layout.addLayout(controls)

        return content

    # ------------------------------------------------------------------
    # Player initialisation
    # ------------------------------------------------------------------

    def _init_player(self) -> None:
        if not PLAYER_AVAILABLE:
            return
        self._player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_output)
        self._player.setVideoOutput(self.video_widget)

        self._audio_output.setVolume(self.volume_slider.value() / 100.0)

        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_state_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)

        try:
            self._player.errorOccurred.connect(self._on_player_error)
        except AttributeError:
            try:
                self._player.error.connect(self._on_player_error_old)
            except AttributeError:
                pass

    def _log_backend_info(self) -> None:
        if not PLAYER_AVAILABLE:
            self._append_log("WARN", "QtMultimedia is not available.")
            return
        try:
            import PySide6
            self._append_log("INFO", f"PySide6: {PySide6.__version__}")
        except Exception:
            pass
        backend = os.environ.get("QT_MEDIA_BACKEND", "(default)")
        self._append_log("INFO", f"Qt media backend: {backend}")
        try:
            from PySide6.QtMultimedia import QMediaDevices  # noqa: F401
            self._append_log("INFO", "QtMultimedia plugin loaded OK.")
        except Exception as exc:
            self._append_log("WARN", f"QtMultimedia plugin load problem: {exc}")

        try:
            import PySide6
            ver = tuple(int(x) for x in PySide6.__version__.split(".")[:2])
            if ver < (6, 6):
                self._append_log(
                    "WARN",
                    "PySide6 < 6.6 has broken HLS support in its FFmpeg "
                    "backend (QTBUG-111378). Run "
                    "`pip install --upgrade \"PySide6>=6.7\"`.",
                )
        except Exception:
            pass

        if self._settings_db is not None:
            self._append_log(
                "INFO", f"Settings DB: {self._settings_db.path}"
            )

        self._detect_external_players()
        for name, path in self._external_players.items():
            if path:
                self._append_log("INFO", f"External player found: {name} ({path})")
        if self._manual_player_path:
            self._append_log(
                "INFO", f"Manual external player: {self._manual_player_path}"
            )

    # ------------------------------------------------------------------
    # External player detection & launching
    # ------------------------------------------------------------------

    def _detect_external_players(self) -> None:
        self._external_players = {
            "MPV": None,
            "VLC": None,
            "MPC-HC": None,
            "KMPlayer": None,
        }

        candidates = {
            "MPV": ["mpv", "mpv.exe"],
            "VLC": ["vlc", "vlc.exe"],
            "MPC-HC": ["mpc-hc64", "mpc-hc64.exe", "mpc-hc", "mpc-hc.exe"],
            "KMPlayer": ["KMPlayer", "KMPlayer.exe", "kmplayer", "kmplayer.exe"],
        }

        common_paths = {
            "VLC": [
                r"C:\Program Files\VideoLAN\VLC\vlc.exe",
                r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
            ],
            "MPV": [
                r"C:\Program Files\mpv\mpv.exe",
                r"C:\Program Files (x86)\mpv\mpv.exe",
                r"C:\Program Files\mpv-x86_64\mpv.exe",
            ],
            "MPC-HC": [
                r"C:\Program Files\MPC-HC\mpc-hc64.exe",
                r"C:\Program Files (x86)\MPC-HC\mpc-hc64.exe",
                r"C:\Program Files\MPC-HC\mpc-hc.exe",
                r"C:\Program Files (x86)\MPC-HC\mpc-hc.exe",
            ],
            "KMPlayer": [
                r"C:\Program Files\The KMPlayer\KMPlayer.exe",
                r"C:\Program Files (x86)\The KMPlayer\KMPlayer.exe",
                r"C:\Program Files\KMPlayer\KMPlayer.exe",
                r"C:\Program Files (x86)\KMPlayer\KMPlayer.exe",
            ],
        }

        for name, exes in candidates.items():
            found = None
            for exe in exes:
                p = shutil.which(exe)
                if p:
                    found = p
                    break
            if not found and sys.platform.startswith("win"):
                for cand in common_paths.get(name, []):
                    if Path(cand).is_file():
                        found = cand
                        break
            self._external_players[name] = found

        self.external_player_combo.blockSignals(True)
        current = self.external_player_combo.currentData()
        self.external_player_combo.clear()
        self.external_player_combo.addItem("None (use built-in)", "")

        for name, path in self._external_players.items():
            if path:
                self.external_player_combo.addItem(name, name)

        if self._manual_player_path and Path(self._manual_player_path).is_file():
            self.external_player_combo.addItem(
                f"Manual: {Path(self._manual_player_path).name}",
                "__manual__",
            )

        if current:
            idx = self.external_player_combo.findData(current)
            if idx >= 0:
                self.external_player_combo.setCurrentIndex(idx)

        self.external_player_combo.blockSignals(False)
        self._on_external_player_changed()

    def _pick_external_player(self) -> None:
        start = self._manual_player_path or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "Select external player executable",
            start,
            "Executables (*.exe);;All files (*)",
        )
        if not path:
            return
        self._manual_player_path = path
        self._append_log("INFO", f"Manual external player set: {path}")
        self._detect_external_players()
        idx = self.external_player_combo.findData("__manual__")
        if idx >= 0:
            self.external_player_combo.setCurrentIndex(idx)

    def _launch_external_player(self) -> None:
        if not self._url_candidates:
            p = self.player_path_edit.text().strip()
            if p and Path(p).is_file():
                self._load_playlist(Path(p))
            if not self._url_candidates:
                return

        player_key = self.external_player_combo.currentData()
        if not player_key:
            return

        if player_key == "__manual__":
            player_path = self._manual_player_path
            player_name = "Manual"
        else:
            player_name = player_key
            player_path = self._external_players.get(player_key)

        if not player_path or not Path(player_path).is_file():
            QMessageBox.warning(
                self, "External Player",
                f"{player_name} executable not found.\n\n"
                f"Use Browse… to pick the executable manually.",
            )
            return

        url = self._url_candidates[0].toString()

        cmd: list[str]
        if player_name == "MPV":
            cmd = [player_path, url, "--force-window=yes", "--no-terminal"]
        else:
            cmd = [player_path, url]

        try:
            subprocess.Popen(cmd)
            self._append_log("INFO", f"Launched {player_name}: {' '.join(cmd)}")
        except Exception as exc:
            self._append_log("ERROR", f"Failed to launch {player_name}: {exc}")

    # ------------------------------------------------------------------
    # Player: loading
    # ------------------------------------------------------------------

    @Slot()
    def _pick_playlist(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open HLS playlist",
            self.player_path_edit.text() or str(Path.home()),
            "HLS playlist (*.m3u8);;All files (*)",
        )
        if path:
            self.player_path_edit.setText(path)
            self._load_playlist(Path(path))

    @Slot()
    def _pick_stream_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Open stream folder",
            self.player_path_edit.text() or str(Path.home()),
        )
        if not folder:
            return
        p = Path(folder)
        master = p / "master.m3u8"
        if master.is_file():
            self.player_path_edit.setText(str(master))
            self._load_playlist(master)
            return
        candidates = sorted(p.glob("*.m3u8"))
        if candidates:
            self.player_path_edit.setText(str(candidates[0]))
            self._load_playlist(candidates[0])
            return
        QMessageBox.warning(
            self, "Player",
            f"No .m3u8 playlist found in:\n{folder}",
        )

    @Slot()
    def _on_player_path_changed(self) -> None:
        if not self._url_candidates and self.player_path_edit.text().strip():
            self.play_btn.setEnabled(True)
        self._on_external_player_changed()

    @Slot()
    def _reload_current(self) -> None:
        path = self.player_path_edit.text().strip()
        if path and Path(path).is_file():
            self._load_playlist(Path(path))

    def _load_playlist(self, playlist: Path) -> None:
        if not PLAYER_AVAILABLE or self._player is None:
            return
        if not playlist.is_file():
            QMessageBox.warning(self, "Player", f"Playlist not found:\n{playlist}")
            return

        master = _find_master_playlist(playlist)
        self._master_path = master
        if master is not None:
            parsed = _parse_master_playlist(master)
            self._audio_renditions = parsed["audio"]
            self._subtitle_renditions = parsed["subtitles"]
        else:
            self._audio_renditions = []
            self._subtitle_renditions = []

        actual_playlist = master if master is not None else playlist
        root = actual_playlist.parent

        candidates: list[QUrl] = []
        try:
            base_url = self._hls_server.start(root)
            http_url = f"{base_url}/{actual_playlist.name}"
            candidates.append(QUrl(http_url))
            self.server_url_edit.setText(http_url)
            self.copy_url_btn.setEnabled(True)
            self.open_url_btn.setEnabled(True)
            self._append_log("INFO", f"Local server: {http_url}")
        except Exception as exc:
            self._append_log("WARN", f"Local HTTP server failed: {exc}")
            self.server_url_edit.setText("")
            self.copy_url_btn.setEnabled(False)
            self.open_url_btn.setEnabled(False)

        candidates.append(QUrl.fromLocalFile(str(actual_playlist.resolve())))

        self._url_candidates = candidates
        self._url_index = 0

        self._append_log("INFO", f"Player: loading {actual_playlist.name}")

        self._populate_track_combos()

        self._on_external_player_changed()

        self._try_current_url()

    def _try_current_url(self) -> None:
        if self._player is None:
            return
        if self._url_index >= len(self._url_candidates):
            return

        url = self._url_candidates[self._url_index]

        self._source_loaded = False
        self.play_btn.setText("Loading…")
        self.play_btn.setEnabled(False)
        self.status_label.setText(f"Player: loading {url.scheme}://…")

        self._append_log("INFO", f"Player: trying {url.toString()}")
        self._player.setSource(url)
        self._player.play()

    def _populate_track_combos(self) -> None:
        self.audio_track_combo.blockSignals(True)
        self.audio_track_combo.clear()
        if self._audio_renditions:
            for i, r in enumerate(self._audio_renditions):
                self.audio_track_combo.addItem(_track_label(r, i + 1), i)
            self.audio_track_combo.setEnabled(len(self._audio_renditions) > 1)
            default_idx = next(
                (i for i, r in enumerate(self._audio_renditions)
                 if r.get("default")),
                0,
            )
            self.audio_track_combo.setCurrentIndex(default_idx)
            self._pending_audio_track = default_idx
        else:
            self.audio_track_combo.addItem("(none)", -1)
            self.audio_track_combo.setEnabled(False)
            self._pending_audio_track = None
        self.audio_track_combo.blockSignals(False)

        self.subtitle_track_combo.blockSignals(True)
        self.subtitle_track_combo.clear()
        self.subtitle_track_combo.addItem("Off", -1)
        if self._subtitle_renditions:
            for i, r in enumerate(self._subtitle_renditions):
                self.subtitle_track_combo.addItem(_track_label(r, i + 1), i)
            self.subtitle_track_combo.setEnabled(True)
        else:
            self.subtitle_track_combo.setEnabled(False)
        self.subtitle_track_combo.setCurrentIndex(0)
        self._pending_subtitle_track = -1
        self.subtitle_track_combo.blockSignals(False)

    # ------------------------------------------------------------------
    # Player: audio/subtitle selection
    # ------------------------------------------------------------------

    @Slot()
    def _on_audio_track_changed(self) -> None:
        if not self._source_loaded:
            self._pending_audio_track = self.audio_track_combo.currentData()
            return
        self._apply_audio_track_choice()

    @Slot()
    def _on_subtitle_track_changed(self) -> None:
        if not self._source_loaded:
            self._pending_subtitle_track = self.subtitle_track_combo.currentData()
            return
        self._apply_subtitle_track_choice()

    def _apply_audio_track_choice(self) -> None:
        if self._player is None:
            return
        idx = self.audio_track_combo.currentData()
        if idx is None or idx < 0:
            return
        setter = getattr(self._player, "setActiveAudioTrack", None)
        if setter is None:
            return
        try:
            setter(int(idx))
            self._append_log("INFO", f"Audio track → #{idx + 1}")
        except Exception as exc:
            self._append_log("WARN", f"Audio track switch failed: {exc}")

    def _apply_subtitle_track_choice(self) -> None:
        if self._player is None:
            return
        data = self.subtitle_track_combo.currentData()
        if data is None:
            return
        setter = getattr(self._player, "setActiveSubtitleTrack", None)
        if setter is None:
            return
        try:
            setter(int(data))
            if int(data) < 0:
                self._append_log("INFO", "Subtitles off")
            else:
                self._append_log("INFO", f"Subtitle track → #{int(data) + 1}")
        except Exception as exc:
            self._append_log("WARN", f"Subtitle switch failed: {exc}")

    # ------------------------------------------------------------------
    # Player: media status / errors
    # ------------------------------------------------------------------

    @Slot(object)
    def _on_media_status_changed(self, status) -> None:
        if not PLAYER_AVAILABLE or self._player is None:
            return

        try:
            name = {
                QMediaPlayer.MediaStatus.NoMedia: "No media",
                QMediaPlayer.MediaStatus.LoadingMedia: "Loading…",
                QMediaPlayer.MediaStatus.LoadedMedia: "Loaded",
                QMediaPlayer.MediaStatus.StalledMedia: "Stalled",
                QMediaPlayer.MediaStatus.BufferingMedia: "Buffering…",
                QMediaPlayer.MediaStatus.BufferedMedia: "Playing",
                QMediaPlayer.MediaStatus.EndOfMedia: "Ended",
                QMediaPlayer.MediaStatus.InvalidMedia: "Invalid media",
            }.get(status, "")
        except Exception:
            name = ""

        if name:
            self.status_label.setText(f"Player: {name}")

        if status in (QMediaPlayer.MediaStatus.LoadedMedia,
                      QMediaPlayer.MediaStatus.BufferedMedia):
            if not self._source_loaded:
                self._source_loaded = True
                self.play_btn.setEnabled(True)
                self.play_btn.setText("Pause")
                QTimer.singleShot(0, self._flush_pending_track_selection)

        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.play_btn.setText("Play")
            self.play_btn.setEnabled(True)

        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._append_log("ERROR", "Player: invalid media.")
            self._maybe_try_next_url("Invalid media")

    def _maybe_try_next_url(self, reason: str) -> None:
        if self._source_loaded:
            self.play_btn.setEnabled(True)
            self.play_btn.setText("Play")
            return

        if self._url_index + 1 >= len(self._url_candidates):
            self.play_btn.setEnabled(True)
            self.play_btn.setText("Play")
            self.status_label.setText(f"Player: {reason}")
            hint = (
                "Playback failed on all URL schemes. If you are on "
                "PySide6 6.5.x, upgrade to 6.7+. You can also copy the "
                "Server URL and open it in VLC / MPV."
            )
            self._append_log("ERROR", hint)
            return

        self._url_index += 1
        QTimer.singleShot(150, self._try_current_url)

    def _flush_pending_track_selection(self) -> None:
        if self._player is None:
            return
        if self._pending_audio_track is not None:
            self._apply_audio_track_choice()
            self._pending_audio_track = None
        if self._pending_subtitle_track is not None:
            self._apply_subtitle_track_choice()
            self._pending_subtitle_track = None

    def _on_player_error(self, err, msg: str) -> None:
        self._append_log("ERROR", f"Player error: {msg or err}")
        self._maybe_try_next_url(f"error: {msg or err}")

    def _on_player_error_old(self, err) -> None:
        self._append_log("ERROR", f"Player error: {err}")
        self._maybe_try_next_url(f"error: {err}")

    # ------------------------------------------------------------------
    # Player: control
    # ------------------------------------------------------------------

    def _toggle_play(self) -> None:
        if not PLAYER_AVAILABLE:
            self._append_log(
                "WARN",
                "Play pressed but QtMultimedia is unavailable in this build.",
            )
            return
        if self._player is None:
            self._append_log(
                "WARN",
                "Play pressed but the media player was not initialised.",
            )
            return

        if not self._url_candidates:
            p = self.player_path_edit.text().strip()
            if not p:
                self._append_log(
                    "WARN",
                    "Play pressed but no playlist path is set. "
                    "Convert a file first or use Open file…",
                )
                return
            if not Path(p).is_file():
                self._append_log(
                    "ERROR", f"Playlist not found: {p}",
                )
                return
            self._load_playlist(Path(p))
            return

        state = self._player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _stop_playback(self) -> None:
        if not PLAYER_AVAILABLE or self._player is None:
            return
        self._player.pause()
        self.play_btn.setText("Play")

    @Slot(int)
    def _on_seek_moved(self, value: int) -> None:
        dur = self._player.duration() if self._player else 0
        self.time_label.setText(f"{_fmt_ms(value)} / {_fmt_ms(dur)}")

    @Slot()
    def _on_seek_released(self) -> None:
        if self._player is not None:
            self._player.setPosition(self.seek_slider.value())

    @Slot(int)
    def _on_volume_changed(self, value: int) -> None:
        if self._audio_output is not None:
            self._audio_output.setVolume(value / 100.0)

    @Slot(int)
    def _on_position_changed(self, pos: int) -> None:
        if not self.seek_slider.isSliderDown():
            self.seek_slider.setValue(pos)
            dur = self._player.duration() if self._player else 0
            self.time_label.setText(f"{_fmt_ms(pos)} / {_fmt_ms(dur)}")

    @Slot(int)
    def _on_duration_changed(self, dur: int) -> None:
        self.seek_slider.setRange(0, max(0, dur))
        pos = self._player.position() if self._player else 0
        self.time_label.setText(f"{_fmt_ms(pos)} / {_fmt_ms(dur)}")

    @Slot(object)
    def _on_state_changed(self, state) -> None:
        if not PLAYER_AVAILABLE:
            return
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_btn.setText("Pause")
        else:
            self.play_btn.setText("Play")

    # ------------------------------------------------------------------
    # Player: server URL helpers
    # ------------------------------------------------------------------

    @Slot()
    def _copy_server_url(self) -> None:
        url = self.server_url_edit.text().strip()
        if not url:
            return
        cb = QApplication.clipboard()
        cb.setText(url)
        self._append_log("INFO", f"Server URL copied: {url}")
        self.status_label.setText("Server URL copied to clipboard.")

    @Slot()
    def _open_server_url(self) -> None:
        url = self.server_url_edit.text().strip()
        if not url:
            return
        QDesktopServices.openUrl(QUrl(url))
        self._append_log("INFO", f"Opening in browser: {url}")

    # ------------------------------------------------------------------
    # Log tab
    # ------------------------------------------------------------------

    def _build_log_tab(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        f = QFont("Consolas")
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setPointSize(9)
        self.log_view.setFont(f)
        layout.addWidget(self.log_view, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        save_btn = QPushButton("Save…")
        save_btn.clicked.connect(self._save_log)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.log_view.clear)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(clear_btn)
        layout.addLayout(btn_row)

        return content

    # ------------------------------------------------------------------
    # Dependency wiring
    # ------------------------------------------------------------------

    def _wire_dependencies(self) -> None:
        self.video_mode.currentIndexChanged.connect(self._update_video_deps)
        self.video_rate_mode.currentIndexChanged.connect(self._update_video_deps)

        self.audio_mode.currentIndexChanged.connect(self._update_audio_deps)
        self.audio_codec.currentIndexChanged.connect(self._update_audio_deps)

        self.subs_mode.currentIndexChanged.connect(self._update_subs_deps)

        self.skip_master_cb.toggled.connect(self._update_advanced_deps)

        self.add_audio_mode.currentIndexChanged.connect(self._update_add_audio_deps)
        self.add_audio_codec.currentIndexChanged.connect(self._update_add_audio_deps)

    def _refresh_all_dependencies(self) -> None:
        self._update_video_deps()
        self._update_audio_deps()
        self._update_subs_deps()
        self._update_advanced_deps()
        self._update_add_audio_deps()

    @Slot()
    def _on_external_player_changed(self) -> None:
        if not hasattr(self, "external_player_combo"):
            return
        has_player = self.external_player_combo.currentData() != ""
        has_stream = bool(self._url_candidates) or (
            hasattr(self, "player_path_edit")
            and bool(self.player_path_edit.text().strip())
        )
        self.launch_player_btn.setEnabled(has_player and has_stream)

    def _update_video_deps(self) -> None:
        mode = self.video_mode.currentText()
        is_copy = (mode == "copy")
        is_encode = (mode == "encode")

        self.video_label.setEnabled(is_copy)
        for cb in self.profile_checks.values():
            cb.setEnabled(is_encode)
        self.video_level.setEnabled(is_encode)
        self.video_rate_mode.setEnabled(is_encode)
        self.video_codec.setEnabled(is_encode)
        self.video_preset.setEnabled(is_encode)

        rate_mode = self.video_rate_mode.currentText()
        crf_ok = is_encode and rate_mode in ("auto", "crf")
        bitrate_ok = is_encode and rate_mode in ("auto", "abr", "2pass")
        self.video_crf_edit.setEnabled(crf_ok)
        self.video_bitrate_edit.setEnabled(bitrate_ok)

    def _update_audio_deps(self) -> None:
        idx = self.audio_mode.currentIndex()
        enc = idx in (1, 3)

        self.audio_tracks.setEnabled(idx == 3)
        self.audio_codec.setEnabled(enc)
        self.audio_profiles_edit.setEnabled(enc)
        self.audio_level.setEnabled(enc)
        self.audio_bitrate_edit.setEnabled(enc)
        self.audio_channels_edit.setEnabled(enc)

        is_aac = self.audio_codec.currentText().lower() == "aac"
        self.audio_profile.setEnabled(enc and is_aac)

    def _update_subs_deps(self) -> None:
        auto = self.subs_mode.currentText() == "auto"
        self.subs_offset.setEnabled(auto)
        self.subs_scale.setEnabled(auto)

    def _update_advanced_deps(self) -> None:
        master = not self.skip_master_cb.isChecked()
        self.default_audio_spin.setEnabled(master)
        self.default_subs_spin.setEnabled(master)

    def _update_add_audio_deps(self) -> None:
        mode = self.add_audio_mode.currentText()
        use_codec = mode in ("auto", "encode")
        self.add_audio_codec.setEnabled(use_codec)
        self.add_audio_level.setEnabled(use_codec)
        is_aac = self.add_audio_codec.currentText().lower() == "aac"
        self.add_audio_profile.setEnabled(use_codec and is_aac)

    # ------------------------------------------------------------------
    # Tab handling
    # ------------------------------------------------------------------

    @Slot(int)
    def _on_tab_changed(self, index: int) -> None:
        self._update_start_button()

    def _update_start_button(self) -> None:
        running = self.worker is not None and self.worker.isRunning()
        idx = self.tabs.currentIndex()
        start_ok = (idx in (TAB_CONVERT, TAB_ADD)) and not running
        self.start_btn.setEnabled(start_ok)

    # ------------------------------------------------------------------
    # Settings persistence
    # ------------------------------------------------------------------

    def _collect_settings(self) -> dict[str, str]:
        """Return a flat dict of all settings worth persisting."""
        data: dict[str, str] = {}

        # Convert tab - Files
        data["convert.input"] = self.input_edit.text()
        data["convert.output"] = self.output_edit.text()
        data["convert.output_next_to_source"] = str(
            int(self.output_next_to_source_cb.isChecked())
        )
        data["convert.clean"] = str(int(self.clean_cb.isChecked()))

        # Video
        data["convert.video_mode"] = self.video_mode.currentText()
        data["convert.video_label"] = self.video_label.text()
        data["convert.video_profiles"] = ",".join(
            str(h) for h, cb in self.profile_checks.items() if cb.isChecked()
        )
        data["convert.video_level"] = self.video_level.currentText()
        data["convert.video_rate_mode"] = self.video_rate_mode.currentText()
        data["convert.video_codec"] = self.video_codec.text()
        data["convert.video_preset"] = self.video_preset.text()
        data["convert.video_bitrate"] = self.video_bitrate_edit.text()
        data["convert.video_crf"] = self.video_crf_edit.text()

        # Audio
        data["convert.audio_mode"] = str(self.audio_mode.currentIndex())
        data["convert.audio_tracks"] = self.audio_tracks.text()
        data["convert.audio_codec"] = self.audio_codec.currentText()
        data["convert.audio_profile"] = self.audio_profile.text()
        data["convert.audio_profiles"] = self.audio_profiles_edit.text()
        data["convert.audio_level"] = self.audio_level.currentText()
        data["convert.audio_bitrate"] = self.audio_bitrate_edit.text()
        data["convert.audio_channels"] = self.audio_channels_edit.text()

        # Subtitles
        data["convert.subs_mode"] = self.subs_mode.currentText()
        data["convert.subs_offset"] = self.subs_offset.text()
        data["convert.subs_scale"] = self.subs_scale.text()

        # Advanced
        data["convert.ffmpeg"] = self.ffmpeg_edit.text()
        data["convert.ffprobe"] = self.ffprobe_edit.text()
        data["convert.threads"] = str(self.threads_spin.value())
        data["convert.segment"] = str(self.segment_spin.value())
        data["convert.keyframe"] = str(self.keyframe_spin.value())
        data["convert.default_audio"] = str(self.default_audio_spin.value())
        data["convert.default_subs"] = str(self.default_subs_spin.value())
        data["convert.skip_master"] = str(int(self.skip_master_cb.isChecked()))
        data["convert.dry_run"] = str(int(self.dry_run_cb.isChecked()))
        data["convert.check_space"] = str(int(self.check_space_cb.isChecked()))

        # Add track tab
        data["add.input"] = self.add_input_edit.text()
        data["add.stream"] = self.add_stream_edit.text()
        data["add.audio_mode"] = self.add_audio_mode.currentText()
        data["add.audio_codec"] = self.add_audio_codec.currentText()
        data["add.audio_profile"] = self.add_audio_profile.text()
        data["add.audio_level"] = self.add_audio_level.currentText()
        data["add.track"] = str(self.add_track_spin.value())
        data["add.lang"] = self.add_lang_edit.text()
        data["add.title"] = self.add_title_edit.text()
        data["add.subs_offset"] = self.add_subs_offset.text()
        data["add.subs_scale"] = self.add_subs_scale.text()
        data["add.skip_master"] = str(int(self.add_skip_master_cb.isChecked()))

        # Player (external player preference and last playlist path)
        data["player.manual_external"] = self._manual_player_path
        data["player.last_playlist"] = self.player_path_edit.text()

        # Window geometry
        try:
            geo = bytes(self.saveGeometry())
            data["window.geometry"] = base64.b64encode(geo).decode("ascii")
        except Exception:
            pass

        return data

    def _on_save_settings(self) -> None:
        if self._settings_db is None:
            QMessageBox.warning(
                self, "Save Settings",
                "The settings database is not available.",
            )
            return
        try:
            self._settings_db.set_many(self._collect_settings())
            self.status_label.setText("Settings saved.")
            self._append_log(
                "OK", f"Settings saved to {self._settings_db.path.name}"
            )
        except Exception as exc:
            self._append_log("ERROR", f"Could not save settings: {exc}")
            QMessageBox.warning(
                self, "Save Settings", f"Could not save settings:\n{exc}",
            )

    def _load_settings_from_db(self) -> None:
        db = self._settings_db
        if db is None:
            return

        # Block signals on every widget we're about to set, so that
        # intermediate values don't trigger dependency updates.
        widgets_to_block = [
            self.input_edit, self.output_edit,
            self.output_next_to_source_cb, self.clean_cb,
            self.video_mode, self.video_label,
            self.video_level, self.video_rate_mode,
            self.video_codec, self.video_preset,
            self.video_bitrate_edit, self.video_crf_edit,
            self.audio_mode, self.audio_tracks,
            self.audio_codec, self.audio_profile,
            self.audio_profiles_edit, self.audio_level,
            self.audio_bitrate_edit, self.audio_channels_edit,
            self.subs_mode, self.subs_offset, self.subs_scale,
            self.ffmpeg_edit, self.ffprobe_edit,
            self.threads_spin, self.segment_spin, self.keyframe_spin,
            self.default_audio_spin, self.default_subs_spin,
            self.skip_master_cb, self.dry_run_cb, self.check_space_cb,
            self.add_input_edit, self.add_stream_edit,
            self.add_audio_mode, self.add_audio_codec,
            self.add_audio_profile, self.add_audio_level,
            self.add_track_spin, self.add_lang_edit, self.add_title_edit,
            self.add_subs_offset, self.add_subs_scale,
            self.add_skip_master_cb,
            self.external_player_combo,
            *self.profile_checks.values(),
        ]
        for w in widgets_to_block:
            try:
                w.blockSignals(True)
            except Exception:
                pass

        try:
            # Helper to set text only when a non-empty value is stored.
            def _set_text(widget, key):
                v = db.get(key, "")
                if v:
                    widget.setText(v)

            def _set_combo(widget, key):
                v = db.get(key, "")
                if not v:
                    return
                idx = widget.findText(v)
                if idx >= 0:
                    widget.setCurrentIndex(idx)

            def _set_bool(widget, key, default: bool):
                widget.setChecked(db.get_bool(key, default))

            def _set_int(widget, key, default: int):
                widget.setValue(db.get_int(key, default))

            # ---- Convert / Files ----
            _set_text(self.input_edit, "convert.input")
            _set_text(self.output_edit, "convert.output")
            _set_bool(
                self.output_next_to_source_cb,
                "convert.output_next_to_source", True,
            )
            _set_bool(self.clean_cb, "convert.clean", False)

            # ---- Video ----
            _set_combo(self.video_mode, "convert.video_mode")
            _set_text(self.video_label, "convert.video_label")
            profiles_str = db.get("convert.video_profiles", "")
            if profiles_str:
                wanted = {int(x) for x in profiles_str.split(",") if x.strip()}
                for h, cb in self.profile_checks.items():
                    cb.setChecked(h in wanted)
            _set_combo(self.video_level, "convert.video_level")
            _set_combo(self.video_rate_mode, "convert.video_rate_mode")
            _set_text(self.video_codec, "convert.video_codec")
            _set_text(self.video_preset, "convert.video_preset")
            _set_text(self.video_bitrate_edit, "convert.video_bitrate")
            _set_text(self.video_crf_edit, "convert.video_crf")

            # ---- Audio ----
            audio_mode = db.get_int("convert.audio_mode", 0)
            if 0 <= audio_mode < self.audio_mode.count():
                self.audio_mode.setCurrentIndex(audio_mode)
            _set_text(self.audio_tracks, "convert.audio_tracks")
            _set_combo(self.audio_codec, "convert.audio_codec")
            _set_text(self.audio_profile, "convert.audio_profile")
            _set_text(self.audio_profiles_edit, "convert.audio_profiles")
            _set_combo(self.audio_level, "convert.audio_level")
            _set_text(self.audio_bitrate_edit, "convert.audio_bitrate")
            _set_text(self.audio_channels_edit, "convert.audio_channels")

            # ---- Subtitles ----
            _set_combo(self.subs_mode, "convert.subs_mode")
            _set_text(self.subs_offset, "convert.subs_offset")
            _set_text(self.subs_scale, "convert.subs_scale")

            # ---- Advanced ----
            _set_text(self.ffmpeg_edit, "convert.ffmpeg")
            _set_text(self.ffprobe_edit, "convert.ffprobe")
            _set_int(self.threads_spin, "convert.threads", GUI_DEFAULT_THREADS)
            _set_int(self.segment_spin, "convert.segment", DEFAULT_SEGMENT_TIME)
            # keyframe is a double spinbox
            try:
                kf = float(db.get("convert.keyframe", str(DEFAULT_KEYFRAME_INTERVAL)))
                self.keyframe_spin.setValue(kf)
            except Exception:
                pass
            _set_int(self.default_audio_spin, "convert.default_audio", 0)
            _set_int(self.default_subs_spin, "convert.default_subs", 0)
            _set_bool(self.skip_master_cb, "convert.skip_master", False)
            _set_bool(self.dry_run_cb, "convert.dry_run", False)
            _set_bool(self.check_space_cb, "convert.check_space", True)

            # ---- Add track ----
            _set_text(self.add_input_edit, "add.input")
            _set_text(self.add_stream_edit, "add.stream")
            _set_combo(self.add_audio_mode, "add.audio_mode")
            _set_combo(self.add_audio_codec, "add.audio_codec")
            _set_text(self.add_audio_profile, "add.audio_profile")
            _set_combo(self.add_audio_level, "add.audio_level")
            _set_int(self.add_track_spin, "add.track", 0)
            _set_text(self.add_lang_edit, "add.lang")
            _set_text(self.add_title_edit, "add.title")
            _set_text(self.add_subs_offset, "add.subs_offset")
            _set_text(self.add_subs_scale, "add.subs_scale")
            _set_bool(self.add_skip_master_cb, "add.skip_master", False)

            # ---- Player ----
            self._manual_player_path = db.get("player.manual_external", "")
            last_playlist = db.get("player.last_playlist", "")
            if last_playlist:
                self.player_path_edit.setText(last_playlist)

            # Update the output hint based on the restored state.
            self._on_output_next_to_source_toggled(
                self.output_next_to_source_cb.isChecked()
            )
        finally:
            for w in widgets_to_block:
                try:
                    w.blockSignals(False)
                except Exception:
                    pass

    def _restore_geometry(self) -> None:
        if self._settings_db is None:
            return
        encoded = self._settings_db.get("window.geometry", "")
        if not encoded:
            return
        try:
            raw = base64.b64decode(encoded.encode("ascii"))
            self.restoreGeometry(raw)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # File dialogs
    # ------------------------------------------------------------------

    def _pick_input(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select input media file",
            self.input_edit.text() or str(Path.home()),
            "Media files (*.mkv *.mp4 *.mov *.m4v *.ts *.webm *.avi);;All files (*)",
        )
        if path:
            self.input_edit.setText(path)
            self._autofill_output(path)

    def _pick_output(self) -> None:
        start = self.output_edit.text()
        if not start and self.input_edit.text():
            if self.output_next_to_source_cb.isChecked():
                start = str(_next_to_source_dir(Path(self.input_edit.text())))
            else:
                start = str(_default_output_dir(Path(self.input_edit.text())))
        path = QFileDialog.getExistingDirectory(
            self, "Select output folder", start or str(Path.home()),
        )
        if path:
            self._set_output_folder_explicitly(Path(path))

    def _set_output_folder_explicitly(self, folder: Path) -> None:
        """Set the output field to a specific folder without triggering
        the auto-derive logic from the 'next to source' checkbox.

        Used when the user picks a folder explicitly (Browse…, Merge
        with…) so the automatic derivation does not override their
        choice."""
        self.output_next_to_source_cb.blockSignals(True)
        self.output_next_to_source_cb.setChecked(False)
        self.output_next_to_source_cb.blockSignals(False)
        self.output_edit.setReadOnly(False)
        self.output_edit.setText(str(folder))

    @Slot()
    def _pick_merge_target(self) -> None:
        """Let the user pick an existing *-stream folder to merge into."""
        start = self.output_edit.text().strip()
        if not start and self.input_edit.text().strip():
            start = str(Path(self.input_edit.text()).parent)
        if not start:
            start = str(Path.home())

        folder = QFileDialog.getExistingDirectory(
            self, "Select existing stream folder to merge into", start,
        )
        if not folder:
            return

        target = Path(folder)
        if not (target / "master.m3u8").is_file():
            # Not necessarily fatal — the folder might be a fresh
            # destination. But warn so the user knows.
            reply = QMessageBox.question(
                self,
                "Not a stream folder",
                f"'{target.name}' does not contain a master.m3u8.\n\n"
                f"Use it as the output folder anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._set_output_folder_explicitly(target)
        self._append_log("INFO", f"Merge target: {target}")

    def _pick_add_input(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select audio or subtitle file",
            self.add_input_edit.text() or str(Path.home()),
            "Audio (*.m4a *.aac *.mp3 *.ac3 *.eac3 *.opus *.flac *.wav *.ogg *.mka);;"
            "Subtitles (*.srt *.ass *.ssa *.vtt *.sub *.sbv);;All files (*)",
        )
        if path:
            self.add_input_edit.setText(path)

    def _pick_add_stream(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select existing HLS stream folder",
            self.add_stream_edit.text() or str(Path.home()),
        )
        if path:
            self.add_stream_edit.setText(path)

    def _autofill_output(self, input_path: str) -> None:
        p = Path(input_path)

        # Respect the "save next to source" checkbox.
        if not self.output_next_to_source_cb.isChecked():
            if not self.output_edit.text().strip():
                self.output_edit.setText(str(_default_output_dir(p)))
            return

        suggested = _next_to_source_dir(p)
        compatible = self._find_compatible_stream_folders(p)

        if not compatible:
            # Nothing matched — just use the derived folder.
            self.output_edit.setText(str(suggested))
            return

        # Confident case: the derived folder already exists on disk.
        suggested_resolved = suggested.resolve()
        for cand in compatible:
            if cand.resolve() == suggested_resolved:
                self.output_edit.setText(str(cand))
                return

        # Ambiguous: more than one candidate → don't guess.
        if len(compatible) > 1:
            names = ", ".join(c.name for c in compatible)
            self._append_log(
                "WARN",
                f"Multiple compatible stream folders found ({names}). "
                f"Not merging automatically — use 'Merge with…' to pick one.",
            )
            self.output_edit.setText(str(suggested))
            return

        # Exactly one candidate — ask.
        target = compatible[0]
        reply = QMessageBox.question(
            self,
            "Merge with existing stream?",
            f"A stream folder next to this file already contains a "
            f"video with the same duration:\n\n  • {target.name}\n\n"
            f"Merge the new conversion into it instead of creating a new "
            f"'{suggested.name}' folder?\n\n"
            f"(Choosing No will create a separate folder.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.output_edit.setText(str(target))
            self._append_log("INFO", f"Merge target: {target}")
            return

        self.output_edit.setText(str(suggested))

    @Slot(bool)
    def _on_output_next_to_source_toggled(self, checked: bool) -> None:
        self.output_edit.setReadOnly(checked)
        if checked and self.input_edit.text().strip():
            self.output_edit.setText(
                str(_next_to_source_dir(Path(self.input_edit.text())))
            )
        # When unchecking, leave the field as-is. The user is taking
        # manual control; we don't want to clobber a folder they may
        # have chosen via Browse… or Merge with…

    # ------------------------------------------------------------------
    # Drag & drop
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if not path:
            return
        idx = self.tabs.currentIndex()

        if idx == TAB_CONVERT:
            self.input_edit.setText(path)
            self._autofill_output(path)
        elif idx == TAB_ADD:
            self.add_input_edit.setText(path)
        elif idx == TAB_PLAYER:
            p = Path(path)
            if p.is_dir():
                m = p / "master.m3u8"
                if m.is_file():
                    self.player_path_edit.setText(str(m))
                    self._load_playlist(m)
            elif p.suffix.lower() == ".m3u8":
                self.player_path_edit.setText(str(p))
                self._load_playlist(p)
        else:
            ext = Path(path).suffix.lower()
            subs = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".sbv"}
            auds = {".m4a", ".aac", ".mp3", ".ac3", ".eac3",
                    ".opus", ".flac", ".wav", ".ogg", ".mka"}
            if ext in subs or ext in auds:
                self.tabs.setCurrentIndex(TAB_ADD)
                self.add_input_edit.setText(path)
            elif ext == ".m3u8":
                self.tabs.setCurrentIndex(TAB_PLAYER)
                self.player_path_edit.setText(str(path))
                self._load_playlist(Path(path))
            else:
                self.tabs.setCurrentIndex(TAB_CONVERT)
                self.input_edit.setText(path)
                self._autofill_output(path)
        event.acceptProposedAction()

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_mapping(
        value: str, *, value_name: str, integer_keys: bool = False,
    ) -> dict:
        if not value:
            return {}
        result: dict = {}
        for item in value.split(","):
            item = item.strip()
            if not item:
                continue
            if "=" not in item:
                raise ConverterError(
                    f"Invalid {value_name} entry '{item}'. "
                    f"Expected KEY=VALUE."
                )
            k, v = item.split("=", 1)
            k = k.strip()
            v = v.strip()
            if not k or not v:
                raise ConverterError(
                    f"Invalid {value_name} entry '{item}'. "
                    f"Expected KEY=VALUE."
                )
            try:
                n = int(v)
            except ValueError as exc:
                raise ConverterError(
                    f"Invalid {value_name} value in '{item}'."
                ) from exc
            if n <= 0:
                raise ConverterError(
                    f"{value_name} values must be positive: {item}"
                )
            if integer_keys:
                try:
                    key_i = int(k)
                except ValueError as exc:
                    raise ConverterError(
                        f"Invalid track number '{k}' in {value_name}."
                    ) from exc
                if key_i <= 0:
                    raise ConverterError(
                        f"Invalid track number '{k}' in {value_name}."
                    )
                if key_i in result:
                    raise ConverterError(f"Duplicate {value_name} key: {k}")
                result[key_i] = n
            else:
                if k in result:
                    raise ConverterError(f"Duplicate {value_name} key: {k}")
                result[k] = n
        return result

    # ------------------------------------------------------------------
    # Preflight disk space check
    # ------------------------------------------------------------------

    def _preflight_disk_check(
        self,
        input_path: Path,
        output_dir: Path,
    ) -> bool:
        if not self.check_space_cb.isChecked():
            return True
        if self.dry_run_cb.isChecked():
            return True

        free, total = _disk_usage_for(output_dir)
        if free < 0:
            return True

        try:
            src_size = input_path.stat().st_size
        except Exception:
            src_size = 0

        if self.video_mode.currentText() == "encode":
            estimated = int(src_size * 3.0)
        else:
            estimated = int(src_size * 1.5)

        self._append_log(
            "INFO",
            f"Disk: {_fmt_bytes(free)} free on output drive "
            f"(estimated need: ~{_fmt_bytes(estimated)})",
        )

        if free >= max(MIN_FREE_SPACE_BYTES, estimated):
            return True

        if free < MIN_FREE_SPACE_BYTES:
            reply = QMessageBox.warning(
                self, "Low disk space",
                f"The output drive only has {_fmt_bytes(free)} free.\n\n"
                f"Estimated required space: ~{_fmt_bytes(estimated)}.\n"
                f"Free up space on this drive, or choose a different "
                f"output folder.\n\n"
                f"Continue anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        else:
            reply = QMessageBox.warning(
                self, "Low disk space",
                f"The output drive only has {_fmt_bytes(free)} free, and "
                f"the output is estimated at ~{_fmt_bytes(estimated)}.\n\n"
                f"You may run out of space mid-conversion.\n\n"
                f"Continue anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
        return reply == QMessageBox.StandardButton.Yes

    # ------------------------------------------------------------------
    # Merge detection
    # ------------------------------------------------------------------

    def _probe_duration_seconds(self, input_path: Path) -> float:
        """Return the duration of a media file in seconds, or 0.0."""
        ffprobe = self.ffprobe_edit.text().strip() or "ffprobe"
        try:
            result = subprocess.run(
                [
                    ffprobe, "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(input_path),
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
            return float((result.stdout or "").strip() or 0.0)
        except Exception:
            return 0.0

    def _find_compatible_stream_folders(
        self, input_path: Path, tolerance_sec: float = 3.0,
    ) -> list[Path]:
        """Return sibling *-stream folders whose source duration matches input.

        A folder is a candidate if:
          * it lives in the same directory as the input file,
          * its name ends with '-stream',
          * it contains a master.m3u8,
          * its .converter.json records a source duration within
            `tolerance_sec` of the new input's duration.
        """
        parent = input_path.parent
        if not parent.is_dir():
            return []

        new_duration = self._probe_duration_seconds(input_path)
        if new_duration <= 0:
            return []

        candidates: list[Path] = []
        for folder in parent.iterdir():
            if not folder.is_dir():
                continue
            if not folder.name.endswith("-stream"):
                continue
            if not (folder / "master.m3u8").is_file():
                continue

            meta_file = folder / ".converter.json"
            if not meta_file.is_file():
                continue

            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                existing_dur = float(
                    data.get("source", {}).get("duration", 0.0)
                )
            except Exception:
                continue

            if existing_dur <= 0:
                continue
            if abs(existing_dur - new_duration) <= tolerance_sec:
                candidates.append(folder)

        return candidates
    # ------------------------------------------------------------------
    # Config building
    # ------------------------------------------------------------------

    def _build_app_config(self) -> AppConfig:
        vmode = self.video_mode.currentText()
        label = self.video_label.text().strip() or None

        video_bitrates = self._parse_mapping(
            self.video_bitrate_edit.text().strip(),
            value_name="--video-bitrate",
        )
        video_crfs = self._parse_mapping(
            self.video_crf_edit.text().strip(),
            value_name="--video-crf",
        )

        if vmode == "encode":
            profiles: list[VideoProfile] = []
            for height, cb in self.profile_checks.items():
                if cb.isChecked():
                    key = str(height)
                    bitrate = video_bitrates.get(key)
                    crf = video_crfs.get(key)
                    if bitrate is not None and crf is not None:
                        raise ConverterError(
                            f"Profile {height}p has both a bitrate and a "
                            f"CRF override. Use only one."
                        )
                    profiles.append(VideoProfile(
                        quality=f"{height}p",
                        height=height,
                        bitrate=bitrate,
                        crf=crf,
                    ))
            if not profiles:
                raise ConverterError(
                    "Select at least one video profile for encode mode."
                )
            video = VideoRequest(mode="encode", profiles=profiles)
        elif vmode == "none":
            if video_bitrates or video_crfs:
                raise ConverterError(
                    "Video bitrate/CRF overrides require --video-mode encode."
                )
            video = VideoRequest(mode="none")
        else:
            if video_bitrates or video_crfs:
                raise ConverterError(
                    "Video bitrate/CRF overrides require --video-mode encode."
                )
            if label and label.isdigit():
                label = f"{label}p"
            video = VideoRequest(mode="copy", label=label)

        amode = self.audio_mode.currentIndex()
        audio_encode_all = (amode == 1)
        audio_skip = (amode == 2)
        audio_requests: list[AudioRequest] = []
        if amode == 3:
            text = self.audio_tracks.text().strip()
            if not text:
                raise ConverterError(
                    "Enter one or more track numbers to encode."
                )
            for token in text.split(","):
                token = token.strip()
                if not token:
                    continue
                try:
                    n = int(token)
                except ValueError as exc:
                    raise ConverterError(
                        f"Invalid audio track number: {token!r}"
                    ) from exc
                if n <= 0:
                    raise ConverterError(
                        f"Audio track numbers must be positive: {n}"
                    )
                audio_requests.append(AudioRequest(track=n, mode="encode"))

        profiles_text = self.audio_profiles_edit.text().strip()
        if profiles_text:
            audio_profiles: list[AudioProfile] = []
            for item in profiles_text.split(","):
                item = item.strip()
                if not item:
                    continue
                try:
                    if ":" in item:
                        b_str, c_str = item.split(":", 1)
                        bitrate = int(b_str)
                        channels = int(c_str)
                    else:
                        bitrate = int(item)
                        channels = 2 if bitrate < 256 else 6
                except ValueError as exc:
                    raise ConverterError(
                        f"Invalid audio profile: {item!r}"
                    ) from exc
                audio_profiles.append(
                    AudioProfile(bitrate=bitrate, channels=channels)
                )
        else:
            audio_profiles = [
                AudioProfile(bitrate=b, channels=2 if b < 256 else 6)
                for b in hls.DEFAULT_AUDIO_PROFILES
            ]

        audio_bitrate_overrides = self._parse_mapping(
            self.audio_bitrate_edit.text().strip(),
            value_name="--audio-bitrate",
            integer_keys=True,
        )
        audio_channel_overrides = self._parse_mapping(
            self.audio_channels_edit.text().strip(),
            value_name="--audio-channels",
            integer_keys=True,
        )

        if not audio_skip and not (audio_encode_all or audio_requests):
            if profiles_text:
                raise ConverterError("--audio-profiles requires audio encoding.")
            if audio_bitrate_overrides:
                raise ConverterError("--audio-bitrate requires audio encoding.")
            if audio_channel_overrides:
                raise ConverterError("--audio-channels requires audio encoding.")

        audio = AudioConfig(
            requests=audio_requests,
            encode_all=audio_encode_all,
            skip=audio_skip,
            profiles=audio_profiles,
            bitrate_overrides=audio_bitrate_overrides,
            channel_overrides=audio_channel_overrides,
        )

        subs_mode = self.subs_mode.currentText()

        threads = self.threads_spin.value()
        segment_time = self.segment_spin.value()
        keyframe = self.keyframe_spin.value()

        if threads <= 0:
            raise ConverterError("Threads must be > 0.")
        if segment_time <= 0:
            raise ConverterError("Segment time must be > 0.")
        if keyframe <= 0:
            raise ConverterError("Keyframe interval must be > 0.")

        ffmpeg = self.ffmpeg_edit.text().strip() or "ffmpeg"
        ffprobe = self.ffprobe_edit.text().strip() or "ffprobe"

        rate_mode = self.video_rate_mode.currentText()
        video_level = self.video_level.currentText()

        if rate_mode not in ("auto", "crf", "abr", "2pass"):
            raise ConverterError(f"Invalid video rate mode: {rate_mode}")
        if video_level not in VIDEO_LEVEL_MULTIPLIERS:
            raise ConverterError(f"Invalid video level: {video_level}")

        if rate_mode == "2pass":
            if video.mode != "encode":
                raise ConverterError(
                    "Rate mode '2pass' requires video mode 'encode'."
                )
            for p in video.profiles:
                if p.crf is not None:
                    raise ConverterError(
                        f"Video profile {p.quality} uses CRF, which is "
                        f"incompatible with '2pass' rate mode."
                    )
                if (p.bitrate is None
                        and video_level == "balanced"
                        and p.height not in hls.DEFAULT_VIDEO_PROFILES):
                    raise ConverterError(
                        f"No default bitrate for {p.height}p. Use "
                        f"Bitrate overrides or set the bitrate level to "
                        f"'source'."
                    )

        if rate_mode == "crf" and video.mode == "encode":
            missing = [p.quality for p in video.profiles if p.crf is None]
            if missing:
                raise ConverterError(
                    "Rate mode 'crf' requires a CRF value for every "
                    "enabled profile.\nMissing: " + ", ".join(missing)
                )

        audio_codec = self.audio_codec.currentText()
        audio_profile = (
            self.audio_profile.text().strip() or DEFAULT_AUDIO_PROFILE
        )
        if audio_encode_all or audio_requests:
            codec = audio_codec.lower()
            if codec == "flac":
                raise ConverterError(
                    "FLAC is not supported for HLS audio encoding."
                )
            if codec not in SUPPORTED_AUDIO_ENCODE_CODECS:
                raise ConverterError(f"Unsupported audio codec: {audio_codec}")

        offset_map: dict[int, int] = {}
        raw_offset = self.subs_offset.text().strip()
        if raw_offset:
            try:
                if "=" not in raw_offset:
                    offset_map = {0: int(raw_offset)}
                else:
                    for item in raw_offset.split(","):
                        item = item.strip()
                        if not item:
                            continue
                        k, v = item.split("=", 1)
                        offset_map[int(k)] = int(v)
            except Exception as exc:
                raise ConverterError(f"Invalid subtitle offset: {exc}") from exc

        scale: Optional[tuple[float, float]] = None
        raw_scale = self.subs_scale.text().strip()
        if raw_scale:
            try:
                num, den = raw_scale.split("/", 1)
                scale = (float(num), float(den))
            except Exception as exc:
                raise ConverterError(f"Invalid subtitle time scale: {exc}") from exc

        subtitles = SubtitleConfig(
            mode=subs_mode,
            offset_ms=offset_map,
            time_scale=scale,
        )

        default_audio = self.default_audio_spin.value() or None
        default_subs = self.default_subs_spin.value() or None

        return AppConfig(
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            output=None,
            clean=self.clean_cb.isChecked(),
            skip_master=self.skip_master_cb.isChecked(),
            video=video,
            video_level=video_level,
            video_rate_mode=rate_mode,
            video_codec=self.video_codec.text().strip() or DEFAULT_VIDEO_CODEC,
            video_preset=self.video_preset.text().strip() or DEFAULT_VIDEO_PRESET,
            threads=threads,
            audio=audio,
            audio_codec=audio_codec,
            audio_profile=audio_profile,
            subtitles=subtitles,
            keyframe_interval=keyframe,
            segment_time=segment_time,
            default_audio_track=default_audio,
            default_subtitle_track=default_subs,
        )

    # ------------------------------------------------------------------
    # Start / cancel
    # ------------------------------------------------------------------

    @Slot()
    def _on_start(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        if self.tabs.currentIndex() not in (TAB_CONVERT, TAB_ADD):
            return

        try:
            if self.tabs.currentIndex() == TAB_CONVERT:
                self._start_convert()
            else:
                self._start_add()
        except ConverterError as exc:
            QMessageBox.warning(self, "Invalid options", str(exc))
        except Exception as exc:
            QMessageBox.critical(
                self, "Unexpected error",
                f"{exc}\n\n{traceback.format_exc()}",
            )

    def _start_convert(self) -> None:
        input_text = self.input_edit.text().strip()
        if not input_text:
            raise ConverterError("Please choose an input media file.")

        input_path = Path(input_text).expanduser()
        if not input_path.is_file():
            raise ConverterError(f"Input file does not exist:\n{input_path}")

        # The Output field is authoritative when it has content (it may
        # have been auto-filled, set via Browse…, or overridden by a
        # merge dialog). Only fall back to auto-derivation when it's
        # empty.
        output_text = self.output_edit.text().strip()
        if output_text:
            output_dir = Path(output_text).expanduser()
        elif self.output_next_to_source_cb.isChecked():
            output_dir = _next_to_source_dir(input_path)
        else:
            output_dir = _default_output_dir(input_path)

        config = self._build_app_config()

        if not self._preflight_disk_check(input_path, output_dir):
            self.status_label.setText("Cancelled at disk-space check.")
            return

        if config.clean and output_dir.exists() and not self.dry_run_cb.isChecked():
            try:
                shutil.rmtree(output_dir)
            except Exception as exc:
                raise ConverterError(f"Could not clean output dir: {exc}")

        self._reset_progress_color()
        self._last_error_was_disk_full = False
        self.log_view.clear()
        self._append_log("INFO", f"Input : {input_path}")
        self._append_log("INFO", f"Output: {output_dir}")

        self.worker = ConversionWorker(
            config,
            mode="convert",
            input_file=input_path,
            output_dir=output_dir,
            dry_run=self.dry_run_cb.isChecked(),
        )
        self._wire_worker()

    def _start_add(self) -> None:
        input_text = self.add_input_edit.text().strip()
        stream_text = self.add_stream_edit.text().strip()

        if not input_text:
            raise ConverterError("Please choose a track file to add.")
        if not stream_text:
            raise ConverterError("Please choose the target stream folder.")

        input_path = Path(input_text).expanduser()
        if not input_path.is_file():
            raise ConverterError(f"Input file does not exist:\n{input_path}")

        stream_dir = Path(stream_text).expanduser()
        if not stream_dir.is_dir():
            raise ConverterError(f"Stream folder does not exist:\n{stream_dir}")

        if not self._preflight_disk_check(input_path, stream_dir):
            self.status_label.setText("Cancelled at disk-space check.")
            return

        options = AddToStreamOptions(
            audio_add_mode=self.add_audio_mode.currentText(),
            audio_codec=self.add_audio_codec.currentText(),
            audio_level=self.add_audio_level.currentText(),
            audio_profile=self.add_audio_profile.text().strip() or None,
            subtitle_offset_ms=self.add_subs_offset.text().strip() or None,
            subtitle_time_scale=self.add_subs_scale.text().strip() or None,
            track=self.add_track_spin.value() or None,
            language=self.add_lang_edit.text().strip() or None,
            title=self.add_title_edit.text().strip() or None,
            skip_master=self.add_skip_master_cb.isChecked(),
        )

        config = AppConfig(
            ffmpeg=self.ffmpeg_edit.text().strip() or "ffmpeg",
            ffprobe=self.ffprobe_edit.text().strip() or "ffprobe",
            output=None,
            clean=False,
            skip_master=True,
            video=VideoRequest(mode="none"),
            video_level=DEFAULT_VIDEO_LEVEL,
            video_rate_mode="auto",
            video_codec=DEFAULT_VIDEO_CODEC,
            video_preset=DEFAULT_VIDEO_PRESET,
            threads=self.threads_spin.value(),
            audio=AudioConfig(),
            audio_codec=self.add_audio_codec.currentText(),
            audio_profile=DEFAULT_AUDIO_PROFILE,
            subtitles=SubtitleConfig(mode="none"),
            keyframe_interval=DEFAULT_KEYFRAME_INTERVAL,
            segment_time=self.segment_spin.value(),
            default_audio_track=None,
            default_subtitle_track=None,
        )

        self._reset_progress_color()
        self._last_error_was_disk_full = False
        self.log_view.clear()
        self._append_log("INFO", f"Track file : {input_path}")
        self._append_log("INFO", f"Stream dir : {stream_dir}")

        self.worker = ConversionWorker(
            config,
            mode="add",
            input_file=input_path,
            stream_dir=stream_dir,
            options=options,
            dry_run=self.dry_run_cb.isChecked(),
        )
        self._wire_worker()

    def _wire_worker(self) -> None:
        assert self.worker is not None
        self.worker.log_signal.connect(self._append_log)
        self.worker.progress_signal.connect(self._on_progress)
        self.worker.finished_signal.connect(self._on_finished)

        self.cancel_btn.setEnabled(True)
        self.start_btn.setEnabled(False)      # <-- explicit
        self.stage_bar.setValue(0)
        self.status_label.setText("Starting…")

        self.worker.start()

    @Slot()
    def _on_cancel(self) -> None:
        if self.worker and self.worker.isRunning():
            self._append_log("WARN", "Cancellation requested…")
            self.worker.cancel()
            self.cancel_btn.setEnabled(False)
            self.status_label.setText("Cancelling…")

    # ------------------------------------------------------------------
    # Progress bar color helpers
    # ------------------------------------------------------------------

    def _reset_progress_color(self) -> None:
        self.stage_bar.setStyleSheet("")

    def _set_progress_error_color(self) -> None:
        self.stage_bar.setStyleSheet(
            "QProgressBar { text-align: center; }"
            "QProgressBar::chunk { background-color: #c0392b; }"
        )

    # ------------------------------------------------------------------
    # Logging / progress slots
    # ------------------------------------------------------------------

    @Slot(str, str)
    def _append_log(self, level: str, message: str) -> None:
        if _is_log_noise(level, message):
            return

        if level == "ERROR" and "no space left on device" in message.lower():
            self._last_error_was_disk_full = True

        color_map = {
            "INFO":  "#202020",
            "OK":    "#1e7a3f",
            "WARN":  "#9a6700",
            "ERROR": "#c0392b",
            "DRY":   "#1560a0",
        }
        color = color_map.get(level, "#202020")
        safe = escape(str(message))
        self.log_view.append(
            f'<span style="color:{color};">[{escape(level)}] {safe}</span>'
        )
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)

    @Slot(object)
    def _on_progress(self, ev: ProgressEvent) -> None:
        try:
            pct = max(0.0, min(100.0, float(ev.percent)))
        except Exception:
            pct = 0.0
        self.stage_bar.setValue(int(pct))

        parts = [f"{ev.stage}: {ev.label}", f"{pct:.1f}%"]
        if ev.eta_seconds and ev.eta_seconds > 0:
            parts.append(f"ETA {int(ev.eta_seconds)}s")
        if ev.speed and ev.speed > 0:
            parts.append(f"{ev.speed:.1f} fps")
        self.status_label.setText(" • ".join(parts))

    @Slot(object)
    def _on_finished(self, result: ConversionResult) -> None:
        self.cancel_btn.setEnabled(False)
        self._update_start_button()

        if result.success:
            self.stage_bar.setValue(100)
            self.status_label.setText("Done.")

            if result.master_playlist:
                mp = Path(result.master_playlist)
                if mp.is_file():
                    self.player_path_edit.setText(str(mp))
                    self._append_log(
                        "INFO",
                        f"Player ready: click Play in the Player tab to "
                        f"preview {mp.name}.",
                    )

            msg = "Conversion completed successfully."
            if result.master_playlist:
                msg += f"\n\nMaster playlist:\n{result.master_playlist}"
            QMessageBox.information(self, "HLS Converter", msg)
        else:
            self._set_progress_error_color()

            if self._last_error_was_disk_full:
                friendly = (
                    "Out of disk space on the output drive.\n\n"
                    "Free up space on the destination drive, or choose a "
                    "different output folder, then try again.\n\n"
                    "Partial files from this run were left in the output "
                    "folder so you can inspect them, but they will be "
                    "removed automatically on the next attempt unless "
                    "you kept the “Clean output first” checkbox off."
                )
            else:
                friendly = _friendly_error_summary(result.error or "")

            self.status_label.setText(f"Failed: {friendly.splitlines()[0]}")
            QMessageBox.critical(
                self, "HLS Converter",
                f"Conversion failed:\n\n{friendly}",
            )

        self.worker = None

    # ------------------------------------------------------------------
    # Log save
    # ------------------------------------------------------------------

    def _save_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save log", str(Path.home() / "hls-log.txt"),
            "Text files (*.txt);;All files (*)",
        )
        if path:
            Path(path).write_text(
                self.log_view.toPlainText(), encoding="utf-8",
            )

    # ------------------------------------------------------------------
    # Close handler
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        try:
            self._hls_server.stop()
        except Exception:
            pass

        try:
            if self._player is not None:
                self._player.stop()
        except Exception:
            pass

        if self.worker and self.worker.isRunning():
            reply = QMessageBox.question(
                self,
                "Conversion in progress",
                "A conversion is still running. Cancel it and quit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.worker.cancel()
                self.worker.wait(5000)
                event.accept()
            else:
                event.ignore()
                return
        else:
            pass

        # Auto-save settings on close.
        try:
            if self._settings_db is not None:
                self._settings_db.set_many(self._collect_settings())
        except Exception:
            pass

        # Release the settings DB lock (allows the file to be deleted
        # afterwards from outside the app).
        try:
            if self._settings_db is not None:
                self._settings_db.close()
        except Exception:
            pass

        event.accept()


# ==================================================================
# Entry point
# ==================================================================

def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_AUTHOR)
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#f0f0f0"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#000000"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f5f5f5"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffdc"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#000000"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#000000"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#f0f0f0"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#000000"))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ff0000"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#308cc6"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorGroup.Disabled,
                     QPalette.ColorRole.Text, QColor("#a0a0a0"))
    palette.setColor(QPalette.ColorGroup.Disabled,
                     QPalette.ColorRole.ButtonText, QColor("#a0a0a0"))
    palette.setColor(QPalette.ColorGroup.Disabled,
                     QPalette.ColorRole.WindowText, QColor("#a0a0a0"))
    app.setPalette(palette)

    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())