import os
import time
import shutil
from pathlib import Path

from ..config import DATA_DIR, TEMP_DIR
from .environment import detect_tools
from .errors import AudioError
from .proc import run_hidden


AUDIO_ERROR = "\u97f3\u9891\u8bc6\u522b\u5931\u8d25"
DEBUG_TEXT_DIR = DATA_DIR / "debug_text"


def _tail(text, limit=1200):
    text = (text or "").strip()
    return text[-limit:] if len(text) > limit else text


def _recent_txt_files(started_at):
    paths = []
    for path in TEMP_DIR.glob("*.txt"):
        try:
            if path.stat().st_mtime >= started_at - 2:
                paths.append(path)
        except OSError:
            continue
    return sorted(paths, key=lambda item: item.stat().st_mtime, reverse=True)


def _read_whisper_text(expected_path, stem, started_at, whisper_proc=None):
    candidates = [Path(expected_path)]
    candidates.extend(sorted(TEMP_DIR.glob(f"{stem}*.txt"), key=lambda item: item.stat().st_mtime, reverse=True))
    candidates.extend(_recent_txt_files(started_at))

    unique_candidates = []
    seen = set()
    for path in candidates:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique_candidates.append(path)

    deadline = time.time() + 10
    while time.time() < deadline:
        for path in unique_candidates + _recent_txt_files(started_at):
            if path.exists():
                text = path.read_text(encoding="utf-8", errors="ignore").strip()
                if text:
                    return text, path
        time.sleep(0.5)

    existing = [str(path) for path in unique_candidates if path.exists()]
    if existing:
        raise RuntimeError(f"transcript file is empty: {existing[0]}")
    output_files = []
    for path in _recent_txt_files(started_at):
        try:
            output_files.append(f"{path} ({path.stat().st_size} bytes)")
        except OSError:
            output_files.append(str(path))
    stdout = _tail(getattr(whisper_proc, "stdout", ""))
    stderr = _tail(getattr(whisper_proc, "stderr", ""))
    raise RuntimeError(
        "transcript file not created: "
        f"{expected_path}; recent_txt={output_files}; stdout={stdout!r}; stderr={stderr!r}"
    )


def transcribe_video(video_path, language="", debug=False):
    tools = detect_tools()
    if not tools["ffmpeg"]["available"]:
        raise AudioError(AUDIO_ERROR, "ffmpeg is not available")
    if not tools["whisper"]["available"]:
        raise AudioError(AUDIO_ERROR, "whisper is not installed")

    video = Path(video_path)
    audio = TEMP_DIR / f"{video.stem}.mp3"
    txt_path = TEMP_DIR / f"{audio.stem}.txt"

    try:
        ffmpeg_proc = run_hidden(
            [tools["ffmpeg"]["path"], "-y", "-i", str(video), str(audio)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        if ffmpeg_proc.returncode != 0:
            raise RuntimeError((ffmpeg_proc.stderr or ffmpeg_proc.stdout or "").strip())
        if not audio.exists() or audio.stat().st_size == 0:
            raise RuntimeError(f"audio file not created: {audio}")

        cmd = [
            tools["whisper"]["path"],
            str(audio),
            "--model",
            "large",
        ]
        if language:
            cmd.extend(["--language", language])
        cmd.extend(["-o", str(TEMP_DIR), "--output_format", "txt"])

        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        started_at = time.time()
        whisper_proc = run_hidden(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
            cwd=str(TEMP_DIR),
            env=env,
        )
        if whisper_proc.returncode != 0:
            raise RuntimeError((whisper_proc.stderr or whisper_proc.stdout or "").strip())
        text, transcript_path = _read_whisper_text(txt_path, audio.stem, started_at, whisper_proc)
        if debug and transcript_path:
            DEBUG_TEXT_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(transcript_path, DEBUG_TEXT_DIR / transcript_path.name)
        return text
    except AudioError:
        raise
    except Exception as exc:
        raise AudioError(AUDIO_ERROR, repr(exc)) from exc
    finally:
        try:
            audio.unlink(missing_ok=True)
        except Exception:
            pass
        if not debug:
            for path in TEMP_DIR.glob(f"{audio.stem}*.txt"):
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass
