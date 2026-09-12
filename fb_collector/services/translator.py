import base64
import mimetypes
import time
import threading
from collections import OrderedDict
from pathlib import Path

import requests

from ..db import setting_get


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
GROQ_TRANSLATE_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_TRANSLATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"
DEFAULT_GROQ_VISION_MODEL = "qwen/qwen3.6-27b"
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
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


def translation_config():
    try:
        provider = setting_get("translation_provider", "auto") or "auto"
        return {
            "provider": provider if provider in {"auto", "groq", "gemini", "free"} else "auto",
            "groq_api_key": setting_get("groq_api_key", "").strip(),
            "groq_model": setting_get("groq_model", DEFAULT_GROQ_MODEL).strip() or DEFAULT_GROQ_MODEL,
            "groq_vision_model": setting_get("groq_vision_model", DEFAULT_GROQ_VISION_MODEL).strip() or DEFAULT_GROQ_VISION_MODEL,
            "gemini_api_key": setting_get("gemini_api_key", "").strip(),
            "gemini_model": setting_get("gemini_model", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL,
            "ai_ocr_enabled": setting_get("ai_ocr_enabled", "0") == "1",
        }
    except Exception:
        return {
            "provider": "auto",
            "groq_api_key": "",
            "groq_model": DEFAULT_GROQ_MODEL,
            "groq_vision_model": DEFAULT_GROQ_VISION_MODEL,
            "gemini_api_key": "",
            "gemini_model": DEFAULT_GEMINI_MODEL,
            "ai_ocr_enabled": False,
        }


def clear_translation_cache():
    with _translate_lock:
        _translation_cache.clear()


def test_translation_service():
    config = translation_config()
    provider = config["provider"]
    if provider == "free":
        return {"ok": False, "provider": "免费备用线路", "text": "", "error": "请选择 Groq、Google Gemini 或自动选择。"}
    if provider == "auto":
        if config["gemini_api_key"]:
            provider = "gemini"
        elif config["groq_api_key"]:
            provider = "groq"
        else:
            return {"ok": False, "provider": "自动选择", "text": "", "error": "尚未配置 Gemini 或 Groq API Key。"}
    try:
        sample = "Bonjour, ceci est un test de traduction OCR et audio."
        if provider == "gemini":
            if not config["gemini_api_key"]:
                raise TranslateError("Gemini API Key 未配置")
            text = translate_with_gemini(sample, config["gemini_api_key"], config["gemini_model"])
            label = "Google Gemini"
        else:
            if not config["groq_api_key"]:
                raise TranslateError("Groq API Key 未配置")
            text = translate_with_groq(sample, config["groq_api_key"], config["groq_model"])
            label = "Groq"
        return {"ok": True, "provider": label, "text": text, "error": ""}
    except Exception as exc:
        return {"ok": False, "provider": provider, "text": "", "error": safe_api_error(exc)}


def ocr_image_with_ai(image_path):
    config = translation_config()
    if not config.get("ai_ocr_enabled"):
        return {"text": "", "error": "", "provider": ""}
    provider = config["provider"]
    if provider == "free":
        return {"text": "", "error": "", "provider": ""}
    if provider == "auto":
        if config["gemini_api_key"]:
            provider = "gemini"
        elif config["groq_api_key"]:
            provider = "groq"
        else:
            return {"text": "", "error": "", "provider": ""}
    try:
        image_data, mime_type = encoded_image(image_path)
        if provider == "gemini":
            if not config["gemini_api_key"]:
                raise TranslateError("Gemini API Key 未配置")
            text = ocr_with_gemini(
                image_data,
                mime_type,
                config["gemini_api_key"],
                config["gemini_model"],
            )
            label = "gemini"
        else:
            if not config["groq_api_key"]:
                raise TranslateError("Groq API Key 未配置")
            text = ocr_with_groq(
                image_data,
                mime_type,
                config["groq_api_key"],
                config["groq_vision_model"],
            )
            label = "groq"
        return {"text": clean_ai_ocr_text(text), "error": "", "provider": label}
    except Exception as exc:
        return {"text": "", "error": safe_api_error(exc), "provider": provider}


def encoded_image(image_path):
    path = Path(image_path)
    data = path.read_bytes()
    if len(data) > 15 * 1024 * 1024:
        raise TranslateError("图片超过 AI OCR 的 15MB 限制")
    mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    if mime_type == "image/jpg":
        mime_type = "image/jpeg"
    return base64.b64encode(data).decode("ascii"), mime_type


def ocr_with_gemini(image_data, mime_type, api_key, model=DEFAULT_GEMINI_MODEL):
    model = validate_model_name(model, DEFAULT_GEMINI_MODEL)
    response = requests.post(
        GEMINI_TRANSLATE_URL.format(model=model),
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json={
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"inline_data": {"mime_type": mime_type, "data": image_data}},
                        {"text": "Extract every visible word from this image exactly in its original language. Preserve line breaks. Do not translate or describe the image. Return [NO_TEXT] only when there is no visible text."},
                    ],
                }
            ],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 4096},
        },
        timeout=90,
    )
    response.raise_for_status()
    data = response.json()
    candidates = data.get("candidates") or []
    parts = (((candidates[0] if candidates else {}).get("content") or {}).get("parts") or [])
    return "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict)).strip()


