import time
import threading
from collections import OrderedDict

import requests


GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
GOOGLE_TRANSLATE_URLS = (
    GOOGLE_TRANSLATE_URL,
    "https://translate.google.com/translate_a/single",
)
MYMEMORY_TRANSLATE_URL = "https://api.mymemory.translated.net/get"
MAX_TRANSLATE_CHUNK = 1800
MYMEMORY_MAX_BYTES = 450
MIN_REQUEST_INTERVAL = 0.8
MAX_CACHE_ITEMS = 1000
GOOGLE_COOLDOWN_SECONDS = 600
_translate_lock = threading.Lock()
_translation_cache = OrderedDict()
_last_request_at = 0.0
_google_cooldown_until = 0.0


class TranslateError(Exception):
    pass


def translate_to_chinese(text, driver=None):
    result = translate_to_chinese_detail(text, driver)
    return result["text"]


def translate_to_chinese_detail(text, driver=None):
    if not text:
        return {"text": "", "error": ""}
    try:
        chunks = split_text(text)
        translated = []
        errors = []
        for chunk in chunks:
            part = translate_chunk(chunk)
            if part:
                translated.append(part)
            else:
                errors.append("empty translation result")
        return {"text": "\n".join(translated).strip(), "error": " | ".join(errors)}
    except Exception as exc:
        return {"text": "", "error": repr(exc)}


def split_text(text):
    text = str(text or "").strip()
    if len(text) <= MAX_TRANSLATE_CHUNK:
        return [text]
    chunks = []
    current = []
    current_len = 0
    for paragraph in text.splitlines():
        piece = paragraph.strip()
        if not piece:
            continue
        if current and current_len + len(piece) + 1 > MAX_TRANSLATE_CHUNK:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        if len(piece) > MAX_TRANSLATE_CHUNK:
            for start in range(0, len(piece), MAX_TRANSLATE_CHUNK):
                chunks.append(piece[start : start + MAX_TRANSLATE_CHUNK])
            continue
        current.append(piece)
        current_len += len(piece) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks or [text[:MAX_TRANSLATE_CHUNK]]


def translate_chunk(text):
    global _last_request_at, _google_cooldown_until
    cache_key = str(text or "")
    with _translate_lock:
        cached = _translation_cache.get(cache_key)
        if cached is not None:
            _translation_cache.move_to_end(cache_key)
            return cached

        errors = []
        if time.monotonic() >= _google_cooldown_until:
            for attempt, endpoint in enumerate(GOOGLE_TRANSLATE_URLS):
                if attempt:
                    time.sleep(1)
                elapsed = time.monotonic() - _last_request_at
                if elapsed < MIN_REQUEST_INTERVAL:
                    time.sleep(MIN_REQUEST_INTERVAL - elapsed)
                try:
                    response = requests.get(
                        endpoint,
                        params={
                            "client": "gtx",
                            "sl": "auto",
                            "tl": "zh-CN",
                            "dt": "t",
                            "q": text,
                        },
                        timeout=30,
                        headers={
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                            "Accept": "application/json,text/plain,*/*",
                        },
                    )
                    _last_request_at = time.monotonic()
                    response.raise_for_status()
                    data = response.json()
                    parts = data[0] if data and isinstance(data[0], list) else []
                    translated = "".join(part[0] for part in parts if part and part[0]).strip()
                    if translated:
                        return remember_translation(cache_key, translated)
                    errors.append("Google: empty translation result")
                except Exception as exc:
                    _last_request_at = time.monotonic()
                    errors.append(f"Google: {exc!r}")
                    status_code = getattr(getattr(exc, "response", None), "status_code", None)
                    if status_code == 429:
                        _google_cooldown_until = time.monotonic() + GOOGLE_COOLDOWN_SECONDS
                    elif status_code is not None and status_code not in {408, 425} and status_code < 500:
                        break

        try:
            parts = []
            for piece in split_utf8_bytes(text, MYMEMORY_MAX_BYTES):
                elapsed = time.monotonic() - _last_request_at
                if elapsed < MIN_REQUEST_INTERVAL:
                    time.sleep(MIN_REQUEST_INTERVAL - elapsed)
                response = requests.get(
                    MYMEMORY_TRANSLATE_URL,
                    params={"q": piece, "langpair": "autodetect|zh-CN", "mt": "1"},
                    timeout=30,
                    headers={"User-Agent": "FBPostCollector/1.4.3"},
                )
                _last_request_at = time.monotonic()
                response.raise_for_status()
                data = response.json()
                translated = str((data.get("responseData") or {}).get("translatedText") or "").strip()
                if not translated or int(data.get("responseStatus") or 200) >= 400:
                    raise TranslateError(str(data.get("responseDetails") or "empty MyMemory translation result"))
                parts.append(translated)
            return remember_translation(cache_key, "\n".join(parts).strip())
        except Exception as exc:
            errors.append(f"MyMemory: {exc!r}")
            raise TranslateError(" | ".join(errors)) from exc


def remember_translation(key, translated):
    _translation_cache[key] = translated
    _translation_cache.move_to_end(key)
    while len(_translation_cache) > MAX_CACHE_ITEMS:
        _translation_cache.popitem(last=False)
    return translated


def split_utf8_bytes(text, limit):
    pieces = []
    current = []
    current_bytes = 0
    for char in str(text or ""):
        size = len(char.encode("utf-8"))
        if current and current_bytes + size > limit:
            pieces.append("".join(current))
            current = []
            current_bytes = 0
        current.append(char)
        current_bytes += size
    if current:
        pieces.append("".join(current))
    return pieces or [""]
