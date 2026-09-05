import json
import os
import shutil
import subprocess
import sys
import threading
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from ..config import DATA_DIR, TOOLS_DIR
from .environment import TESSERACT_LANGS, detect_tools


INSTALL_LOG_PATH = DATA_DIR / "component_install.log"
INSTALL_LOCK = threading.Lock()
INSTALL_STATE = {
    "status": "idle",
    "components": [],
    "current": "",
    "message": "",
    "results": [],
    "started_at": "",
    "finished_at": "",
}
COMPONENT_LABELS = {
    "tesseract": "Tesseract OCR及语言包",
    "ffmpeg": "ffmpeg",
    "whisper": "Whisper语音识别",
    "yt_dlp": "yt-dlp",
}
WINGET_PACKAGES = {
    "tesseract": "UB-Mannheim.TesseractOCR",
    "ffmpeg": "Gyan.FFmpeg",
    "yt_dlp": "yt-dlp.yt-dlp",
}
TESSDATA_URLS = {
    "eng": "https://github.com/tesseract-ocr/tessdata/raw/main/eng.traineddata",
    "por": "https://github.com/tesseract-ocr/tessdata/raw/main/por.traineddata",
    "ara": "https://github.com/tesseract-ocr/tessdata/raw/main/ara.traineddata",
    "chi_sim": "https://github.com/tesseract-ocr/tessdata/raw/main/chi_sim.traineddata",
}
YT_DLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"


def now_text():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def installation_status():
    with INSTALL_LOCK:
        return dict(INSTALL_STATE)


def start_installation(components):
    requested = []
    for component in components or []:
        if component in COMPONENT_LABELS and component not in requested:
            requested.append(component)
    if not requested:
        raise ValueError("没有可安装的缺失组件")
    with INSTALL_LOCK:
        if INSTALL_STATE["status"] == "running":
            raise RuntimeError("组件安装正在进行中")
        INSTALL_STATE.update(
            {
                "status": "running",
                "components": requested,
                "current": "",
                "message": "准备安装缺失组件",
                "results": [],
                "started_at": now_text(),
                "finished_at": "",
            }
        )
    thread = threading.Thread(target=_install_worker, args=(requested,), daemon=True)
    thread.start()
    return installation_status()


def _install_worker(components):
    results = []
    for component in components:
        label = COMPONENT_LABELS[component]
        with INSTALL_LOCK:
            INSTALL_STATE["current"] = component
            INSTALL_STATE["message"] = f"正在安装：{label}"
        try:
            result = install_one(component)
        except Exception as exc:
            result = {
                "component": component,
                "label": label,
                "success": False,
                "returncode": -1,
                "output": repr(exc),
            }
        results.append(result)
        append_install_log(result)
        with INSTALL_LOCK:
            INSTALL_STATE["results"] = list(results)

    failed = [item for item in results if not item["success"]]
    with INSTALL_LOCK:
        INSTALL_STATE.update(
            {
                "status": "failed" if failed else "success",
                "current": "",
                "message": f"安装完成：成功 {len(results) - len(failed)}，失败 {len(failed)}",
                "results": results,
                "finished_at": now_text(),
            }
        )


def install_one(component):
    label = COMPONENT_LABELS[component]
    if component == "tesseract":
        return install_tesseract()
    if component == "yt_dlp":
        return install_yt_dlp()
    command = install_command(component)
    completed = run_command(command)
    return {
        "component": component,
        "label": label,
        "success": completed.returncode == 0,
        "returncode": completed.returncode,
        "output": tail_output(completed.stdout, completed.stderr),
    }


def install_command(component):
    if component == "whisper":
        python_command = external_python_command()
        if not python_command:
            raise RuntimeError("未检测到可安装组件的 Python。请先安装 Python 3.10 或更高版本。")
        return python_command + ["-m", "pip", "install", "--upgrade", "openai-whisper"]

    package_id = WINGET_PACKAGES.get(component)
    winget = shutil.which("winget")
    if not winget:
        raise RuntimeError("未检测到 winget，请先安装或更新 Microsoft App Installer。")
    return [
        winget,
        "install",
        "--exact",
        "--id",
        package_id,
        "--scope",
        "user",
        "--silent",
        "--accept-source-agreements",
        "--accept-package-agreements",
    ]