def ocr_with_groq(image_data, mime_type, api_key, model=DEFAULT_GROQ_VISION_MODEL):
    response = requests.post(
        GROQ_TRANSLATE_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": validate_model_name(model, DEFAULT_GROQ_VISION_MODEL, allow_slash=True),
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract every visible word from this image exactly in its original language. Preserve line breaks. Do not translate or describe the image. Return [NO_TEXT] only when there is no visible text."},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_data}"}},
                    ],
                }
            ],
            "temperature": 0,
            "max_completion_tokens": 4096,
        },
        timeout=90,
    )
    response.raise_for_status()
    data = response.json()
    return str((((data.get("choices") or [{}])[0].get("message") or {}).get("content")) or "").strip()


def clean_ai_ocr_text(text):
    value = str(text or "").strip()
    if value.upper() in {"[NO_TEXT]", "NO_TEXT", "NO TEXT"}:
        return ""
    if value.startswith("```") and value.endswith("```"):
        value = value[3:-3].strip()
        if value.lower().startswith("text\n"):
            value = value[5:]
    return value.strip()


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


def translate_chunk(text, config=None):
    global _last_request_at, _google_cooldown_until
    config = dict(config or translation_config())
    provider = config.get("provider") or "auto"
    cache_key = (
        provider,
        config.get("groq_model") or "",
        config.get("gemini_model") or "",
        str(text or ""),
    )
    with _translate_lock:
        cached = _translation_cache.get(cache_key)
        if cached is not None:
            _translation_cache.move_to_end(cache_key)
            return cached

        errors = []
        ai_providers = []
        if provider == "auto":
            ai_providers = ["gemini", "groq"]
        elif provider in {"gemini", "groq"}:
            ai_providers = [provider]
        for ai_provider in ai_providers:
            api_key = str(config.get(f"{ai_provider}_api_key") or "").strip()
            if not api_key:
                errors.append(f"{ai_provider}: API Key 未配置")
                continue
            try:
                elapsed = time.monotonic() - _last_request_at
                if elapsed < MIN_REQUEST_INTERVAL:
                    time.sleep(MIN_REQUEST_INTERVAL - elapsed)
                if ai_provider == "gemini":
                    translated = translate_with_gemini(
                        text,
                        api_key,
                        config.get("gemini_model") or DEFAULT_GEMINI_MODEL,
                    )
                else:
                    translated = translate_with_groq(
                        text,
                        api_key,
                        config.get("groq_model") or DEFAULT_GROQ_MODEL,
                    )
                _last_request_at = time.monotonic()
                if translated:
                    return remember_translation(cache_key, translated)
                errors.append(f"{ai_provider}: empty translation result")
            except Exception as exc:
                _last_request_at = time.monotonic()
                errors.append(f"{ai_provider}: {safe_api_error(exc)}")

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
                    headers={"User-Agent": "FBPostCollector/1.4.5"},
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


def translate_with_groq(text, api_key, model=DEFAULT_GROQ_MODEL):
    response = requests.post(
        GROQ_TRANSLATE_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": validate_model_name(model, DEFAULT_GROQ_MODEL, allow_slash=True),
            "messages": [
                {
                    "role": "system",
                    "content": "You are a translation engine. Translate the user text into Simplified Chinese. Preserve names, numbers, line breaks, and meaning. Return only the translation.",
                },
                {"role": "user", "content": str(text or "")},
            ],
            "temperature": 0,
            "max_completion_tokens": 4096,
        },
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    translated = str((((data.get("choices") or [{}])[0].get("message") or {}).get("content")) or "").strip()
    if not translated:
        raise TranslateError("Groq returned empty content")
    return translated


def translate_with_gemini(text, api_key, model=DEFAULT_GEMINI_MODEL):
    model = validate_model_name(model, DEFAULT_GEMINI_MODEL)
    response = requests.post(
        GEMINI_TRANSLATE_URL.format(model=model),
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json={
            "system_instruction": {
                "parts": [
                    {
                        "text": "Translate the supplied text into Simplified Chinese. Preserve names, numbers, line breaks, and meaning. Return only the translation."
                    }
                ]
            },
            "contents": [{"role": "user", "parts": [{"text": str(text or "")}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 4096},
        },
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    candidates = data.get("candidates") or []
    parts = (((candidates[0] if candidates else {}).get("content") or {}).get("parts") or [])
    translated = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict)).strip()
    if not translated:
        raise TranslateError("Gemini returned empty content")
    return translated


def validate_model_name(model, default, allow_slash=False):
    allowed = r"[A-Za-z0-9._/-]{1,100}" if allow_slash else r"[A-Za-z0-9._-]{1,100}"
    import re

    value = str(model or "").strip()
    return value if re.fullmatch(allowed, value) else default


def safe_api_error(exc):
    if isinstance(exc, TranslateError):
        return str(exc)[:300]
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status:
        return f"HTTP {status}"
    return type(exc).__name__


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
