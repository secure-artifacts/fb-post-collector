import os
import shutil
import subprocess
from pathlib import Path

from ..config import BASE_DIR, DATA_DIR, TOOLS_DIR
from .browser_profiles import chrome_executable

USER_TOOLS_DIR = DATA_DIR / "tools"
TESSERACT_LANGS = ("eng", "por", "ara", "chi_sim")


def first_existing(paths):
    for path in paths:
        if path and Path(path).exists():
            return str(Path(path))
    return ""


def command_available(command):
    candidate = Path(command) if command else None
    found = str(candidate) if candidate and candidate.exists() else shutil.which(command)
    if not found:
        return {"available": False, "path": "", "version": ""}
    version = ""
    try:
        proc = subprocess.run([found, "--version"], capture_output=True, text=True, timeout=5)
        version = (proc.stdout or proc.stderr).splitlines()[0] if (proc.stdout or proc.stderr) else ""
    except Exception:
        version = "已找到，但版本检测失败"
    return {"available": True, "path": found, "version": version}


def tessdata_dirs(tesseract_path=""):
    dirs = []
    if tesseract_path:
        dirs.append(Path(tesseract_path).parent / "tessdata")
    dirs.extend(
        [
            USER_TOOLS_DIR / "tesseract" / "tessdata",
            TOOLS_DIR / "tesseract" / "tessdata",
        ]
    )
    unique = []
    seen = set()
    for directory in dirs:
        key = str(directory).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(directory)
    return unique


def lang_available(directories, lang):
    return any((Path(directory) / f"{lang}.traineddata").exists() for directory in directories)


def detect_tools():
    winget_links = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links"
    local_programs = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs"
    tesseract_path = first_existing(
        [
            TOOLS_DIR / "tesseract" / "tesseract.exe",
            USER_TOOLS_DIR / "tesseract" / "tesseract.exe",
            Path(os.environ.get("ProgramFiles", "")) / "Tesseract-OCR" / "tesseract.exe",
            Path(os.environ.get("ProgramFiles(x86)", "")) / "Tesseract-OCR" / "tesseract.exe",
            local_programs / "Tesseract-OCR" / "tesseract.exe",
            winget_links / "tesseract.exe",
            shutil.which("tesseract") or "",
        ]
    )
    ffmpeg_path = first_existing(
        [
            TOOLS_DIR / "ffmpeg" / "ffmpeg.exe",
            USER_TOOLS_DIR / "ffmpeg" / "ffmpeg.exe",
            winget_links / "ffmpeg.exe",
            Path(os.environ.get("ProgramFiles", "")) / "ffmpeg" / "bin" / "ffmpeg.exe",
            shutil.which("ffmpeg") or "",
        ]
    )
    whisper = command_available("whisper")
    ytdlp_path = first_existing(
        [
            TOOLS_DIR / "yt-dlp.exe",
            TOOLS_DIR / "yt-dlp" / "yt-dlp.exe",
            USER_TOOLS_DIR / "yt-dlp.exe",
            USER_TOOLS_DIR / "yt-dlp" / "yt-dlp.exe",
            winget_links / "yt-dlp.exe",
            shutil.which("yt-dlp.exe") or "",
            shutil.which("yt-dlp") or "",
        ]
    )
    ytdlp = command_available(ytdlp_path) if ytdlp_path else {"available": False, "path": "", "version": ""}
    tessdata = tessdata_dirs(tesseract_path)
    chrome_path = chrome_executable()
    chrome_available = bool(chrome_path and Path(chrome_path).exists())
    return {
        "base_dir": str(BASE_DIR),
        "tessdata_dirs": [str(path) for path in tessdata],
        "tesseract": {
            "available": bool(tesseract_path),
            "path": tesseract_path,
            **{lang: lang_available(tessdata, lang) for lang in TESSERACT_LANGS},
        },
        "ffmpeg": {"available": bool(ffmpeg_path), "path": ffmpeg_path},
        "whisper": whisper,
        "yt_dlp": ytdlp,
        "chrome": {
            "available": chrome_available,
            "path": chrome_path if chrome_available else "",
        },
    }
