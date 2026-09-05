import re
import threading
import time

import requests

from ..config import APP_VERSION


REPOSITORY = "secure-artifacts/fb-post-collector"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
CHECK_CACHE_SECONDS = 600
_CACHE_LOCK = threading.Lock()
_CACHE = {"checked_at": 0.0, "value": None}


def version_tuple(value):
    numbers = [int(item) for item in re.findall(r"\d+", str(value or ""))[:4]]
    return tuple((numbers + [0, 0, 0, 0])[:4])


def preferred_assets(assets):
    output = []
    priority = {".exe": 0, ".msi": 1, ".zip": 2}
    for asset in assets or []:
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        suffix = next((item for item in priority if name.lower().endswith(item)), "")
        if not suffix or not url.startswith("https://github.com/"):
            continue
        output.append(
            {
                "name": name,
                "url": url,
                "size": int(asset.get("size") or 0),
                "kind": suffix.lstrip(".").upper(),
            }
        )
    return sorted(output, key=lambda item: priority.get("." + item["kind"].lower(), 9))


def check_for_update(force=False):
    now = time.time()
    with _CACHE_LOCK:
        if not force and _CACHE["value"] and now - _CACHE["checked_at"] < CHECK_CACHE_SECONDS:
            return dict(_CACHE["value"])

    response = requests.get(
        LATEST_RELEASE_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"FBPostCollector/{APP_VERSION}"},
        timeout=12,
    )
    response.raise_for_status()
    release = response.json()
    latest_version = str(release.get("tag_name") or "").lstrip("vV")
    release_url = str(release.get("html_url") or "")
    if not release_url.startswith("https://github.com/"):
        release_url = f"https://github.com/{REPOSITORY}/releases"
    value = {
        "ok": True,
        "current_version": APP_VERSION,
        "latest_version": latest_version,
        "update_available": version_tuple(latest_version) > version_tuple(APP_VERSION),
        "release_name": release.get("name") or release.get("tag_name") or "",
        "release_notes": release.get("body") or "",
        "published_at": release.get("published_at") or "",
        "release_url": release_url,
        "assets": preferred_assets(release.get("assets")),
    }
    with _CACHE_LOCK:
        _CACHE.update({"checked_at": now, "value": value})
    return dict(value)