def writable_tools_dir():
    for path in (TOOLS_DIR, DATA_DIR / "tools"):
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return path
        except OSError:
            continue
    fallback = DATA_DIR / "tools"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def is_writable_dir(path):
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def writable_tessdata_dir(tesseract_path=""):
    if tesseract_path:
        native = Path(tesseract_path).parent / "tessdata"
        if is_writable_dir(native):
            return native
    dest = writable_tools_dir() / "tesseract" / "tessdata"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def download_file(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    request = urllib.request.Request(url, headers={"User-Agent": "FBPostCollector"})
    with urllib.request.urlopen(request, timeout=180) as response, tmp.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    tmp.replace(dest)


def install_tesseract():
    notes = []
    tools = detect_tools()
    if not tools["tesseract"]["available"]:
        command = install_command("tesseract")
        completed = run_command(command)
        notes.append(tail_output(completed.stdout, completed.stderr) or "已尝试安装 Tesseract")
        if completed.returncode != 0:
            return {
                "component": "tesseract",
                "label": COMPONENT_LABELS["tesseract"],
                "success": False,
                "returncode": completed.returncode,
                "output": "\n".join(part for part in notes if part),
            }
        tools = detect_tools()
    tessdata = writable_tessdata_dir(tools["tesseract"].get("path") or "")
    missing = [lang for lang in TESSERACT_LANGS if not tools["tesseract"].get(lang)]
    for lang in missing:
        dest = tessdata / f"{lang}.traineddata"
        if dest.exists():
            continue
        download_file(TESSDATA_URLS[lang], dest)
        notes.append(f"已下载语言包 {lang}")
    tools = detect_tools()
    complete = tools["tesseract"]["available"] and all(tools["tesseract"].get(lang) for lang in TESSERACT_LANGS)
    return {
        "component": "tesseract",
        "label": COMPONENT_LABELS["tesseract"],
        "success": complete,
        "returncode": 0 if complete else -1,
        "output": "\n".join(part for part in notes if part) or ("Tesseract 及语言包已就绪" if complete else "语言包安装未完成"),
    }


def install_yt_dlp():
    dest = writable_tools_dir() / "yt-dlp.exe"
    notes = []
    try:
        download_file(YT_DLP_URL, dest)
        notes.append(f"已下载 yt-dlp 到 {dest}")
    except Exception as exc:
        notes.append(f"直接下载失败：{exc}")
        command = install_command("yt_dlp")
        completed = run_command(command)
        notes.append(tail_output(completed.stdout, completed.stderr))
        if completed.returncode != 0 and not dest.exists():
            return {
                "component": "yt_dlp",
                "label": COMPONENT_LABELS["yt_dlp"],
                "success": False,
                "returncode": completed.returncode,
                "output": "\n".join(part for part in notes if part),
            }
    tools = detect_tools()
    success = bool(tools["yt_dlp"].get("available") or dest.exists())
    return {
        "component": "yt_dlp",
        "label": COMPONENT_LABELS["yt_dlp"],
        "success": success,
        "returncode": 0 if success else -1,
        "output": "\n".join(part for part in notes if part),
    }


def external_python_command():
    candidates = []
    if not getattr(sys, "frozen", False):
        candidates.append(sys.executable)
    for name in ("python", "python3"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    py_launcher = shutil.which("py")
    if py_launcher:
        return [py_launcher, "-3"]
    for candidate in candidates:
        if candidate and Path(candidate).name.lower() not in {"fbpostcollector.exe"}:
            return [candidate]
    return []


def run_command(command):
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=3600,
        creationflags=creationflags,
    )


def tail_output(stdout, stderr, limit=4000):
    text = "\n".join(part.strip() for part in (stdout or "", stderr or "") if part.strip())
    return text[-limit:]


def append_install_log(result):
    entry = {"time": now_text(), **result}
    try:
        with INSTALL_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
