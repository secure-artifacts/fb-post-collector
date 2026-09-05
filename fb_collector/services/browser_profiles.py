import json
import os
import base64
import mimetypes
import shutil
import subprocess
import urllib.request
from pathlib import Path

from ..config import DATA_DIR


def _read_local_state(user_data: Path) -> dict:
    local_state = user_data / "Local State"
    if not local_state.exists():
        return {}
    try:
        data = json.loads(local_state.read_text(encoding="utf-8", errors="ignore"))
        return data.get("profile", {}).get("info_cache", {}) or {}
    except Exception:
        return {}


def _avatar_data_url(profile_dir: Path, info: dict) -> str:
    file_name = info.get("gaia_picture_file_name") or "Google Profile Picture.png"
    avatar_path = profile_dir / file_name
    if not avatar_path.exists():
        return ""
    try:
        mime = mimetypes.guess_type(str(avatar_path))[0] or "image/png"
        data = base64.b64encode(avatar_path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{data}"
    except Exception:
        return ""


def _profile_label(profile_dir: Path, info: dict) -> str:
    name = info.get("name") or info.get("gaia_name") or info.get("user_name")
    if name:
        return f"{profile_dir.name} ({name})"
    prefs = profile_dir / "Preferences"
    if prefs.exists():
        try:
            data = json.loads(prefs.read_text(encoding="utf-8", errors="ignore"))
            name = data.get("profile", {}).get("name") or data.get("account_info", [{}])[0].get("email")
            if name:
                return f"{profile_dir.name} ({name})"
        except Exception:
            pass
    return profile_dir.name


def chrome_user_data_dirs():
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    return [
        ("chrome", local / "Google" / "Chrome" / "User Data"),
        ("edge", local / "Microsoft" / "Edge" / "User Data"),
    ]


def chrome_executable():
    candidates = [
        Path(os.environ.get("ProgramFiles", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        shutil.which("chrome") or "",
        shutil.which("chrome.exe") or "",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    return "chrome"


def account_debug_port(account_id):
    return 9330 + int(account_id)


def debug_port_alive(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}/json/version", timeout=1) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def read_devtools_port(user_data_dir):
    path = Path(user_data_dir) / "DevToolsActivePort"
    try:
        first = path.read_text(encoding="utf-8", errors="ignore").splitlines()[0].strip()
        return int(first)
    except (OSError, IndexError, ValueError):
        return None


def chrome_using_profile(user_data_dir):
    needle = str(Path(user_data_dir)).lower()
    if not needle:
        return False
    for line in chrome_command_lines():
        lowered = line.lower()
        if "--user-data-dir" in lowered and needle in lowered:
            return True
    return False


def chrome_command_lines():
    if os.name != "nt":
        return []
    creationflags = subprocess.CREATE_NO_WINDOW
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | ForEach-Object { $_.CommandLine }",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=creationflags,
        )
    except Exception:
        return []
    return [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]


def profile_lock_files(user_data_dir):
    path = Path(user_data_dir)
    names = ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile")
    return [path / name for name in names if (path / name).exists() or (path / name).is_symlink()]


def clear_profile_locks(user_data_dir):
    for lock in profile_lock_files(user_data_dir):
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            pass


def clear_stale_profile_locks(user_data_dir):
    if not profile_lock_files(user_data_dir):
        return True
    if chrome_using_profile(user_data_dir):
        return False
    clear_profile_locks(user_data_dir)
    return True


def list_profiles():
    profiles = []
    dedicated = DATA_DIR / "browser_profiles" / "chrome_dedicated"
    profiles.append(
        {
            "browser_type": "chrome",
            "mode": "dedicated",
            "name": "专用抓取配置",
            "display_name": "专用抓取配置",
            "account_name": "",
            "email": "",
            "avatar_data_url": "",
            "initial": "专",
            "path": str(dedicated),
            "profile_directory": "Default",
            "value": str(dedicated),
        }
    )
    for browser_type, user_data in chrome_user_data_dirs():
        if not user_data.exists():
            continue
        info_cache = _read_local_state(user_data)
        for profile_dir in sorted(user_data.iterdir(), key=lambda p: p.name):
            if profile_dir.name != "Default" and not profile_dir.name.startswith("Profile "):
                continue
            info = info_cache.get(profile_dir.name, {})
            display_name = info.get("name") or profile_dir.name
            account_name = info.get("gaia_name") or info.get("gaia_given_name") or ""
            email = info.get("user_name") or ""
            value = f"{user_data}|{profile_dir.name}"
            profiles.append(
                {
                    "browser_type": browser_type,
                    "mode": "existing",
                    "name": f"{browser_type}: {_profile_label(profile_dir, info)}",
                    "display_name": display_name,
                    "account_name": account_name,
                    "email": email,
                    "avatar_data_url": _avatar_data_url(profile_dir, info),
                    "initial": (display_name or account_name or profile_dir.name or "?")[0].upper(),
                    "path": str(user_data),
                    "profile_directory": profile_dir.name,
                    "value": value,
                }
            )
    return profiles
