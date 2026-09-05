import time

import requests


GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
MAX_TRANSLATE_CHUNK = 1800


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
    last_error = None
    for attempt in range(3):
        try:
            response = requests.get(
                GOOGLE_TRANSLATE_URL,
                params={
                    "client": "gtx",
                    "sl": "auto",
                    "tl": "zh-CN",
                    "dt": "t",
                    "q": text,
                },
                timeout=30,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            response.raise_for_status()
            data = response.json()
            parts = data[0] if data and isinstance(data[0], list) else []
            translated = "".join(part[0] for part in parts if part and part[0]).strip()
            if translated:
                return translated
            last_error = TranslateError("empty translation result")
        except Exception as exc:
            last_error = exc
        if attempt < 2:
            time.sleep(0.5 * (attempt + 1))
    raise TranslateError(repr(last_error))
